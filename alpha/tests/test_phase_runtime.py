"""Phase control-plane tests. No submitted program executes on the host."""
import copy
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from alpha import phase_runtime as runtime
from web_lab.sandbox_runtime import DockerRuntime

IMAGE = 'sha256:' + 'a' * 64
FINGERPRINT = {'engine_id': 'test', 'context': 'local'}


def envelope(content=b'ELF-test', code=0, **extra):
    return json.dumps(dict(protocol='alpha-compile-1', exit_code=code, stdout='', stderr='',
                           artifact=base64.b64encode(content).decode(), **extra)).encode()


def container(profile, image=IMAGE):
    return {'Image': image, 'Config': {'User': '65534:65534',
        'Entrypoint': ['python', '-I', '-S', '-u', '/runner/supervisor.py'],
        'Cmd': None, 'Volumes': None, 'Env': ['PATH=/usr/local/bin:/usr/bin'] + [k+'=' for k in runtime.PROXIES]},
        'HostConfig': {'NetworkMode': 'none', 'Privileged': False, 'ReadonlyRootfs': True,
        'CapDrop': ['ALL'], 'CapAdd': None, 'SecurityOpt': ['no-new-privileges:true'],
        'Memory': profile['memory'], 'MemorySwap': profile['memory'], 'NanoCpus': 500000000,
        'PidsLimit': 32, 'IpcMode': 'none', 'PidMode': '', 'CgroupnsMode': 'private',
        'Binds': None, 'Devices': [], 'DeviceRequests': None, 'PortBindings': {},
        'LogConfig': {'Type': 'none'}, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0},
        'Tmpfs': {'/tmp': 'rw,noexec,nosuid,nodev,size='+str(profile['tmp_bytes'])+',nr_inodes=256,mode=1777'},
        'Ulimits': [{'Name': k, 'Soft': v, 'Hard': v} for k, v in
                    [('cpu', profile['cpu_seconds']), ('fsize', 1048576), ('nofile', 64), ('core', 0)]]},
        'Mounts': [{'Type': 'tmpfs', 'Destination': '/tmp'}]}


class FakeProcess:
    def __init__(self, stdout=b'', stderr=b'', code=0, cancel=None):
        self.stdout, self.stderr, self.stdin = io.BytesIO(stdout), io.BytesIO(stderr), io.BytesIO()
        self.returncode = code
        self.cancel = cancel
        self.killed = False

    def poll(self):
        if self.cancel is not None and not self.killed:
            self.cancel.set()
            return None
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class PhaseRuntimeContract(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(runtime, 'PhaseRuntime'), 'common phase runtime is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with patch.object(runtime.PhaseRuntime, 'refresh', return_value={'ready': False}):
            self.broker = runtime.PhaseRuntime(self.root)
        self.broker.image_id = IMAGE
        self.broker.context = 'local'
        self.broker.fingerprint = FINGERPRINT

    def ready_broker(self, exit_code=0):
        self.broker.refresh = Mock(return_value={'ready': True, 'image_id': IMAGE, 'reason': 'test only'})
        self.broker.inspect_policy = Mock()
        self.broker.cleanup = Mock(return_value=True)
        self.broker.command = Mock(side_effect=lambda args, **kwargs:
            json.dumps([{'State': {'StartedAt': '2026-01-01T00:00:00Z', 'Running': False,
                                 'Error': '', 'ExitCode': exit_code, 'OOMKilled': False}}])
            if args[0] == 'inspect' else '')

    def test_inherits_only_control_plane_and_uses_new_scope(self):
        self.assertIsInstance(self.broker, DockerRuntime)
        self.assertEqual(self.broker.folder, self.root / 'sandbox_round2')
        old = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:20]
        self.assertNotEqual(self.broker.scope, old)
        for name in ['command', 'preflight', 'cleanup', 'recover']:
            self.assertIs(getattr(runtime.PhaseRuntime, name), getattr(DockerRuntime, name))

    def test_profiles_keep_python_and_runtime_limits_frozen(self):
        for lang in ['c', 'cpp', 'python']:
            limits = runtime.limits_for(lang, 'run')
            self.assertEqual(limits['memory'], 134217728)
            self.assertEqual(limits['wall_seconds'], 3)
            self.assertEqual(limits['cpu_seconds'], 2)
            self.assertEqual(limits['tmp_bytes'], 16777216)
            self.assertEqual(limits['output_bytes'], 16384)
        self.assertEqual(runtime.limits_for('python', 'compile'), runtime.RUN_LIMITS)
        for lang in ['c', 'cpp']:
            limits = runtime.limits_for(lang, 'compile')
            self.assertEqual(limits['memory'], 536870912)
            self.assertEqual(limits['wall_seconds'], 20)
            self.assertEqual(limits['cpu_seconds'], 10)
            self.assertEqual(limits['cpu_budget_seconds'], 9)
            self.assertEqual(limits['tmp_bytes'], 33554432)

    def test_create_profiles_preserve_security_and_fixed_entrypoint(self):
        for lang, phase in [('python', 'compile'), ('python', 'run'), ('c', 'compile'), ('cpp', 'run')]:
            args = self.broker.create_args('aic-task-test', IMAGE, language=lang, phase=phase)
            for required in ['--pull=never', '--network=none', '--read-only', '--user=65534:65534',
                             '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--pids-limit=32']:
                self.assertIn(required, args)
            self.assertFalse(any(a in args for a in ['--privileged', '--mount', '-v', '--publish', '--entrypoint']))
            self.assertEqual(args[-1], IMAGE)
            self.assertIn('noexec', args[args.index('--tmpfs') + 1])
        args = self.broker.create_args('aic-task-test', IMAGE, language='c', phase='compile')
        self.assertIn('--memory=512m', args)
        self.assertIn('cpu=10:10', args)

    def test_effective_policy_rejects_each_security_or_profile_drift(self):
        for lang, phase in [('python', 'run'), ('c', 'compile')]:
            profile = runtime.limits_for(lang, phase)
            original = container(profile)
            image = {'Id': IMAGE, 'Config': {'Env': ['PATH=/usr/local/bin:/usr/bin']}}
            for mutation in [None, ('HostConfig', 'NetworkMode', 'bridge'), ('HostConfig', 'ReadonlyRootfs', False),
                             ('HostConfig', 'Memory', 999999999), ('Config', 'User', '0'),
                             ('HostConfig', 'CapAdd', ['SYS_ADMIN']), ('HostConfig', 'Binds', ['/host:/host']),
                             ('HostConfig', 'SecurityOpt', ['seccomp=unconfined']),
                             ('Config', 'Env', ['PATH=/usr/local/bin:/usr/bin', 'API_KEY=secret']),
                             ('HostConfig', 'Tmpfs', {'/tmp': 'rw,exec,nosuid,nodev,size=33554432,nr_inodes=256'})]:
                item = copy.deepcopy(original)
                if mutation:
                    item[mutation[0]][mutation[1]] = mutation[2]
                self.broker.command = Mock(side_effect=lambda args, **kwargs: json.dumps([image if args[0] == 'image' else item]))
                if mutation:
                    with self.assertRaises(runtime.SandboxUnavailable):
                        self.broker.inspect_policy('aic-task-test', language=lang, phase=phase)
                else:
                    self.broker.inspect_policy('aic-task-test', language=lang, phase=phase)

    def test_readiness_requires_only_new_matching_receipts(self):
        self.broker.folder.mkdir()
        manifest = {'image_id': IMAGE, 'policy_sha256': runtime.policy_hash()}
        (self.broker.folder / 'runtime.json').write_text(json.dumps(manifest), encoding='utf8')
        self.broker.preflight = Mock(return_value=FINGERPRINT)
        self.broker.command = Mock(return_value=json.dumps([{'Id': IMAGE, 'Config': {}}]))
        self.assertFalse(self.broker.refresh()['ready'])
        evidence_path = self.root / 'evidence.json'
        evidence = {'suite':'alpha-five-language-v1','accepted':True,'verified_languages':list(runtime.LANGUAGE_CONFIG),
                    'runtime':{**manifest,'fingerprint':FINGERPRINT},'cases':[{'passed':True}],'legacy':{'accepted':True}}
        evidence_path.write_text(json.dumps(evidence),encoding='utf8')
        receipt = {**manifest, 'accepted': True, 'fingerprint': FINGERPRINT,
                   'suite':'alpha-five-language-v1','languages':list(runtime.LANGUAGE_CONFIG),
                   'evidence':str(evidence_path),'evidence_sha256':hashlib.sha256(evidence_path.read_bytes()).hexdigest()}
        path = self.broker.folder / 'acceptance.json'
        path.write_text(json.dumps(receipt), encoding='utf8')
        self.assertTrue(self.broker.refresh()['ready'])
        for key, value in [('image_id', 'sha256:'+'b'*64), ('policy_sha256', 'wrong'), ('fingerprint', {}), ('accepted', False)]:
            path.write_text(json.dumps({**receipt, key: value}), encoding='utf8')
            self.assertFalse(self.broker.refresh()['ready'])
            self.assertFalse(self.broker.status()['isolation_verified'])
        self.broker.acceptance = True
        self.assertTrue(self.broker.refresh()['ready'])
        self.assertFalse(self.broker.status()['isolation_verified'])

    def test_constructor_never_recovers_or_writes(self):
        with patch.object(runtime.PhaseRuntime, 'refresh', return_value={'ready': False}), \
             patch.object(runtime.PhaseRuntime, 'recover') as recovery:
            runtime.PhaseRuntime(self.root)
        recovery.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_inputs_never_start_docker(self):
        self.ready_broker()
        for args in [('js', 'run', 'pass', '', None), ('c', 'link', 'x', '', None),
                     ('python', 'run', 'x'*200001, '', None), ('python', 'run', 'pass', 'x'*64001, None),
                     ('cpp', 'run', '', '', b'x'*1048577), ('c', 'run', '', '', None),
                     ('python', 'run', '\ud800', '', None), ('c', 'compile', '', '', None)]:
            run = self.broker.execute_phase(args[0], args[1], source=args[2], stdin=args[3], artifact=args[4])
            self.assertEqual(run['status'], 'invalid_input')
            self.assertFalse(run['executed'])
        self.broker.command.assert_not_called()

    def test_pre_cancelled_phase_does_not_create_container(self):
        self.ready_broker()
        event = threading.Event()
        event.set()
        run = self.broker.execute_phase('python', 'run', source='print(1)', cancel_event=event)
        self.assertEqual(run['status'], 'cancelled')
        self.assertFalse(run['executed'])
        self.broker.command.assert_not_called()

    def test_successful_native_compile_returns_bounded_artifact_only_after_verified_exit(self):
        self.ready_broker()
        with patch.object(runtime.subprocess, 'Popen', return_value=FakeProcess(stdout=envelope(b'compiled artifact'))):
            result = self.broker.execute_phase('c', 'compile', source='int main(void){return 0;}')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['artifact'], b'compiled artifact')
        self.assertTrue(result['executed'] and result['cleaned'])
        self.assertEqual(result['phase'], 'compile')
        self.assertEqual(result['language'], 'c')

    def test_compile_stdout_cannot_forge_success_and_artifact_is_never_copied_on_failure(self):
        self.ready_broker(exit_code=1)
        with patch.object(runtime.subprocess, 'Popen', return_value=FakeProcess(stdout=b'{"compiled":true}', code=1)):
            result = self.broker.execute_phase('cpp', 'compile', source='broken')
        self.assertEqual(result['status'], 'service_error')
        self.assertNotIn('artifact', result)

    def test_nonzero_signal_resource_and_supervisor_status_are_from_docker_state(self):
        for exit_code, expected, reason in [(139, 'exception', None), (134, 'exception', None),
                (152, 'resource_limit', 'memory'), (153, 'resource_limit', 'pids'),
                (124, 'timeout', None), (125, 'service_error', None)]:
            self.ready_broker(exit_code)
            with patch.object(runtime.subprocess, 'Popen', return_value=FakeProcess(code=exit_code)):
                result = self.broker.execute_phase('python', 'run', source='pass')
            self.assertEqual(result['status'], expected)
            self.assertEqual(result.get('resource_reason'), reason)

    def test_cleanup_failure_overrides_success_and_suppresses_artifact(self):
        self.ready_broker()
        self.broker.cleanup.return_value = False
        with patch.object(runtime.subprocess, 'Popen', return_value=FakeProcess(stdout=envelope())):
            result = self.broker.execute_phase('c', 'compile', source='int main(){}')
        self.assertEqual(result['status'], 'service_error')
        self.assertFalse(result['cleaned'])
        self.assertNotIn('artifact', result)

    def test_cancellation_during_run_kills_and_cleans_exact_container(self):
        self.ready_broker(exit_code=137)
        event = threading.Event()
        process = FakeProcess(code=137, cancel=event)
        with patch.object(runtime.subprocess, 'Popen', return_value=process):
            result = self.broker.execute_phase('python', 'run', source='while True: pass', cancel_event=event)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(result['cleaned'])
        self.assertTrue(process.killed)
        self.assertTrue(any(call.args[0][0] == 'kill' for call in self.broker.command.call_args_list))
        self.broker.cleanup.assert_called_once_with(result['container_name'])

    def test_combined_output_and_invalid_utf8_remain_bounded(self):
        for raw in [b'x'*18000, b'\xff'*6000]:
            self.ready_broker()
            with patch.object(runtime.subprocess, 'Popen', return_value=FakeProcess(stdout=raw)):
                result = self.broker.execute_phase('python', 'run', source='pass')
            self.assertEqual(result['status'], 'output_limit')
            self.assertLessEqual(len((result['stdout'] + result['stderr']).encode('utf8')), 16384)

    def test_binary_input_is_encoded_transport_only(self):
        self.ready_broker()
        with patch.object(runtime.subprocess, 'Popen', return_value=FakeProcess(stdout=b'3\n')):
            result = self.broker.execute_phase('cpp', 'run', stdin='a\x00b', artifact=b'ELF-test')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['stdin_sha256'], hashlib.sha256(b'a\0b').hexdigest())

    def test_cancelled_native_compile_never_exposes_envelope_or_artifact(self):
        self.ready_broker(exit_code=137)
        event=threading.Event()
        with patch.object(runtime.subprocess,'Popen',return_value=FakeProcess(stdout=envelope(b'private-artifact'),code=137,cancel=event)):
            result=self.broker.execute_phase('c','compile',source='int main(){}',cancel_event=event)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['stdout'],'')
        self.assertNotIn('artifact',result)

    def test_envelope_parser_rejects_mismatch_missing_oversized_and_forged_control(self):
        self.assertEqual(runtime.compile_envelope(envelope(), 0)['artifact'], b'ELF-test')
        for data in [envelope(code=1), envelope(content=b''), envelope(b'x'*1048577),
                     b'not JSON', b'{"compiled":true}']:
            with self.assertRaises(runtime.SandboxUnavailable):
                runtime.compile_envelope(data, 0)


class TrustedSupervisorContract(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parents[1] / 'native_image' / 'supervisor.py'
        self.assertTrue(path.exists(), 'trusted supervisor missing')
        spec = importlib.util.spec_from_file_location('round2_trusted_supervisor', path)
        self.supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.supervisor)

    def test_user_positive_exit_cannot_spoof_reserved_statuses(self):
        for code in [1, 124, 125, 134, 139, 152, 153, 255]:
            self.assertEqual(self.supervisor.child_exit_status(code, {}, {}), 1)
        self.assertEqual(self.supervisor.child_exit_status(0, {}, {}), 0)
        for signal, expected in [(-11, 139), (-6, 134), (-24, 124), (-9, 137)]:
            self.assertEqual(self.supervisor.child_exit_status(signal, {}, {}), expected)

    def test_cgroup_events_override_child_success(self):
        self.assertEqual(self.supervisor.child_exit_status(0, {'oom_kill': 1}, {}), 152)
        self.assertEqual(self.supervisor.child_exit_status(0, {}, {'max': 1}), 153)

    def test_fixed_native_compiler_argv_has_no_shell_or_user_flags(self):
        self.assertEqual(self.supervisor.compile_argv('c'), ['gcc', '-std=c17', '-O0', '-pipe', '/tmp/main.c', '-o', '/tmp/program', '-lm'])
        self.assertEqual(self.supervisor.compile_argv('cpp'), ['g++', '-std=c++17', '-O0', '-pipe', '/tmp/main.cpp', '-o', '/tmp/program'])
        with self.assertRaises(ValueError):
            self.supervisor.compile_argv('user; command')

    def test_compiler_cpu_budget_uses_cgroup_not_compiler_diagnostic(self):
        child=FakeProcess()
        child.pid=123
        child.poll=lambda: None if not child.killed else -9
        def kill_group(*args): child.killed=True
        with patch.object(self.supervisor,'read_events',return_value={'usage_usec':10000001}), \
             patch.object(self.supervisor.os,'killpg',side_effect=kill_group,create=True), \
             patch.object(self.supervisor.signal,'SIGKILL',9,create=True):
            outputs,limited,cpu_limited,cpu_used=self.supervisor.bounded_compiler_output(child,0,10)
        self.assertTrue(child.killed and cpu_limited)
        self.assertFalse(limited)
        self.assertGreaterEqual(cpu_used,10)


if __name__ == '__main__':
    unittest.main()
