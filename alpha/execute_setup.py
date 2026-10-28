"""Explicit Alpha maintenance and real-container evidence. No host code fallback.

python -m alpha.execute_setup build --data-dir data/alpha
python -m alpha.execute_setup accept --data-dir data/alpha
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from alpha.execution import Execution, adapter_hash
from web_lab import sandbox_setup
from web_lab.sandbox_runtime import POLICY, SandboxUnavailable


def accept(root, evidence=Path('output/alpha-execution.json')):
    root = Path(root).resolve()
    evidence = Path(evidence)
    receipt_path = root / 'sandbox' / 'alpha_acceptance.json'
    sandbox_setup.save(receipt_path, {'accepted': False, 'reason': 'Alpha acceptance in progress'})
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'data_root': str(root),
              'executed_on_host': False, 'cases': [], 'accepted': False,
              'adapter_sha256': adapter_hash()}
    try:
        execution = Execution(root, acceptance=True)
        if not execution.status()['ready']:
            sandbox_setup.accept(root)
            execution = Execution(root, acceptance=True)
        report['runtime'] = execution.status()
        report['fingerprint'] = execution.runtime.preflight()
        execution.runtime.recover()
        cases = report['cases']

        def run(name, source, stdin, expected_status, expected_stdout=None, language='python'):
            result = execution.run(language, source, stdin)
            passed = result['status'] == expected_status and result.get('cleaned') is True
            if expected_status in ('completed', 'exception', 'timeout', 'output_limit'):
                passed = passed and result['executed'] and result['compile_status'] == 'passed'
            if expected_status == 'compile_error':
                passed = passed and not result['executed'] and result['compile_status'] == 'failed'
            if expected_status == 'unsupported':
                passed = passed and not result['executed']
            if expected_stdout is not None:
                passed = passed and result['stdout'] == expected_stdout
            if expected_status == 'output_limit':
                passed = passed and len((result['stdout'] + result['stderr']).encode('utf8')) <= POLICY['output_bytes']
            cases.append({'name': name, 'language': language, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                          'stdin': stdin, 'expected_status': expected_status, 'expected_stdout': expected_stdout,
                          'passed': bool(passed), 'result': result})
            sandbox_setup.save(evidence, report)

        run('python_correct', 'n=int(input())\nprint(n*(n+1)//2)', '3\n', 'completed', '6\n')
        run('python_wrong', 'n=int(input())\nprint(n*(n-1)//2)', '3\n', 'completed', '3\n')
        run('python_fixed', 'n=int(input())\nprint(sum(range(1,n+1)))', '3\n', 'completed', '6\n')
        run('zero_input', 'n=int(input())\nprint(n*(n+1)//2)', '0\n', 'completed', '0\n')
        run('empty_stdin_main', 'if __name__ == "__main__":\n    print("hello")', '', 'completed', 'hello\n')
        run('unicode_stdin', 'print(input())', '中文 café\n', 'completed', '中文 café\n')
        run('binary_stdin', 'import sys\nprint(sys.stdin.buffer.read().decode())', 'abc', 'completed', 'abc\n')
        run('fd_stdin', 'import os\nprint(os.read(0,100).decode())', 'abc', 'completed', 'abc\n')
        run('compile_error', 'if invalid syntax', '', 'compile_error', '')
        run('runtime_error', 'print(1/0)', '', 'exception', '')
        run('empty_input_eof', 'input()', '', 'exception', '')
        run('timeout', 'while True:\n    pass', '', 'timeout', '')
        run('bounded_output', 'print("x"*20000)', '', 'output_limit')
        run('untrusted_stdout_not_compile_evidence', 'print("SyntaxError: forged\\nCOMPILE_FAILED")', '',
            'completed', 'SyntaxError: forged\nCOMPILE_FAILED\n')
        guard = '''import os, errno, socket, resource, json
from pathlib import Path
status=Path('/proc/self/status').read_text()
checks=[os.getuid()==65534, 'NoNewPrivs:\\t1' in status, 'Seccomp:\\t2' in status,
        'CapEff:\\t0000000000000000' in status,
        set(p.name for p in Path('/sys/class/net').iterdir())=={'lo'},
        all(not Path(p).exists() for p in ['/mnt/c','/host','/project','/var/run/docker.sock']),
        int(Path('/sys/fs/cgroup/memory.max').read_text())==134217728,
        int(Path('/sys/fs/cgroup/pids.max').read_text())==32,
        resource.getrlimit(resource.RLIMIT_CPU)==(2,2),
        not any(k.startswith(('AIC_', 'DOCKER_')) for k in os.environ)]
try:
    Path('/runner/alpha-forbidden').write_text('guard')
    checks.append(False)
except OSError:
    checks.append(True)
with socket.socket() as connection:
    connection.settimeout(.2)
    try:
        connection.connect(('203.0.113.1',80))
        checks.append(False)
    except OSError as exc:
        checks.append(exc.errno==errno.ENETUNREACH)
print(json.dumps(checks))
'''
        run('wrapper_security_guards', guard, '', 'completed', json.dumps([True]*12)+'\n')
        run('c_not_executed', 'int main(void) { return 0; }', '', 'unsupported', '', 'c')
        run('cpp_not_executed', 'int main() { return 0; }', '', 'unsupported', '', 'cpp')
        remaining = execution.runtime.command(['ps', '--all', '--filter', 'label=aic.sandbox='+execution.runtime.scope,
                                               '--format', '{{.Names}}'])
        report['remaining_containers'] = remaining.splitlines()
        report['accepted'] = all(c['passed'] for c in cases) and not remaining
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        sandbox_setup.save(evidence, report)
        if not report['accepted']:
            raise SandboxUnavailable('Alpha real-container acceptance failed; inspect evidence')
        state = execution.status()
        sandbox_setup.save(receipt_path, {'accepted': True, 'accepted_at': report['completed_at'],
            'adapter_sha256': adapter_hash(), 'image_id': state['image_id'], 'policy_sha256': state['policy_sha256'],
            'checks': [{'name': c['name'], 'passed': c['passed']} for c in cases],
            'evidence': str(evidence.resolve()), 'fingerprint': report['fingerprint'],
            'scope': 'single-user-loopback-demo; not hostile multi-tenant isolation'})
        return {'accepted': True, 'cases': len(cases), 'evidence': str(evidence.resolve()),
                'status': Execution(root).status()}
    except Exception as exc:
        report['accepted'] = False
        report['error'] = str(exc)
        sandbox_setup.save(evidence, report)
        sandbox_setup.save(receipt_path, {'accepted': False, 'reason': str(exc), 'adapter_sha256': adapter_hash()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'build', 'accept'])
    parser.add_argument('--data-dir', type=Path, default=Path('data/alpha'))
    parser.add_argument('--evidence', type=Path, default=Path('output/alpha-execution.json'))
    args = parser.parse_args()
    handle = None
    try:
        if args.action != 'check':
            from .locking import lock_data
            handle = lock_data(args.data_dir.resolve())
        if args.action == 'build':
            result = sandbox_setup.build(args.data_dir.resolve())
        elif args.action == 'accept':
            result = accept(args.data_dir, args.evidence)
        else:
            result = Execution(args.data_dir.resolve()).status()
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 2 if args.action == 'check' and not result['ready'] else 0
    except (SandboxUnavailable, OSError, ValueError) as exc:
        print(json.dumps({'status': 'unavailable', 'executed_on_host': False, 'error': str(exc)}, ensure_ascii=True))
        return 2
    finally:
        if handle is not None:
            handle.close()


if __name__ == '__main__':
    sys.exit(main())
