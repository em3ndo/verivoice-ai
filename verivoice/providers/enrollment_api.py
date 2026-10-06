"""Small facade composing the three independent enrollment adapters."""
from .enrollment_common import EnrollmentError, LANGUAGES
from .deepgram_api import DeepgramAPI
from .gemini_enrollment_api import GeminiEnrollmentAPI
from .hiya_enrollment_api import HiyaEnrollmentAPI

class EnrollmentProviders:
    def __init__(self, settings):
        self.settings = settings
        gemini = GeminiEnrollmentAPI(settings)
        deepgram = DeepgramAPI(settings)
        hiya = HiyaEnrollmentAPI(settings)
        self.phrases = gemini.phrases
        self.phrase_matches = gemini.phrase_matches
        self.transcribe = deepgram.transcribe
        self.upload_checked = hiya.upload_checked
        self.finish = hiya.finish

    def ready(self):
        self.settings.require("gemini_api_key", "deepgram_api_key", "hiya_api_key",
                              "hiya_region", "hiya_owner", "hiya_space")
