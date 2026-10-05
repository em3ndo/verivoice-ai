"""Hiya synthetic-voice detection; a high score means NON-synthetic.
This is not proof of live speech: no replay score is currently documented.
Docs: https://developer.hiya.com/docs/audio-intel/scores
"""
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Any
import ssl
import certifi
import websockets
from .hiya_client import HiyaClient, optional_score, segment
from ._streaming import stream_messages

@dataclass(frozen=True)
class SynthesisResult:
    non_synthetic_score: float | None
    state: str
    verification_handle: str | None
    replay_score: float | None = None

    @classmethod
    def from_response(cls, data):
        state = data.get("state", "unknown")
        score = (data.get("scores") or {}).get("synthesis") if state == "performed" else None
        return cls(optional_score(score), state, data.get("handle"))

class HiyaSynthesisAPI:
    def __init__(self, client: HiyaClient):
        self.client = client
        if client.settings.hiya_authenticity_model not in ("digital", "phone"):
            raise ValueError("HIYA_AUTHENTICITY_MODEL must be digital or phone.")

    def verify(self, audio_handle: str) -> SynthesisResult:
        segment(audio_handle)
        data = self.client.request("POST", self.client.space_path + "/verifications/authenticity",
                                  {"audio": audio_handle, "model": self.client.settings.hiya_authenticity_model})
        return SynthesisResult.from_response(data)

    def get_result(self, verification_handle: str):
        data = self.client.request("GET", self.client.space_path + "/verifications/authenticity/" + segment(verification_handle))
        return SynthesisResult.from_response(data)

    async def stream(self, media_chunks: AsyncIterable[bytes],
                     drain_timeout: float = 30) -> AsyncIterator[dict[str, Any]]:
        """Stream a decodable media format (e.g. WAV), not unspecified raw PCM.
        Events include chunk.scores.synthesis and a final verification object.
        Browser capture/encoding and audio routing will be integrated later.
        """
        url = self.client.base_url.replace("https://", "wss://", 1) + "/" + self.client.space_path + "/verify/authenticity"
        async with websockets.connect(url, additional_headers=self.client.headers,
                user_agent_header=None, ssl=ssl.create_default_context(cafile=certifi.where()), open_timeout=15, max_size=4 * 1024 * 1024) as socket:
            import json
            await socket.send(json.dumps({"model": self.client.settings.hiya_authenticity_model}))
            async for message in stream_messages(socket, media_chunks, {"type": "close"},
                    terminal_type="verification", drain_timeout=drain_timeout):
                if message.get("type") == "error":
                    raise RuntimeError("Hiya streaming verification failed.")
                yield message
