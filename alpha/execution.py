"""Alpha stdin/stdout adapter; every compile and run happens in frozen V2 Docker.

The host only validates sizes and encodes transport. It never parses, compiles,
imports, or executes submitted programs. The additional Alpha acceptance receipt
is bound to this adapter, the frozen image, and its policy. Runtime.refresh also
validates the original receipt against the live Docker Engine fingerprint.
"""
import base64
import hashlib
import json
from pathlib import Path

from web_lab.sandbox_runtime import DockerRuntime, POLICY

SOURCE_BYTES = 200000
STDIN_BYTES = 64000
UNSUPPORTED = '尚无独立验收的 C/C++ 编译与运行环境；本次未执行'


def adapter_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _wrapper(source, stdin=None):
    encoded = base64.b64encode(source.encode('utf8')).decode('ascii')
    prefix = 'import base64, os, sys\n_source = base64.b64decode(' + repr(encoded) + ')\n'
    if stdin is None:
        # Submitted source is compiled but never evaluated in this phase.
        return prefix + "compile(_source, '/tmp/main.py', 'exec')\nraise SystemExit(0)\n"
    data = base64.b64encode(stdin.encode('utf8')).decode('ascii')
    return prefix + (
        "from pathlib import Path\nimport runpy\n"
        "Path('/tmp/main.py').write_bytes(_source)\n"
        "Path('/tmp/program.stdin').write_bytes(base64.b64decode(" + repr(data) + "))\n"
        "_input = open('/tmp/program.stdin', 'rb')\n"
        "os.dup2(_input.fileno(), 0)\n_input.close()\n"
        "sys.stdin = open(0, 'r', encoding='utf8')\n"
        "sys.argv = ['/tmp/main.py']\n"
        "runpy.run_path('/tmp/main.py', run_name='__main__')\n"
        "raise SystemExit(0)\n"
    )


class Execution:
    def __init__(self, root, *, acceptance=False):
        self.root = Path(root)
        self.runtime = DockerRuntime(self.root)
        # Only used by the explicit local maintenance CLI, never a request field.
        # This bypasses Alpha receipt checking, never V2 isolation acceptance.
        self.acceptance = acceptance

    def status(self):
        state = self.runtime.refresh()
        ready = bool(state.get('ready'))
        reason = state.get('reason', 'Docker 未就绪')
        if ready and not self.acceptance:
            try:
                receipt = json.loads((self.root / 'sandbox' / 'alpha_acceptance.json').read_text(encoding='utf8'))
                ready = (receipt.get('accepted') is True
                         and receipt.get('adapter_sha256') == adapter_hash()
                         and receipt.get('image_id') == state.get('image_id')
                         and receipt.get('policy_sha256') == state.get('policy_sha256'))
            except (OSError, ValueError, TypeError, AttributeError):
                ready = False
            if not ready:
                reason = 'Alpha 标准输入/输出适配器未通过当前版本的真实容器验收'
        languages = {
            'python': {'supported': True, 'ready': ready, 'reason': reason},
            'c': {'supported': False, 'ready': False, 'reason': UNSUPPORTED},
            'cpp': {'supported': False, 'ready': False, 'reason': UNSUPPORTED},
        }
        return {**state, 'ready': ready, 'reason': reason, 'languages': languages,
                'isolation_verified': bool(ready and state.get('isolation_verified') and not self.acceptance),
                'limits': {**POLICY, 'source_bytes': SOURCE_BYTES, 'stdin_bytes': STDIN_BYTES,
                           'phases': 2}, 'adapter_sha256': adapter_hash()}

    @staticmethod
    def _response(status, error=None):
        return {'status': status, 'executed': False, 'stdout': '', 'stderr': '',
                'error': error, 'compile_status': 'not_executed', 'cleaned': True,
                'elapsed_seconds': 0, 'limits': {**POLICY, 'source_bytes': SOURCE_BYTES,
                'stdin_bytes': STDIN_BYTES, 'phases': 2}}

    def run(self, language, source, stdin):
        if language != 'python':
            return self._response('unsupported', UNSUPPORTED if language in ('c', 'cpp') else '不支持的语言；本次未执行')
        if not isinstance(source, str) or not source.strip() or not isinstance(stdin, str):
            return self._response('invalid_input', '源码须为非空文本，标准输入须为文本（允许为空）')
        try:
            if len(source.encode('utf8')) > SOURCE_BYTES or len(stdin.encode('utf8')) > STDIN_BYTES:
                return self._response('invalid_input', '源码或标准输入超过字节上限')
            compiled_source, run_source = _wrapper(source), _wrapper(source, stdin)
        except UnicodeError:
            return self._response('invalid_input', '源码或输入不是有效 UTF-8 文本')
        if max(len(compiled_source.encode('utf8')), len(run_source.encode('utf8'))) > 400000:
            return self._response('invalid_input', '编码后源码超过冻结运行时上限')
        state = self.status()
        if not state['ready']:
            return self._response('unavailable', state['reason'])
        base = self._response('service_error', '执行服务未能确认结果')
        compile_result = None
        try:
            compile_result = self.runtime.execute(compiled_source, 0)
            trustworthy = compile_result.get('executed') and compile_result.get('cleaned')
            if compile_result.get('status') != 'completed' or not trustworthy:
                compile_status = 'failed' if trustworthy and compile_result.get('status') == 'exception' else 'not_completed'
                status = 'compile_error' if compile_status == 'failed' else compile_result.get('status', 'service_error')
                if not trustworthy and status != 'unavailable':
                    status = 'service_error'
                return {**base, **compile_result, 'status': status, 'executed': False,
                        'compile_status': compile_status, 'compile_result': compile_result}
            result = self.runtime.execute(run_source, 0)
            if result.get('status') == 'completed' and not (result.get('executed') and result.get('cleaned')):
                result = {**result, 'status': 'service_error', 'error': '执行或容器清理未确认，结果不可作为成功证据'}
            return {**base, **result, 'compile_status': 'passed', 'compile_result': compile_result}
        except Exception:
            # No host execution fallback, and no transport exception details leaked.
            uncertain = {**base, 'cleaned': False, 'execution_unknown': True}
            if compile_result is not None:
                uncertain.update(compile_result=compile_result, compile_status=(
                    'passed' if compile_result.get('status') == 'completed' and
                    compile_result.get('executed') and compile_result.get('cleaned') else 'not_completed'))
            return uncertain
