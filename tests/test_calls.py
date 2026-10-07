import asyncio
import base64
from dataclasses import replace
from contextlib import asynccontextmanager
import io
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave
import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from verivoice.app import create_app
from verivoice.confidence import Confidence, DECLINE, REMOVAL
from verivoice.enrollment import Accounts
from verivoice.calls import enough_speech, wav_window, INTRO
from verivoice.call_audio import VoiceWindows
from verivoice.providers.hiya_live_identity import LiveIdentity, LiveScores
from verivoice.providers.hiya_voiceprint_builder import BuiltVoiceprint
from test_enrollment import FakeProviders

class ConfidenceTests(unittest.TestCase):
    def test_human_ema_uses_minimum_of_adjusted_synthesis_and_replay(self):
        state=Confidence()
        self.assertIsNone(state.ch)
        report=state.update(.96,.94,.86)
        self.assertEqual(report['h'],.86)
        self.assertEqual(report['ch'],.86)
        self.assertEqual(report['c'],.86)
        report=state.update(.98,.9,.4)
        self.assertAlmostEqual(report['ca'],.97)
        self.assertAlmostEqual(report['ch'],.63)
        self.assertEqual(report['action'],'revoke')
        self.assertEqual(state.notification(0)['message'],REMOVAL)

    def test_synthesis_adjustment_preserves_raw_scores_and_does_not_compound(self):
        state=Confidence()
        for _ in range(2):
            report=state.update(.95,.6,.9)
            self.assertEqual(report['s'],.6)
            self.assertEqual(report['r'],.9)
            self.assertAlmostEqual(report['s_adjusted'],.8)
            self.assertAlmostEqual(report['h'],.8)
            self.assertAlmostEqual(report['ch'],.8)
        for synthesis, expected in [(0,.5),(1,1)]:
            report=Confidence().update(1,synthesis,1)
            self.assertEqual(report['s_adjusted'],expected)
            self.assertEqual(report['h'],expected)

    def test_incomplete_or_invalid_authenticity_never_updates_ema(self):
        state=Confidence()
        self.assertIsNone(state.update(.99,.99,None)['c'])
        state.update(.9,.9,.9)
        previous=state.snapshot()
        self.assertEqual(state.update(.1,None,.1),previous)
        with self.assertRaises(ValueError):state.update(.1,.1,float('nan'))
        self.assertEqual(state.snapshot(),previous)

    def test_ema_thresholds_and_warning_cadence(self):
        state = Confidence()
        self.assertIsNone(state.c)
        self.assertIsNone(state.notification(0))
        self.assertEqual(state.update(.9, 1, 1)["c"], .9)
        self.assertEqual(state.update(.6, 1, 1)["c"], .75)
        self.assertEqual(state.notification(0)["type"], "warning")
        self.assertIsNone(state.notification(9.99))
        self.assertEqual(state.notification(10)["type"], "warning")
        self.assertEqual(state.update(.5, 1, 1)["c"], .625)
        self.assertEqual(state.notification(11)["message"], REMOVAL)
        self.assertIsNone(state.notification(12))
        self.assertEqual(state.ch, 1)
        self.assertEqual(state.cl, 1)

    def test_boundaries_missing_and_invalid_scores(self):
        state = Confidence()
        self.assertEqual(state.update(None, 1, 1)["action"], "pending")
        for score, action in [(.8,"allow"), (.7,"pending"), (.699,"revoke"), (.799,"warn")]:
            self.assertEqual(Confidence().update(score, 1, 1)["action"],action)
        state.update(.7, 1, 1)
        self.assertEqual(state.update(None, 1, 1)["c"], .7)
        for invalid in [float("nan"), float("inf"), -1, 2, True]:
            with self.assertRaises(ValueError):state.update(invalid, 1, 1)

    def test_quiet_filter_and_wav_format(self):
        pcm = struct.pack('<h', 8000)*64000
        self.assertTrue(enough_speech(pcm))
        self.assertFalse(enough_speech(bytes(128000)))
        with wave.open(io.BytesIO(wav_window(pcm))) as audio:
            self.assertEqual((audio.getnchannels(),audio.getframerate(),audio.getsampwidth()),(1,16000,2))
            self.assertEqual(audio.getnframes(),64000)

    def test_quiet_laptop_speech_is_not_discarded(self):
        quiet_voice=struct.pack('<h',120)*32000
        self.assertTrue(enough_speech(bytes(64000)+quiet_voice))
        # Near-silent input is excluded; Hiya determines whether audible input
        # is genuine speech, instead of estimating noise from the response.
        self.assertFalse(enough_speech(struct.pack('<h',5)*64000))
        self.assertFalse(enough_speech(bytes(128000)))


class VoiceWindowTests(unittest.TestCase):
    def test_quiet_response_is_submitted_after_pause_despite_old_chunk_boundary(self):
        collector=VoiceWindows()
        # A response beginning three seconds into a fixed four-second window
        # used to be split into two pieces, both rejected as too short.
        quiet_voice=b''.join(struct.pack('<hh',level,-level)*160
                             for level in [60,90,130,90,60]*20)
        self.assertTrue(enough_speech(quiet_voice))
        self.assertEqual(collector.feed(bytes(96000)),[])
        self.assertEqual(collector.feed(quiet_voice),[])
        windows=collector.feed(bytes(19200))
        self.assertEqual(len(windows),1)
        self.assertEqual(windows[0],bytes(6400)+quiet_voice+bytes(19200))

    def test_continuous_speech_is_preserved_in_fresh_windows(self):
        collector=VoiceWindows()
        voice=struct.pack('<hh',100,-100)*64000
        windows=[]
        for start in range(0,len(voice),2100):
            windows.extend(collector.feed(voice[start:start+2100]))
        self.assertEqual(len(windows),2)
        self.assertEqual(b''.join(windows),voice)

    def test_silence_and_brief_click_do_not_trigger_verification(self):
        collector=VoiceWindows()
        self.assertEqual(collector.feed(bytes(320000)),[])
        self.assertEqual(collector.feed(struct.pack('<h',1000)*1600+bytes(32000)),[])
        self.assertEqual(collector.short_responses,1)

    def test_window_boundary_is_not_a_response_pause(self):
        collector=VoiceWindows()
        collector.feed(struct.pack('<h',1000)*64000)
        self.assertFalse(collector.buffer)
        self.assertFalse(collector.at_pause,'Four-second cutoff must not end the security-phrase stage')
        collector.feed(bytes(16000))
        self.assertFalse(collector.at_pause)
        collector.feed(bytes(3200))
        self.assertTrue(collector.at_pause)

class FakeSpeaker:
    def __init__(self):
        self.texts=[];self.audio=[];self.closed=False
    @asynccontextmanager
    async def connect(self, **kwargs):
        self.instruction=kwargs['system_instruction']
        self.queue=asyncio.Queue()
        try:yield self
        finally:self.closed=True
    async def send_text(self,text):
        self.texts.append(text)
        blob=SimpleNamespace(data=b'\x00\x00'*240,mime_type='audio/pcm;rate=24000')
        self.queue.put_nowait(SimpleNamespace(server_content=SimpleNamespace(
            interrupted=False,model_turn=SimpleNamespace(parts=[SimpleNamespace(inline_data=blob)]))))
    async def send_audio(self,pcm):self.audio.append(pcm)
    async def receive_turn(self):yield await self.queue.get()

class FakeIdentity:
    def __init__(self,scores):self.scores=iter(scores);self.targets=[];self.recordings=[]
    async def score(self,wav,account):
        self.targets.append(account)
        self.recordings.append(wav)
        score = next(self.scores)
        return score if isinstance(score, LiveScores) else LiveScores(score, 1, 1)

class CallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'accounts.sqlite3'
        self.providers=FakeProviders();self.speaker=FakeSpeaker();self.identity=FakeIdentity([.9,.6,.5])
        self.client=TestClient(create_app(settings=self.providers.settings,database=self.path,
            providers=self.providers,call_speaker=self.speaker,call_identity=self.identity))
        self.details={'email':'call@example.com','password':'long test password','language':'en',
            'consent':True,'phone_region':'US','phone_number':'2025550123'}
        self.client.post('/api/register',headers={'X-Verivoice':'enrollment'},json=self.details)
        self.accounts=Accounts(self.path)
        with self.accounts.connect() as db:
            uid=db.execute('SELECT id FROM accounts').fetchone()[0]
            db.execute("UPDATE accounts SET state='complete' WHERE id=?",(uid,))
        self.origin={'origin':'http://testserver'}
    def tearDown(self):self.client.close();self.temp.cleanup()
    def receive_type(self,ws,kind):
        for _ in range(12):
            data=ws.receive_json()
            if data['type']==kind:return data
        self.fail('Expected call message '+kind)
    def send_window(self,ws):
        ws.send_json({"type":"intro_played"})
        for _ in range(16):ws.send_bytes(struct.pack('<h',8000)*4000)
    def test_call_phone_routing_background_ema_and_removal(self):
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready')
            self.assertIsNone(self.receive_type(ws,'confidence')['c'])
            self.receive_type(ws,'audio')
            for expected in [.9,.75,.625]:
                self.send_window(ws)
                self.assertAlmostEqual(self.receive_type(ws,'confidence')['c'],expected)
            notice=self.receive_type(ws,'removed')
            self.assertEqual(notice['message'],REMOVAL)
            self.assertEqual(notice['close_after'],4)
            with self.assertRaises(WebSocketDisconnect):ws.receive_json()
        self.assertTrue(self.speaker.audio)
        self.assertTrue(self.speaker.closed)
        self.assertEqual(self.identity.targets[0]['phone'],'+12025550123')
        self.assertEqual(self.identity.targets[0]['voiceprint'],'main')
    def test_unknown_phone_goodbye_before_disconnect(self):
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550124'})
            self.assertEqual(ws.receive_json()['message'],DECLINE)
            self.assertEqual(ws.receive_json()['type'],'audio')
            self.assertEqual(ws.receive_json()['type'],'goodbye_complete')
            ws.send_json({'type':'played'})
            with self.assertRaises(WebSocketDisconnect):ws.receive_json()
        self.assertIn(DECLINE,self.speaker.texts[0])
        self.assertFalse(self.identity.targets)
        self.assertFalse(self.speaker.audio)

    def test_authenticity_failure_removes_even_with_high_identity(self):
        self.identity.scores=iter([LiveScores(.98,.2,.95)])
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'confidence');self.receive_type(ws,'audio')
            self.send_window(ws)
            report=self.receive_type(ws,'confidence')
            self.assertEqual(report['ca'],.98)
            self.assertAlmostEqual(report['ch'],.6)
            self.assertAlmostEqual(report['c'],.6)
            self.receive_type(ws,'removed')
            self.assertEqual(len(self.speaker.texts),1)
            with self.assertRaises(WebSocketDisconnect):ws.receive_json()
        # A fresh call after removal gets a fresh prompt gate and confidence state.
        self.identity.scores=iter([LiveScores(.98,.95,.95)])
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready')
            self.assertIsNone(self.receive_type(ws,'confidence')['c'])
            self.receive_type(ws,'audio');self.receive_type(ws,'intro_complete')
            self.send_window(ws)
            self.assertEqual(self.receive_type(ws,'confidence')['c'],.95)
            self.receive_type(ws,'verified')
            ws.send_json({'type':'hangup'})

    def test_missing_replay_ends_with_availability_error(self):
        self.identity.scores=iter([LiveScores(.99,.99,None)])
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'confidence');self.receive_type(ws,'audio')
            self.send_window(ws)
            self.assertEqual(self.receive_type(ws,'error')['type'],'error')

    def test_initial_verification_holds_conversation_until_pass(self):
        self.identity.scores=iter([.75,.85,.9])
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'confidence')
            self.receive_type(ws,'audio');self.receive_type(ws,'intro_complete')
            self.assertIn(INTRO,self.speaker.texts[0])
            self.assertIn(INTRO,self.speaker.instruction)
            # Audio before prompt playback does not get scored or sent to Gemini.
            ws.send_bytes(struct.pack('<h',8000)*4000)
            self.send_window(ws)
            self.assertEqual(self.receive_type(ws,'confidence')['c'],.75)
            self.receive_type(ws,'warning')
            self.assertEqual(len(self.speaker.texts),1)
            self.assertFalse(self.speaker.audio)
            self.send_window(ws)
            self.assertEqual(self.receive_type(ws,'confidence')['c'],.8)
            self.receive_type(ws,'verified');self.receive_type(ws,'audio')
            self.assertIn('what they would like to talk about',self.speaker.texts[1])
            self.send_window(ws)
            self.receive_type(ws,'confidence')
            self.assertTrue(self.speaker.audio)
            ws.send_json({'type':'hangup'})

    def test_repeated_prompt_acknowledgments_do_not_discard_speech(self):
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'confidence')
            self.receive_type(ws,'audio');self.receive_type(ws,'intro_complete')
            ws.send_json({'type':'intro_played'})
            self.receive_type(ws,'listening')
            for _ in range(8):ws.send_bytes(struct.pack('<h',8000)*4000)
            ws.send_json({'type':'intro_played'})
            for _ in range(8):ws.send_bytes(struct.pack('<h',8000)*4000)
            self.assertEqual(self.receive_type(ws,'confidence')['ca'],.9)
            self.receive_type(ws,'verified')
            ws.send_json({'type':'hangup'})

    def test_quiet_initial_response_reaches_hiya_after_pause_and_unlocks_conversation(self):
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'intro_complete')
            ws.send_json({'type':'intro_played'})
            voice=b''.join(struct.pack('<hh',level,-level)*160
                           for level in [60,90,130,90,60]*20)
            pcm=bytes(96000)+voice+bytes(19200)
            for start in range(0,len(pcm),3200):ws.send_bytes(pcm[start:start+3200])
            self.receive_type(ws,'microphone_received')
            self.receive_type(ws,'verification_started')
            self.assertEqual(self.receive_type(ws,'confidence')['c'],.9)
            self.receive_type(ws,'verified')
            self.assertEqual(len(self.identity.recordings),1)
            with wave.open(io.BytesIO(self.identity.recordings[0])) as recording:
                self.assertEqual(recording.readframes(recording.getnframes()),bytes(6400)+voice+bytes(19200))
            ws.send_json({'type':'hangup'})

    def test_below_threshold_result_explicitly_requests_another_attempt(self):
        self.identity.scores=iter([LiveScores(.95,.5,.95)])
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'intro_complete')
            self.send_window(ws)
            self.assertEqual(self.receive_type(ws,'confidence')['c'],.75)
            self.assertIn('Hiya checked your voice',self.receive_type(ws,'verification_retry')['message'])
            self.assertEqual(len(self.speaker.texts),1)
            ws.send_json({'type':'hangup'})

    def test_qualified_call_recording_updates_only_future_calls_after_hangup(self):
        with self.accounts.connect() as db:
            uid=db.execute('SELECT id FROM accounts').fetchone()[0]
        originals=[f'original-{i}' for i in range(5)]
        self.accounts.update(uid,audios=originals)
        self.identity.scores=iter([LiveScores(.95,.8,.9,'call-audio','call-verification',4,2)])
        async def build(account,samples):
            self.assertEqual(account['id'],uid)
            self.assertEqual(account['voiceprint'],'main')
            self.assertEqual(samples[0]['audio'],'call-audio')
            return BuiltVoiceprint('adaptive-call',tuple(originals+['call-audio']),1)
        with patch('verivoice.providers.hiya_voiceprint_builder.HiyaVoiceprintBuilder.build',side_effect=build):
            with self.client:
                with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
                    ws.send_json({'type':'start','phone':'+12025550123'})
                    self.receive_type(ws,'ready');self.receive_type(ws,'intro_complete')
                    self.send_window(ws);self.receive_type(ws,'verified')
                    self.assertEqual(self.accounts.get(uid)['voiceprint'],'main')
                    ws.send_json({'type':'hangup'})
                    with self.assertRaises(WebSocketDisconnect):
                        while True:ws.receive_json()
                for _ in range(100):
                    if self.accounts.get(uid)['voiceprint']=='adaptive-call':break
                    time.sleep(.01)
                self.assertEqual(self.accounts.get(uid)['voiceprint'],'adaptive-call')
                self.assertTrue(self.accounts.get(uid)['security_phrase_saved'])
                self.identity.scores=iter([LiveScores(.95,.95,.95,'second-phrase','second-verification',4,2)])
                with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
                    ws.send_json({'type':'start','phone':'+12025550123'})
                    self.receive_type(ws,'ready');self.receive_type(ws,'intro_complete')
                    self.send_window(ws);self.receive_type(ws,'verified')
                    self.assertEqual(self.identity.targets[-1]['voiceprint'],'adaptive-call')
                    with self.accounts.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM voiceprint_samples').fetchone()[0],1)
                    ws.send_json({'type':'hangup'})
    def test_origin_authentication_and_call_page(self):
        self.assertEqual(self.client.get('/call').status_code,200)
        for origin in [{}, {'origin':'https://evil.example'}]:
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect('/api/call',headers=origin):pass
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/call').status_code,400)
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/api/call',headers=self.origin):pass
    def test_provider_failure_and_hangup(self):
        self.identity.scores=iter([None])
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'confidence');self.receive_type(ws,'audio')
            self.send_window(ws)
            self.assertIn('verification is unavailable',self.receive_type(ws,'error')['message'])
        self.assertTrue(self.speaker.closed)
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready')
            ws.send_json({'type':'hangup'})
            while True:
                try:ws.receive_json()
                except WebSocketDisconnect:break

    def test_cannot_select_another_enrolled_accounts_phone(self):
        with self.accounts.connect() as db:
            row = dict(db.execute("SELECT * FROM accounts").fetchone())
            row.update(id='other-account', email='other-call@example.com', phone='+12025550124')
            columns = ','.join(row)
            db.execute(f"INSERT INTO accounts ({columns}) VALUES ({','.join('?' for _ in row)})",tuple(row.values()))
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550124'})
            self.assertIn('signed-in account',ws.receive_json()['message'])
        self.assertFalse(self.speaker.texts)

    def test_logout_terminates_active_call(self):
        with self.client.websocket_connect('/api/call',headers=self.origin) as ws:
            ws.send_json({'type':'start','phone':'+12025550123'})
            self.receive_type(ws,'ready');self.receive_type(ws,'confidence');self.receive_type(ws,'audio')
            self.client.post('/api/logout',headers={'X-Verivoice':'enrollment'},json={})
            self.assertEqual(self.receive_type(ws,'error')['type'],'error')
        self.assertTrue(self.speaker.closed)

class LiveIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_then_match_against_selected_voiceprint(self):
        settings=replace(FakeProviders().settings, hiya_api_key='test-only-key')
        requests=[]
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('/audios'):
                return httpx.Response(201,json={'handle':'fresh-audio','state':'available'})
            return httpx.Response(201,json={'handle':'result','state':'performed',
                'scores':{'identity':.82,'synthesis':.42,'replay':.92}})
        client=httpx.AsyncClient(base_url='https://api.hiya.com/audiointel/us/v1/',transport=httpx.MockTransport(handler))
        with patch('verivoice.providers.hiya_live_identity.httpx.AsyncClient',return_value=client):
            score=await LiveIdentity(settings).score(wav_window(bytes(128000)),{
                'id':'account-identity','voiceprint':'main','hiya_owner':'owner','hiya_space':'main','hiya_region':'us'})
        self.assertEqual((score.identity,score.synthesis,score.replay),(.82,.42,.92))
        self.assertEqual(score.audio_handle,'fresh-audio')
        self.assertEqual(score.verification_handle,'result')
        self.assertEqual(score.audio_seconds,4)
        import json
        self.assertEqual(json.loads(requests[1].content),{'audio':'fresh-audio','identity':'account-identity','voiceprint':'main'})
        self.assertEqual(len(requests),2)
        self.assertTrue(base64.b64decode(json.loads(requests[0].content)['file']).startswith(b'RIFF'))
