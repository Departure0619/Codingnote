import copy,json,sys,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from alpha.app import create_app
from alpha.tutor import Tutor
from alpha.migrations import backup_database,restore_backup
from question_bank.validate import validate_bank

class OfflineExecution:
    def status(self):return {'ready':False,'reason':'fresh offline test','languages':{}}
    def run(self,*args,**kwargs):raise AssertionError('No submitted code on host')

def synthetic_question():
    return {'id':'PUBLIC-CONTRACT-FIXTURE','title':'Synthetic marker protocol','statement':'Output the marker PUBL after an arbitrary label.',
        'difficulty':'fixture','knowledge':{'primary':'输入输出','tags':['synthetic-marker']},
        'input_format':'One label','output_format':'A marker and label','samples':[{'stdin':'cedar\n','stdout':'PUBLcedar\n','explanation':'Synthetic fixture only'}],
        'constraints':['Test fixture, not a distributed builtin question'], 'source':{'kind':'new_public_scope_test_fixture','attribution':'New synthetic public-scope test; no external question content'},
        'license':{'public_distribution_confirmed':True,'statement':'owner-confirmed project test scope, root licence pending','tests':'owner-confirmed project test scope, root licence pending','reference':'no reference solution distributed'},
        'test_cases':[{'id':'marker-case','stdin':'cedar\n','stdout':'PUBLcedar\n','purpose':'sample'}]}

class PublicAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.tutor=Tutor(transport=lambda *a: (_ for _ in ()).throw(AssertionError('No model transport')),round_ledger_path=self.root/'synthetic-budget.sqlite3')
        self.app=create_app(self.root,execution=OfflineExecution(),tutor=self.tutor)
        self.client=TestClient(self.app);self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None);self.tmp.cleanup()
    def create(self,title='Synthetic notebook label'):
        r=self.client.post('/api/tasks',json={'title':title,'statement':'An independently authored label fixture','language':'python','code':"print('public-notebook')",'source_url':'https://example.org/public-notebook','knowledge_primary':'输入输出','knowledge_tags':['synthetic-label']})
        self.assertEqual(r.status_code,200,r.text);return r.json()
    def test_home_and_empty_bank_have_no_invented_questions(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.app.state.bank.import_receipt['active_questions'],0)
        self.assertEqual(self.client.get('/api/tasks').json(),[])
        self.assertEqual(self.app.state.bank.listing()['items'],[])
    def test_no_experimental_or_legacy_app_import(self):
        self.assertFalse(any(n.startswith('experiments.') or n=='web_lab.app' for n in sys.modules))
    def test_manual_create_edit_versions_and_source_tags(self):
        t=self.create();body={k:t[k] for k in ['title','statement','source_url','revision']}
        body.update(language='python',code="print('public-notebook-v2')",knowledge_primary='字符串',knowledge_tags=['synthetic-second'])
        r=self.client.put('/api/tasks/'+t['id'],json=body);self.assertEqual(r.status_code,200,r.text)
        saved=r.json();self.assertEqual(len(saved['versions']),2)
        self.assertEqual(saved['versions'][0]['source'],t['versions'][0]['source'])
        self.assertEqual(self.client.get('/api/tasks/'+t['id']).json(),saved)
        self.assertEqual(self.client.put('/api/tasks/'+t['id'],json=body).status_code,409)
    def test_source_import_is_local_candidate_not_network_fetch(self):
        r=self.client.post('/api/import/source',json={'url':'https://codeforces.com/problemset/problem/991/Z','text':''})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['platform'],'Codeforces')
        self.assertEqual(r.json()['original_id'],'991/Z')
        self.assertEqual(self.client.get('/api/tasks').json(),[])
        self.assertEqual(self.client.post('/api/import/source',json={'url':'http://127.0.0.1/private','text':''}).status_code,422)
    def test_mistake_error_review_and_association_are_preserved(self):
        tasks=[self.create('Synthetic link '+str(i)) for i in range(2)];errors=[]
        for t in tasks:
            e=self.client.post('/api/tasks/'+t['id']+'/errors',json={'version_id':t['current_version_id'],'observed':'Synthetic marker mismatch','category':'label','reason':'Synthetic reason','status':'user_confirmed'})
            self.assertEqual(e.status_code,200,e.text);errors.append(e.json())
            m=self.client.put('/api/tasks/'+t['id']+'/mistake',json={'original_version_id':t['current_version_id'],'summary':'Synthetic review note','tags':['marker-review'],'confirmed':True,'needs_review':True})
            self.assertEqual(m.status_code,200,m.text)
            review=self.client.post('/api/mistakes/'+m.json()['id']+'/reviews',json={'outcome':'unsure','notes':'Synthetic revisit'})
            self.assertEqual(review.status_code,200,review.text)
        assoc=self.client.post('/api/associations',json={'left_error_id':errors[0]['id'],'right_error_id':errors[1]['id'],'rationale':'Both involve a synthetic marker'})
        self.assertEqual(assoc.status_code,200,assoc.text)
        self.assertEqual(self.client.get('/api/associations/'+assoc.json()['id']).status_code,200)
    def test_default_ai_disabled_never_sends(self):
        t=self.create();r=self.client.post('/api/tasks/'+t['id']+'/chat',json={'version_id':t['current_version_id'],'message':'Synthetic question','request_id':'disabled'})
        self.assertEqual(r.status_code,503);self.assertEqual(self.tutor.mode,'disabled');self.assertEqual(self.tutor.key,'')
    def test_generic_authorized_bank_import_is_not_removed(self):
        q=synthetic_question();bank={'schema_version':'1.0','bank_id':'new-authorized-fixture','questions':[q]}
        self.assertEqual(validate_bank(bank),[])
        self.assertEqual(self.app.state.bank.import_asset(bank)['inserted'],1)
        response=self.client.post('/api/bank/'+q['id']+'/practice',json={'language':'python','request_id':'new-fixture-practice'})
        self.assertEqual(response.status_code,200,response.text)
    def test_invalid_asset_is_rejected_before_mutating_database(self):
        bank={'schema_version':'1.0','bank_id':'no-permission','questions':[synthetic_question()]};bank['questions'][0]['license']={}
        with self.assertRaises(ValueError):self.app.state.bank.import_asset(bank)
        self.assertEqual(self.app.state.bank.listing()['items'],[])
    def test_nonempty_contract_rejects_duplicate_and_bad_sample(self):
        q=synthetic_question();bank={'schema_version':'1.0','bank_id':'new-fixture','questions':[q,copy.deepcopy(q)]}
        self.assertTrue(any('duplicate' in e for e in validate_bank(bank)))
        bank['questions']=[q];q['samples'][0]['stdout']='wrong-marker'
        self.assertTrue(any('sample' in e for e in validate_bank(bank)))
    def test_missing_attribution_cannot_enter_practice_database(self):
        q=synthetic_question();q['source'].pop('attribution')
        self.assertTrue(validate_bank({'schema_version':'1.0','bank_id':'attribution-required','questions':[q]}))
    def test_backup_restore_keeps_new_manual_records(self):
        t=self.create();backup=self.root/'snapshot.sqlite3';backup_database(self.app.state.store.path,backup)
        restored=self.root/'restored'/'learning.sqlite3';restored.parent.mkdir()
        restore_backup(backup,restored)
        other=create_app(restored.parent,execution=OfflineExecution(),tutor=Tutor(transport=lambda *a:None))
        with TestClient(other) as client:self.assertEqual(client.get('/api/tasks/'+t['id']).json(),t)

if __name__=='__main__':unittest.main()
