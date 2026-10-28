"""Trusted PID 1; an independent wall deadline survives broker interruption."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

child = None


def stop(signum, frame):
    if child is not None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os._exit(124)


signal.signal(signal.SIGALRM, stop)
signal.signal(signal.SIGTERM, stop)
# Includes bounded stdin reading, so a lost broker cannot leave PID 1 waiting.
signal.alarm(3)
try:
    payload = json.loads(sys.stdin.buffer.read(410000))
    if type(payload['input']) is not int or not 0 <= payload['input'] <= 8:
        raise ValueError('invalid input')
    source = payload['source']
    if not isinstance(source, str) or len(source.encode('utf8')) > 400000:
        raise ValueError('invalid source')
    Path('/tmp/student.py').write_text(source, encoding='utf8')
    child = subprocess.Popen([sys.executable, '-I', '-S', '-u', '/runner/invoke.py'],
                             stdin=subprocess.PIPE, start_new_session=True)
    child.communicate(json.dumps(payload['input']).encode('ascii'))
    code = child.returncode
    # These reserved statuses are interpreted by the host using actual exit state.
    sys.exit(124 if code == -signal.SIGXCPU else 1 if code else 0)
except Exception as exc:
    print(type(exc).__name__ + ': task supervisor failed', file=sys.stderr)
    sys.exit(125)
finally:
    if child is not None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
