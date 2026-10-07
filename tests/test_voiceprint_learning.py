import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from verivoice.enrollment import Accounts
from verivoice.providers.hiya_live_identity import LiveScores, duration_seconds
from verivoice.providers.hiya_voiceprint_builder import BuiltVoiceprint, HiyaVoiceprintBuilder
from verivoice.voiceprint_learning import VoiceprintLearning
from test_enrollment import FakeProviders


class LearningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.accounts=Accounts(Path(self.temp.name)/'accounts.sqlite3')
        provider=FakeProviders()
        self.settings=replace(provider.settings,hiya_api_key='test-key')
        token=self.accounts.register('learn@example.com','a long test password','en',provider,
            phone_region='US',phone_number='2025550123')
        self.uid=self.accounts.authenticate(token)['id']
        self.originals=[f'original-{n}' for n in range(5)]
        self.accounts.update(self.uid,state='complete',audios=self.originals)
        self.account=self.accounts.get(self.uid)
        self.events=[]
        self.learning=VoiceprintLearning(self.accounts,self.settings,
            record=lambda call,event,**details:self.events.append(event))
        self.scores=LiveScores(.7,.7,.7,'call-audio','verification',4,2)

    async def asyncTearDown(self):
        await self.learning.close()
        self.temp.cleanup()

    def test_raw_thresholds_and_duplicate_or_insufficient_recordings(self):
        for field in ('identity','synthesis','replay'):
            self.assertFalse(self.learning.accept(self.account,replace(self.scores,**{field:.699}), language_score=1))
            self.assertFalse(self.learning.accept(self.account,replace(self.scores,**{field:None}), language_score=1))
        self.assertFalse(self.learning.accept(self.account,replace(self.scores,synthesis=.5), language_score=1))
        self.assertFalse(self.learning.accept(self.account,replace(self.scores,voice_seconds=.8), language_score=1))
        self.assertFalse(self.learning.accept(self.account,replace(self.scores,voice_seconds=None), language_score=1))
        self.assertFalse(self.learning.accept(self.account,replace(self.scores,audio_handle=None), language_score=1))
        self.assertFalse(self.learning.accept(dict(self.account,hiya_space='different'),self.scores, language_score=1))
        self.assertTrue(self.learning.accept(self.account,self.scores, language_score=1))
        self.assertFalse(self.learning.accept(self.account,self.scores, language_score=1))
        with self.accounts.connect() as db:
            row=dict(db.execute('SELECT * FROM voiceprint_samples').fetchone())
        self.assertEqual(row['account'],self.uid)
        self.assertEqual(row['synthesis_score'],.7)
        self.assertEqual(row['language_score'],1)
        self.assertEqual(row['source_voiceprint'],'main')

    def test_measured_language_is_checked_at_the_same_threshold(self):
        for language in (None,.699):
            self.assertFalse(self.learning.accept(self.account,self.scores,language_score=language))
        self.assertTrue(self.learning.accept(self.account,self.scores,language_score=.7))
        with self.accounts.connect() as db:
            self.assertEqual(db.execute('SELECT language_score FROM voiceprint_samples').fetchone()[0],.7)

    async def test_security_phrase_is_reserved_once_and_marked_saved_only_after_compute(self):
        self.assertFalse(self.account['security_phrase_saved'])
        self.assertFalse(self.learning.accept(self.account,replace(self.scores,synthesis=.6),is_security_phrase=True, language_score=1))
        self.assertTrue(self.learning.accept(self.account,self.scores,is_security_phrase=True, language_score=1))
        another=replace(self.scores,audio_handle='another-phrase')
        self.assertFalse(self.learning.accept(self.account,another,is_security_phrase=True, language_score=1))
        self.assertFalse(self.accounts.get(self.uid)['security_phrase_saved'])
        async def fail(*args):raise RuntimeError('unavailable')
        self.learning.builder.build=fail
        with self.assertRaises(RuntimeError):await self.learning.refresh(self.uid,'test')
        self.assertFalse(self.accounts.get(self.uid)['security_phrase_saved'])
        async def succeed(*args):return BuiltVoiceprint('adaptive-phrase',tuple(self.originals+['call-audio']),1)
        self.learning.builder.build=succeed
        await self.learning.refresh(self.uid,'test')
        self.assertTrue(self.accounts.get(self.uid)['security_phrase_saved'])
        # Use the stale call snapshot deliberately: eligibility reads current state.
        self.assertFalse(self.learning.accept(self.account,another,is_security_phrase=True, language_score=1))
        self.assertTrue(self.learning.accept(self.account,replace(self.scores,audio_handle='conversation'),is_security_phrase=False, language_score=1))
        provider=FakeProviders()
        token=self.accounts.register('other@example.com','a long test password','en',provider,
            phone_region='US',phone_number='2025550124')
        other=self.accounts.authenticate(token)
        self.assertFalse(other['security_phrase_saved'])
        self.assertTrue(self.learning.accept(other,another,is_security_phrase=True, language_score=1))

    async def test_atomic_activation_preserves_enrollment_and_routes_future_calls(self):
        self.learning.accept(self.account,self.scores, language_score=1)
        entered=asyncio.Event();release=asyncio.Event()
        async def build(account,samples):
            entered.set();await release.wait()
            self.assertEqual(samples[0]['audio'],'call-audio')
            return BuiltVoiceprint('adaptive-test',tuple(self.originals+['call-audio']),1)
        self.learning.builder.build=build
        task=asyncio.create_task(self.learning.refresh(self.uid,'test-call'))
        await entered.wait()
        self.assertEqual(self.accounts.enrolled_phone(self.account['phone'])['voiceprint'],'main')
        release.set();await task
        updated=self.accounts.enrolled_phone(self.account['phone'])
        self.assertEqual(updated['voiceprint'],'adaptive-test')
        self.assertEqual(json.loads(updated['audios']),self.originals)
        self.assertEqual(self.account['voiceprint'],'main','An in-progress call retains its original version')
        with self.accounts.connect() as db:
            history=[row['handle'] for row in db.execute('SELECT handle FROM voiceprint_versions')]
        self.assertEqual(set(history),{'main','adaptive-test'})

    async def test_failed_build_keeps_active_version_and_retries_on_restart(self):
        self.learning.accept(self.account,self.scores, language_score=1)
        async def fail(*args):raise RuntimeError('unavailable')
        self.learning.builder.build=fail
        self.learning.schedule(self.uid,'test-call')
        await asyncio.gather(*self.learning.tasks.values())
        self.assertEqual(self.accounts.get(self.uid)['voiceprint'],'main')
        self.assertIn('voiceprint_update_failed',self.events)
        async def succeed(*args):return BuiltVoiceprint('adaptive-retry',tuple(self.originals+['call-audio']),1)
        self.learning.builder.build=succeed
        await self.learning.resume()
        await asyncio.gather(*self.learning.tasks.values())
        self.assertEqual(self.accounts.get(self.uid)['voiceprint'],'adaptive-retry')
        await self.learning.resume()
        self.assertFalse(self.learning.tasks,'Already processed samples must not retrain on each startup')

    async def test_account_change_during_build_prevents_activation(self):
        self.learning.accept(self.account,self.scores, language_score=1)
        async def build(*args):
            with self.accounts.connect() as db:
                db.execute("UPDATE accounts SET voiceprint='newer-version' WHERE id=?",(self.uid,))
            return BuiltVoiceprint('stale-version',tuple(self.originals+['call-audio']),1)
        self.learning.builder.build=build
        with self.assertRaises(RuntimeError):await self.learning.refresh(self.uid,'test-call')
        self.assertEqual(self.accounts.get(self.uid)['voiceprint'],'newer-version')


class BuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings=replace(FakeProviders().settings,hiya_api_key='test-key')
        self.originals=[f'original-{n}' for n in range(5)]
        self.account={'id':'identity','voiceprint':'main','audios':json.dumps(self.originals),
                      'hiya_owner':'owner','hiya_space':'main','hiya_region':'us'}
        self.members=[];self.requests=[];self.vp=None
        self.original_seconds=20
        self.fail_compute=False

    def handler(self,request):
        self.requests.append(request)
        path=request.url.path;method=request.method
        if path.endswith('/voiceprints/identity/main'):
            return httpx.Response(200,json={'state':'computed','model':'digital/v1','minAudios':5})
        if '/audios/' in path and '/voiceprints/' not in path:
            name=path.rsplit('/',1)[-1]
            return httpx.Response(200,json={'state':'available','duration':f'PT{self.original_seconds if name.startswith("original-") else 4}S'})
        if path.endswith('/voiceprints') and method=='POST':
            self.vp=json.loads(request.content)|{'state':'notComputable','audios':0}
            return httpx.Response(201,json=self.vp)
        if path.endswith('/audios'):
            return httpx.Response(200,json=[{'handle':audio} for audio in self.members])
        if '/audios/' in path:
            self.members.append(path.rsplit('/',1)[-1])
            self.vp['audios']=len(self.members)
            self.vp['state']='computable'
            return httpx.Response(200,json=self.vp)
        if path.endswith(':compute'):
            if self.fail_compute:return httpx.Response(503,json={})
            self.vp['state']='computed'
            return httpx.Response(200,json=self.vp)
        return httpx.Response(200,json=self.vp) if self.vp else httpx.Response(404,json={})

    async def build(self,samples):
        client=httpx.AsyncClient(base_url='https://api.hiya.com/audiointel/us/v1/',transport=httpx.MockTransport(self.handler))
        with patch('verivoice.providers.hiya_voiceprint_builder.httpx.AsyncClient',return_value=client):
            return await HiyaVoiceprintBuilder(self.settings).build(self.account,samples)

    async def test_preserves_originals_selects_latest_within_limit_and_resumes_idempotently(self):
        samples=[{'id':i,'audio':f'call-{i}','seconds':4} for i in range(1,8)]
        result=await self.build(samples)
        self.assertEqual(result.added,5)
        self.assertEqual(set(self.members),set(self.originals+[f'call-{i}' for i in range(3,8)]))
        self.assertTrue(result.handle.startswith('adaptive-'))
        self.assertFalse(any(request.method!='GET' and '/voiceprints/identity/main' in request.url.path for request in self.requests))
        self.requests.clear()
        again=await self.build(samples)
        self.assertEqual(again,result)
        self.assertTrue(all(request.method=='GET' for request in self.requests))

    async def test_failed_compute_can_resume_without_readding_members(self):
        samples=[{'id':1,'audio':'call-1','seconds':4}]
        self.fail_compute=True
        with self.assertRaises(RuntimeError):await self.build(samples)
        attached=list(self.members)
        self.fail_compute=False
        result=await self.build(samples)
        self.assertEqual(self.members,attached)
        self.assertEqual(result.added,1)

    async def test_no_capacity_creates_no_new_voiceprint(self):
        self.original_seconds=24
        result=await self.build([{'id':1,'audio':'call-1','seconds':4}])
        self.assertEqual(result.handle,'main')
        self.assertEqual(result.added,0)
        self.assertTrue(all(request.method=='GET' for request in self.requests))

    def test_duration_parsing(self):
        self.assertEqual(duration_seconds('PT1M2.5S'),62.5)
        self.assertEqual(duration_seconds('PT0.9S'),.9)
        self.assertIsNone(duration_seconds(None))
        self.assertIsNone(duration_seconds('PT'))
        self.assertIsNone(duration_seconds('NaN'))
