"""Run locally: python -m verivoice.app (single worker, loopback only)."""
from collections import defaultdict, deque
from pathlib import Path
import threading
import time
from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from .config import Settings, ConfigurationError, ROOT
from .enrollment import Accounts, Enrollment, MAX_AUDIO
from .providers.enrollment_api import EnrollmentProviders, EnrollmentError
from .phone import calling_regions
from .calls import CallService

class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)
    language: str = "en"
    consent: bool = False

class Registration(Credentials):
    phone_region: str = Field(min_length=2, max_length=2)
    phone_number: str = Field(min_length=1, max_length=40)

def create_app(*, settings=None, database=None, providers=None, call_speaker=None, call_identity=None):
    settings = settings or Settings.from_env()
    providers = providers or EnrollmentProviders(settings)
    accounts = Accounts(database or ROOT / "data" / "accounts.sqlite3")
    enrollment = Enrollment(accounts, providers)
    calls = CallService(accounts, settings, speaker=call_speaker, identity=call_identity)
    # Serializes operations in this local single-worker demo, including login and retries.
    lock = threading.Lock()
    attempts = defaultdict(deque)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    app.mount("/static", StaticFiles(directory=ROOT / "verivoice" / "web"), name="static")

    @app.middleware("http")
    async def protect(request, call_next):
        if request.method == "POST":
            if request.headers.get("X-Verivoice") != "enrollment":
                return JSONResponse({"error": "Reload the enrollment page and try again."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"error": "Cross-origin requests are not allowed."}, status_code=403)
            try:
                length = int(request.headers.get("content-length", "-1"))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_AUDIO:
                return JSONResponse({"error": "Missing or excessive request size."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self)"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"error": "Invalid account details or request format."}, status_code=422)

    @app.exception_handler(EnrollmentError)
    async def enrollment_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(ConfigurationError)
    async def configuration_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(Exception)
    async def provider_error(request, exc):
        # SDK exceptions can contain request text, URLs or headers. Never return them.
        return JSONResponse({"error": "Provider request failed. Check API credentials, quota and Hiya resources, then retry. Your saved progress is retained."}, status_code=502)

    def account(request):
        return accounts.authenticate(request.cookies.get("vv_session", ""))

    def respond(token):
        response = JSONResponse(enrollment.status(accounts.authenticate(token)))
        response.set_cookie("vv_session", token, httponly=True, samesite="strict", max_age=43200)
        return response

    def throttle(request):
        key = request.client.host if request.client else "local"
        now = time.monotonic()
        queue = attempts[key]
        while queue and queue[0] < now-60:
            queue.popleft()
        if len(queue) >= 10:
            raise EnrollmentError("Too many account attempts. Wait one minute and try again.")
        queue.append(now)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "verivoice" / "web" / "index.html")

    @app.get("/call")
    def call_page(request: Request):
        account(request)
        return FileResponse(ROOT / "verivoice" / "web" / "call.html")

    @app.websocket("/api/call")
    async def call_socket(socket: WebSocket):
        await calls.handle(socket)

    @app.get("/api/setup")
    def setup():
        names = ["gemini_api_key", "deepgram_api_key", "hiya_api_key", "hiya_region", "hiya_owner", "hiya_space"]
        missing = [name.upper() for name in names if not getattr(settings, name)]
        return {"ready": not missing, "missing": missing}

    @app.post("/api/register")
    def register(body: Registration, request: Request):
        with lock:
            throttle(request)
            if not body.consent:
                raise EnrollmentError("Please agree to the enrollment data flow first.")
            return respond(accounts.register(body.email, body.password, body.language, providers,
                phone_region=body.phone_region, phone_number=body.phone_number))

    @app.get("/api/calling-codes")
    def countries():
        return calling_regions()

    @app.post("/api/login")
    def login(body: Credentials, request: Request):
        with lock:
            throttle(request)
            return respond(accounts.login(body.email, body.password))

    @app.post("/api/logout")
    def logout(request: Request):
        accounts.logout(request.cookies.get("vv_session", ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie("vv_session")
        return response

    @app.get("/api/me")
    def me(request: Request):
        return enrollment.status(account(request))

    @app.post("/api/record/{index}")
    async def record(index: int, request: Request):
        account(request)  # Authenticate before receiving a recording.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_AUDIO:
                raise EnrollmentError("Recording is too large.")
        def process():
            with lock:
                return enrollment.record(account(request), index, bytes(data))
        return await run_in_threadpool(process)

    @app.post("/api/finish")
    def finish(request: Request):
        with lock:
            return enrollment.finish(account(request))

    return app

if __name__ == "__main__":
    import uvicorn
    # Avoid logging SDK exception details (which may include enrollment content).
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, access_log=False, log_level="critical")
