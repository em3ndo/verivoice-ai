import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import httpx
from fastapi.testclient import TestClient
from verivoice.app import create_app
from verivoice.config import Settings
from verivoice.enrollment import Accounts, validate_audio
from verivoice.providers.enrollment_api import EnrollmentError
from verivoice.providers.hiya_client import HiyaClient
from verivoice.providers.hiya_enrollment_api import HiyaEnrollmentAPI
from verivoice.providers.deepgram_api import DeepgramAPI


def wav(seconds=6):
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
        f.writeframes(b"\x00\x00" * int(16000*seconds))
    return out.getvalue()


class FakeProviders:
    def __init__(self):
        self.settings = Settings(hiya_owner="owner", hiya_space="main", hiya_region="us")
        self.language = "en"
        self.matches = True
        self.synthetic = False
        self.complete = True
        self.uploads = 0
        self.finishes = 0
    def ready(self): pass
    def phrases(self, language):
        self.language = language
        return [f"Sentence number {i} for a natural voice recording." for i in range(5)]
    def transcribe(self, audio): return "recognized phrase", self.language
    def phrase_matches(self, *args): return self.matches
    def upload_checked(self, audio):
        if self.synthetic: raise EnrollmentError("Synthetic speech rejected.")
        self.uploads += 1
        return f"audio-{self.uploads}"
    def finish(self, identity, audios):
        self.finishes += 1
        if not self.complete: raise EnrollmentError("Voiceprint not computed.")


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name)/"accounts.sqlite3"
        self.providers = FakeProviders()
        self.client = TestClient(create_app(settings=self.providers.settings,
            database=self.db, providers=self.providers), raise_server_exceptions=False)
        self.headers = {"X-Verivoice": "enrollment"}
        self.details = {"email": "person@example.com", "password": "a long test password", "language": "en", "consent": True}
    def tearDown(self):
        self.client.close(); self.temp.cleanup()
    def post(self, path, **kwargs):
        return self.client.post(path, headers=self.headers, **kwargs)
    def register(self):
        response = self.post("/api/register", json=self.details)
        self.assertEqual(response.status_code, 200, response.text)
        return response
    def record(self, i):
        return self.post(f"/api/record/{i}", content=wav())

    def test_full_enrollment_and_password_session_storage(self):
        response = self.register()
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        for i in range(5):
            response = self.record(i)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["accepted"], i+1)
            self.assertEqual(response.json()["state"], "pending")
        response = self.post("/api/finish", json={})
        self.assertEqual(response.json()["state"], "complete")
        self.assertTrue(response.json()["identity"].startswith("vv-"))
        self.assertNotIn(self.details["password"].encode(), self.db.read_bytes())
        self.post("/api/logout", json={})
        self.assertEqual(self.client.get("/api/me").status_code, 400)
        response = self.post("/api/login", json=self.details)
        self.assertEqual(response.json()["state"], "complete")

    def test_wrong_language_missing_language_wrong_phrase_and_synthetic(self):
        self.register()
        for language in ["ru", None]:
            self.providers.language = language
            self.assertEqual(self.record(0).status_code, 400)
        self.providers.language = "en"
        self.providers.matches = False
        self.assertEqual(self.record(0).status_code, 400)
        self.assertEqual(self.providers.uploads, 0)
        self.providers.matches = True
        self.providers.synthetic = True
        self.assertEqual(self.record(0).status_code, 400)
        self.assertEqual(self.client.get("/api/me").json()["accepted"], 0)

    def test_never_complete_without_hiya_and_retry_preserves_samples(self):
        self.register()
        self.assertEqual(self.post("/api/finish", json={}).status_code, 400)
        self.assertEqual(self.providers.finishes, 0)
        for i in range(5): self.record(i)
        self.providers.complete = False
        self.assertEqual(self.post("/api/finish", json={}).status_code, 400)
        self.assertEqual(self.client.get("/api/me").json()["state"], "pending")
        self.providers.complete = True
        self.assertEqual(self.post("/api/finish", json={}).json()["state"], "complete")
        self.assertEqual(self.providers.uploads, 5)

    def test_duplicate_and_out_of_order_samples(self):
        self.register()
        self.assertEqual(self.record(1).status_code, 400)
        self.assertEqual(self.record(0).status_code, 200)
        self.assertEqual(self.record(0).status_code, 400)
        self.assertEqual(self.providers.uploads, 1)

    def test_session_isolation(self):
        self.register(); self.record(0)
        self.post("/api/logout", json={})
        self.details["email"] = "other@example.com"
        self.register()
        self.assertEqual(self.client.get("/api/me").json()["accepted"], 0)
        self.assertEqual(self.post("/api/finish", json={}).status_code, 400)

    def test_csrf_consent_and_secret_redaction(self):
        self.assertEqual(self.client.post("/api/register", json=self.details).status_code, 403)
        self.assertEqual(self.client.post("/api/register", json=self.details,
            headers={**self.headers, "Origin": "https://evil.example"}).status_code, 403)
        self.details["consent"] = False
        self.assertEqual(self.post("/api/register", json=self.details).status_code, 400)
        self.details["password"] = "private"*30
        response = self.post("/api/register", json=self.details)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private", response.text)

    def test_supported_languages(self):
        for code in ["en", "es", "hi", "ru"]:
            self.details["email"] = code+"@example.com"
            self.details["language"] = code
            self.assertEqual(self.register().json()["language"], code)
        self.details["language"] = "zh"
        self.details["email"] = "new@example.com"
        self.assertEqual(self.post("/api/register", json=self.details).status_code, 400)

    def test_audio_limits(self):
        self.assertEqual(validate_audio(wav()), 6)
        for data in [b"bad", wav(2), wav(21), wav()[:-10]]:
            with self.assertRaises(EnrollmentError): validate_audio(data)

    def test_missing_configuration_visible_without_secret(self):
        with TestClient(create_app(settings=Settings(), database=Path(self.temp.name)/"blank.sqlite")) as client:
            self.assertIn("GEMINI_API_KEY",client.get("/api/setup").json()["missing"])
            self.assertEqual(client.get("/").status_code,200)


class ProviderEnrollmentTests(unittest.TestCase):
    def test_hiya_screening_distinguishes_low_missing_and_accepted_scores(self):
        settings = Settings(hiya_api_key="private", hiya_region="us", hiya_owner="owner", hiya_space="main")
        for score in [0.49, None, 0.5, 0.64, 0.75]:
            with self.subTest(score=score):
                def handle(request):
                    if request.url.path.endswith("/audios"):
                        return httpx.Response(201, json={"state": "available", "handle": "recording"})
                    return httpx.Response(200, json={"state": "performed", "handle": "check",
                        "scores": {"synthesis": score}})
                def client(_):
                    return HiyaClient(settings, transport=httpx.MockTransport(handle))
                with patch("verivoice.providers.hiya_enrollment_api.HiyaClient", side_effect=client):
                    api = HiyaEnrollmentAPI(settings)
                    if score is not None and score >= 0.5:
                        self.assertEqual(api.upload_checked(wav()), "recording")
                    elif score is None:
                        with self.assertRaisesRegex(EnrollmentError, "did not return a completed"):
                            api.upload_checked(wav())
                    else:
                        with self.assertRaisesRegex(EnrollmentError, "0.49.*0.50"):
                            api.upload_checked(wav())

    def test_hiya_correct_endpoints_and_resume_after_partial_attach(self):
        settings = Settings(hiya_api_key="private",hiya_region="us",hiya_owner="owner",hiya_space="main")
        members = ["a0", "a1"]
        state = {"value":"notComputable"}
        posts = []
        def handle(request):
            path = request.url.path
            if request.method == "GET" and path.endswith("/audios"):
                return httpx.Response(200,json=[{"handle": x} for x in members])
            if request.method == "POST":
                posts.append(path)
                if "/audios/" in path:
                    members.append(path.rsplit("/",1)[1])
                    if len(members)==5: state["value"]="computable"
                elif path.endswith(":compute"): state["value"]="computed"
            return httpx.Response(200,json={"state":state["value"],"audios":len(members)})
        def client(_): return HiyaClient(settings,transport=httpx.MockTransport(handle))
        with patch("verivoice.providers.hiya_enrollment_api.HiyaClient", side_effect=client):
            api=HiyaEnrollmentAPI(settings)
            api.finish("person",[f"a{i}" for i in range(5)])
            api.finish("person",[f"a{i}" for i in range(5)])
        self.assertEqual(len(posts),4)
        self.assertTrue(posts[-1].endswith("/voiceprints/person/main:compute"))
        self.assertEqual(len(members),5)

    def test_deepgram_optout_and_no_forced_language(self):
        def handle(request):
            self.assertEqual(request.url.params["mip_opt_out"],"true")
            self.assertEqual(request.url.params["detect_language"],"true")
            self.assertNotIn("language",request.url.params)
            return httpx.Response(200,json={"results":{"channels":[{"detected_language":"hi","alternatives":[{"transcript":"नमस्ते"}]}]}})
        client=httpx.Client(transport=httpx.MockTransport(handle))
        with patch("verivoice.providers.deepgram_api.httpx.Client",return_value=client):
            self.assertEqual(DeepgramAPI(Settings(deepgram_api_key="private")).transcribe(wav()),("नमस्ते","hi"))

if __name__ == "__main__": unittest.main()
