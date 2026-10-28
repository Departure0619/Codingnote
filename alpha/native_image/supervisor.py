"""Trusted container PID 1; never imported to execute learner code on the host.

Exit statuses are a control protocol from this supervisor, not learner stdout:
0 completed, 1 ordinary child error, 124 deadline/CPU, 125 supervisor error,
134 SIGABRT, 137 SIGKILL without observed OOM, 139 SIGSEGV, 152 observed cgroup
memory OOM, 153 observed cgroup PID exhaustion. Positive child exit codes are
always normalized to 1 so a program cannot forge these reserved statuses.
"""
import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from managed import java_layout, decode_classes, encode_classes
LANGUAGES = json.loads(Path(__file__).with_name('languages.json').read_text(encoding='utf8'))

SOURCE_BYTES = 200000
STDIN_BYTES = 64000
ARTIFACT_BYTES = 1048576
PAYLOAD_BYTES = 2000000
OUTPUT_BYTES = 16384


def bounded_compiler_output(child, cpu_start, cpu_limit):
    chunks = {'stdout': bytearray(), 'stderr': bytearray()}
    lock, limited = threading.Lock(), threading.Event()
    total = 0
    def read(key):
        nonlocal total
        while True:
            part = getattr(child, key).read1(4096)
            if not part: break
            with lock:
                available = max(0, OUTPUT_BYTES - total)
                chunks[key].extend(part[:available]); total += min(available, len(part))
                if len(part) > available:
                    limited.set()
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
    threads = [threading.Thread(target=read, args=(key,), daemon=True) for key in chunks]
    for thread in threads: thread.start()
    child.stdin.close()
    cpu_limited = False
    while child.poll() is None:
        if read_events('cpu.stat')['usage_usec'] - cpu_start >= cpu_limit * 1000000:
            cpu_limited = True
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            break
        time.sleep(.02)
    child.wait()
    for thread in threads: thread.join()
    values, remaining = {}, OUTPUT_BYTES
    for key, raw in chunks.items():
        text = bytes(raw).decode('utf8', errors='replace').encode('utf8')
        if len(text) > remaining: limited.set()
        text = text[:remaining].decode('utf8', errors='ignore')
        remaining -= len(text.encode('utf8')); values[key] = text
    cpu_used = max(0, read_events('cpu.stat')['usage_usec'] - cpu_start) / 1000000
    return values, limited.is_set(), cpu_limited or cpu_used >= cpu_limit, cpu_used


def compile_argv(language):
    if language == 'c':
        return ['gcc', '-std=c17', '-O0', '-pipe', '/tmp/main.c', '-o', '/tmp/program', '-lm']
    if language == 'cpp':
        return ['g++', '-std=c++17', '-O0', '-pipe', '/tmp/main.cpp', '-o', '/tmp/program']
    raise ValueError('unsupported compiler language')


def child_exit_status(code, memory_delta, pids_delta):
    if memory_delta.get('oom_kill', 0) > 0 or memory_delta.get('oom', 0) > 0:
        return 152
    if pids_delta.get('max', 0) > 0:
        return 153
    if code is None:
        return 125
    if code < 0:
        if -code == 24:  # Linux SIGXCPU
            return 124
        # Restrict the trusted protocol to enumerated signal outcomes.
        return {6: 134, 9: 137, 11: 139}.get(-code, 1)
    return 0 if code == 0 else 1


def read_events(name):
    result = {}
    for line in Path('/sys/fs/cgroup/' + name).read_text(encoding='ascii').splitlines():
        key, value = line.split()
        result[key] = int(value)
    return result


def delta(after, before):
    return {key: max(0, value - before.get(key, 0)) for key, value in after.items()}


def decode_field(payload, key, limit):
    value = payload.get(key, '')
    if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
        raise ValueError('invalid encoded field')
    raw = base64.b64decode(value, validate=True)
    if len(raw) > limit:
        raise ValueError('field over limit')
    return raw


def sealed_program(binary):
    # Keep /tmp noexec. The artifact never touches a host file or an exec mount.
    import fcntl
    flags = os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    fd = os.memfd_create('alpha-program', flags=flags)
    try:
        position = 0
        while position < len(binary):
            count = os.write(fd, binary[position:])
            if count <= 0:
                raise OSError('short artifact write')
            position += count
        os.fchmod(fd, 0o500)
        if all(hasattr(fcntl, key) for key in ['F_ADD_SEALS', 'F_GET_SEALS', 'F_SEAL_WRITE',
                                              'F_SEAL_GROW', 'F_SEAL_SHRINK', 'F_SEAL_SEAL']):
            seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
            if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals != seals:
                raise OSError('artifact seals not confirmed')
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except Exception:
        os.close(fd)
        raise


def main():
    child = None
    artifact_fd = None

    def terminate_group():
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def deadline(signum, frame):
        terminate_group()
        os._exit(124)

    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGTERM, deadline)
    try:
        # The actual cgroup profile, not untrusted payload, selects the deadline.
        memory_limit = int(Path('/sys/fs/cgroup/memory.max').read_text(encoding='ascii'))
        if memory_limit not in (134217728, 536870912):
            raise ValueError('unexpected memory profile')
        native_compile_profile = memory_limit == 536870912
        signal.alarm(20 if native_compile_profile else 3)
        memory_before, pids_before = read_events('memory.events'), read_events('pids.events')
        raw = sys.stdin.buffer.read(PAYLOAD_BYTES + 1)
        if len(raw) > PAYLOAD_BYTES:
            raise ValueError('transport over limit')
        payload = json.loads(raw)
        language, phase = payload['language'], payload['phase']
        if language not in LANGUAGES or phase not in ('compile', 'run'):
            raise ValueError('invalid phase')
        if native_compile_profile != (LANGUAGES[language]['compile_profile'] == 'native' and phase == 'compile'):
            raise ValueError('phase/profile mismatch')
        source = decode_field(payload, 'source', SOURCE_BYTES)
        stdin = decode_field(payload, 'stdin', STDIN_BYTES)
        artifact = decode_field(payload, 'artifact', ARTIFACT_BYTES)
        pass_fds = ()
        config = LANGUAGES[language]
        if not config['artifact']:
            if artifact:
                raise ValueError('source-only language artifact is not accepted')
            Path('/tmp', config['filename']).write_bytes(source)
            argv = config['compile_command' if phase == 'compile' else 'run_command']
        elif phase == 'compile':
            if artifact:
                raise ValueError('compile artifact is not accepted')
            if language == 'java':
                try:
                    filename, entry = java_layout(source.decode('utf8'))
                except ValueError as exc:
                    print(json.dumps({'protocol':'alpha-compile-1','exit_code':1,
                        'stdout':'','stderr':str(exc),'artifact':'',
                        'cpu_seconds_used':0,'limit_reason':None}), flush=True)
                    return 1
                Path('/tmp/classes').mkdir()
                Path('/tmp', filename).write_bytes(source)
                argv = config['compile_command'] + ['/tmp/' + filename]
            else:
                Path('/tmp', config['filename']).write_bytes(source)
                argv = config['compile_command']
        elif language == 'java':
            entry, classes = decode_classes(artifact)
            Path('/tmp/classes').mkdir()
            for filename, content in classes.items():
                Path('/tmp/classes', filename).write_bytes(content)
            argv = config['run_command'] + [entry]
        else:
            if not artifact:
                raise ValueError('missing compiled artifact')
            artifact_fd = sealed_program(artifact)
            argv = ['/proc/self/fd/' + str(artifact_fd)]
            pass_fds = (artifact_fd,)
        pipe_options = {'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE} if native_compile_profile else {}
        cpu_before = read_events('cpu.stat')['usage_usec']
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, start_new_session=True, pass_fds=pass_fds, **pipe_options)
        if native_compile_profile:
            # Reserve one CPU second before the unchanged kernel hard limit (10).
            # Linux may kill a compiler descendant slightly before reported usage
            # reaches 10, and GCC then masks its signal as an ordinary exit(1).
            # A stricter aggregate budget gives PID1 time to preserve real evidence.
            compiler_output, output_limited, cpu_limited, cpu_used = bounded_compiler_output(child, cpu_before, 9)
        else:
            child.communicate(stdin if phase == 'run' else b'')
        memory_delta = delta(read_events('memory.events'), memory_before)
        pids_delta = delta(read_events('pids.events'), pids_before)
        status = child_exit_status(child.returncode, memory_delta, pids_delta)
        if native_compile_profile:
            if cpu_limited: status = 124
            if output_limited: status = 154
            binary = b''
            if status == 0:
                if language == 'java':
                    try:
                        binary = encode_classes(Path('/tmp/classes'), entry)
                    except ValueError:
                        # Unsupported class layouts/sizes are compilation diagnostics,
                        # not infrastructure failures. Keep all artifact limits intact.
                        status = 1
                        diagnostic = 'Java 编译产物超出单文件支持范围：最多128个ASCII文件名的class文件，传输合计不超过1MiB。'
                        available = OUTPUT_BYTES - len(compiler_output['stdout'].encode('utf8'))
                        compiler_output['stderr'] = diagnostic.encode('utf8')[:available].decode('utf8', errors='ignore')
                else:
                    with Path('/tmp/program').open('rb') as program:
                        binary = program.read(ARTIFACT_BYTES + 1)
                if status == 0 and (not binary or len(binary) > ARTIFACT_BYTES):
                    raise ValueError('artifact size invalid')
            # Only this trusted supervisor writes the envelope. Compiler diagnostics
            # are captured as data, so pragmas/source cannot impersonate control.
            print(json.dumps({'protocol':'alpha-compile-1', 'exit_code':status,
                **compiler_output, 'artifact':base64.b64encode(binary).decode('ascii'),
                'cpu_seconds_used':cpu_used, 'limit_reason':'cpu' if cpu_limited else None}, ensure_ascii=True), flush=True)
        return status
    except Exception as exc:
        print(type(exc).__name__ + ': trusted phase supervisor failed', file=sys.stderr)
        return 125
    finally:
        terminate_group()
        if artifact_fd is not None:
            os.close(artifact_fd)


if __name__ == '__main__':
    sys.exit(main())
