import json
import unittest
from unittest.mock import AsyncMock

from verivoice.config import Settings, ConfigurationError
from verivoice.providers.gemini_speaker_api import ConfidenceReport, GeminiSpeakerAPI, SpeakerSession


class SpeakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_preserves_evidence_and_missing_scores(self):
        session = AsyncMock()
        report = ConfidenceReport("turn-1", .68, .8, None, .9, "challenge",
                                  reasons=("Language result unavailable",),
                                  evidence={"deepgram": {"transcript": "hola", "languages": ["es"]}})
        await SpeakerSession(session).report(report)
        args = session.send_client_content.call_args.kwargs
        self.assertFalse(args["turn_complete"])
        payload = json.loads(args["turns"]["parts"][0]["text"])
        self.assertIsNone(payload["language_consistency"])
        self.assertEqual(payload["evidence"], report.evidence)
        await SpeakerSession(session).report(report, announce=True)
        self.assertTrue(session.send_client_content.call_args.kwargs["turn_complete"])

    async def test_invalid_score_does_not_reach_model(self):
        session = AsyncMock()
        report = ConfidenceReport("turn-1", float("nan"), .8, .8, .8, "allow")
        with self.assertRaises(ValueError):
            await SpeakerSession(session).report(report)
        session.send_client_content.assert_not_called()

    async def test_pcm_format_and_audio_end(self):
        session = AsyncMock()
        speaker = SpeakerSession(session)
        with self.assertRaises(ValueError):
            await speaker.send_audio(b"x")
        await speaker.send_audio(b"\x00\x00")
        blob = session.send_realtime_input.call_args.kwargs["audio"]
        self.assertEqual(blob.mime_type, "audio/pcm;rate=16000")
        await speaker.end_audio()
        session.send_realtime_input.assert_awaited_with(audio_stream_end=True)

    def test_missing_key_and_redaction(self):
        with self.assertRaises(ConfigurationError):
            GeminiSpeakerAPI(Settings())
        self.assertNotIn("test-private-key", repr(Settings(gemini_api_key="test-private-key")))
