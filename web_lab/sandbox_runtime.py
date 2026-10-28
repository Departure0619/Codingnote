"""Fail-closed Docker task broker. No host eval/exec of submitted source."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

IMAGE_DIR = Path(__file__).with_name('sandbox_image')
IMAGE_ID_RE = re.compile(r'sha256:[0-9a-f]{64}')
OUTPUT_BYTES = 16384
POLICY = {'cpu':0.5, 'memory':134217728, 'pids':32, 'wall_seconds':3,
          'broker_seconds':6, 'output_bytes':OUTPUT_BYTES, 'tmp_bytes':16777216,
          'tmp_inodes':256, 'file_bytes':1048576, 'policy_version':1}
PROXIES = ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
           'http_proxy','https_proxy','all_proxy','no_proxy']


class SandboxUnavailable(RuntimeError):
    pass


def policy_hash():
    value = hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode())
    for path in [Path(__file__), Path(__file__).with_name('sandbox_contract.py'),
                 Path(__file__).with_name('sandbox_setup.py'),
                 Path(__file__).with_name('sandbox_service.py'), IMAGE_DIR / '.dockerignore',
                 *sorted(IMAGE_DIR.glob('*'))]:
        if path.is_file():
            value.update(path.name.encode()); value.update(path.read_bytes())
    return value.hexdigest()


def bounded_text(stdout, stderr):
    # Replacement characters for invalid/partial UTF-8 must not expand the
    # persisted/displayed output beyond the raw capture budget.
    values=[]; remaining=OUTPUT_BYTES
    for raw in (stdout,stderr):
        text=bytes(raw).decode('utf8',errors='replace')
        text=text.encode('utf8')[:remaining].decode('utf8',errors='ignore')
        remaining-=len(text.encode('utf8'));values.append(text)
    return tuple(values)


def execution_status(exit_code, oom, timed_out, output_limited, cleaned):
    if not cleaned or exit_code is None or exit_code == 125:
        return 'service_error'
    if output_limited:
        return 'output_limit'
    if timed_out or exit_code == 124:
        return 'timeout'
    if oom:
        return 'resource_limit'
    return 'completed' if exit_code == 0 else 'exception'


class DockerRuntime:
    def __init__(self, root, *, acceptance=False):
        self.root = Path(root)
        self.folder = self.root / 'sandbox'
        self.scope = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:20]
        self.acceptance = acceptance
        self.blocked_reason = None
        self.context = None
        self.image_id = None
        self.lock = threading.RLock()
        self._status = {'ready':False,'reason':'未完成 Docker 真实安全验收','isolation_verified':False}
        if shutil.which('docker'):
            self.refresh()
        else:
            self._status['reason'] = '未发现 Docker CLI；未启动隔离执行环境'

    def command(self, args, timeout=10):
        argv = ['docker'] + (['--context', self.context] if self.context else []) + args
        env = dict(os.environ)
        # Explicit local context; prevent accidental remote daemon overrides.
        for name in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:
            env.pop(name,None)
        try:
            process = subprocess.run(argv, capture_output=True, timeout=timeout, env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except (OSError, subprocess.TimeoutExpired):
            raise SandboxUnavailable('Docker 控制服务不可用或超时') from None
        if process.returncode:
            raise SandboxUnavailable('Docker 控制命令失败：' + args[0])
        return process.stdout.decode('utf8',errors='replace').strip()

    def preflight(self):
        if not shutil.which('docker'):
            raise SandboxUnavailable('未发现 Docker CLI')
        contexts=json.loads(self.command(['context','inspect']))
        context=contexts[0]
        endpoint=context['Endpoints']['docker']['Host']
        if not (endpoint.startswith('npipe://') or endpoint == 'unix:///var/run/docker.sock'):
            raise SandboxUnavailable('只允许本机 Docker named pipe 或标准 Unix socket')
        self.context=context['Name']
        info=json.loads(self.command(['info','--format','{{json .}}']))
        if info.get('OSType')!='linux':
            raise SandboxUnavailable('需要 Linux containers')
        if not any('seccomp' in s for s in info.get('SecurityOptions',[])):
            raise SandboxUnavailable('Engine 未提供默认 seccomp')
        if info.get('CgroupVersion')!='2':
            raise SandboxUnavailable('本方案要求已验证的 cgroup v2')
        if info.get('Warnings'):
            raise SandboxUnavailable('Docker 资源/安全警告需要维护者审查后处理')
        return {'engine_id':info.get('ID'),'server_version':info.get('ServerVersion'),
                'kernel':info.get('KernelVersion'),'endpoint':endpoint,'context':self.context,
                'security_options':sorted(info.get('SecurityOptions',[])),
                'cgroup_version':info.get('CgroupVersion'),'default_runtime':info.get('DefaultRuntime')}

    def refresh(self):
        with self.lock:
            try:
                if self.blocked_reason:
                    raise SandboxUnavailable(self.blocked_reason)
                fingerprint=self.preflight()
                manifest=json.loads((self.folder/'runtime.json').read_text(encoding='utf8'))
                if manifest.get('policy_sha256')!=policy_hash() or not IMAGE_ID_RE.fullmatch(manifest.get('image_id','')):
                    raise SandboxUnavailable('镜像/执行策略已变化，需要重新构建验收')
                self.image_id=manifest['image_id']
                image=json.loads(self.command(['image','inspect',self.image_id]))[0]
                if image['Id']!=self.image_id or image.get('Config',{}).get('Volumes'):
                    raise SandboxUnavailable('镜像检查失败或声明了不允许的卷')
                if not self.acceptance:
                    receipt=json.loads((self.folder/'acceptance.json').read_text(encoding='utf8'))
                    if receipt.get('accepted') is not True or receipt.get('fingerprint')!=fingerprint or receipt.get('image_id')!=self.image_id or receipt.get('policy_sha256')!=policy_hash():
                        raise SandboxUnavailable('没有匹配当前镜像/Engine 的真实安全验收记录')
                self._status={'ready':True,'reason':'本机 Docker 安全验收通过' if not self.acceptance else '维护者验收模式',
                              'isolation_verified':not self.acceptance,'image_id':self.image_id,
                              'limits':POLICY,'policy_sha256':policy_hash()}
            except (SandboxUnavailable,OSError,ValueError,KeyError,IndexError):
                self._status={'ready':False,'reason':self.blocked_reason or 'Docker/镜像未就绪或未通过真实安全验收',
                              'isolation_verified':False}
            return dict(self._status)

    def status(self):
        return dict(self._status)

    def create_args(self, name, image_id):
        if not IMAGE_ID_RE.fullmatch(image_id):
            raise SandboxUnavailable('只接受已记录的固定镜像 ID')
        return ['create','--pull=never','--name',name,'--label','aic.sandbox='+self.scope,
                '--network=none','--read-only','--user=65534:65534','--cap-drop=ALL',
                '--security-opt=no-new-privileges:true','--cgroupns=private','--ipc=none',
                '--cpus=0.5','--memory=128m','--memory-swap=128m','--pids-limit=32',
                '--ulimit','cpu=2:2','--ulimit','fsize=1048576:1048576',
                '--ulimit','nofile=64:64','--ulimit','core=0:0',
                '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=16777216,nr_inodes=256,mode=1777',
                '--log-driver=none','--restart=no','--stop-timeout=1','--interactive',
                *[arg for key in PROXIES for arg in ['--env',key+'=']],image_id]

    def inspect_policy(self, container):
        item=json.loads(self.command(['inspect',container]))[0]
        host=item['HostConfig']; config=item['Config']
        checks=[item['Image']==self.image_id, config['User']=='65534:65534',
                config['Entrypoint']==['python','-I','-S','-u','/runner/supervisor.py'],
                not config.get('Cmd'), not config.get('Volumes'), host['NetworkMode']=='none',
                host['Privileged'] is False, host['ReadonlyRootfs'] is True,
                host['CapDrop']==['ALL'], not host.get('CapAdd'),
                'no-new-privileges:true' in host['SecurityOpt'],
                not any('unconfined' in s for s in host['SecurityOpt']),
                host['Memory']==134217728,host['MemorySwap']==134217728,
                host['NanoCpus']==500000000,host['PidsLimit']==32,
                host['IpcMode']=='none',not host.get('PidMode'),host['CgroupnsMode']=='private',
                not host.get('Binds'),not host.get('Devices'),not host.get('DeviceRequests'),
                not host.get('PortBindings'),host['LogConfig']['Type']=='none',
                set(host.get('Tmpfs',{}))=={'/tmp'},
                all(m['Type']=='tmpfs' and m['Destination']=='/tmp' for m in item.get('Mounts',[]))]
        tmp_options=set(host.get('Tmpfs',{}).get('/tmp','').split(','))
        checks.append({'noexec','nosuid','nodev','size=16777216','nr_inodes=256'}<=tmp_options)
        limits={u['Name']:(u['Soft'],u['Hard']) for u in host.get('Ulimits',[])}
        checks.append(all(limits.get(k)==v for k,v in {'cpu':(2,2),'fsize':(1048576,1048576),'nofile':(64,64),'core':(0,0)}.items()))
        image=json.loads(self.command(['image','inspect',self.image_id]))[0]
        allowed={e.split('=',1)[0] for e in image['Config'].get('Env',[])}|set(PROXIES)
        checks.append(all(e.split('=',1)[0] in allowed for e in config.get('Env',[])))
        if not all(checks):
            raise SandboxUnavailable('实际容器权限/资源/挂载配置未通过检查')
        return item

    def cleanup(self, name):
        try:
            self.command(['rm','--force',name])
        except SandboxUnavailable:
            pass
        try:
            # Positive evidence of absence; an inspect connection failure is not absence.
            names=self.command(['ps','--all','--filter','label=aic.sandbox='+self.scope,
                                '--format','{{.Names}}']).splitlines()
            clean=name not in names
        except SandboxUnavailable:
            clean=False
        if not clean:
            self.blocked_reason='执行容器清理未确认；沙箱已关闭，需要维护者处理'
            self.refresh()
        return clean

    def recover(self):
        if not shutil.which('docker'):
            return
        try:
            self.preflight()
            names=self.command(['ps','--all','--filter','label=aic.sandbox='+self.scope,
                                '--format','{{.Names}}']).splitlines()
            for name in names:
                if re.fullmatch(r'aic-task-[0-9a-f]{32}',name):
                    self.cleanup(name)
        except (SandboxUnavailable,OSError,ValueError,KeyError,IndexError):
            self.blocked_reason='启动时无法检查遗留容器；沙箱已关闭'
        self.refresh()

    def execute(self, source, n):
        with self.lock:
            if not self.refresh()['ready']:
                return {'status':'unavailable','executed':False,'stdout':'','stderr':'',
                        'error':self._status['reason'],'elapsed_seconds':0,'cleaned':True}
            if type(n) is not int or not 0<=n<=8 or len(source.encode('utf8'))>400000:
                raise ValueError('源码或输入超出已审核合同')
            name='aic-task-'+uuid.uuid4().hex
            started=time.perf_counter(); process=None; output_limited=False; timed_out=False
            executed=False; exit_code=None; oom=False; error=None
            chunks={'stdout':bytearray(),'stderr':bytearray()}; total=0
            output_lock=threading.Lock(); overflow=threading.Event(); threads=[]
            try:
                self.command(self.create_args(name,self.image_id))
                self.inspect_policy(name)
                argv=['docker','--context',self.context,'start','--attach','--interactive',name]
                env=dict(os.environ)
                for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:
                    env.pop(key,None)
                process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                def read(stream,key):
                    nonlocal total
                    try:
                        while True:
                            part=stream.read1(1024)
                            if not part:break
                            with output_lock:
                                available=max(0,OUTPUT_BYTES-total)
                                chunks[key].extend(part[:available]); total+=min(len(part),available)
                                if len(part)>available:overflow.set()
                    except (OSError,ValueError):
                        pass
                for key in chunks:
                    thread=threading.Thread(target=read,args=(getattr(process,key),key),daemon=True)
                    thread.start();threads.append(thread)
                payload=json.dumps({'source':source,'input':n},ensure_ascii=False).encode('utf8')
                def write():
                    try:process.stdin.write(payload);process.stdin.close()
                    except (OSError,ValueError):pass
                writer=threading.Thread(target=write,daemon=True);writer.start();threads.append(writer)
                deadline=time.perf_counter()+POLICY['broker_seconds']
                while process.poll() is None:
                    if overflow.is_set():output_limited=True;break
                    if time.perf_counter()>=deadline:timed_out=True;break
                    time.sleep(.02)
                if timed_out or output_limited:
                    try:self.command(['kill',name],timeout=5)
                    except SandboxUnavailable:pass
                    process.kill()
                process.wait(timeout=3)
                for thread in threads:thread.join(timeout=1)
                output_limited=output_limited or overflow.is_set()
                state=json.loads(self.command(['inspect',name]))[0]['State']
                executed=bool(state.get('StartedAt') and not state['StartedAt'].startswith('0001'))
                if state.get('Running') or not executed or state.get('Error'):
                    raise SandboxUnavailable('Docker 未提供可信的任务结束状态')
                exit_code=state['ExitCode'];oom=state['OOMKilled']
                if process.returncode not in (0,exit_code) and not (timed_out or output_limited):
                    raise SandboxUnavailable('执行连接失败，输出可能不完整')
            except (SandboxUnavailable,OSError,ValueError,KeyError,IndexError,subprocess.TimeoutExpired):
                error='执行服务失败，结果不可作为成功证据';exit_code=None
            finally:
                cleaned=self.cleanup(name)
                if process is not None:
                    if process.poll() is None:process.kill()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:pass
                    for thread in threads:thread.join(timeout=1)
                    for stream in [process.stdin,process.stdout,process.stderr]:
                        try:stream.close()
                        except OSError:pass
            output_limited=output_limited or sum(len(bytes(raw).decode('utf8',errors='replace').encode('utf8'))
                                                  for raw in chunks.values())>OUTPUT_BYTES
            status=execution_status(exit_code,oom,timed_out,output_limited,cleaned)
            if status=='service_error':error=error or '清理或运行状态未确认，结果不可作为成功证据'
            stdout,stderr=bounded_text(chunks['stdout'],chunks['stderr'])
            return {'status':status,'executed':executed,'stdout':stdout,
                    'stderr':stderr,'exit_code':exit_code,
                    'elapsed_seconds':round(time.perf_counter()-started,4),'cleaned':cleaned,
                    'output_truncated':output_limited,'oom_killed':oom,'error':error,
                    'container_name':name,'image_id':self.image_id}
