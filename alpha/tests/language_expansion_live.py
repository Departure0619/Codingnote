"""Opt-in five-language acceptance; real programs execute only in Docker."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from fastapi.testclient import TestClient
from alpha.app import create_app
from alpha.runner import Execution
from alpha.phase_runtime import policy_hash, SandboxUnavailable
from web_lab.sandbox_setup import save


def java(body, name='Main'):
    return 'public class '+name+' { public static void main(String[] args) throws Exception { '+body+' } }'


JAVA_GUARD = java(r'''
var fs=java.nio.file.Path.class;
String status=java.nio.file.Files.readString(java.nio.file.Path.of("/proc/self/status"));
boolean ok=status.contains("Uid:\t65534") && status.contains("NoNewPrivs:\t1")
 && status.contains("Seccomp:\t2") && status.contains("CapEff:\t0000000000000000");
ok &= java.nio.file.Files.readString(java.nio.file.Path.of("/sys/fs/cgroup/memory.max")).trim().equals("134217728");
ok &= java.nio.file.Files.readString(java.nio.file.Path.of("/sys/fs/cgroup/pids.max")).trim().equals("32");
ok &= !java.nio.file.Files.exists(java.nio.file.Path.of("/var/run/docker.sock"));
ok &= !java.nio.file.Files.exists(java.nio.file.Path.of("/project"));
ok &= System.getenv("ALPHA_TEST_PRIVATE_KEY")==null;
try { java.nio.file.Files.writeString(java.nio.file.Path.of("/runner/forbidden"),"x"); ok=false; } catch(java.io.IOException e) {}
try(var s=new java.net.Socket()) {s.connect(new java.net.InetSocketAddress("203.0.113.1",80),200);ok=false;} catch(java.io.IOException e) {}
try(var paths=java.nio.file.Files.list(java.nio.file.Path.of("/sys/class/net"))) {ok &= paths.count()==1;}
System.out.println(ok);
''')

JS_GUARD = r'''
const fs=require('fs'), net=require('net');
const status=fs.readFileSync('/proc/self/status','utf8');
let ok=process.getuid()===65534 && status.includes('NoNewPrivs:\t1') && status.includes('Seccomp:\t2') && status.includes('CapEff:\t0000000000000000');
ok &&= fs.readFileSync('/sys/fs/cgroup/memory.max','utf8').trim()==='134217728';
ok &&= fs.readFileSync('/sys/fs/cgroup/pids.max','utf8').trim()==='32';
ok &&= !fs.existsSync('/var/run/docker.sock') && !fs.existsSync('/project');
ok &&= !process.env.ALPHA_TEST_PRIVATE_KEY;
ok &&= JSON.stringify(fs.readdirSync('/sys/class/net'))==='["lo"]';
try {fs.writeFileSync('/runner/forbidden','x');ok=false;} catch(e) {ok &&= e.code==='EROFS';}
const s=net.connect({host:'203.0.113.1',port:80});
s.on('connect',()=>{console.log(false);s.destroy()});
s.on('error',e=>{console.log(ok && e.code==='ENETUNREACH');s.destroy()});
s.setTimeout(300,()=>{console.log(false);s.destroy()});
'''


def accept(root, evidence):
    root, evidence = Path(root).resolve(), Path(evidence).resolve()
    if evidence.exists():
        raise ValueError('Use a new evidence path; previous receipts are never overwritten.')
    execution = Execution(root, acceptance=True)
    runtime = execution.runtime
    receipt = runtime.folder / 'acceptance.json'
    report = {'started_at':datetime.now(timezone.utc).isoformat(), 'runtime_root':str(root),
              'suite':'alpha-five-language-v1', 'verified_languages':list(execution.status()['languages']),
              'real_ai_calls':0, 'executed_on_host':False, 'accepted':False, 'cases':[]}
    save(receipt, {'accepted':False, 'reason':'five-language acceptance in progress'})

    def record(name, passed, **details):
        report['cases'].append({'name':name, 'passed':bool(passed), **details})
        save(evidence, report)
        print(('PASS ' if passed else 'FAIL ')+name, flush=True)

    def run(name, language, source, stdin='', expected='completed', stdout=None, verify=None):
        result=execution.run(language, source, stdin)
        passed=result['status']==expected and result.get('cleaned') is True
        passed &= result.get('source_sha256')==hashlib.sha256(source.encode()).hexdigest()
        passed &= result.get('language')==language
        if expected=='compile_error':
            passed &= not result['executed'] and result['compile_status']=='failed'
        else:
            passed &= result.get('executed') and result['compile_status']=='passed'
            passed &= result.get('stdin_sha256')==hashlib.sha256(stdin.encode()).hexdigest()
        if stdout is not None: passed &= result['stdout']==stdout
        if verify: passed &= bool(verify(result))
        record(name, passed, language=language, source=source, stdin=stdin,
               expected_status=expected, expected_stdout=stdout, result=result)
        return result

    try:
        if not execution.status()['ready']: raise SandboxUnavailable('Build the pinned image first.')
        runtime.recover()
        report['runtime']=execution.status()
        report['manifest']=json.loads((runtime.folder/'runtime.json').read_text(encoding='utf8'))
        for language, hello, echo, total, invalid, error, loop, output, memory, exitcode in [
            ('java', java('System.out.println("Hello World");'),
             java('System.out.write(System.in.readAllBytes());'),
             java('var s=new java.util.Scanner(System.in); long n=0;while(s.hasNextLong())n+=s.nextLong();System.out.println(n);'),
             'public class Main { broken syntax }', java('throw new RuntimeException("fixture");'),
             java('while(true){}'), java('System.out.print("x".repeat(30000));'),
             java('var f=sun.misc.Unsafe.class.getDeclaredField("theUnsafe");f.setAccessible(true);var u=(sun.misc.Unsafe)f.get(null);for(int i=0;i<512;i++){long p=u.allocateMemory(1048576);u.setMemory(p,1048576,(byte)1);}'),
             java('System.exit(124);')),
            ('javascript','console.log("Hello World")',
             "process.stdout.write(require('fs').readFileSync(0))",
             "const t=require('fs').readFileSync(0,'utf8').trim();console.log(t?t.split(/\\s+/).map(Number).reduce((a,b)=>a+b,0):0)",
             'const = ;', 'throw new Error("fixture")', 'while(true){}',
             'process.stdout.write("x".repeat(30000))',
             'const a=[];while(true)a.push(Buffer.alloc(1048576,1))', 'process.exit(124)')]:
            run(language+'_hello_empty_input',language,hello,stdout='Hello World\n')
            run(language+'_stdin_multiple_groups',language,total,'1 2\n3 -4\n',stdout='2\n')
            run(language+'_unicode_binary_input',language,echo,'中文 café\x00z',stdout='中文 café\x00z')
            run(language+'_compile_or_syntax_error',language,invalid,expected='compile_error')
            run(language+'_runtime_exception',language,error,expected='exception',verify=lambda r:'fixture' in r['stderr'])
            run(language+'_timeout',language,loop,expected='timeout')
            run(language+'_output_limit',language,output,expected='output_limit',verify=lambda r:len((r['stdout']+r['stderr']).encode())<=16384)
            run(language+'_cgroup_memory_limit',language,memory,expected='resource_limit',verify=lambda r:r['resource_reason']=='memory')
            run(language+'_nonzero_cannot_forge_timeout',language,exitcode,expected='exception')
            with patch.dict('os.environ', {'ALPHA_TEST_PRIVATE_KEY':'synthetic-only-not-a-real-key'}):
                run(language+'_security',language,JAVA_GUARD if language=='java' else JS_GUARD,stdout='true\n')
            write=java('java.nio.file.Files.writeString(java.nio.file.Path.of("/tmp/marker"),"x");') if language=='java' else "require('fs').writeFileSync('/tmp/marker','x')"
            read=java('System.out.println(java.nio.file.Files.exists(java.nio.file.Path.of("/tmp/marker")));') if language=='java' else "console.log(require('fs').existsSync('/tmp/marker'))"
            run(language+'_temporary_write',language,write,stdout='')
            run(language+'_temporary_not_reused',language,read,stdout='false\n')
            compiled=runtime.execute_phase(language,'compile',source=loop)
            artifact=compiled.pop('artifact',None)
            event=threading.Event();timer=threading.Timer(.7,event.set);timer.start()
            try: result=runtime.execute_phase(language,'run',source=loop,artifact=artifact,cancel_event=event)
            finally: timer.cancel()
            record(language+'_live_cancel',compiled['status']=='completed' and result['status']=='cancelled' and result['cleaned'],result=result,compile_result=compiled)
        run('java_custom_public_name', 'java', java('System.out.println("custom");','Answer'),stdout='custom\n')
        run('java_helpers_and_inner_class', 'java', 'class Helper {static int n(){return 7;}} public class Main {static class Inner{} public static void main(String[]a){System.out.println(Helper.n());}}',stdout='7\n')
        run('java_package_rejected', 'java', 'package example; '+java(''),expected='compile_error',verify=lambda r:'package' in r['stderr'])
        run('java_non_ascii_inner_class_diagnostic', 'java', 'public class Main {static class 内部 {} public static void main(String[]a){}}',expected='compile_error',verify=lambda r:'产物' in r['stderr'])
        run('java_no_main', 'java', 'public class Main {}',expected='exception',verify=lambda r:'main' in r['stderr'])
        run('java_heap_error_not_success', 'java', java('byte[] b=new byte[100000000];System.out.println(b.length);'),expected='exception',verify=lambda r:'OutOfMemoryError' in r['stderr'])
        run('java_stderr', 'java', java('System.out.println("out");System.err.println("err");'),stdout='out\n',verify=lambda r:r['stderr']=='err\n')
        run('javascript_stderr', 'javascript', 'console.log("out");console.error("err")',stdout='out\n',verify=lambda r:r['stderr']=='err\n')
        run('java_runtime_version', 'java', java('System.out.println(System.getProperty("java.runtime.version"));'),stdout='17.0.18+8\n')
        run('node_runtime_version', 'javascript', 'console.log(process.version)',stdout='v22.22.0\n')
        run('java_pid_limit', 'java', java('try {for(int i=0;i<64;i++)new ProcessBuilder("/bin/sleep","5").start();}catch(Throwable e){}'),expected='resource_limit',verify=lambda r:r['resource_reason']=='pids')
        run('javascript_pid_limit', 'javascript', "const cp=require('child_process');let failed=false;for(let i=0;i<64;i++){const p=cp.spawn('/bin/sleep',['5'],{stdio:'ignore'});p.on('error',()=>{if(!failed){failed=true;process.exit(0)}})}",expected='resource_limit',verify=lambda r:r['resource_reason']=='pids')
        for language in ('java','javascript'):
            with patch.object(runtime,'preflight',side_effect=SandboxUnavailable('Docker unavailable fixture')):
                result=execution.run(language,'code','')
            record(language+'_docker_unavailable_fixture',result['status']=='unavailable' and not result['executed'],fixture=True,result=result)

        # Real API writes only to an isolated temporary learner database.
        with tempfile.TemporaryDirectory(prefix='alpha-round35-') as folder:
            with TestClient(create_app(folder,execution=execution)) as client:
                task=client.post('/api/tasks',json={'title':'round35 evidence','language':'java','code':java('System.out.println(1);')}).json()
                url='/api/tasks/'+task['id']; results=[]; versions=[]
                for i,(language,source,expected) in enumerate([
                    ('java',java('System.out.println(1);'),'1\n'),
                    ('java',java('System.out.println(2);'),'2\n'),
                    ('javascript',java('System.out.println(2);'),None),
                    ('javascript','console.log(3)','3\n'),
                    ('javascript','throw new Error("failed")',None)]):
                    if i:
                        response=client.put(url,json={'title':task['title'],'revision':task['revision'],'language':language,'code':source})
                        assert response.status_code==200,response.text
                        task=response.json()
                    version=task['current_version_id'];versions.append(version)
                    response=client.post(url+'/run',json={'version_id':version,'stdin':'','expected_output':expected,'request_id':'case-'+str(i)})
                    assert response.status_code==200,response.text
                    item=response.json();results.append(item)
                    record('api_version_evidence_'+str(i),item['version_id']==version and item['result']['language']==language and item['result']['source_sha256']==hashlib.sha256(source.encode()).hexdigest() and (item['result'].get('matches_expected') is True if expected else item['result']['status']==('compile_error' if i==2 else 'exception')),evidence=item)
                historical=client.get(url).json()
                record('api_immutable_old_evidence',len(set(versions))==5 and len(historical['evidence'])==5 and {e['id']:e for e in historical['evidence']}=={e['id']:e for e in results},evidence=historical['evidence'])
            with TestClient(create_app(folder,execution=execution)) as restarted:
                record('api_reopened_data',restarted.get(url).json()==historical)

        # Run the unmodified previous real suite on this new image/root.
        from .live_execution import accept as legacy_accept
        legacy_path=evidence.with_name(evidence.stem+'-legacy.json')
        if legacy_path.exists(): raise ValueError('Legacy receipt path already exists')
        legacy=legacy_accept(root,legacy_path)
        save(receipt, {'accepted':False,'reason':'finishing five-language acceptance'})
        report['legacy']=legacy
        record('legacy_three_language_suite',legacy['accepted'],evidence=str(legacy_path),cases=legacy['cases'])
        remaining=runtime.command(['ps','--all','--filter','label=aic.sandbox='+runtime.scope,'--format','{{.Names}}'])
        record('all_containers_cleaned',not remaining,remaining=remaining.splitlines())
        report['accepted']=all(case['passed'] for case in report['cases'])
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        save(evidence,report)
        if not report['accepted']: raise SandboxUnavailable('Five-language acceptance has failures; see receipt.')
        save(receipt,{'accepted':True,'suite':'alpha-five-language-v1','image_id':runtime.image_id,'policy_sha256':policy_hash(),
            'fingerprint':runtime.preflight(),'languages':list(execution.status()['languages']),
            'evidence':str(evidence),'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
            'accepted_at':report['completed_at'],'scope':'trusted local single user; Docker shared kernel'})
        return {'accepted':True,'cases':len(report['cases']),'legacy_cases':legacy['cases'],'evidence':str(evidence)}
    except Exception as exc:
        report.update(accepted=False,error=str(exc));save(evidence,report)
        save(receipt,{'accepted':False,'reason':str(exc)})
        raise
