"""Durable version-bound tasks, separate from frozen F03/F04 teaching evidence."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
import time

from .sandbox_contract import contract
from .sandbox_runtime import DockerRuntime
from .store import decode, uid, utc
from . import provenance


def public_job(job):
    value=dict(job)
    if value.get('mode')=='submit':
        value.pop('inputs',None)
    results=value.get('results') or []
    hidden=[r for r in results if not r.get('visible',True)]
    value['results']=[r for r in results if r.get('visible',True)]
    value['hidden_summary']={'total':len(hidden),'passed':sum(r['status']=='passed' for r in hidden)}
    return value


class SandboxService:
    def __init__(self,store):
        self.store=store;self.runtime=DockerRuntime(store.root)
        self.runtime.recover()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='aic-sandbox')
        self.slots=threading.BoundedSemaphore(8)
        with self.store.connection(write=True) as db:
            rows=db.execute("SELECT * FROM sandbox_jobs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                self.store.update(db,'sandbox_jobs','task_id',row['task_id'],
                    {'status':'service_error','completed_at':utc(),'error':'服务重启，旧任务未确认完成；请建立新任务'})
                self.store.event(db,row['session_id'],'sandbox_interrupted',{'task_id':row['task_id']})

    def close(self):self.pool.shutdown(wait=True)
    def status(self):return self.runtime.status()

    def jobs(self,eid):
        with self.store.connection() as db:
            self.store.experiment(db,eid)
            return [public_job(decode(r)) for r in db.execute('SELECT * FROM sandbox_jobs WHERE session_id=? ORDER BY created_at,rowid',(eid,))]

    def get(self,eid,task_id):
        with self.store.connection() as db:
            row=decode(db.execute('SELECT * FROM sandbox_jobs WHERE session_id=? AND task_id=?',(eid,task_id)).fetchone())
        if not row:raise KeyError('运行记录不属于当前会话或不存在')
        return public_job(row)

    def submit(self,eid,version_id,source_sha256,request_id,mode,n=None,input_origin=None):
        from .service import Conflict
        acquired=False
        try:
            with self.store.connection(write=True) as db:
                e=self.store.experiment(db,eid);c=contract(e)
                v=next((v for v in e['code_versions'] if v['version_id']==version_id),None)
                if v is None:raise ValueError('源码版本不属于当前会话')
                actual=hashlib.sha256(v['source'].encode('utf8')).hexdigest()
                if actual!=v['source_sha256'] or actual!=source_sha256:
                    raise Conflict('源码哈希与保存版本不匹配；请刷新后提交')
                inputs=[n] if mode=='run' else [t['input'] for t in c['tests']]
                if mode=='run' and (type(n) is not int or not 0<=n<=8):raise ValueError('输入必须是 0～8 的整数，布尔值不合法')
                old=decode(db.execute('SELECT * FROM sandbox_jobs WHERE session_id=? AND request_id=?',(eid,request_id)).fetchone())
                if old:
                    if any(old[k]!=value for k,value in {'version_id':version_id,'source_sha256':actual,'mode':mode,'inputs':inputs,'contract_sha256':c['sha256']}.items()):
                        raise Conflict('请求 ID 已绑定其他版本、输入或测试合同；请建立新运行')
                    return public_job(old)
                ready=self.status()['ready']
                if ready:
                    acquired=self.slots.acquire(blocking=False)
                    if not acquired:raise Conflict('执行队列已满，请等待当前任务完成')
                row={'task_id':uid(),'session_id':eid,'version_id':version_id,'source_sha256':actual,
                     'request_id':request_id,'mode':mode,'inputs':inputs,'contract_sha256':c['sha256'],
                     'contract_id':c['id'],'created_at':utc(),'completed_at':None,
                     'status':'queued' if ready else 'unavailable','executed':False,
                     'results':[],'elapsed_seconds':0,'error':None if ready else self.status()['reason']}
                if not ready:row['completed_at']=utc()
                self.store.insert(db,'sandbox_jobs',row)
                self.store.event(db,eid,'sandbox_task_saved',{'task_id':row['task_id'],'version_id':version_id,
                    'source_sha256':actual,'mode':mode,'input_origin':provenance.record(input_origin,e)})
            if ready:
                try:self.pool.submit(self.run,row['task_id'],v['source'],c)
                except RuntimeError:
                    with self.store.connection(write=True) as db:
                        self.store.update(db,'sandbox_jobs','task_id',row['task_id'],{'status':'service_error','error':'执行队列不可用','completed_at':utc()})
                else:
                    acquired=False
            return self.get(eid,row['task_id'])
        finally:
            if acquired:self.slots.release()

    def run(self,task_id,source,c):
        started=time.perf_counter();results=[];executed=False;status='service_error';error=None;job=None
        try:
            with self.store.connection(write=True) as db:
                job=decode(db.execute('SELECT * FROM sandbox_jobs WHERE task_id=?',(task_id,)).fetchone())
                self.store.update(db,'sandbox_jobs','task_id',task_id,{'status':'running'})
            if hashlib.sha256(source.encode()).hexdigest()!=job['source_sha256']:
                raise ValueError('source snapshot mismatch')
            tests=[{'input':job['inputs'][0],'visible':True}] if job['mode']=='run' else c['tests']
            for test in tests:
                result=self.runtime.execute(source,test['input'])
                if result['status']=='completed' and (not result.get('executed') or not result.get('cleaned')):
                    result.update(status='service_error',error='执行或清理未确认，不能获得成功结果')
                executed=executed or result['executed']
                result.update(input=test['input'],visible=test['visible'])
                if job['mode']=='submit':
                    result['expected_output']=test['expected_output']
                    if result['status']=='completed':
                        result['status']='passed' if result['stdout'].replace('\r\n','\n')==test['expected_output'] else 'failed'
                else:
                    # Run records preserve a reference when this input is already
                    # public and reviewed. Other legal inputs have no judge claim.
                    result['expected_output']=next((t['expected_output'] for t in c['tests']
                        if t['visible'] and t['input']==test['input']),None)
                    result['reference_scope']='reviewed_public_test' if result['expected_output'] is not None else 'no_reviewed_reference'
                results.append(result)
                if result['status'] in {'service_error','unavailable','timeout','output_limit','resource_limit','exception'}:
                    break
            failures=[r for r in results if r['status'] not in {'completed','passed'}]
            failure=next((r for r in failures if r['status']!='failed'),failures[0] if failures else None)
            status=failure['status'] if failure else 'passed' if job['mode']=='submit' and len(results)==len(tests) else 'completed' if job['mode']=='run' else 'service_error'
            error=failure.get('error') if failure else None
        except Exception:
            status='service_error';error='执行服务异常；没有生成成功结果'
        finally:
            try:
                with self.store.connection(write=True) as db:
                    self.store.update(db,'sandbox_jobs','task_id',task_id,{'status':status,'results':results,
                        'executed':executed,'elapsed_seconds':round(time.perf_counter()-started,4),
                        'completed_at':utc(),'error':error})
                    if job:
                        self.store.event(db,job['session_id'],'sandbox_task_finished',{'task_id':task_id,
                            'version_id':job['version_id'],'source_sha256':job['source_sha256'],'status':status})
            finally:self.slots.release()
