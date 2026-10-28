"""Round 3.5 control contracts. No submitted program executes on the host."""
import base64
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from alpha.app import create_app
from alpha.runner import Execution, LANGUAGES
from alpha.phase_runtime import limits_for, RUN_LIMITS, COMPILE_LIMITS
from tests.helpers import OfflineExecution


class LanguageExpansionTests(unittest.TestCase):
    def managed(self):
        self.assertTrue(Path('alpha/native_image/managed.py').exists(), 'managed source/artifact adapter missing')
        return importlib.import_module('alpha.native_image.managed')

    def test_registry_has_five_languages_with_bounded_phase_profiles(self):
        self.assertEqual(set(LANGUAGES), {'c', 'cpp', 'python', 'java', 'javascript'})
        for language in LANGUAGES:
            self.assertEqual(limits_for(language, 'run'), RUN_LIMITS)
        self.assertEqual(limits_for('java', 'compile'), COMPILE_LIMITS)
        self.assertEqual(limits_for('javascript', 'compile'), RUN_LIMITS)

    def test_api_accepts_java_and_javascript_but_rejects_unknown_language(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(directory, execution=OfflineExecution())) as client:
            for language in ['java', 'javascript']:
                response = client.post('/api/tasks', json={'title': 'fixture', 'language': language, 'code': 'source'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['versions'][0]['language'], language)
            self.assertEqual(client.post('/api/tasks', json={'title': 'fixture', 'language': 'go'}).status_code, 422)

    def test_java_layout_handles_main_custom_public_and_helper_types(self):
        layout = self.managed().java_layout
        for code, expected in [
            ('public class Main {}', ('Main.java','Main')),
            ('public final class Answer {}', ('Answer.java','Answer')),
            ('class Helper {} class Main {}', ('Main.java','Main')),
            ('class Helper {} public class Solution {}', ('Solution.java','Solution')),
            ('public record Pair(int a, int b) {}', ('Pair.java','Pair')),
            ('// public class Fake {}\nclass Main { String s="public class Evil {}"; }', ('Main.java','Main')),
            ('/* package bad; */ class Main { class Nested {} }', ('Main.java','Main')),
        ]:
            with self.subTest(code=code): self.assertEqual(layout(code),expected)

    def test_java_layout_rejects_packages_multiple_public_types_and_ambiguous_entry(self):
        layout=self.managed().java_layout
        for source in ['package demo; public class Main {}', 'public class A {} public class B {}',
                       'class A {} class B {}', 'interface Only {}', 'public class 中文 {}']:
            with self.subTest(source=source), self.assertRaises(ValueError): layout(source)

    def test_java_artifact_is_bounded_named_data_not_host_files_or_zip_extraction(self):
        validate=self.managed().decode_classes
        good={'entry':'Main','classes':{'Main.class':base64.b64encode(b'\xca\xfe\xba\xbe-test').decode()}}
        entry,files=validate(json.dumps(good).encode())
        self.assertEqual(entry,'Main');self.assertEqual(files['Main.class'],b'\xca\xfe\xba\xbe-test')
        for name in ['../Main.class','/tmp/Main.class','Main/Other.class','main.py','Main.class/..']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate(json.dumps({'entry':'Main','classes':{name:good['classes']['Main.class']}}).encode())
        for entry in ['../Main','Main; echo x','Main.Other','Other']:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                validate(json.dumps({**good,'entry':entry}).encode())
        with self.assertRaises(ValueError): validate(b'x'*1048577)

    def test_unavailable_new_languages_do_not_run_or_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            execution=Execution(directory)
            with patch.object(execution,'status',return_value={'ready':False,'reason':'Docker unavailable'}), patch.object(execution.runtime,'execute_phase') as execute:
                for language in ['java','javascript']:
                    result=execution.run(language,'code','')
                    self.assertEqual(result['status'],'unavailable')
                    self.assertFalse(result['executed'])
                execute.assert_not_called()

    def test_status_exposes_registry_metadata_even_when_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            status=Execution(directory).status()
            self.assertIn('java',status['languages'])
            self.assertEqual(status['languages']['java']['runtime_version'],'17.0.18+8')
            self.assertEqual(status['languages']['javascript']['runtime_version'],'22.22.0')
            self.assertFalse(status['languages']['java']['ready'])


if __name__=='__main__': unittest.main()
