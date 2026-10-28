import copy,json,tempfile,unittest
from pathlib import Path
from question_bank.validate import validate_bank

EMPTY={'schema_version':'1.0','bank_id':'aic-public-empty','questions':[]}
class PublicContractTests(unittest.TestCase):
    def test_empty_bank_is_valid_public_initial_state(self):
        self.assertEqual(validate_bank(EMPTY),[])
    def test_invalid_header_is_rejected(self):
        self.assertTrue(validate_bank({'questions':[]}))
    def test_missing_questions_is_not_silently_empty(self):
        self.assertTrue(validate_bank({'schema_version':'1.0','bank_id':'bad'}))
    def test_nonempty_unlicensed_question_is_rejected(self):
        bank=copy.deepcopy(EMPTY);bank['questions']=[{'id':'LOCAL-UNAUTHORIZED'}]
        self.assertTrue(validate_bank(bank))

if __name__=='__main__':unittest.main()
