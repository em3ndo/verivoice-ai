import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from verivoice.app import create_app
from verivoice.enrollment import Accounts
from verivoice.voiceprint_learning import VoiceprintLearning
from verivoice.providers.hiya_live_identity import LiveScores
from verivoice.providers.hiya_voiceprint_builder import BuiltVoiceprint
from verivoice.calls import CALL_INSTRUCTION, call_instruction, opening_for, INTRO
from verivoice.call_prompts import OPENINGS, BANKER_OPENINGS, FAREWELLS, LANGUAGE_NAMES
from verivoice.config import SUPPORTED_LANGUAGES
from test_enrollment import FakeProviders, wav

class MultiVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite'
        self.providers=FakeProviders()
        self.client=TestClient(create_app(settings=self.providers.settings,database=self.path,providers=self.providers))
        self.accounts=Accounts(self.path)
        self.details=dict(email='voice@example.com',password='a long test password',language='en',consent=True,phone_region='US',phone_number='2025550123')
        self.post('/api/register',self.details)
        self.root=self.accounts.authenticate(self.client.cookies['vv_session'])['id']
        self.accounts.update(self.root,state='complete',audios=['old-'+str(i) for i in range(5)])
    def tearDown(self):self.client.close();self.temp.cleanup()
    def post(self,url,body):return self.client.post(url,json=body,headers={'X-Verivoice':'enrollment'})
    def test_email_and_phone_login_need_no_registration_fields(self):
        for identifier in ['VOICE@example.com','202-555-0123','+1 (202) 555-0123']:
            self.post('/api/logout',{})
            r=self.post('/api/login',dict(identifier=identifier,password=self.details['password']))
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['language'],'en')
        self.assertEqual(self.post('/api/login',dict(identifier='+12025550123',password='wrong')).status_code,400)
    def test_new_language_uses_same_account_with_independent_identity_and_progress(self):
        r=self.post('/api/voices',{'language':'es'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['language'],'es');self.assertEqual(r.json()['accepted'],0)
        self.assertNotIn('es',r.json()['available_languages']);self.assertNotIn('en',r.json()['available_languages'])
        spanish=self.accounts.authenticate(self.client.cookies['vv_session'])
        self.assertNotEqual(spanish['id'],self.root);self.assertEqual(spanish['account_id'],self.root)
        self.assertEqual(spanish['phone'], '+12025550123')
        self.assertIsNone(self.accounts.enrolled_phone(spanish['phone'],'es'))
        for index in range(5):
            r=self.client.post('/api/record/'+str(index),content=wav(),headers={'X-Verivoice':'enrollment','Content-Type':'audio/wav'})
            self.assertEqual(r.status_code,200)
        self.assertEqual(self.post('/api/finish',{}).json()['state'],'complete')
        target=self.accounts.enrolled_phone(spanish['phone'],'es')
        self.assertEqual(target['id'],spanish['id'])
        self.assertEqual(self.accounts.get(self.root)['audios'],'["old-0", "old-1", "old-2", "old-3", "old-4"]')
        self.assertEqual(self.post('/api/voice',{'language':'en'}).json()['state'],'complete')
        self.assertEqual(self.accounts.authenticate(self.client.cookies['vv_session'])['id'],self.root)
        self.assertEqual(self.post('/api/voice',{'language':'es'}).json()['accepted'],5)
    def test_duplicate_unsupported_and_unauthenticated_enrollment_rejected(self):
        self.assertEqual(self.post('/api/voices',{'language':'en'}).status_code,400)
        self.assertEqual(self.post('/api/voices',{'language':'zh'}).status_code,400)
        self.post('/api/voices',{'language':'es'})
        self.assertEqual(self.post('/api/voices',{'language':'es'}).status_code,400)
        self.assertEqual(self.post('/api/voice',{'language':'ru'}).status_code,400)
        self.post('/api/logout',{})
        self.assertEqual(self.post('/api/voices',{'language':'hi'}).status_code,400)
    def test_selection_is_session_local_and_cannot_select_other_account(self):
        token=self.client.cookies['vv_session'];self.post('/api/voices',{'language':'es'})
        other=self.accounts.login(self.details['email'],self.details['password'])
        self.assertEqual(self.accounts.authenticate(other)['language'],'en')
        self.assertEqual(self.accounts.authenticate(token)['language'],'es')
        self.assertEqual(self.post('/api/voice',{'language':'hi'}).status_code,400)
    def test_additive_migration_and_status_keep_existing_profile_and_hide_provider_ids(self):
        rebuilt=Accounts(self.path)
        self.assertEqual(rebuilt.enrolled_phone('+12025550123')['id'],self.root)
        r=self.client.get('/api/me').json()
        self.assertNotIn('identity',r);self.assertNotIn('voiceprint',r)
        self.assertEqual(r['available_languages'],['es','hi','ru'])
    def test_all_supported_languages_have_complete_spoken_scripts_and_explicit_instructions(self):
        for scripts in (OPENINGS, BANKER_OPENINGS, FAREWELLS, LANGUAGE_NAMES):
            self.assertEqual(set(scripts),set(SUPPORTED_LANGUAGES))
        for language in SUPPORTED_LANGUAGES:
            instruction=call_instruction(True,language)
            self.assertIn(f'{LANGUAGE_NAMES[language]} ({language})',instruction)
            self.assertIn(OPENINGS[language],instruction)
            self.assertIn('never read them aloud',instruction)
            self.assertIn(FAREWELLS[language],call_instruction(False,language))
            self.assertIn('Satoshi Bank',BANKER_OPENINGS[language])
        for language in ('es','hi','ru'):
            self.assertNotIn('Before we can chat',OPENINGS[language])
            self.assertNotIn('Your voice has been verified',BANKER_OPENINGS[language])

    def test_banker_role_is_explicit_fiction_and_english_opening_is_preserved(self):
        self.assertIn('pretend banker at the imaginary firm Satoshi Bank',CALL_INSTRUCTION)
        self.assertIn('Never perform or claim to perform real payments',CALL_INSTRUCTION)
        self.assertIn(INTRO,call_instruction(True))
        self.assertIn('Con VeriVoice',opening_for('es'))
        self.assertIn('मेरी आवाज़',opening_for('hi'))

class AdditionalVoiceLearningTests(unittest.IsolatedAsyncioTestCase):
    async def test_adaptation_updates_only_selected_language_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts=Accounts(Path(tmp)/'db.sqlite');providers=FakeProviders()
            token=accounts.register('test@example.com','a long test password','en',providers,phone_region='US',phone_number='2025550123')
            root=accounts.authenticate(token)['id'];accounts.update(root,state='complete',audios=['en']*5)
            spanish=accounts.add_voice(token,'es',providers);uid=spanish['id']
            accounts.update(uid,state='complete',audios=['es-'+str(i) for i in range(5)])
            spanish=accounts.get(uid)
            learning=VoiceprintLearning(accounts,providers.settings,record=lambda *args,**kwargs:None)
            scores=LiveScores(.9,.9,.9,'spanish-audio','verification',4,2)
            self.assertTrue(learning.accept(spanish,scores,language_score=1,is_security_phrase=True))
            async def build(account,samples):
                self.assertEqual(account['id'],uid)
                return BuiltVoiceprint('adaptive-es',tuple(['es-'+str(i) for i in range(5)]+['spanish-audio']),1)
            learning.builder.build=build
            await learning.refresh(uid,'test')
            self.assertEqual(accounts.get(uid)['voiceprint'],'adaptive-es')
            self.assertTrue(accounts.get(uid)['security_phrase_saved'])
            self.assertEqual(accounts.get(root)['voiceprint'],'main')
            self.assertFalse(accounts.get(root)['security_phrase_saved'])
            await learning.close()
