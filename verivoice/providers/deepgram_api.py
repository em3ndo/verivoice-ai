"""Flux Multilingual streaming STT + language observations (not biometrics).
Docs: https://developers.deepgram.com/reference/speech-to-text/listen-flux
Language output: https://deepgram.com/learn/flux-multilingual-technical-deep-dive
"""
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
import ssl
import certifi
import websockets
from websockets.exceptions import ConnectionClosedOK
from verivoice.config import Settings, SUPPORTED_LANGUAGES
from ._streaming import stream_messages

@dataclass(frozen=True)
class LanguageObservation:
    transcript: str
    languages: tuple[str, ...]
    event: str
    turn_index: int | None
    audio_start: float | None
    audio_end: float | None
    # Missing language output remains unknown. EOT/word confidence isn't LID confidence.
    language_confidence: float | None = None

    @property
    def primary_language(self) -> str | None:
        return self.languages[0] if self.languages else None

    @classmethod
    def from_message(cls, message: dict[str, Any]):
        if message.get("type") != "TurnInfo":
            return None
        languages = message.get("languages") or []
        if not isinstance(languages, list) or not all(isinstance(x, str) for x in languages):
            raise ValueError("Malformed Flux language output.")
        return cls(message.get("transcript", ""), tuple(languages), message.get("event", ""),
                   message.get("turn_index"), message.get("audio_window_start"),
                   message.get("audio_window_end"))

class DeepgramAPI:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()

    @staticmethod
    def stream_url(sample_rate: int = 16000) -> str:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive.")
        params = [("model", "flux-general-multi"), ("encoding", "linear16"),
                  ("sample_rate", str(sample_rate))]
        params.extend(("language_hint", lang) for lang in SUPPORTED_LANGUAGES)
        # Hints bias detection; they aren't a strict allowlist.
        return "wss://api.deepgram.com/v2/listen?" + urlencode(params)

    async def stream(self, pcm_chunks: AsyncIterable[bytes], sample_rate: int = 16000,
                     drain_timeout: float = 30) -> AsyncIterator[dict[str, Any]]:
        """Stream mono signed 16-bit little-endian PCM. Yields all provider events.
        Supply live chunks (or pace a file) instead of sending a WAV header as PCM.
        A future policy should evaluate final EndOfTurn observations once per turn.
        """
        self.settings.require("deepgram_api_key")
        async with websockets.connect(self.stream_url(sample_rate), additional_headers={
            "Authorization": "Token " + self.settings.deepgram_api_key,
        }, ssl=ssl.create_default_context(cafile=certifi.where()), open_timeout=15, max_size=4 * 1024 * 1024) as socket:
            try:
                async for message in stream_messages(socket, pcm_chunks,
                        {"type": "CloseStream"}, drain_timeout=drain_timeout):
                    if message.get("type") == "Error":
                        raise RuntimeError("Deepgram stream error: " + str(message.get("code", "unknown")))
                    yield message
            except ConnectionClosedOK:
                return
