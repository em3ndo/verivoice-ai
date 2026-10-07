"""Repeated digital identity verifications on fresh four-second microphone windows."""
import asyncio
import base64
import time
import httpx
from .hiya_client import HiyaAPIError, segment
from .hiya_identity_api import IdentityResult

class LiveIdentity:
    def __init__(self, settings):
        settings.require("hiya_api_key", "hiya_region", "hiya_owner", "hiya_space")
        if settings.hiya_region not in ("us", "eu"):
            raise HiyaAPIError("Unsupported Hiya region.")
        self.settings = settings
        self.base = f"https://api.hiya.com/audiointel/{settings.hiya_region}/v1/"
        self.space = f"spaces/{segment(settings.hiya_owner)}/{segment(settings.hiya_space)}"

    async def score(self, wav, account):
        for field in ("hiya_owner", "hiya_space", "hiya_region"):
            if account[field] != getattr(self.settings, field):
                raise HiyaAPIError("Account voiceprint belongs to a different Hiya configuration.")
        async with httpx.AsyncClient(base_url=self.base,
                headers={"Authorization": "Bearer " + self.settings.hiya_api_key}, timeout=15) as client:
            async def request(method, path, body=None):
                result = await client.request(method, path, json=body)
                if result.is_error:
                    raise HiyaAPIError("Hiya identity request failed.")
                data = result.json()
                if not isinstance(data, dict):
                    raise HiyaAPIError("Invalid Hiya response.")
                return data

            audio = await request("POST", self.space + "/audios", {"file": base64.b64encode(wav).decode()})
            handle = audio["handle"]
            deadline = time.monotonic() + 15
            while audio.get("state") != "available":
                if audio.get("state") in {"failed", "error", "notAvailable"} or time.monotonic() >= deadline:
                    raise HiyaAPIError("Hiya could not process call audio.")
                await asyncio.sleep(.25)
                audio = await request("GET", self.space + "/audios/" + segment(handle))
            data = await request("POST", self.space + "/verifications/identity",
                {"audio": handle, "identity": account["id"], "voiceprint": account["voiceprint"]})
            while data.get("state") != "performed":
                if data.get("state") in {"failed", "error"} or not data.get("handle") or time.monotonic() >= deadline:
                    raise HiyaAPIError("Hiya identity result unavailable.")
                await asyncio.sleep(.25)
                data = await request("GET", self.space + "/verifications/identity/" + segment(data["handle"]))
            return IdentityResult.from_response(data).match_score
