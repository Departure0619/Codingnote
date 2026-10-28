"""Unified Alpha compile/run facade. Original round-one receipts stay reproducible.

Only PhaseRuntime can execute source or artifacts, always inside Docker. Artifact
bytes never enter SQLite, API responses, or host executable files.
"""
from .phase_runtime import PhaseRuntime, RUN_LIMITS, COMPILE_LIMITS

SOURCE_BYTES = 200000
STDIN_BYTES = 64000
from .languages import LANGUAGES, public_metadata


class Execution:
    supports_cancellation = True

    def __init__(self, root, *, acceptance=False):
        self.runtime = PhaseRuntime(root, acceptance=acceptance)

    def status(self):
        state = self.runtime.refresh()
        return {**state, 'generation': 3, 'languages': {
            language: {**public_metadata(language), 'supported': True, 'ready': bool(state.get('ready')),
                       'reason': state.get('reason', '执行环境未就绪')}
            for language in LANGUAGES},
            'limits': {'run': RUN_LIMITS, 'native_compile': COMPILE_LIMITS,
                       'source_bytes': SOURCE_BYTES, 'stdin_bytes': STDIN_BYTES}}

    @staticmethod
    def response(status, error=None):
        return {'status': status, 'executed': False, 'stdout': '', 'stderr': '',
                'error': error, 'compile_status': 'not_executed', 'cleaned': True,
                'elapsed_seconds': 0}

    def run(self, language, source, stdin, *, cancel_event=None):
        if language not in LANGUAGES:
            return self.response('unsupported', '不支持此语言；本次未执行。')
        if not isinstance(source, str) or not source.strip() or not isinstance(stdin, str):
            return self.response('invalid_input', '代码须为非空文本，标准输入须为文本（可以为空）。')
        try:
            if len(source.encode('utf8')) > SOURCE_BYTES or len(stdin.encode('utf8')) > STDIN_BYTES:
                return self.response('invalid_input', '代码或标准输入超过字节上限。')
        except UnicodeError:
            return self.response('invalid_input', '代码或输入不是有效 UTF-8 文本。')
        if cancel_event and cancel_event.is_set():
            return self.response('cancelled', '已停止执行。')
        state = self.status()
        if not state['ready']:
            return self.response('unavailable', state['reason'])
        compiled = None
        try:
            compiled = self.runtime.execute_phase(language, 'compile', source=source, cancel_event=cancel_event)
            artifact = compiled.pop('artifact', None)
            trustworthy = compiled.get('executed') and compiled.get('cleaned')
            if compiled.get('status') != 'completed' or not trustworthy:
                failed = trustworthy and compiled.get('status') == 'exception'
                status = 'compile_error' if failed else compiled.get('status', 'service_error')
                if compiled.get('status') == 'completed' or not compiled.get('cleaned'):
                    status = 'service_error'
                return {**self.response(status), **compiled, 'status': status, 'executed': False,
                        'compile_status': 'failed' if failed else 'not_completed', 'compile_result': compiled}
            if cancel_event and cancel_event.is_set():
                result = self.response('cancelled', '编译已完成，运行前已停止。')
            else:
                result = self.runtime.execute_phase(language, 'run', source=source, stdin=stdin,
                                                    artifact=artifact, cancel_event=cancel_event)
            result.pop('artifact', None)
            if not result.get('cleaned') or (result.get('status') == 'completed' and not result.get('executed')):
                result.update(status='service_error', error='执行或容器清理未确认，结果不可作为成功证据。')
            return {**self.response('service_error'), **result, 'compile_status': 'passed',
                    'compile_result': compiled, 'elapsed_seconds': round(
                        compiled.get('elapsed_seconds', 0) + result.get('elapsed_seconds', 0), 4),
                    'run_elapsed_seconds': result.get('elapsed_seconds', 0)}
        except Exception:
            result = self.response('service_error', '执行服务中断，无法确认结果；未回退到宿主机执行。')
            result.update(cleaned=False, execution_unknown=True)
            if compiled is not None:
                compiled.pop('artifact', None)
                result.update(compile_result=compiled, compile_status=(
                    'passed' if compiled.get('status') == 'completed' and compiled.get('executed')
                    and compiled.get('cleaned') else 'not_completed'))
            return result
