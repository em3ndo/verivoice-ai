import asyncio
import base64
from dataclasses import replace
from contextlib import asynccontextmanager
import io
from pathlib import Path
import struct
import tempfile
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
from verivoice.providers.hiya_live_identity import LiveIdentity
from test_enrollment import FakeProviders

class ConfidenceTests(unittest.TestCase):
    def test_ema_thresholds_and_warning_cadence(self):
        state = Confidence()
        self.assertIsNone(state.c)
        self.assertIsNone(state.notification(0))
        self.assertEqual(state.update(.9)["c"], .9)
        self.assertEqual(state.update(.6)["c"], .75)
        self.assertEqual(state.notification(0)["type"], "warning")
        self.assertIsNone(state.notification(9.99))
        self.assertEqual(state.notification(10)["type"], "warning")
        self.assertEqual(state.update(.5)["c"], .625)
        self.assertEqual(state.notification(11)["message"], REMOVAL)
        self.assertIsNone(state.notification(12))
        self.assertEqual(state.ch, 1)
        self.assertEqual(state.cl, 1)

    def test_boundaries_missing_and_invalid_scores(self):
        state = Confidence()
        self.assertEqual(state.update(None)["action"], "pending")
        for score, action in [(.8,"allow"), (.7,"pending"), (.699,"revoke"), (.799,"warn")]:
            self.assertEqual(Confidence().update(score)["action"],action)
        state.update(.7)
        self.assertEqual(state.update(None)["c"], .7)
        for invalid in [float("nan"), float("inf"), -1, 2, True]:
            with self.assertRaises(ValueError):state.update(invalid)

    def test_quiet_filter_and_wav_format(self):
        pcm = struct.pack('<h', 8000)*64000
        self.assertTrue(enough_speech(pcm))
        self.assertFalse(enough_speech(bytes(128000)))
        with wave.open(io.BytesIO(wav_window(pcm))) as audio:
            self.assertEqual((audio.getnchannels(),audio.getframerate(),audio.getsampwidth()),(1,16000,2))
            self.assertEqual(audio.getnframes(),64000)

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
    def __init__(self,scores):self.scores=iter(scores);self.targets=[]
    async def score(self,wav,account):
        self.targets.append(account)
        return next(self.scores)

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
        self.assertEqual(score,.82)
        import json
        self.assertEqual(json.loads(requests[1].content),{'audio':'fresh-audio','identity':'account-identity','voiceprint':'main'})
        self.assertEqual(len(requests),2)
        self.assertTrue(base64.b64decode(json.loads(requests[0].content)['file']).startswith(b'RIFF'))
