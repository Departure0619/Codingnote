"""Read-only check, explicit image build, and real Docker-only acceptance.

Never installs Docker, changes Windows features, or executes student code on host.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .sandbox_runtime import DockerRuntime, SandboxUnavailable, IMAGE_DIR, POLICY, policy_hash

BASE_IMAGE='python:3.12-slim-bookworm'


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.pending.json')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temporary.replace(path)


def check(root):
    value={'checked_at':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
           'python':platform.python_version(),'docker_cli':shutil.which('docker'),
           'docker_desktop_default_path_present':Path(r'C:\Program Files\Docker\Docker\Docker Desktop.exe').exists(),
           'dependencies':{},'wsl':{},'docker':{}}
    for name in ['fastapi','uvicorn','pydantic','httpx']:
        try:value['dependencies'][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:value['dependencies'][name]=None
    if os.name=='nt':
        for flags in [['--status'],['--list','--verbose'],['--version']]:
            try:
                p=subprocess.run(['wsl',*flags],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
                raw=p.stdout+p.stderr
                value['wsl'][' '.join(flags)]={'exit_code':p.returncode,'output':raw.decode('utf-16-le' if b'\x00' in raw else 'utf8',errors='replace')}
            except (OSError,subprocess.TimeoutExpired):value['wsl'][' '.join(flags)]={'error':'WSL check failed or timed out'}
    runtime=DockerRuntime(root)
    try:value['docker']['fingerprint']=runtime.preflight()
    except SandboxUnavailable as exc:value['docker']['reason']=str(exc)
    value['sandbox']=runtime.status()
    return value


def build(root):
    runtime=DockerRuntime(root,acceptance=True)
    fingerprint=runtime.preflight()
    runtime.folder.mkdir(parents=True,exist_ok=True)
    # Rebuild invalidates old receipt before any mutation.
    save(runtime.folder/'acceptance.json',{'accepted':False,'reason':'image rebuild requires acceptance'})
    runtime.command(['pull',BASE_IMAGE],timeout=300)
    image=json.loads(runtime.command(['image','inspect',BASE_IMAGE]))[0]
    digests=image.get('RepoDigests',[])
    if not digests:raise SandboxUnavailable('基础镜像无内容 digest，拒绝构建')
    base=digests[0]
    iid=runtime.folder/'image.iid'
    runtime.command(['build','--network=none','--build-arg','BASE_IMAGE='+base,
                     '--iidfile',str(iid.resolve()),str(IMAGE_DIR)],timeout=300)
    image_id=iid.read_text(encoding='ascii').strip()
    manifest={'image_id':image_id,'base_digest':base,'base_requested':BASE_IMAGE,
              'policy_sha256':policy_hash(),'built_at':datetime.now(timezone.utc).isoformat(),
              'fingerprint':fingerprint}
    save(runtime.folder/'runtime.json',manifest)
    return manifest


def accept(root):
    runtime=DockerRuntime(root,acceptance=True)
    save(runtime.folder/'acceptance.json',{'accepted':False,'reason':'acceptance in progress'})
    fingerprint=runtime.preflight()
    if not runtime.refresh()['ready']:raise SandboxUnavailable('先 build 固定镜像，然后验收')
    runtime.recover()
    cases=[]
    def run(name,source,n,verify):
        result=runtime.execute(source,n)
        passed=bool(result.get('executed') and result.get('cleaned') and verify(result))
        cases.append({'name':name,'source_sha256':hashlib.sha256(source.encode('utf8')).hexdigest(),
                      'input':n,'passed':passed,'result':result})
        if not passed:raise SandboxUnavailable('真实容器验收失败：'+name)
    try:
        # Exactly three core programs; all execution is through DockerRuntime.
        run('correct','def solve(n):\n    return n*(n+1)//2\n',3,
            lambda r:r['status']=='completed' and r['stdout']=='6\n')
        run('wrong','def solve(n):\n    return n*(n-1)//2\n',3,
            lambda r:r['status']=='completed' and r['stdout']=='3\n' and r['stdout']!='6\n')
        run('infinite_loop','def solve(n):\n    while True:\n        pass\n',3,
            lambda r:r['status']=='timeout')
        # Benign deterministic guards: introspection and bounded writes, no escape samples.
        guard="""def solve(n):
    import os, resource, errno, socket, subprocess, importlib.util
    from pathlib import Path
    status=Path('/proc/self/status').read_text()
    cpu=Path('/sys/fs/cgroup/cpu.max').read_text().split()
    result=[os.getuid()==65534, 'NoNewPrivs:\\t1' in status, 'Seccomp:\\t2' in status,
        set(p.name for p in Path('/sys/class/net').iterdir())=={'lo'},
        all(not Path(p).exists() for p in ['/mnt/c','/host','/project','/var/run/docker.sock','/run/desktop/mnt/host']),
        int(Path('/sys/fs/cgroup/memory.max').read_text())==134217728,
        int(Path('/sys/fs/cgroup/pids.max').read_text())==32,
        cpu[0]!='max' and int(cpu[0])/int(cpu[1])<=0.5,
        resource.getrlimit(resource.RLIMIT_CPU)==(2,2),
        resource.getrlimit(resource.RLIMIT_NOFILE)==(64,64),
        os.statvfs('/tmp').f_blocks*os.statvfs('/tmp').f_frsize<=16777216,
        os.statvfs('/tmp').f_files<=256,
        'CapEff:\\t0000000000000000' in status,
        not any(k.startswith(('AIC_','DOCKER_')) for k in os.environ),
        int(Path('/sys/fs/cgroup/memory.swap.max').read_text())==0,
        importlib.util.find_spec('pip') is None]
    try:
        Path('/runner/forbidden').write_text('guard')
        result.append(False)
    except OSError:
        result.append(True)
    try:
        with open('/tmp/bounded-file','wb') as f:
            f.write(b'x'*1048577)
        result.append(False)
    except OSError:
        result.append(True)
    with socket.socket() as connection:
        connection.settimeout(0.2)
        try:
            connection.connect(('203.0.113.1',80))
            result.append(False)
        except OSError as exc:
            result.append(exc.errno==errno.ENETUNREACH)
    # Bounded quota probes, never fork bombs or escape attempts.
    files=[]
    try:
        for i in range(20):
            path=Path('/tmp/quota-'+str(i));files.append(path)
            path.write_bytes(b'x'*1048576)
        result.append(False)
    except OSError as exc:
        result.append(exc.errno==errno.ENOSPC)
    finally:
        for path in files:
            path.unlink(missing_ok=True)
    children=[]
    try:
        for i in range(34):
            children.append(subprocess.Popen(['/bin/sleep','5']))
        result.append(False)
    except OSError as exc:
        result.append(exc.errno==errno.EAGAIN)
    finally:
        for child in children:
            child.kill()
        for child in children:
            child.wait()
    return result
"""
        def guarded(result):
            if result['status']!='completed':return False
            values=json.loads(result['stdout'])
            return len(values)==21 and all(v is True for v in values)
        run('network_files_permissions_resources',guard,3,guarded)
        run('bounded_output','def solve(n):\n    print("x"*17000)\n    return n\n',3,
            lambda r:r['status']=='output_limit' and len((r['stdout']+r['stderr']).encode('utf8'))<=POLICY['output_bytes'])
        run('invalid_utf8_output',"def solve(n):\n    import os\n    os.write(1,b'\\xff'*6000)\n    return n\n",3,
            lambda r:r['status']=='output_limit' and len((r['stdout']+r['stderr']).encode('utf8'))<=POLICY['output_bytes'])
        remaining=runtime.command(['ps','--all','--filter','label=aic.sandbox='+runtime.scope,'--format','{{.Names}}'])
        if remaining:raise SandboxUnavailable('验收后仍有任务容器')
        receipt={'accepted':True,'accepted_at':datetime.now(timezone.utc).isoformat(),
                 'fingerprint':fingerprint,'image_id':runtime.image_id,'policy_sha256':policy_hash(),
                 'checks':cases,'scope':'single-user-loopback-demo; not hostile multi-tenant isolation'}
        save(runtime.folder/'acceptance.json',receipt)
        return receipt
    except Exception:
        save(runtime.folder/'acceptance.json',{'accepted':False,'checks':cases,'reason':'real acceptance failed',
            'image_id':runtime.image_id,'policy_sha256':policy_hash()})
        raise


def main():
    parser=argparse.ArgumentParser(description='AIC sandbox: no installation or host student execution')
    parser.add_argument('action',choices=['check','build','accept'])
    parser.add_argument('--data-dir',type=Path,default=Path('data/aic_demo'))
    args=parser.parse_args()
    root=args.data_dir.resolve()
    try:
        result={'check':check,'build':build,'accept':accept}[args.action](root)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.action=='check' and not result['sandbox']['ready']:return 2
        return 0
    except (SandboxUnavailable,OSError,ValueError) as exc:
        print(json.dumps({'status':'unavailable','executed_on_host':False,'error':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':sys.exit(main())
