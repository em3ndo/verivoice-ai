"""Print configuration status without revealing credentials or making requests."""
from verivoice.config import Settings, SUPPORTED_LANGUAGES

def main():
    settings = Settings.from_env()
    print("Demo languages:", ", ".join(SUPPORTED_LANGUAGES))
    print("Deepgram key:", "configured" if settings.deepgram_api_key else "missing")
    print("Hiya key:", "configured" if settings.hiya_api_key else "missing")
    print("Gemini key:", "configured" if settings.gemini_api_key else "missing")
    print("Gemini model:", settings.gemini_model)
    for name in ("hiya_region", "hiya_owner", "hiya_space", "hiya_identity", "hiya_voiceprint"):
        print(name.upper() + ":", "configured" if getattr(settings, name) else "needed")

if __name__ == "__main__":
    main()
