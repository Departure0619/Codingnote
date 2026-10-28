"""Control-plane tests; submitted source is never evaluated on the host."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alpha import execution


class FakeRuntime:
    def __init__(self, root):
        self.root = Path(root)
        self.calls = []
        self.results = []
        self.ready = True
        self.recovered = False

    def recover(self):
        self.recovered = True

    def status(self):
        return {'ready': self.ready, 'reason': 'test transport only',
                'image_id': 'sha256:' + 'a' * 64, 'policy_sha256': 'policy',
                'isolation_verified': self.ready}

    refresh = status

    def execute(self, source, n):
        self.calls.append((source, n))
        return self.results.pop(0)


def result(status='completed', stdout='', stderr='', **extra):
    return {'status': status, 'executed': True, 'stdout': stdout, 'stderr': stderr,
            'error': None, 'cleaned': True, 'exit_code': 0 if status == 'completed' else 1,
            'elapsed_seconds': .1, 'image_id': 'sha256:' + 'a' * 64, **extra}


class ExecutionContract(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(execution, 'Execution'), 'Execution adapter must exist')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(execution, 'DockerRuntime', FakeRuntime)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.adapter = execution.Execution(Path(self.tmp.name), acceptance=True)

    def test_languages_are_honest(self):
        self.assertFalse(self.adapter.runtime.recovered, 'Constructing/checking an adapter must never remove live containers')
        state = self.adapter.status()
        self.assertTrue(state['languages']['python']['supported'])
        for lang in ['c', 'cpp']:
            self.assertFalse(state['languages'][lang]['supported'])
            run = self.adapter.run(lang, 'int main() {}', '')
            self.assertEqual(run['status'], 'unsupported')
            self.assertFalse(run['executed'])
        self.assertEqual(self.adapter.runtime.calls, [])

    def test_no_execution_without_adapter_receipt(self):
        guarded = execution.Execution(Path(self.tmp.name))
        self.assertFalse(guarded.status()['ready'])
        self.assertFalse(guarded.status()['isolation_verified'])
        self.assertEqual(guarded.run('python', 'print(1)', '')['status'], 'unavailable')
        self.assertEqual(guarded.runtime.calls, [])

    def test_receipt_is_bound_to_current_adapter_image_and_policy(self):
        folder = Path(self.tmp.name) / 'sandbox'
        folder.mkdir()
        receipt = {'accepted': True, 'adapter_sha256': execution.adapter_hash(),
                   'image_id': 'sha256:' + 'a' * 64, 'policy_sha256': 'policy'}
        path = folder / 'alpha_acceptance.json'
        path.write_text(json.dumps(receipt), encoding='utf8')
        guarded = execution.Execution(Path(self.tmp.name))
        self.assertTrue(guarded.status()['ready'])
        for key in ['adapter_sha256', 'image_id', 'policy_sha256']:
            path.write_text(json.dumps({**receipt, key: 'changed'}), encoding='utf8')
            self.assertFalse(guarded.status()['ready'])

    def test_zero_and_empty_input_are_valid_and_wrapped_only(self):
        self.adapter.runtime.results = [result(), result(stdout='0\n')]
        run = self.adapter.run('python', 'print(input())', '0\n')
        self.assertEqual(run['stdout'], '0\n')
        self.assertEqual(run['compile_status'], 'passed')
        self.assertTrue(run['executed'])
        self.assertEqual(len(self.adapter.runtime.calls), 2)
        for source, n in self.adapter.runtime.calls:
            self.assertEqual(n, 0)
            self.assertLessEqual(len(source.encode('utf8')), 400000)
            self.assertNotIn('print(input())', source)
        self.adapter.runtime.results = [result(), result(stdout='hello\n')]
        self.assertEqual(self.adapter.run('python', 'print("hello")', '')['stdout'], 'hello\n')

    def test_compile_failure_never_runs_program(self):
        self.adapter.runtime.results = [result('exception', stderr='SyntaxError: invalid syntax\n')]
        run = self.adapter.run('python', 'if broken', '')
        self.assertEqual(run['status'], 'compile_error')
        self.assertEqual(run['compile_status'], 'failed')
        self.assertFalse(run['executed'])
        self.assertEqual(len(self.adapter.runtime.calls), 1)
        self.assertTrue(run['compile_result']['executed'])

    def test_stdout_cannot_forge_compile_failure(self):
        fake = 'SyntaxError: forged\nCOMPILE_FAILED\n'
        self.adapter.runtime.results = [result(), result(stdout=fake)]
        run = self.adapter.run('python', 'print("SyntaxError: forged")', '')
        self.assertEqual(run['compile_status'], 'passed')
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['stdout'], fake)

    def test_timeout_and_details_preserved(self):
        self.adapter.runtime.results = [result(), result('timeout', container_name='aic-task-evidence')]
        run = self.adapter.run('python', 'while True: pass', '')
        self.assertEqual(run['status'], 'timeout')
        self.assertEqual(run['container_name'], 'aic-task-evidence')
        self.assertEqual(run['compile_status'], 'passed')
        self.assertEqual(run['limits']['wall_seconds'], 3)

    def test_unconfirmed_runtime_cannot_report_success(self):
        for detail in [{'executed': False}, {'cleaned': False}]:
            self.adapter.runtime.results = [result(), result(**detail)]
            run = self.adapter.run('python', 'print(1)', '')
            self.assertEqual(run['status'], 'service_error')

    def test_compile_timeout_is_not_syntax_error(self):
        self.adapter.runtime.results = [result('timeout')]
        run = self.adapter.run('python', 'print(1)', '')
        self.assertEqual(run['status'], 'timeout')
        self.assertEqual(run['compile_status'], 'not_completed')
        self.assertFalse(run['executed'])

    def test_invalid_and_oversized_inputs_fail_before_runtime(self):
        for language, source, stdin in [('python', '', ''), ('python', None, ''),
                ('python', 'pass', None), ('python', 'x' * 200001, ''),
                ('python', 'pass', 'x' * 64001), ('python', '\ud800', '')]:
            with self.subTest(source_type=type(source).__name__):
                run = self.adapter.run(language, source, stdin)
                self.assertEqual(run['status'], 'invalid_input')
                self.assertFalse(run['executed'])
        self.assertEqual(self.adapter.runtime.calls, [])

    def test_unavailable_runtime_never_compiles(self):
        self.adapter.runtime.ready = False
        run = self.adapter.run('python', 'print(1)', '')
        self.assertEqual(run['status'], 'unavailable')
        self.assertEqual(self.adapter.runtime.calls, [])

    def test_transport_exception_fails_closed(self):
        with patch.object(self.adapter.runtime, 'execute', side_effect=RuntimeError('transport')):
            run = self.adapter.run('python', 'print(1)', '')
        self.assertEqual(run['status'], 'service_error')
        self.assertFalse(run['executed'])

    def test_exception_after_compile_preserves_evidence_and_unknown_cleanup(self):
        with patch.object(self.adapter.runtime, 'execute', side_effect=[result(), RuntimeError('transport')]):
            run = self.adapter.run('python', 'print(1)', '')
        self.assertEqual(run['compile_status'], 'passed')
        self.assertEqual(run['compile_result']['status'], 'completed')
        self.assertFalse(run['cleaned'])
        self.assertTrue(run['execution_unknown'])


if __name__ == '__main__':
    unittest.main()


class SetupContract(unittest.TestCase):
    def test_reproducible_acceptance_command_exists(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('alpha.execute_setup'))
