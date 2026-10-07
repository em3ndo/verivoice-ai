"""Build immutable replacement voiceprints; never alter the active voiceprint."""
import asyncio
import hashlib
import json
from dataclasses import dataclass

import httpx

from .hiya_client import HiyaAPIError, segment
from .hiya_live_identity import duration_seconds

MAX_SECONDS = 120


@dataclass(frozen=True)
class BuiltVoiceprint:
    handle: str
    audios: tuple[str, ...]
    added: int


class HiyaVoiceprintBuilder:
    def __init__(self, settings):
        self.settings = settings

    async def build(self, account, samples):
        settings = self.settings
        settings.require("hiya_api_key", "hiya_region", "hiya_owner", "hiya_space")
        if settings.hiya_region not in ("us", "eu") or any(
                account[field] != getattr(settings, field) for field in ("hiya_region", "hiya_owner", "hiya_space")):
            raise HiyaAPIError("Voiceprint configuration mismatch.")
        originals = json.loads(account["audios"])
        if len(originals) != 5 or len(set(originals)) != 5:
            raise HiyaAPIError("Adaptive enrollment requires the original five recordings.")
        base = f"spaces/{segment(settings.hiya_owner)}/{segment(settings.hiya_space)}"
        identity = segment(account["id"])
        async with httpx.AsyncClient(
                base_url=f"https://api.hiya.com/audiointel/{settings.hiya_region}/v1/",
                headers={"Authorization": "Bearer " + settings.hiya_api_key}, timeout=20) as client:
            async def request(method, path, body=None, *, missing=False):
                response = await client.request(method, path, json=body)
                if missing and response.status_code == 404:
                    return None
                if response.is_error:
                    raise HiyaAPIError(f"Voiceprint update failed (HTTP {response.status_code}).")
                result = response.json()
                if not isinstance(result, dict):
                    raise HiyaAPIError("Invalid voiceprint response.")
                return result

            active = await request("GET", f"{base}/voiceprints/{identity}/{segment(account['voiceprint'])}")
            if active.get("state") != "computed" or active.get("model") != "digital/v1":
                raise HiyaAPIError("Adaptive enrollment requires a computed digital/v1 voiceprint.")
            total = 0
            for audio in originals:
                data = await request("GET", f"{base}/audios/{segment(audio)}")
                seconds = duration_seconds(data.get("duration"))
                if data.get("state") != "available" or seconds is None or seconds <= 0:
                    raise HiyaAPIError("An original enrollment recording is unavailable.")
                total += seconds
            if total > MAX_SECONDS:
                raise HiyaAPIError("Original enrollment exceeds the model duration limit.")
            selected = list(originals)
            # Keep the original enrollment and the newest eligible recordings
            # that fit. The full sample history remains separate from enrollment.
            for sample in sorted(samples, key=lambda item: item["id"], reverse=True):
                audio = sample["audio"]
                if audio in selected or total + sample["seconds"] > MAX_SECONDS:
                    continue
                data = await request("GET", f"{base}/audios/{segment(audio)}", missing=True)
                if data is None or data.get("state") != "available":
                    continue
                seconds = duration_seconds(data.get("duration"))
                if seconds is None or seconds <= 0 or total + seconds > MAX_SECONDS:
                    continue
                selected.append(audio)
                total += seconds
            if len(selected) == len(originals):
                return BuiltVoiceprint(account["voiceprint"], tuple(originals), 0)

            # Stable membership hash lets a retry resume a partially built
            # version without creating another resource or duplicate members.
            digest = hashlib.sha256(json.dumps(["digital/v1", sorted(selected)]).encode()).hexdigest()[:24]
            handle = "adaptive-" + digest
            path = f"{base}/voiceprints/{identity}/{handle}"
            vp = await request("GET", path, missing=True)
            if vp is None:
                vp = await request("POST", base + "/voiceprints", {
                    "handle": handle, "identity": account["id"], "model": "digital/v1", "minAudios": 5})
            if vp.get("model") != "digital/v1" or vp.get("minAudios") != 5:
                raise HiyaAPIError("Unexpected replacement voiceprint settings.")
            attached = set()
            page = 1
            while True:
                response = await client.get(path + "/audios", params={"page-size": 50, "page": page})
                if response.is_error or not isinstance(response.json(), list):
                    raise HiyaAPIError("Could not inspect replacement voiceprint members.")
                attached.update(item["handle"] for item in response.json())
                if page >= int(response.headers.get("Page-Total", "1")):
                    break
                page += 1
            if not attached.issubset(selected):
                raise HiyaAPIError("Replacement voiceprint has unexpected members.")
            if vp.get("state") == "computed" and attached != set(selected):
                raise HiyaAPIError("Computed replacement voiceprint has incomplete members.")
            if vp.get("state") != "computed":
                for audio in selected:
                    if audio not in attached:
                        await request("POST", path + "/audios/" + segment(audio))
                vp = await request("GET", path)
                if vp.get("state") == "computable":
                    await request("POST", path + ":compute")
                elif vp.get("state") != "computed":
                    raise HiyaAPIError("Replacement voiceprint is not computable.")
            for _ in range(30):
                vp = await request("GET", path)
                if vp.get("state") == "computed":
                    if vp.get("audios") != len(selected):
                        raise HiyaAPIError("Replacement recording count mismatch.")
                    return BuiltVoiceprint(handle, tuple(selected), len(selected) - len(originals))
                await asyncio.sleep(.5)
            raise HiyaAPIError("Replacement voiceprint computation timed out.")
