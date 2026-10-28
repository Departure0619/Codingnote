"""Shared compile/run broker for Python, C17 and C++17.

Frozen V2 owns daemon selection, preflight, create baseline, recovery and positive
cleanup verification. This broker extends the payload and native compile profile;
every language follows the same inspected container and bounded transport path.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid
from web_lab.sandbox_runtime import (DockerRuntime, SandboxUnavailable, POLICY,
    PROXIES, IMAGE_ID_RE, bounded_text, policy_hash as frozen_policy_hash)

from .languages import LANGUAGE_CONFIG

IMAGE_DIR = Path(__file__).with_name('native_image')
RUN_LIMITS = {**POLICY, 'cpu_seconds': 2, 'policy_version': 2}
COMPILE_LIMITS = {**RUN_LIMITS, 'memory': 536870912, 'tmp_bytes': 33554432,
                  'cpu_seconds': 10, 'cpu_budget_seconds': 9, 'wall_seconds': 20, 'broker_seconds': 25}
ARTIFACT_BYTES = 1048576
WIRE_BYTES = 2000000


def limits_for(language, phase):
    if language not in LANGUAGE_CONFIG or phase not in ('compile', 'run'):
        raise ValueError('invalid phase')
    return dict(COMPILE_LIMITS if LANGUAGE_CONFIG[language]['compile_profile'] == 'native' and phase == 'compile' else RUN_LIMITS)


def policy_hash():
    value = hashlib.sha256(frozen_policy_hash().encode())
    for file in [Path(__file__), Path(__file__).with_name('runner.py'),
                 Path(__file__).with_name('languages.py'), IMAGE_DIR / 'languages.json', IMAGE_DIR / 'managed.py', IMAGE_DIR / 'NODE_LICENSE',
                 IMAGE_DIR / 'Dockerfile', IMAGE_DIR / '.dockerignore', IMAGE_DIR / 'supervisor.py']:
        value.update(file.name.encode()); value.update(file.read_bytes())
    return value.hexdigest()


def compile_envelope(raw, exit_code):
    """Decode only the trusted compiler supervisor envelope, never run stdout."""
    try:
        if len(raw) > WIRE_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if data.get('protocol') != 'alpha-compile-1' or type(data.get('exit_code')) is not int or data['exit_code'] != exit_code:
            raise ValueError()
        for field in ('stdout', 'stderr', 'artifact'):
            if not isinstance(data.get(field), str):
                raise ValueError()
        if len((data['stdout'] + data['stderr']).encode('utf8')) > RUN_LIMITS['output_bytes']:
            raise ValueError()
        if len(data['artifact']) > ((ARTIFACT_BYTES + 2) // 3) * 4:
            raise ValueError()
        artifact = base64.b64decode(data['artifact'], validate=True)
        if len(artifact) > ARTIFACT_BYTES or bool(artifact) != (exit_code == 0):
            raise ValueError()
        return {'stdout': data['stdout'], 'stderr': data['stderr'], 'artifact': artifact,
                'cpu_seconds_used': data.get('cpu_seconds_used'), 'limit_reason': data.get('limit_reason')}
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise SandboxUnavailable('编译阶段传输不完整或协议不匹配') from None


class PhaseRuntime(DockerRuntime):
    def __init__(self, root, *, acceptance=False):
        # Do not call base constructor: it refreshes the old receipt before the
        # new folder/scope exists. Constructors/checks are strictly read-only.
        self.root = Path(root)
        self.folder = self.root / 'sandbox_round2'
        self.scope = hashlib.sha256((str(self.root.resolve()) + ':round2').encode()).hexdigest()[:20]
        self.acceptance, self.blocked_reason = acceptance, None
        self.context, self.image_id, self.fingerprint = None, None, None
        self.lock = threading.RLock()
        self._status = {'ready': False, 'reason': '执行环境未验收', 'isolation_verified': False}
        self.refresh()

    def refresh(self):
        with self.lock:
            try:
                if self.blocked_reason:
                    raise SandboxUnavailable(self.blocked_reason)
                self.fingerprint = self.preflight()
                manifest = json.loads((self.folder / 'runtime.json').read_text(encoding='utf8'))
                policy = policy_hash()
                if manifest.get('policy_sha256') != policy or not IMAGE_ID_RE.fullmatch(manifest.get('image_id', '')):
                    raise SandboxUnavailable('镜像或执行策略变化，需要重新构建验收')
                self.image_id = manifest['image_id']
                image = json.loads(self.command(['image', 'inspect', self.image_id]))[0]
                if image['Id'] != self.image_id or image.get('Config', {}).get('Volumes'):
                    raise SandboxUnavailable('镜像身份不匹配或声明了卷')
                if not self.acceptance:
                    receipt = json.loads((self.folder / 'acceptance.json').read_text(encoding='utf8'))
                    if (receipt.get('accepted') is not True or receipt.get('fingerprint') != self.fingerprint
                        or receipt.get('image_id') != self.image_id or receipt.get('policy_sha256') != policy):
                        raise SandboxUnavailable('缺少当前镜像与 Engine 的真实验收记录')
                    if receipt.get('suite') != 'alpha-five-language-v1' or set(receipt.get('languages', [])) != set(LANGUAGE_CONFIG):
                        raise SandboxUnavailable('验收未覆盖当前全部语言，请运行五语言验收。')
                    raw_evidence = Path(receipt['evidence']).read_bytes()
                    if hashlib.sha256(raw_evidence).hexdigest() != receipt.get('evidence_sha256'):
                        raise SandboxUnavailable('验收证据已变化，需要重新验收。')
                    evidence = json.loads(raw_evidence)
                    observed = evidence.get('runtime', {})
                    cases = evidence.get('cases', [])
                    if (evidence.get('suite') != 'alpha-five-language-v1' or evidence.get('accepted') is not True
                        or set(evidence.get('verified_languages', [])) != set(LANGUAGE_CONFIG)
                        or any(observed.get(key) != receipt.get(key) for key in ('image_id','policy_sha256','fingerprint'))
                        or not isinstance(cases, list) or not cases or any(case.get('passed') is not True for case in cases)
                        or evidence.get('legacy', {}).get('accepted') is not True):
                        raise SandboxUnavailable('验收证据范围、环境或结果不匹配。')
                self._status = {'ready': True, 'reason': '本机 Docker 验收通过' if not self.acceptance else '维护者验收模式',
                    'isolation_verified': not self.acceptance, 'image_id': self.image_id, 'policy_sha256': policy,
                    'fingerprint': self.fingerprint}
            except (SandboxUnavailable, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                self._status = {'ready': False, 'reason': self.blocked_reason or (
                    str(exc) if isinstance(exc, SandboxUnavailable) else '执行镜像尚未完成本机验收，请先运行环境检查。'),
                    'isolation_verified': False}
            return dict(self._status)

    def create_args(self, name, image_id, *, language='python', phase='run'):
        limits = limits_for(language, phase)
        args = super().create_args(name, image_id)
        if limits == COMPILE_LIMITS:
            replacements = {'--memory=128m': '--memory=512m', '--memory-swap=128m': '--memory-swap=512m',
                'cpu=2:2': 'cpu=10:10',
                '/tmp:rw,noexec,nosuid,nodev,size=16777216,nr_inodes=256,mode=1777':
                '/tmp:rw,noexec,nosuid,nodev,size=33554432,nr_inodes=256,mode=1777'}
            args = [replacements.get(arg, arg) for arg in args]
        return args

    def inspect_policy(self, container, *, language='python', phase='run'):
        limits = limits_for(language, phase)
        item = json.loads(self.command(['inspect', container]))[0]
        host, config = item['HostConfig'], item['Config']
        checks = [item['Image'] == self.image_id, config['User'] == '65534:65534',
            config['Entrypoint'] == ['python', '-I', '-S', '-u', '/runner/supervisor.py'],
            not config.get('Cmd'), not config.get('Volumes'), host['NetworkMode'] == 'none',
            host['Privileged'] is False, host['ReadonlyRootfs'] is True,
            host['CapDrop'] == ['ALL'], not host.get('CapAdd'),
            'no-new-privileges:true' in host['SecurityOpt'],
            not any('unconfined' in s for s in host['SecurityOpt']),
            host['Memory'] == limits['memory'], host['MemorySwap'] == limits['memory'],
            host['NanoCpus'] == 500000000, host['PidsLimit'] == 32,
            host['IpcMode'] == 'none', not host.get('PidMode'), host['CgroupnsMode'] == 'private',
            not host.get('Binds'), not host.get('Devices'), not host.get('DeviceRequests'),
            not host.get('PortBindings'), host['LogConfig']['Type'] == 'none',
            host['RestartPolicy']['Name'] == 'no',
            set(host.get('Tmpfs', {})) == {'/tmp'},
            all(m['Type'] == 'tmpfs' and m['Destination'] == '/tmp' for m in item.get('Mounts', []))]
        options = set(host.get('Tmpfs', {}).get('/tmp', '').split(','))
        checks.append({'noexec', 'nosuid', 'nodev', 'size=' + str(limits['tmp_bytes']), 'nr_inodes=256'} <= options)
        ulimits = {u['Name']: (u['Soft'], u['Hard']) for u in host.get('Ulimits', [])}
        checks.append(all(ulimits.get(k) == (v, v) for k, v in {
            'cpu': limits['cpu_seconds'], 'fsize': ARTIFACT_BYTES, 'nofile': 64, 'core': 0}.items()))
        image = json.loads(self.command(['image', 'inspect', self.image_id]))[0]
        allowed = {e.split('=', 1)[0] for e in image['Config'].get('Env', [])} | set(PROXIES)
        checks.append(all(e.split('=', 1)[0] in allowed for e in config.get('Env', [])))
        if not all(checks):
            raise SandboxUnavailable('实际容器权限、资源或挂载未通过检查')
        return item

    def execute_phase(self, language, phase, *, source='', stdin='', artifact=None, cancel_event=None):
        empty = {'executed': False, 'stdout': '', 'stderr': '', 'cleaned': True, 'elapsed_seconds': 0,
                 'language': language, 'phase': phase}
        try:
            limits = limits_for(language, phase)
            source_raw, input_raw = source.encode('utf8'), stdin.encode('utf8')
            if len(source_raw) > 200000 or len(input_raw) > 64000:
                raise ValueError()
            if phase == 'compile' or not LANGUAGE_CONFIG[language]['artifact']:
                if not source.strip() or artifact is not None:
                    raise ValueError()
            elif not isinstance(artifact, bytes) or not artifact or len(artifact) > ARTIFACT_BYTES:
                raise ValueError()
        except (ValueError, TypeError, AttributeError, UnicodeError):
            return {**empty, 'status': 'invalid_input', 'error': '执行参数无效或超过上限。'}
        if cancel_event is not None and cancel_event.is_set():
            return {**empty, 'status': 'cancelled', 'error': '已停止执行。'}
        with self.lock:
            if not self.refresh()['ready']:
                return {**empty, 'status': 'unavailable', 'error': self._status['reason']}
            native_compile = LANGUAGE_CONFIG[language]['compile_profile'] == 'native' and phase == 'compile'
            wire_limit = WIRE_BYTES if native_compile else limits['output_bytes']
            name = 'aic-task-' + uuid.uuid4().hex
            started = time.perf_counter()
            process, exit_code, error = None, None, None
            timed_out = cancelled = executed = oom = False
            chunks = {'stdout': bytearray(), 'stderr': bytearray()}
            total, threads = 0, []
            output_lock, overflow = threading.Lock(), threading.Event()
            try:
                self.command(self.create_args(name, self.image_id, language=language, phase=phase))
                self.inspect_policy(name, language=language, phase=phase)
                env = dict(os.environ)
                for key in ['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH']:
                    env.pop(key, None)
                process = subprocess.Popen(['docker', '--context', self.context, 'start', '--attach', '--interactive', name],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                def read(stream, key):
                    nonlocal total
                    try:
                        while True:
                            part = stream.read1(4096)
                            if not part: break
                            with output_lock:
                                available = max(0, wire_limit - total)
                                chunks[key].extend(part[:available]); total += min(len(part), available)
                                if len(part) > available: overflow.set()
                    except (OSError, ValueError):
                        pass
                for key in chunks:
                    thread = threading.Thread(target=read, args=(getattr(process, key), key), daemon=True)
                    thread.start(); threads.append(thread)
                payload = json.dumps({'language': language, 'phase': phase,
                    'source': base64.b64encode(source_raw).decode(), 'stdin': base64.b64encode(input_raw).decode(),
                    'artifact': base64.b64encode(artifact or b'').decode()}).encode()
                def write():
                    try: process.stdin.write(payload); process.stdin.close()
                    except (OSError, ValueError): pass
                writer = threading.Thread(target=write, daemon=True); writer.start(); threads.append(writer)
                deadline = time.perf_counter() + limits['broker_seconds']
                while process.poll() is None:
                    cancelled = bool(cancel_event is not None and cancel_event.is_set())
                    timed_out = time.perf_counter() >= deadline
                    if overflow.is_set() or cancelled or timed_out: break
                    time.sleep(.02)
                if cancelled or timed_out or overflow.is_set():
                    try: self.command(['kill', name], timeout=5)
                    except SandboxUnavailable: pass
                    process.kill()
                process.wait(timeout=3)
                for thread in threads: thread.join(timeout=1)
                state = json.loads(self.command(['inspect', name]))[0]['State']
                executed = bool(state.get('StartedAt') and not state['StartedAt'].startswith('0001'))
                if state.get('Running') or not executed or state.get('Error'):
                    raise SandboxUnavailable('Docker 未返回可信的任务结束状态')
                exit_code, oom = state['ExitCode'], state['OOMKilled']
                if process.returncode not in (0, exit_code) and not (cancelled or timed_out or overflow.is_set()):
                    raise SandboxUnavailable('执行连接失败，输出可能不完整')
            except (SandboxUnavailable, OSError, ValueError, TypeError, KeyError, IndexError, subprocess.TimeoutExpired):
                error, exit_code = '执行服务失败，结果不可作为成功证据。', None
            finally:
                cleaned = self.cleanup(name)
                if process is not None:
                    if process.poll() is None: process.kill()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: pass
                    for thread in threads: thread.join(timeout=1)
                    for stream in [process.stdin, process.stdout, process.stderr]:
                        try: stream.close()
                        except OSError: pass
            output_limited = overflow.is_set()
            compiled_artifact = None
            compiler_cpu_used, compiler_limit_reason = None, None
            native_wire = chunks['stdout']
            native_stderr = chunks['stderr']
            if native_compile:
                # Wire envelopes/artifacts are never learner output, including
                # cancelled, truncated, OOM or otherwise unconfirmed transport.
                chunks = {'stdout': bytearray(), 'stderr': bytearray()}
            if native_compile and (exit_code in (0, 1, 134, 139, 152, 153, 154) or (exit_code == 124 and native_wire)) and not (output_limited or cancelled or timed_out):
                try:
                    if native_stderr: raise SandboxUnavailable('unexpected compiler transport stderr')
                    decoded = compile_envelope(native_wire, exit_code)
                    chunks = {key: bytearray(decoded[key].encode('utf8')) for key in ('stdout', 'stderr')}
                    compiled_artifact = decoded['artifact']
                    compiler_cpu_used, compiler_limit_reason = decoded['cpu_seconds_used'], decoded['limit_reason']
                except SandboxUnavailable:
                    if exit_code != 124:
                        error, exit_code = '编译结果传输未确认，不能作为成功证据。', None
                    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
            output_limited |= (not native_compile and sum(len(bytes(raw).decode('utf8', errors='replace').encode('utf8'))
                               for raw in chunks.values()) > limits['output_bytes']) or exit_code == 154
            status = ('service_error' if not cleaned or exit_code is None or exit_code == 125 else
                      'cancelled' if cancelled else 'output_limit' if output_limited else
                      'timeout' if timed_out or exit_code == 124 else
                      'resource_limit' if oom or exit_code in (152, 153) else
                      'completed' if exit_code == 0 else 'exception')
            reason = 'memory' if oom or exit_code == 152 else 'pids' if exit_code == 153 else compiler_limit_reason
            if status == 'service_error': error = error or '执行或容器清理未确认。'
            elif status != 'completed': error = {'exception': '程序异常退出。', 'resource_limit': '程序触发内存或进程数量限制。',
                'timeout': '该阶段超过执行时间限制。', 'output_limit': '输出超过 16 KiB 上限，已停止。',
                'cancelled': '运行已停止，容器已清理。'}[status]
            stdout, stderr = bounded_text(chunks['stdout'], chunks['stderr'])
            result = {**empty, 'status': status, 'executed': executed, 'cleaned': cleaned,
                'stdout': stdout, 'stderr': stderr, 'error': error, 'exit_code': exit_code,
                'elapsed_seconds': round(time.perf_counter() - started, 4), 'output_truncated': output_limited,
                'resource_reason': reason, 'oom_killed': oom, 'termination_signal': {134:'SIGABRT',139:'SIGSEGV',137:'SIGKILL'}.get(exit_code),
                'cpu_seconds_used': compiler_cpu_used,
                'container_name': name, 'image_id': self.image_id, 'policy_sha256': policy_hash(),
                'fingerprint': self.fingerprint, 'limits': limits,
                'source_sha256': hashlib.sha256(source_raw).hexdigest(), 'stdin_sha256': hashlib.sha256(input_raw).hexdigest()}
            if native_compile and status == 'completed':
                result['artifact'] = compiled_artifact
                result['artifact_sha256'] = hashlib.sha256(compiled_artifact).hexdigest()
            if artifact:
                result['artifact_sha256'] = hashlib.sha256(artifact).hexdigest()
            return result
