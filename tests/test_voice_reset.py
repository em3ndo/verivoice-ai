"""Language reset contract, with mocked Hiya (no real identities are deleted)."""
import asyncio
import json
from unittest.mock import Mock, patch
import unittest
import httpx
import test_multivoice
from verivoice.providers.hiya_client import HiyaClient, HiyaAPIError
from verivoice.providers.hiya_enrollment_api import HiyaEnrollmentAPI
from verivoice.providers.hiya_live_identity import LiveScores
from verivoice.voiceprint_learning import VoiceprintLearning
from test_enrollment import wav


class ResetTests(unittest.TestCase):
    post = test_multivoice.MultiVoiceTests.post
    tearDown = test_multivoice.MultiVoiceTests.tearDown
    def setUp(self):
        test_multivoice.MultiVoiceTests.setUp(self)
        self.providers.reset = Mock()

    def reset(self, language='en', confirmed=True):
        return self.post('/api/voice/reset', dict(language=language, confirmed=confirmed))

    def test_reset_each_language_clears_phrase_flag_history_and_allows_reenrollment(self):
        self.post('/api/voices', {'language': 'es'})
        spanish = self.accounts.authenticate(self.client.cookies['vv_session'])['id']
        self.accounts.update(spanish, state='complete', audios=['es-old']*5)
        for language, uid, other in [('en', self.root, spanish), ('es', spanish, self.root)]:
            with self.subTest(language=language):
                before = self.accounts.get(other)
                profile = self.accounts.get(uid)
                table = self.accounts.profile_table(profile)
                learning = VoiceprintLearning(self.accounts, self.providers.settings, record=lambda *a, **k: None)
                self.assertTrue(learning.accept(profile, LiveScores(.9,.9,.9,'sample','verification',4,2), language_score=1, is_security_phrase=True))
                with self.accounts.connect() as db:
                    db.execute(f"UPDATE {table} SET security_phrase_saved=1,voiceprint='adaptive-old' WHERE id=?", (uid,))
                    db.execute('INSERT INTO voiceprint_versions VALUES (?,?,?,?,?)', (uid,'adaptive-old','main','[]',0))
                    db.execute('INSERT INTO voiceprint_learning_state VALUES (?,?)', (uid,1))
                result = self.reset(language)
                self.assertEqual(result.status_code, 200, result.text)
                self.providers.reset.assert_called_with(uid)
                self.assertEqual(result.json()['accepted'], 0)
                fresh = self.accounts.get(uid)
                self.assertEqual(fresh['state'], 'pending')
                self.assertEqual(fresh['voiceprint'], 'main')
                self.assertFalse(fresh['security_phrase_saved'])
                self.assertEqual(json.loads(fresh['audios']), [])
                self.assertEqual(self.accounts.get(other), before)
                self.assertIsNone(self.accounts.enrolled_phone(fresh['phone'],language))
                with self.accounts.connect() as db:
                    for history in ('voiceprint_samples','voiceprint_versions','voiceprint_learning_state'):
                        self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {history} WHERE account=?',(uid,)).fetchone()[0],0)
                self.providers.language=language
                for index in range(5):
                    response=self.client.post('/api/record/'+str(index),content=wav(),headers={'X-Verivoice':'enrollment'})
                    self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(self.post('/api/finish',{}).json()['state'],'complete')
                self.assertEqual(self.accounts.enrolled_phone(fresh['phone'],language)['id'],uid)
                # Results from a call begun before reset must not train the new voice.
                self.assertFalse(learning.accept(profile,LiveScores(.9,.9,.9,'stale','verification',4,2),language_score=1))
                self.assertTrue(learning.accept(self.accounts.get(uid),LiveScores(.9,.9,.9,'new','verification',4,2),language_score=1,is_security_phrase=True))

    def test_confirmation_auth_and_owned_language_required(self):
        for language, confirmed in [('en',False),('ru',True),('unknown',True)]:
            self.assertEqual(self.reset(language,confirmed).status_code,400)
        self.post('/api/logout',{})
        self.assertEqual(self.reset().status_code,400)
        self.providers.reset.assert_not_called()
        self.assertEqual(self.accounts.get(self.root)['state'],'complete')

    def test_remote_failure_disables_voice_until_successful_retry(self):
        from verivoice.providers.enrollment_common import EnrollmentError
        self.providers.reset.side_effect=EnrollmentError('Retry deletion')
        self.assertEqual(self.reset().status_code,400)
        self.assertEqual(self.accounts.get(self.root)['state'],'resetting')
        self.assertEqual(self.client.get('/api/me').json()['phrase'],None)
        self.assertEqual(self.post('/api/finish',{}).status_code,400)
        self.assertEqual(self.client.post('/api/record/0',content=wav(),headers={'X-Verivoice':'enrollment'}).status_code,400)
        self.providers.reset.side_effect=None
        self.assertEqual(self.reset().json()['state'],'pending')

    def test_configuration_mismatch_never_deletes(self):
        with self.accounts.connect() as db:
            db.execute("UPDATE accounts SET hiya_space='another-space' WHERE id=?",(self.root,))
        self.assertEqual(self.reset().status_code,400)
        self.providers.reset.assert_not_called()


class HiyaResetTests(unittest.TestCase):
    def test_all_pages_and_versions_deleted_before_identity_with_empty_204(self):
        from verivoice.config import Settings
        settings=Settings(hiya_api_key='test',hiya_owner='owner',hiya_space='space',hiya_region='us')
        seen=[]
        def respond(request):
            seen.append((request.method,request.url.path))
            if request.method=='GET':
                page=request.url.params['page']
                return httpx.Response(200,json=[{'handle':'main' if page=='1' else 'adaptive-hash'}],headers={'Page-Total':'2'})
            return httpx.Response(204)
        client=HiyaClient(settings,transport=httpx.MockTransport(respond))
        with patch('verivoice.providers.hiya_enrollment_api.HiyaClient',return_value=client):
            HiyaEnrollmentAPI(settings).reset('vv-stored-handle')
        self.assertEqual([path.split('/')[-1] for method,path in seen if method=='DELETE'],['main','adaptive-hash','vv-stored-handle'])
        self.assertTrue(all('vv-stored-handle' in path for _,path in seen))

    def test_missing_is_retryable_but_other_statuses_fail(self):
        from verivoice.config import Settings
        settings=Settings(hiya_api_key='test',hiya_owner='owner',hiya_space='space',hiya_region='us')
        for code in (404,403,500,202):
            with self.subTest(code=code):
                def respond(request):
                    return httpx.Response(404 if request.method=='GET' else code)
                client=HiyaClient(settings,transport=httpx.MockTransport(respond))
                with patch('verivoice.providers.hiya_enrollment_api.HiyaClient',return_value=client):
                    if code==404:
                        HiyaEnrollmentAPI(settings).reset('identity')
                    else:
                        with self.assertRaises(HiyaAPIError): HiyaEnrollmentAPI(settings).reset('identity')
