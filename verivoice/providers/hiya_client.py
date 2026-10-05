"""Shared Hiya Audio Intelligence transport; no secret or response-body logging.
Authentication: https://developer.hiya.com/docs/guides/voice-protection/authentication/authenticate-using-api-keys
"""
import base64
from pathlib import Path
from urllib.parse import quote
from typing import Any
import httpx
from verivoice.config import Settings, ConfigurationError

class HiyaAPIError(RuntimeError):
    pass

def segment(value: str) -> str:
    if not value:
        raise ConfigurationError("Hiya resource handle is missing.")
    return quote(value, safe="")

def optional_score(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("Invalid provider score; expected a number between 0 and 1.")
    return float(value)

class HiyaClient:
    def __init__(self, settings: Settings | None = None, *, transport=None):
        self.settings = settings or Settings.from_env()
        self.settings.require("hiya_api_key", "hiya_region")
        if self.settings.hiya_region not in ("us", "eu"):
            raise ConfigurationError("HIYA_REGION must be us or eu.")
        self.base_url = f"https://api.hiya.com/audiointel/{self.settings.hiya_region}/v1"
        self.headers = {"Authorization": "Bearer " + self.settings.hiya_api_key,
                        "User-Agent": "VeriVoice/0.1"}
        self.http = httpx.Client(base_url=self.base_url + "/", headers=self.headers,
                                 timeout=60, transport=transport)

    @property
    def space_path(self):
        self.settings.require("hiya_owner", "hiya_space")
        return f"spaces/{segment(self.settings.hiya_owner)}/{segment(self.settings.hiya_space)}"

    def request(self, method: str, path: str, body=None) -> dict[str, Any]:
        response = self.http.request(method, path, json=body)
        if response.is_error:
            # Don't include raw provider bodies, auth headers, or user metadata.
            raise HiyaAPIError(f"Hiya request failed (HTTP {response.status_code}); check token, region, resources and permissions.")
        result = response.json()
        if not isinstance(result, dict):
            raise HiyaAPIError("Expected an object response from Hiya.")
        return result

    def check_auth(self):
        return self.request("GET", "user")

    def upload_audio(self, path: str | Path):
        """Upload an explicit user-selected media file, never automatically at import."""
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return self.request("POST", self.space_path + "/audios", {"file": encoded})

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
