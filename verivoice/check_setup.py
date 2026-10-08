"""Check local configuration without revealing keys or making provider requests."""
from verivoice.config import Settings, SUPPORTED_LANGUAGES

REQUIRED = (
    'gemini_api_key', 'deepgram_api_key', 'soniox_api_key', 'hiya_api_key',
    'hiya_region', 'hiya_owner', 'hiya_space', 'gemini_model',
    'gemini_enrollment_model', 'soniox_model',
)


def main():
    settings = Settings.from_env()
    print('Supported languages:', ', '.join(SUPPORTED_LANGUAGES))
    for name in REQUIRED:
        value = getattr(settings, name)
        display = value if name.endswith('_model') else 'configured' if value else 'missing'
        print(name.upper() + ':', display or 'missing')
    missing = [name.upper() for name in REQUIRED if not getattr(settings, name)]
    invalid_region = bool(settings.hiya_region and settings.hiya_region not in ('us', 'eu'))
    if missing:
        print('Missing required settings:', ', '.join(missing))
    if invalid_region:
        print('HIYA_REGION must be us or eu, matching your actual Hiya resources.')
    print('HIYA_IDENTITY / HIYA_VOICEPRINT are optional for the website.')
    print('This check does not validate API keys, model access, Hiya resources, quotas, or provider balances.')
    if missing or invalid_region:
        print('Local configuration is incomplete. Edit .env and run this check again.')
        return 1
    print('Local configuration is complete. Start with: python -m verivoice.app')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
