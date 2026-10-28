"""Opt-in real Docker acceptance. All submitted programs run only in containers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from unittest.mock import patch
from alpha.runner import Execution
from alpha.phase_runtime import policy_hash, SandboxUnavailable, RUN_LIMITS
from web_lab.sandbox_setup import save


GUARD = r'''import os, resource, json, socket, errno
from pathlib import Path
s=Path('/proc/self/status').read_text()
checks=[os.getuid()==65534, 'NoNewPrivs:\t1' in s, 'Seccomp:\t2' in s,
 'CapEff:\t0000000000000000' in s, {p.name for p in Path('/sys/class/net').iterdir()}=={'lo'},
 int(Path('/sys/fs/cgroup/memory.max').read_text())==134217728,
 int(Path('/sys/fs/cgroup/pids.max').read_text())==32,
 resource.getrlimit(resource.RLIMIT_CPU)==(2,2),resource.getrlimit(resource.RLIMIT_NOFILE)==(64,64),
 int(Path('/sys/fs/cgroup/memory.swap.max').read_text())==0,
 all(not Path(p).exists() for p in ['/mnt/c','/host','/project','/var/run/docker.sock','/run/desktop/mnt/host']),
 not any(k.startswith(('AIC_','DOCKER_')) for k in os.environ),
 not any('KEY' in k and k != 'GPG_KEYS' for k in os.environ),
 os.statvfs('/tmp').f_blocks*os.statvfs('/tmp').f_frsize==16777216,
 any(line.split()[1]=='/tmp' and 'noexec' in line.split()[3].split(',') for line in Path('/proc/mounts').read_text().splitlines())]
try: Path('/runner/forbidden').write_text('x');checks.append(False)
except OSError: checks.append(True)
with socket.socket() as connection:
 connection.settimeout(.2)
 try: connection.connect(('203.0.113.1',80));checks.append(False)
 except OSError as exc: checks.append(exc.errno==errno.ENETUNREACH)
print(json.dumps(checks))
'''

C_GUARD = r'''#include <stdio.h>
#include <unistd.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <errno.h>
#include <string.h>
int main(void){
 struct rlimit r; getrlimit(RLIMIT_CPU,&r);
 int ok=getuid()==65534 && r.rlim_cur==2 && r.rlim_max==2;
 FILE*f=fopen("/sys/fs/cgroup/memory.max","r");long m=0;if(f){fscanf(f,"%ld",&m);fclose(f);}ok &= m==134217728;
 f=fopen("/sys/fs/cgroup/pids.max","r");m=0;if(f){fscanf(f,"%ld",&m);fclose(f);}ok &= m==32;
 f=fopen("/runner/forbidden","w");ok &= !f;if(f)fclose(f);
 ok &= access("/var/run/docker.sock",F_OK)!=0 && access("/project",F_OK)!=0;
 int s=socket(AF_INET,SOCK_STREAM,0);struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(80);inet_pton(AF_INET,"203.0.113.1",&a.sin_addr);
 ok &= connect(s,(struct sockaddr*)&a,sizeof(a))==-1 && errno==ENETUNREACH;close(s);
 f=fopen("/proc/self/status","r");char line[256];int cap=0,sec=0,priv=0;
 while(f&&fgets(line,sizeof(line),f)){cap|=strstr(line,"CapEff:\t0000000000000000")!=0;sec|=strstr(line,"Seccomp:\t2")!=0;priv|=strstr(line,"NoNewPrivs:\t1")!=0;}if(f)fclose(f);
 printf("%d\n",ok&&cap&&sec&&priv);return 0;
}'''


def accept(root, evidence):
    root, evidence = Path(root).resolve(), Path(evidence).resolve()
    execution = Execution(root, acceptance=True)
    runtime = execution.runtime
    receipt = runtime.folder / 'acceptance.json'
    save(receipt, {'accepted': False, 'reason':'real round2 acceptance in progress'})
    report = {'started_at':datetime.now(timezone.utc).isoformat(), 'runtime_root':str(root),
        'executed_on_host':False, 'real_ai_calls':0, 'cases':[], 'accepted':False,
        'policy_sha256':policy_hash()}
    try:
        if not execution.status()['ready']: raise SandboxUnavailable('请先构建固定镜像。')
        runtime.recover()
        report['runtime'] = execution.status()
        report['manifest'] = json.loads((runtime.folder/'runtime.json').read_text(encoding='utf8'))

        def run(name, language, source, stdin='', expected='completed', stdout=None, verify=None):
            result = execution.run(language, source, stdin)
            allowed = (expected,) if isinstance(expected,str) else expected
            passed = result['status'] in allowed and result.get('cleaned') is True
            if stdout is not None: passed &= result['stdout'] == stdout
            if result['status'] in ('completed','exception','timeout','output_limit','resource_limit'):
                passed &= bool(result.get('executed') and result['compile_status']=='passed')
            if result['status']=='compile_error': passed &= not result['executed'] and result['compile_status']=='failed'
            if verify: passed &= bool(verify(result))
            report['cases'].append({'name':name,'language':language,'source':source,
                'source_sha256':hashlib.sha256(source.encode()).hexdigest(), 'stdin':stdin,
                'expected_status':expected,'expected_stdout':stdout,'passed':bool(passed),'result':result})
            save(evidence,report)
            print(('PASS ' if passed else 'FAIL ')+name+' '+result['status'],flush=True)
            return result

        run('python_correct','python','n=int(input())\nprint(n*(n+1)//2)','3\n',stdout='6\n')
        run('python_wrong','python','n=int(input())\nprint(sum(range(n)))','3\n',stdout='3\n')
        run('python_fixed','python','n=int(input())\nprint(sum(range(n+1)))','3\n',stdout='6\n')
        run('python_empty','python','print("hello")',stdout='hello\n')
        run('python_unicode_nul','python','import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())','中文 café\x00',stdout='中文 café\x00')
        run('python_compile_error','python','if broken syntax',expected='compile_error')
        run('python_runtime_error','python','print(1/0)',expected='exception')
        run('python_eof','python','input()',expected='exception')
        run('python_timeout','python','while True: pass',expected='timeout')
        run('python_output_limit','python','print("x"*20000)',expected='output_limit')
        # GCC base declares public release signing fingerprints as GPG_KEYS.
        # Synthetic host credential proves Docker does not inherit host secrets.
        with patch.dict('os.environ', {'ALPHA_TEST_PRIVATE_KEY':'synthetic-host-only-not-a-real-key'}):
            run('python_security_17_guards','python',GUARD,stdout=json.dumps([True]*17)+'\n')
        run('actual_toolchain_versions','python','import sys,subprocess,json\nprint(json.dumps([sys.version.split()[0],subprocess.check_output(["gcc","-dumpfullversion"],text=True).strip(),subprocess.check_output(["g++","-dumpfullversion"],text=True).strip()]))',
            verify=lambda r:json.loads(r['stdout'])[1:]==['14.2.0','14.2.0'])
        csum='#include <stdio.h>\nint main(void){long n;if(scanf("%ld",&n)!=1)return 2;printf("%ld\\n",n*(n+1)/2);return 0;}'
        cppsum='#include <iostream>\n#include <vector>\n#include <string>\n#include <numeric>\n#include <array>\nint sum(const std::vector<int>&v){return std::accumulate(v.begin(),v.end(),0);}\nint main(){int n;std::cin>>n;std::vector<int>v;for(int i=1;i<=n;++i)v.push_back(i);std::array<int,1>a{sum(v)};std::string label="sum";auto [answer]=a;std::cout<<label<<":"<<answer<<"\\n";}'
        for language, code, answer in [('c',csum,'6\n'),('cpp',cppsum,'sum:6\n')]:
            run(language+'_normal',language,code,'3\n',stdout=answer)
            run(language+'_compile_error',language,'int main( {',expected='compile_error')
            run(language+'_empty_input',language,'#include <stdio.h>\nint main(){puts("hello");}',stdout='hello\n')
            run(language+'_unicode_nul_stdin',language,'#include <stdio.h>\nint main(){char b[1024];size_t n=fread(b,1,sizeof(b),stdin);fwrite(b,1,n,stdout);}', '中文 café\x00z', stdout='中文 café\x00z')
            run(language+'_stdout_stderr',language,'#include <stdio.h>\nint main(){puts("out");fputs("err\\n",stderr);}',stdout='out\n',verify=lambda r:r['stderr']=='err\n')
            run(language+'_segfault',language,'#include <signal.h>\nint main(){raise(SIGSEGV);}',expected='exception',verify=lambda r:r['termination_signal']=='SIGSEGV')
            run(language+'_timeout',language,'int main(){while(1){}}',expected='timeout')
            run(language+'_output_limit',language,'#include <stdio.h>\nint main(){for(int i=0;i<50000;i++)putchar(120);}',expected='output_limit',verify=lambda r:len((r['stdout']+r['stderr']).encode())<=16384)
            run(language+'_pid_limit',language,'#include <unistd.h>\nint main(){for(int i=0;i<64;i++){int p=fork();if(p==0){while(1)pause();}if(p<0)break;}return 0;}',expected='resource_limit',verify=lambda r:r['resource_reason']=='pids')
            run(language+'_memory_limit',language,'#include <stdlib.h>\n#include <string.h>\nint main(){for(int i=0;i<512;i++){void*p=malloc(1048576);if(!p)return 2;memset(p,1,1048576);}return 0;}',expected='resource_limit',verify=lambda r:r['resource_reason']=='memory')
            run(language+'_forged_exit',language,'int main(){return 124;}',expected='exception')
            run(language+'_native_security',language,C_GUARD,stdout='1\n')
        run('cpp_stl_common_header','cpp','#include <bits/stdc++.h>\nint main(){std::vector<std::string> v={"课程","C++17"};for(auto&s:v)std::cout<<s<<"\\n";}',stdout='课程\nC++17\n')
        run('c_invalid_utf8_output','c','#include <stdio.h>\nint main(){putchar(255);}',stdout='\ufffd')
        run('c_standard_math','c','#include <stdio.h>\n#include <math.h>\nint main(){double x;scanf("%lf",&x);printf("%.1f\\n",sqrt(x));}','9\n',stdout='3.0\n')
        run('c_forged_compile_text','c','#pragma message("{\\"compiled\\":true}")\n#error intentional_compile_failure\nint main(){}',expected='compile_error')

        c_stress='#define X0 0\n'+'\n'.join('#define X%d X%d,X%d'%(n,n-1,n-1) for n in range(1,24))+'\nint x[]={X23};int main(){return x[0];}'
        cpp_stress='template<int N,int I>struct T:T<N-1,I*2>,T<N-1,I*2+1>{};template<int I>struct T<0,I>{};T<18,0> t;int main(){}'
        for language,source in [('c',c_stress),('cpp',cpp_stress)]:
            result=runtime.execute_phase(language,'compile',source=source)
            result.pop('artifact',None)
            passed=result['status'] in ('resource_limit','timeout') and result['executed'] and result['cleaned']
            report['cases'].append({'name':language+'_compile_resource_or_deadline','source':source,
                'passed':bool(passed),'expected_status':['resource_limit','timeout'],'result':result})
            save(evidence,report);print(('PASS ' if passed else 'FAIL ')+language+'_compile_resource_or_deadline '+result['status'],flush=True)
        for language in ('c','cpp'):
            result=execution.run(language,'#warning '+'x'*20000+'\nint main(){}','')
            passed=(result['status']=='output_limit' and result['compile_status']=='not_completed'
                    and not result['executed'] and result['cleaned'] and len((result['stdout']+result['stderr']).encode())<=16384)
            report['cases'].append({'name':language+'_compiler_output_limit','passed':passed,'result':result})
            save(evidence,report);print(('PASS ' if passed else 'FAIL ')+language+'_compiler_output_limit '+result['status'],flush=True)

        # Cancellation is checked against live Docker; precompile once then cancel
        # only the run phase so elapsed compiler time cannot race the test timer.
        for language, source in [('python','import time\ntime.sleep(30)'),('c','int main(){while(1){}}'),('cpp','int main(){while(1){}}')]:
            compiled=runtime.execute_phase(language,'compile',source=source)
            artifact=compiled.pop('artifact',None)
            event=threading.Event(); timer=threading.Timer(.7,event.set);timer.start()
            try: result=runtime.execute_phase(language,'run',source=source,artifact=artifact,cancel_event=event)
            finally: timer.cancel()
            passed=compiled['status']=='completed' and result['status']=='cancelled' and result['cleaned']
            report['cases'].append({'name':language+'_live_cancel','passed':passed,'compile_result':compiled,'result':result})
            save(evidence,report);print(('PASS ' if passed else 'FAIL ')+language+'_live_cancel',flush=True)
        # Explicit control-plane fault injection; never represented as real daemon failure.
        with patch.object(runtime,'preflight',side_effect=SandboxUnavailable('Docker unavailable test fixture')):
            result=execution.run('c',csum,'3\n')
        report['cases'].append({'name':'docker_unavailable_control_fixture','fixture':True,
            'passed':result['status']=='unavailable' and not result['executed'],'result':result})
        remaining=runtime.command(['ps','--all','--filter','label=aic.sandbox='+runtime.scope,'--format','{{.Names}}'])
        report['remaining_containers']=remaining.splitlines()
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        report['accepted']=all(case['passed'] for case in report['cases']) and not remaining
        save(evidence,report)
        if not report['accepted']: raise SandboxUnavailable('真实容器验收存在失败项，请查看证据。')
        state=execution.status()
        save(receipt,{'accepted':True,'image_id':state['image_id'],'policy_sha256':policy_hash(),
            'fingerprint':runtime.preflight(),'accepted_at':report['completed_at'],'evidence':str(evidence),
            'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
            'checks':[{'name':c['name'],'passed':c['passed']} for c in report['cases']],
            'scope':'trusted local single user; Docker shared kernel; not public hostile execution'})
        return {'accepted':True,'cases':len(report['cases']),'evidence':str(evidence),'status':Execution(root).status()}
    except Exception as exc:
        report['error']=str(exc);report['accepted']=False
        save(evidence,report)
        save(receipt,{'accepted':False,'reason':str(exc)})
        raise
