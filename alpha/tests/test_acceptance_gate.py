"""Receipt coverage/integrity gates, entirely synthetic control-plane fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from alpha.phase_runtime import PhaseRuntime, policy_hash
from alpha.languages import LANGUAGES


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        with patch.object(PhaseRuntime,'refresh',return_value={'ready':False}):
            self.runtime=PhaseRuntime(self.root)
        self.image='sha256:'+'a'*64;self.fingerprint={'engine':'fixture'}
        self.runtime.folder.mkdir()
        (self.runtime.folder/'runtime.json').write_text(json.dumps({'image_id':self.image,'policy_sha256':policy_hash()}))
        self.report={'suite':'alpha-five-language-v1','accepted':True,'verified_languages':list(LANGUAGES),
            'runtime':{'image_id':self.image,'policy_sha256':policy_hash(),'fingerprint':self.fingerprint},
            'cases':[{'passed':True}], 'legacy':{'accepted':True}}
        self.evidence=self.root/'evidence.json'
        self.receipt={'accepted':True,'suite':'alpha-five-language-v1','languages':list(LANGUAGES),
            'image_id':self.image,'policy_sha256':policy_hash(),'fingerprint':self.fingerprint,
            'evidence':str(self.evidence)}

    def write(self):
        self.evidence.write_text(json.dumps(self.report))
        self.receipt['evidence_sha256']=hashlib.sha256(self.evidence.read_bytes()).hexdigest()
        (self.runtime.folder/'acceptance.json').write_text(json.dumps(self.receipt))

    def ready(self):
        with patch.object(self.runtime,'preflight',return_value=self.fingerprint),patch.object(self.runtime,'command',return_value=json.dumps([{'Id':self.image,'Config':{}}])):
            return self.runtime.refresh()['ready']

    def test_complete_matching_receipt_is_ready(self):
        self.write();self.assertTrue(self.ready())

    def test_legacy_or_partial_coverage_cannot_enable_new_languages(self):
        for key,value in [('suite','round2'),('languages',['c','cpp','python'])]:
            with self.subTest(key=key):
                original=self.receipt[key];self.receipt[key]=value;self.write()
                self.assertFalse(self.ready());self.receipt[key]=original

    def test_missing_or_changed_evidence_disables_runtime(self):
        self.write();self.evidence.write_text('{}');self.assertFalse(self.ready())
        self.write();self.evidence.unlink();self.assertFalse(self.ready())

    def test_failed_wrong_runtime_or_partial_report_is_rejected_even_with_matching_hash(self):
        for key,value in [('accepted',False),('suite','old-suite'),('verified_languages',['python']),
                          ('cases',[{'passed':False}]),('cases',[]),('legacy',{'accepted':False}),
                          ('runtime',{'image_id':self.image,'policy_sha256':'wrong','fingerprint':self.fingerprint})]:
            with self.subTest(key=key,value=value):
                original=self.report[key];self.report[key]=value;self.write()
                self.assertFalse(self.ready());self.report[key]=original
