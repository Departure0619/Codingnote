"""Explicit round-two image maintenance; never touches round-one receipts."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from .phase_runtime import PhaseRuntime, IMAGE_DIR, policy_hash, SandboxUnavailable
from .runner import Execution
from web_lab.sandbox_setup import save
from .locking import lock_data

GCC_IMAGE = 'gcc:14.2.0-bookworm@sha256:b99b86a28812b1e6453a231a947dc43d76fe192788a12f344a9b568bf9f5d24c'
PYTHON_IMAGE = 'python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70'


JAVA_IMAGE = 'eclipse-temurin:17.0.18_8-jdk-jammy@sha256:978ed38b7785312f7761bee5e24cfcd7ac2fe466c5f07acee7b322e0bae6d0ec'
NODE_IMAGE = 'node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94'

def build(root):
    runtime = PhaseRuntime(root, acceptance=True)
    fingerprint = runtime.preflight()
    save(runtime.folder / 'acceptance.json', {'accepted': False, 'reason': 'rebuild requires acceptance'})
    bases = {}
    for label, requested in [('gcc', GCC_IMAGE), ('python', PYTHON_IMAGE), ('java', JAVA_IMAGE), ('node', NODE_IMAGE)]:
        try:
            image = json.loads(runtime.command(['image', 'inspect', requested]))[0]
        except SandboxUnavailable:
            runtime.command(['pull', requested], timeout=600)
            image = json.loads(runtime.command(['image', 'inspect', requested]))[0]
        digest_reference = requested.split('@')[0].split(':')[0] + '@' + requested.split('@')[1]
        if digest_reference not in image.get('RepoDigests', []):
            raise SandboxUnavailable('基础镜像摘要不匹配')
        bases[label] = {'reference': requested, 'image_id': image['Id']}
    iid = runtime.folder / 'image.iid'
    runtime.command(['build', '--network=none', '--build-arg', 'GCC_IMAGE='+GCC_IMAGE,
        '--build-arg', 'PYTHON_IMAGE='+PYTHON_IMAGE, '--build-arg', 'JAVA_IMAGE='+JAVA_IMAGE,
        '--build-arg', 'NODE_IMAGE='+NODE_IMAGE, '--iidfile', str(iid.resolve()), str(IMAGE_DIR)], timeout=600)
    image = json.loads(runtime.command(['image', 'inspect', iid.read_text(encoding='ascii').strip()]))[0]
    manifest = {'image_id': image['Id'], 'policy_sha256': policy_hash(), 'bases': bases,
        'built_at': datetime.now(timezone.utc).isoformat(), 'fingerprint': fingerprint,
        'toolchain': {'gcc':'14.2.0', 'g++':'14.2.0', 'c_standard':'c17', 'cpp_standard':'c++17',
                      'java':'17.0.18+8', 'node':'22.22.0', 'python':'3.12 (full version recorded by real acceptance)'},
        'build_network':'none', 'build_context':str(IMAGE_DIR.resolve())}
    save(runtime.folder / 'runtime.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'build', 'accept'])
    parser.add_argument('--data-dir', type=Path, default=Path('data/alpha-runtime-round35'))
    parser.add_argument('--evidence', type=Path, default=Path('output/round35/execution.json'))
    args = parser.parse_args()
    handle = None
    try:
        if args.action != 'check': handle = lock_data(args.data_dir.resolve())
        if args.action == 'build': result = build(args.data_dir.resolve())
        elif args.action == 'accept':
            from .tests.language_expansion_live import accept
            result = accept(args.data_dir.resolve(), args.evidence)
        else: result = Execution(args.data_dir.resolve()).status()
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 2 if args.action == 'check' and not result['ready'] else 0
    except (SandboxUnavailable, OSError, ValueError) as exc:
        print(json.dumps({'status':'unavailable', 'error':str(exc), 'executed_on_host':False}, ensure_ascii=True))
        return 2
    finally:
        if handle is not None: handle.close()


if __name__ == '__main__':
    sys.exit(main())
