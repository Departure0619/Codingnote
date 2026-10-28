"""Local Alpha HTTP boundary. No legacy .env or learner data is loaded."""
from pathlib import Path
import hashlib
import json
import secrets
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from . import models
from .store import Store, Conflict, Missing, uid, now, encode, digest
from .tutor import Tutor
from .public_records import public_records
from web_lab.ai_provider import AIUnavailable

RETEST_CHALLENGE_TTL=300


def _review_snapshot_digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()


def create_app(root, *, execution=None, tutor=None):
    app = FastAPI(title='AI 编程错题学习工作台 Alpha', docs_url=None, redoc_url=None)
    store = Store(root)
    from . import teaching_records
    teaching_records.install(store)
    if execution is None:
        from .runner import Execution
        execution = Execution(root)
    tutor = tutor or Tutor()
    if hasattr(tutor, 'attach'):
        tutor.attach(store)
    # Serialized run/chat submissions prevent duplicate external work. DB transactions
    # are short; network or Docker work never holds a SQLite write lock.
    operation_lock = threading.Lock()
    cancellation_lock = threading.Lock()
    cancellations = {}
    chat_cancellations = {}
    retest_challenges = {}
    retest_challenge_lock = threading.Lock()
    app.state.store, app.state.tutor, app.state.execution = store, tutor, execution
    from .integration_api import install
    bank = install(app, store, execution, operation_lock, cancellation_lock, cancellations)
    from .importing import install as install_import
    install_import(app,store)

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        host = request.headers.get('host', '').split(':')[0]
        if host not in {'127.0.0.1', 'localhost', 'testserver'}:
            return JSONResponse({'detail': '仅允许本机访问。'}, status_code=403)
        origin = request.headers.get('origin')
        if request.method not in {'GET', 'HEAD'} and origin:
            if urlsplit(origin).netloc != request.headers.get('host') or urlsplit(origin).scheme != 'http':
                return JSONResponse({'detail': '不接受其他网站发起的修改请求。'}, status_code=403)
        if int(request.headers.get('content-length', '0') or 0) > (7100000 if request.url.path=='/api/import/ocr' else 1500000):
            return JSONResponse({'detail': '内容过大，请缩短题目或代码。'}, status_code=413)
        if request.method not in {'GET','HEAD'} and request.url.path.startswith('/api/') and request.url.path not in {'/api/ai','/api/ai/retest/approve','/api/import/ocr'} and hasattr(tutor,'check_context'):
            try:
                submitted=await request.json()
            except (ValueError,UnicodeError):
                submitted=None
            try:
                tutor.check_context({},submitted)
            except ValueError:
                return JSONResponse({'detail':'填写内容包含当前AI凭据，请移除后再保存。'},status_code=422)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def database_failure(request, exc):
        return JSONResponse({'detail': '数据库暂时无法读写。本次操作未确认保存，请保留编辑内容，检查磁盘空间或稍后重试。'}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        fields = ', '.join(dict.fromkeys(str(e['loc'][-1]) for e in exc.errors()))
        return JSONResponse({'detail': '填写内容无效或超出长度限制，请检查：' + fields + '。'}, status_code=422)

    @app.get('/api/status')
    def status():
        return {'product': 'Alpha', 'integration_phase': 1, 'schema_version': 3,
                'execution': execution.status(), 'ai': tutor.status(), 'data_dir': str(store.root.resolve())}

    @app.get('/api/tasks')
    def tasks():
        return store.list_tasks()

    @app.post('/api/tasks')
    def create(value: models.Task):
        recognition=prepare_recognition(value)
        with store.atomic():
            task = store.save_task(value.model_dump())
            store.set_source(task['id'], value.model_dump(exclude_none=True))
            save_recognition(task['id'],recognition)
            result={**bank.detail(task['id']),**teaching_records.outputs(store,task['id'])}
        return result

    @app.get('/api/tasks/{task_id}')
    def detail(task_id: str):
        return {**bank.detail(task_id),**teaching_records.outputs(store,task_id)}

    @app.put('/api/tasks/{task_id}')
    def save(task_id: str, value: models.SaveTask):
        recognition=prepare_recognition(value,task_id)
        with store.atomic():
            task = store.save_task(value.model_dump(), task_id)
            store.set_source(task_id, value.model_dump(exclude_none=True))
            save_recognition(task_id,recognition)
            result={**bank.detail(task['id']),**teaching_records.outputs(store,task['id'])}
        return result

    def prepare_recognition(value,task_id=None):
        from .importing import recognize_source
        previous=teaching_records.outputs(store,task_id)['source_recognition'] if task_id else None
        if value.source_candidate is None and previous is None: return None
        candidate=recognize_source(value.source_url,value.statement)
        old_source=store.source(task_id) if task_id else {}
        platform=value.source_platform if value.source_platform is not None else (old_source or {}).get('source_platform',candidate['platform'])
        original=value.original_id if value.original_id is not None else (old_source or {}).get('original_id',candidate['original_id'])
        candidate.update(platform=platform,original_id=original)
        unchanged=previous and all(previous.get(k)==candidate.get(k) for k in ('url','platform','original_id'))
        confirmed=bool(value.source_confirmed if value.source_candidate is not None else unchanged and previous['confirmed'])
        candidate.update(confirmed=confirmed,confirmation_source='user' if confirmed else 'unconfirmed')
        return candidate

    def save_recognition(task_id,candidate):
        if candidate is not None:
            with store.connect() as db:
                db.execute('INSERT INTO source_recognition VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET value=excluded.value',(task_id,encode(candidate)))

    @app.post('/api/tasks/{task_id}/run')
    def run(task_id: str, value: models.Run):
        signature = digest(encode(value.model_dump(exclude={'request_id'})))
        with operation_lock:
            prior = store.prior_request('evidence', task_id, value.request_id, signature)
            if prior:
                store.detail(task_id)
                if prior['result'].get('status') == 'running':
                    raise Conflict('上次执行结果尚未确认，不会重复执行。请查看历史或稍后手动发起新的运行。')
                return public_records(prior)
            task, version = store.version(task_id, value.version_id, current=True)
            if not version['source'].strip():
                raise HTTPException(422, '代码为空，请先写入并保存代码。')
            pending = store.add_evidence(task_id, version, 'local', '本机 Docker 隔离运行', value.stdin,
                value.expected_output, {'status': 'running', 'executed': False, 'stdout': '', 'stderr': ''}, value.request_id, signature)
            cancel_event = threading.Event()
            with cancellation_lock:
                cancellations[(task_id, value.request_id)] = cancel_event
            try:
                options = {'cancel_event': cancel_event} if getattr(execution, 'supports_cancellation', False) else {}
                result = execution.run(version['language'], version['source'], value.stdin, **options)
            except Exception:
                result = {'status': 'service_error', 'executed': False, 'execution_unknown': True,
                          'stdout': '', 'stderr': '', 'error': '执行服务意外中断，无法确认结果；没有回退到主机执行。'}
            finally:
                with cancellation_lock:
                    cancellations.pop((task_id, value.request_id), None)
            if value.expected_output is not None and result.get('executed') and result['status'] == 'completed':
                result['matches_expected'] = result['stdout'].replace('\r\n', '\n').rstrip('\n') == value.expected_output.replace('\r\n', '\n').rstrip('\n')
            return store.finish_evidence(pending, result)

    @app.post('/api/tasks/{task_id}/run/{request_id}/cancel')
    def cancel_run(task_id: str, request_id: str):
        # Deliberately outside operation_lock: cancellation must interrupt work.
        with cancellation_lock:
            event = cancellations.get((task_id, request_id))
            if event is None or not getattr(execution, 'supports_cancellation', False):
                raise Conflict('该运行尚未开始或已经结束，请查看执行记录。')
            event.set()
        return {'requested': True, 'message': '已请求停止；清理完成后会保存最终结果。'}

    @app.post('/api/tasks/{task_id}/external')
    def external(task_id: str, value: models.External):
        task, version = store.version(task_id, value.version_id)
        return store.add_evidence(task_id, version, 'external', value.source, '', None,
            {'status': value.verdict, 'notes': value.notes, 'executed': False, 'provenance': '用户录入的外部判题，未经本地核验'}, uid(), '')

    @app.post('/api/tasks/{task_id}/chat')
    def chat(task_id: str, value: models.Chat):
        signature = digest(encode(value.model_dump(exclude={'request_id'})))
        with operation_lock:
            prior = store.prior_request('turns', task_id, value.request_id, signature)
            if prior:
                store.detail(task_id)
                if prior['status'] == 'running':
                    raise Conflict('上次辅导结果尚未确认，不会重复调用。请查看历史后决定是否另发问题。')
                if prior['status'] == 'failed':
                    raise HTTPException(503, prior['error'])
                return public_records(prior)
            task, version = store.version(task_id, value.version_id, current=True)
            from .teaching_protocol import context_for
            context = context_for(store,task,version,value)
            if hasattr(tutor,'check_context'):
                tutor.check_context(context,value.message)
            item = {'id': uid(), 'task_id': task_id, 'version_id': version['id'], 'source_sha256': version['source_sha256'],
                    'message': value.message, 'answer': '', 'provider': tutor.status()['mode'], 'status': 'running',
                    'error': None, 'context': context, 'suggested_tags': [], 'request_id': value.request_id,
                    'signature': signature, 'created_at': now()}
            store.add_turn(item)
            event=threading.Event()
            with cancellation_lock:
                chat_cancellations[(task_id,value.request_id)]=event
            try:
                options={'cancel_event':event} if isinstance(tutor,Tutor) else {}
                item.update(tutor.reply(public_records(context), value.message,**options))
                item['status'] = 'completed'
            except AIUnavailable as exc:
                item['error'] = str(exc)
                item['status'] = 'failed'
                if getattr(exc,'unverified_reading',None):
                    item['unverified_reading']=exc.unverified_reading
            finally:
                with cancellation_lock:
                    chat_cancellations.pop((task_id,value.request_id),None)
            teaching_records.finish_turn(store,item)
            if item['error']:
                return JSONResponse({'detail':item['error'],'id':item['id'],'status':'failed'},status_code=503)
            return public_records(item)

    @app.post('/api/tasks/{task_id}/chat/{request_id}/cancel')
    def cancel_chat(task_id: str,request_id: str):
        with cancellation_lock:
            event=chat_cancellations.get((task_id,request_id))
            if event is None: raise Conflict('该辅导尚未开始或已经结束，请查看历史。')
            event.set()
        return {'requested':True,'message':'已请求取消；已发送的上游请求可能计费，用量未知时保留费用预留。'}

    @app.get('/api/ai/usage')
    def ai_usage(): return teaching_records.usage(store)

    @app.post('/api/teaching/{turn_id}/decision')
    def teaching_decision(turn_id: str,value: models.TeachingDecision):
        return teaching_records.decide(store,turn_id,value)

    @app.put('/api/tasks/{task_id}/mistake')
    def mistake(task_id: str, value: models.Mistake):
        return store.save_mistake(task_id, value.model_dump())

    @app.get('/api/mistakes')
    def mistakes(q: str = '', language: str = '', tag: str = '', source: str = '', primary: str = '',
                 secondary: str = '', reason: str = '', since: str = '', until: str = '', review: str = '',
                 confirmation: str = '', sort: str = 'recent'):
        return store.mistakes(q, language, tag, source, primary, secondary, reason, since, until, review, confirmation, sort)

    @app.post('/api/mistakes/{mid}/reviews')
    def review(mid: str, value: models.Review):
        return store.review(mid, value.outcome, value.notes)

    @app.get('/api/recommendations')
    def recommendations():
        return store.recommendations()

    @app.get('/api/learning/recommendations')
    def learning_recommendations(task_id: str = ''):
        from .learning_recommendations import build
        return build(store,bank,task_id)

    @app.patch('/api/recommendations/{rid}')
    def recommendation_status(rid: str, value: models.RecommendationStatus):
        return store.recommendation_status(rid, value.status)

    @app.get('/api/settings')
    def settings():
        return store.settings()

    @app.put('/api/settings')
    def set_settings(value: models.Settings):
        return store.settings(value.model_dump())

    @app.put('/api/ai')
    def ai(value: models.AISettings):
        try:
            if value.approved_requests and (not value.approve_paid_calls or value.mode != 'deepseek' or not value.api_key.strip()):
                raise ValueError('启用真实请求需要 API Key，并由你单独明确同意可能产生的费用。')
            return tutor.configure(value.mode, value.api_key, value.approved_requests,
                                   model=value.model,budget_usd=value.budget_usd,timeout=value.timeout)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get('/api/ai/retest')
    def retest_review():
        if not isinstance(tutor,Tutor):
            return {'eligible':False,'reason':'当前辅导适配器不支持受控批准。'}
        review=tutor.retest_review()
        if review.get('eligible'):
            challenge_id=secrets.token_hex(16);token=secrets.token_urlsafe(32)
            expires=time.monotonic()+RETEST_CHALLENGE_TTL
            challenge={'token':token,'expires':expires,'review_fingerprint':review['review_fingerprint'],
                'snapshot_digest':_review_snapshot_digest(tutor.round_budget.snapshot())}
            with retest_challenge_lock:
                for cid in list(retest_challenges):
                    if retest_challenges[cid]['expires']<=time.monotonic():retest_challenges.pop(cid,None)
                if len(retest_challenges)>=16:retest_challenges.pop(next(iter(retest_challenges)))
                retest_challenges[challenge_id]=challenge
            review['challenge']={'id':challenge_id,'token':token,'review_fingerprint':review['review_fingerprint'],
                'expires_at':time.time()+RETEST_CHALLENGE_TTL}
        return review

    @app.post('/api/ai/retest/approve')
    def approve_retest(request:Request,value:models.RetestApproval):
        host=request.headers.get('host','')
        if (request.headers.get('origin')!='http://'+host or
            request.headers.get('sec-fetch-site')!='same-origin' or request.headers.get('sec-fetch-mode')!='cors'):
            raise HTTPException(403,'请从本机设置页主动确认受控复验，不接受后台或跨站批准。')
        if value.confirm_scope is not True or value.approve_paid_calls is not True:
            raise HTTPException(422,'必须主动确认复验范围、第三方发送及费用；默认不批准。')
        if not value.api_key.strip() or '\r' in value.api_key or '\n' in value.api_key:
            raise HTTPException(422,'请在本机密码框填写Key。')
        with retest_challenge_lock:challenge=retest_challenges.pop(value.challenge_id,None)
        if not challenge or challenge['expires']<=time.monotonic():
            raise HTTPException(403,'本次确认已过期或已经使用，请刷新设置后重新核对。')
        if not secrets.compare_digest(challenge['token'],value.confirmation_token):
            raise HTTPException(403,'确认凭据无效，本次未批准。')
        review=tutor.retest_review()
        if (not review.get('eligible') or value.review_fingerprint!=review.get('review_fingerprint') or
            challenge['review_fingerprint']!=review.get('review_fingerprint') or
            challenge['snapshot_digest']!=_review_snapshot_digest(tutor.round_budget.snapshot())):
            raise HTTPException(409,'审查修订或账本状态已变化，未批准；请重新核对设置。')
        approval={'source':'local_settings_confirmation','confirmed':True,'challenge_id':value.challenge_id,
                  'approved_at':now(),'review_fingerprint':review['review_fingerprint']}
        try:
            scope=tutor._review_scope()
            if scope.get('review_fingerprint')!=approval['review_fingerprint']:
                raise HTTPException(409,'确认过程中修订发生变化，未批准；请重新核对设置。')
            return tutor.approve_retest(scope,approval,value.api_key)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None

    @app.get('/api/backup')
    def backup():
        tmp = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
        path = Path(tmp.name)
        tmp.close()
        try:
            store.backup(path, public_export=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return FileResponse(path, filename='alpha-learning-export.sqlite3', media_type='application/octet-stream',
                            headers={'X-Alpha-Export': 'redacted-learning-records-not-a-restorable-backup'},
                            background=BackgroundTask(path.unlink, missing_ok=True))

    static = Path(__file__).with_name('static')
    if static.exists():
        app.mount('/', StaticFiles(directory=static, html=True), name='ui')
    return app
