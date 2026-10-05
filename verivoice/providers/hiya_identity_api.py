"""Match speech to an enrolled voiceprint. Does not establish liveness.
Docs: https://developer.hiya.com/docs/guides/voice-protection/results/perform-a-verification/identity
"""
from dataclasses import dataclass
from .hiya_client import HiyaClient, optional_score, segment

@dataclass(frozen=True)
class IdentityResult:
    match_score: float | None
    state: str
    verification_handle: str | None

    @classmethod
    def from_response(cls, data):
        state = data.get("state", "unknown")
        return cls(optional_score(data.get("score")) if state == "performed" else None,
                   state, data.get("handle"))

class HiyaIdentityAPI:
    def __init__(self, client: HiyaClient):
        self.client = client

    def verify(self, audio_handle: str, *, identity: str | None = None,
               voiceprint: str | None = None) -> IdentityResult:
        settings = self.client.settings
        identity = identity or settings.hiya_identity
        voiceprint = voiceprint or settings.hiya_voiceprint
        segment(identity); segment(voiceprint); segment(audio_handle)
        result = self.client.request("POST", self.client.space_path + "/verifications/identity",
                                     {"audio": audio_handle, "identity": identity, "voiceprint": voiceprint})
        return IdentityResult.from_response(result)

    def get_result(self, verification_handle: str):
        data = self.client.request("GET", self.client.space_path + "/verifications/identity/" + segment(verification_handle))
        return IdentityResult.from_response(data)

    def enroll(self, *args, **kwargs):
        """Explicit stub: enrollment UI/identity and voiceprint creation come next."""
        raise NotImplementedError("Enrollment needs verified audio and an identity/voiceprint setup flow. Configure existing HIYA_IDENTITY and HIYA_VOICEPRINT for now.")
