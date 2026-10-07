import asyncio
import json
import unittest
from urllib.parse import parse_qs, urlsplit
import httpx
from verivoice.config import Settings, ConfigurationError
from verivoice.providers.deepgram_api import DeepgramAPI, LanguageObservation
from verivoice.providers.hiya_client import HiyaClient, HiyaAPIError
from verivoice.providers.hiya_identity_api import HiyaIdentityAPI, IdentityResult
from verivoice.providers.hiya_synthesis_api import HiyaSynthesisAPI, SynthesisResult
from verivoice.providers._streaming import stream_messages

class ProviderTests(unittest.TestCase):
    def test_multilingual_endpoint_and_unknown_language(self):
        url = urlsplit(DeepgramAPI.stream_url())
        query = parse_qs(url.query)
        self.assertEqual(url.path, "/v2/listen")
        self.assertEqual(query["model"], ["flux-general-multi"])
        self.assertEqual(query["language_hint"], ["en", "hi", "es", "ru"])
        obs = LanguageObservation.from_message({"type": "TurnInfo", "event": "EndOfTurn",
              "transcript": "Hello", "end_of_turn_confidence": .99})
        self.assertIsNone(obs.primary_language)
        self.assertIsNone(obs.language_confidence)
        obs = LanguageObservation.from_message({"type": "TurnInfo", "languages": ["ru", "en"]})
        self.assertEqual(obs.primary_language, "ru")

    def test_score_direction_and_unavailable_scores(self):
        self.assertEqual(SynthesisResult.from_response({"state": "performed", "scores": {"synthesis": .9}}).non_synthetic_score, .9)
        self.assertIsNone(SynthesisResult.from_response({"state": "performedWithoutScore"}).non_synthetic_score)
        self.assertIsNone(IdentityResult.from_response({"state": "error", "score": .99}).match_score)
        with self.assertRaises(ValueError):
            IdentityResult.from_response({"state": "performed", "score": float("nan")})

    def test_current_identity_response_and_legacy_compatibility(self):
        current = {"state": "performed", "scores": {"identity": .96,
            "synthesis": .42, "replay": .92}}
        self.assertEqual(IdentityResult.from_response(current).match_score, .96)
        self.assertEqual(IdentityResult.from_response({**current, "score": .1}).match_score, .96)
        self.assertEqual(IdentityResult.from_response({"state": "performed", "score": .8}).match_score, .8)
        self.assertIsNone(IdentityResult.from_response({"state": "performed", "scores": {"synthesis": .99}}).match_score)
        self.assertIsNone(IdentityResult.from_response({**current, "state": "processing"}).match_score)
        self.assertIsNone(IdentityResult.from_response({"state": "performed", "score": .8,
            "scores": {"identity": None}}).match_score)

    def test_correct_hiya_requests_and_missing_enrollment(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(201, json={"state": "performed", "score": .8, "scores": {"synthesis": .9}})
        settings = Settings(hiya_api_key="fake-test-token", hiya_region="us", hiya_owner="owner", hiya_space="space")
        with HiyaClient(settings, transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ConfigurationError):
                HiyaIdentityAPI(client).verify("audio")
            self.assertEqual(len(requests), 0)
            identity = HiyaIdentityAPI(client).verify("audio", identity="speaker", voiceprint="main")
            synthesis = HiyaSynthesisAPI(client).verify("audio")
        self.assertEqual(identity.match_score, .8)
        self.assertEqual(synthesis.non_synthetic_score, .9)
        self.assertEqual(requests[0].url.path, "/audiointel/us/v1/spaces/owner/space/verifications/identity")
        self.assertEqual(json.loads(requests[0].content), {"audio": "audio", "identity": "speaker", "voiceprint": "main"})
        self.assertEqual(requests[1].headers["User-Agent"], "VeriVoice/0.1")

    def test_errors_do_not_expose_credentials_or_body(self):
        settings = Settings(hiya_api_key="private-test-token", hiya_region="eu")
        with HiyaClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(401, text="sensitive response"))) as client:
            with self.assertRaises(HiyaAPIError) as context:
                client.check_auth()
        self.assertNotIn("private-test-token", str(context.exception))
        self.assertNotIn("sensitive response", str(context.exception))
        self.assertNotIn("private-test-token", repr(settings))

class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_is_sent_while_receiving_and_final_drained(self):
        class Socket:
            def __init__(self):
                self.sent = []
                self.queue = asyncio.Queue()
            async def send(self, data):
                self.sent.append(data)
                if isinstance(data, bytes):
                    await self.queue.put(json.dumps({"type": "chunk"}))
                else:
                    await self.queue.put(json.dumps({"type": "verification"}))
            async def recv(self):
                return await self.queue.get()
        async def audio():
            yield b"audio"
        socket = Socket()
        messages = [m async for m in stream_messages(socket, audio(), {"type": "close"}, "verification")]
        self.assertEqual([m["type"] for m in messages], ["chunk", "verification"])
        self.assertEqual(socket.sent, [b"audio", '{"type": "close"}'])

    async def test_audio_source_failure_propagates_without_hanging(self):
        class Socket:
            async def recv(self):
                await asyncio.Event().wait()
            async def send(self, data):
                pass
        async def audio():
            raise ValueError("capture failed")
            yield b""
        with self.assertRaisesRegex(ValueError, "capture failed"):
            async for _ in stream_messages(Socket(), audio(), {}):
                pass

    async def test_drain_timeout(self):
        class Socket:
            async def recv(self):
                await asyncio.Event().wait()
            async def send(self, data):
                pass
        async def audio():
            if False:
                yield b""
        with self.assertRaises(TimeoutError):
            async for _ in stream_messages(Socket(), audio(), {}, drain_timeout=.01):
                pass

if __name__ == "__main__":
    unittest.main()
