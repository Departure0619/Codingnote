import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from alpha.runner import Execution


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.execution = Execution(self.tmp.name)
        self.runtime = self.execution.runtime
        self.ready = patch.object(self.execution, 'status', return_value={'ready': True})
        self.ready.start()

    def tearDown(self):
        self.ready.stop()
        self.tmp.cleanup()

    def phase(self, status='completed', **extra):
        return dict(status=status, executed=True, cleaned=True, stdout='', stderr='',
                    elapsed_seconds=.2, image_id='sha256:fixture', **extra)

    def test_native_artifact_never_persisted_and_both_phases_bound(self):
        with patch.object(self.runtime, 'execute_phase', side_effect=[
            self.phase(artifact=b'ELF-test'), self.phase()]) as execute:
            result = self.execution.run('cpp', 'int main(){}', '中文\x00')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['compile_status'], 'passed')
        self.assertNotIn('artifact', result['compile_result'])
        self.assertEqual(result['elapsed_seconds'], .4)
        self.assertEqual(execute.call_args_list[1].kwargs['artifact'], b'ELF-test')
        self.assertEqual(execute.call_args_list[1].kwargs['stdin'], '中文\x00')
        self.assertNotIn('ELF-test', json.dumps(result))

    def test_compile_error_never_runs(self):
        with patch.object(self.runtime, 'execute_phase', return_value=self.phase('exception')) as execute:
            result = self.execution.run('c', 'invalid syntax', '')
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result['status'], 'compile_error')
        self.assertEqual(result['compile_status'], 'failed')
        self.assertFalse(result['executed'])

    def test_compile_resource_failure_not_syntax_error(self):
        with patch.object(self.runtime, 'execute_phase', return_value=self.phase('resource_limit')):
            result = self.execution.run('cpp', 'int main(){}', '')
        self.assertEqual(result['status'], 'resource_limit')
        self.assertEqual(result['compile_status'], 'not_completed')
        self.assertFalse(result['executed'])

    def test_uncertain_cleanup_never_success(self):
        failure = self.phase()
        failure['cleaned'] = False
        with patch.object(self.runtime, 'execute_phase', return_value=failure):
            result = self.execution.run('python', 'print(1)', '')
        self.assertEqual(result['status'], 'service_error')
        self.assertFalse(result['executed'])

    def test_invalid_utf8_size_language_and_empty_source_no_docker(self):
        with patch.object(self.runtime, 'execute_phase') as execute:
            for language, source, stdin in [('js','1',''),('c','',''),('c','x','\ud800'),
                ('cpp','x','a'*64001),('python','a'*200001,'')]:
                result = self.execution.run(language, source, stdin)
                self.assertFalse(result['executed'])
            execute.assert_not_called()

    def test_cancelled_between_phases_has_no_student_execution(self):
        event = threading.Event()
        def compile_phase(*args, **kwargs):
            event.set()
            return self.phase(artifact=b'ELF-test')
        with patch.object(self.runtime, 'execute_phase', side_effect=compile_phase) as execute:
            result = self.execution.run('c', 'int main(){}', '', cancel_event=event)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['compile_status'], 'passed')
        self.assertFalse(result['executed'])

    def test_unavailable_does_not_execute_or_fallback(self):
        with patch.object(self.execution, 'status', return_value={'ready':False,'reason':'Docker 不可用'}), \
             patch.object(self.runtime, 'execute_phase') as execute:
            result = self.execution.run('c', 'int main(){}', '')
        self.assertEqual(result['status'], 'unavailable')
        execute.assert_not_called()

    def test_transport_exception_after_compile_preserves_compilation_success(self):
        with patch.object(self.runtime,'execute_phase',side_effect=[self.phase(artifact=b'ELF-test'),OSError('fixture')]):
            result=self.execution.run('c','int main(){}','')
        self.assertEqual(result['status'],'service_error')
        self.assertEqual(result['compile_status'],'passed')
