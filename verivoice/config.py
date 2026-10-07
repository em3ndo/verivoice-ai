"""Load backend-only credentials; environment variables override the local .env."""
from dataclasses import dataclass, field, fields
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_LANGUAGES = ("en", "hi", "es", "ru")

class ConfigurationError(ValueError):
    pass

@dataclass(frozen=True)
class Settings:
    deepgram_api_key: str = field(default="", repr=False)
    soniox_api_key: str = field(default="", repr=False)
    soniox_model: str = "stt-rt-v5"
    hiya_api_key: str = field(default="", repr=False)
    hiya_region: str = ""
    hiya_owner: str = ""
    hiya_space: str = ""
    hiya_identity: str = ""
    hiya_voiceprint: str = ""
    hiya_authenticity_model: str = "digital"
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = "gemini-3.8-live"

    gemini_enrollment_model: str = "gemini-3.8-flash"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        return cls(**{item.name: os.getenv(item.name.upper(), item.default).strip()
                      for item in fields(cls)})

    def require(self, *names):
        missing = [name.upper() for name in names if not getattr(self, name)]
        if missing:
            raise ConfigurationError("Set " + ", ".join(missing) + " in your local .env.")
