import asyncio
import json
import unittest
from verivoice.providers.soniox_api import score_tokens, SonioxSession, SonioxError
from verivoice.confidence import Confidence, REMOVAL


def token(language='en', confidence=.95, **kwargs):
    return dict(text='word',language=language,confidence=confidence,is_final=True,**kwargs)

class LanguageTests(unittest.TestCase):
    def test_ratio_only_counts_confident_final_original_lexical_tokens(self):
        tokens=[token('en'), token('es'),token('es'),token('fr'),token('en',.79),
                token('en',None),dict(token(),is_final=False),dict(token(),text='!'),
                dict(token(),translation_status='translation'),dict(token(),text='<fin>')]
        score=score_tokens(tokens,'en',{'es':'Spanish'})
        self.assertEqual(score.score,.25)
        self.assertEqual(score.total_tokens,4)
        self.assertEqual(score.excluded_uncertain_tokens,2)
        self.assertEqual(score.dominant_other_name,'Spanish')
    def test_missing_evidence_never_passes(self):
        for tokens in ([],[token(None)],[token('en',.2)],[token('en',float('nan'))],[token('en',True)]):
            self.assertIsNone(score_tokens(tokens,'en').score)
        self.assertEqual(score_tokens([token(),token(None)],'en').score,.5)
    def test_boundary_ties_and_duplicates(self):
        t=token('en',.8,start_ms=0,end_ms=100)
        s=score_tokens([t,t,token('es'),token('fr')],'en')
        self.assertEqual(s.score,1/3)
        self.assertIsNone(s.dominant_other)
    def test_language_ema_warning_and_shared_removal(self):
        state=Confidence();state.update(1,1,1,1)
        metadata=dict(dominant_other='es',dominant_other_name='Spanish',registered_language_name='English')
        state.update(1,1,1,.6,**metadata)
        self.assertEqual(state.cl,.8)
        self.assertEqual(state.notification(0)['type'],'warning')
        self.assertIsNone(state.notification(1))
        state.update(1,1,1,.5,**metadata)
        notice=state.notification(2)
        self.assertEqual(notice['type'],'removed')
        self.assertIn('Spanish',notice['message'])
        other=Confidence();other.update(.2,1,1,.5,**metadata)
        self.assertEqual(other.notification(0)['message'],REMOVAL)
    def test_missing_language_does_not_update_any_ema(self):
        state=Confidence();self.assertIsNone(state.update(1,1,1)['c'])
        state.update(1,1,1,1);before=state.snapshot()
        self.assertEqual(state.update(.1,.1,.1),before)

class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalization_keeps_windows_separate(self):
        class Socket:
            sent=[]
            responses=iter([{'tokens':[dict(token(),is_final=False)]},
                {'tokens':[token(),{'text':'<fin>','is_final':True}]},
                {'tokens':[token('es'),{'text':'<fin>','is_final':True}]}])
            async def send(self,x):self.sent.append(x)
            async def recv(self):return json.dumps(next(self.responses))
        socket=Socket();session=SonioxSession(socket,'en',{})
        self.assertEqual((await session.score(bytes(32000))).score,1)
        self.assertEqual((await session.score(bytes(32000))).score,0)
        self.assertEqual(json.loads(socket.sent[2]),{'type':'finalize'})
    async def test_provider_error_is_not_a_score(self):
        class Socket:
            async def send(self,x):pass
            async def recv(self):return '{"error_code":400}'
        with self.assertRaises(SonioxError):await SonioxSession(Socket(),'en',{}).score(bytes(32000))

    async def test_exhausted_balance_has_safe_actionable_message(self):
        class Socket:
            async def send(self,x):pass
            async def recv(self):return json.dumps({'error_code':402,'error_type':'organization_balance_exhausted','error_message':'untrusted'})
        with self.assertRaisesRegex(SonioxError,'balance is exhausted'):
            await SonioxSession(Socket(),'en',{}).score(bytes(32000))
