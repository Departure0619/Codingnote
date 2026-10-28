"""Additive feature tables in the existing learner SQLite; no credentials."""
import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from .store import now, uid, encode, row, digest, Conflict, Missing
from .migrations import backup_database, restore_backup, inspect_database

SCHEMA='''
CREATE TABLE ai_authorizations(id TEXT PRIMARY KEY,model TEXT NOT NULL,max_requests INTEGER NOT NULL,budget_usd REAL NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE ai_calls(id TEXT PRIMARY KEY,authorization_id TEXT NOT NULL REFERENCES ai_authorizations(id),transport TEXT NOT NULL,model TEXT NOT NULL,state TEXT NOT NULL,reserved_usd REAL NOT NULL,usage_json TEXT,cost_usd REAL,error_code TEXT,created_at TEXT NOT NULL);
CREATE TABLE teaching_outputs(turn_id TEXT PRIMARY KEY REFERENCES turns(id),kind TEXT NOT NULL,payload TEXT NOT NULL,checks TEXT NOT NULL,status TEXT NOT NULL,saved_id TEXT,decided_at TEXT);
CREATE TABLE summary_versions(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),version_id TEXT NOT NULL REFERENCES versions(id),turn_id TEXT REFERENCES turns(id),text TEXT NOT NULL,evidence_ids TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE source_recognition(task_id TEXT PRIMARY KEY REFERENCES tasks(id),value TEXT NOT NULL);
CREATE TABLE feature_versions(name TEXT PRIMARY KEY,version INTEGER NOT NULL);
INSERT INTO feature_versions VALUES('teaching_import',1);
'''

READING_SCHEMA='''CREATE TABLE teaching_readings(
 turn_id TEXT PRIMARY KEY REFERENCES turns(id),state TEXT NOT NULL CHECK(state='unverified'),
 source TEXT NOT NULL CHECK(source='model_unverified'),text TEXT NOT NULL,
 validation_code TEXT NOT NULL CHECK(validation_code='INCOMPATIBLE_RESPONSE'),
 call_id TEXT NOT NULL REFERENCES ai_calls(id),raw_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)'''


def _apply_readings(path):
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        db.execute(READING_SCHEMA)
        db.execute("INSERT INTO feature_versions VALUES('unverified_readings',1)")
        if list(db.execute('PRAGMA foreign_key_check')):raise ValueError('失败阅读表外键校验失败')


def _install_readings(store):
    with store.connect() as db:
        version=db.execute("SELECT version FROM feature_versions WHERE name='unverified_readings'").fetchone()
        if version:
            if version[0]!=1:raise ValueError('未知失败阅读功能版本。')
            return
        nonempty=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]>0
    if nonempty:
        before=inspect_database(store.path)
        backup=store.root/'migration_backups'/('before-unverified-readings-'+uid()+'.sqlite3')
        backup_database(store.path,backup)
        with tempfile.TemporaryDirectory(prefix='alpha-reading-rehearsal-') as folder:
            trial=Path(folder)/'trial.sqlite3'
            restore_backup(backup,trial);_apply_readings(trial)
            after=inspect_database(trial)
            if any(after['counts'].get(t)!=n+(1 if t=='feature_versions' else 0)
                    for t,n in before['counts'].items()):
                raise ValueError('失败阅读升级演练改变旧数据，未升级。')
    _apply_readings(store.path)


def _apply(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        for sql in SCHEMA.split(';'):
            if sql.strip(): db.execute(sql)
        if list(db.execute('PRAGMA foreign_key_check')): raise ValueError('功能表外键校验失败')


def install(store):
    with store.connect() as db:
        if db.execute("SELECT name FROM sqlite_master WHERE name='feature_versions'").fetchone():
            if db.execute("SELECT version FROM feature_versions WHERE name='teaching_import'").fetchone()[0]!=1:
                raise ValueError('未知教学导入功能版本。')
            db.execute("UPDATE ai_calls SET state='unknown',error_code='SERVICE_INTERRUPTED' WHERE state='running'")
            installed=True
        else:installed=False
        nonempty=db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]>0
    if nonempty and not installed:
        before=inspect_database(store.path)
        backup=store.root/'migration_backups'/('before-teaching-import-'+uid()+'.sqlite3')
        backup_database(store.path,backup)
        with tempfile.TemporaryDirectory(prefix='alpha-feature-rehearsal-') as folder:
            trial=Path(folder)/'trial.sqlite3'
            restore_backup(backup,trial); _apply(trial)
            after=inspect_database(trial)
            if any(after['counts'].get(t)!=n for t,n in before['counts'].items()):
                raise ValueError('功能升级演练改变旧数据，未升级。')
    if not installed:_apply(store.path)
    _install_readings(store)


def usage(store):
    with store.connect() as db:
        calls=[dict(r) for r in db.execute('SELECT * FROM ai_calls ORDER BY created_at')]
        auths=[dict(r) for r in db.execute('SELECT * FROM ai_authorizations ORDER BY created_at')]
    for c in calls: c['usage']=json.loads(c.pop('usage_json')) if c['usage_json'] else None
    return {'real_requests':sum(c['transport']=='real' for c in calls),
            'fixture_requests':sum(c['transport']=='fixture' for c in calls),
            'known_tokens':sum((c['usage'] or {}).get('total_tokens',0) for c in calls),
            'known_cost_usd':sum(c['cost_usd'] or 0 for c in calls),
            'real_known_tokens':sum((c['usage'] or {}).get('total_tokens',0) for c in calls if c['transport']=='real'),
            'fixture_known_tokens':sum((c['usage'] or {}).get('total_tokens',0) for c in calls if c['transport']=='fixture'),
            'real_known_cost_usd':sum(c['cost_usd'] or 0 for c in calls if c['transport']=='real'),
            'fixture_known_cost_usd':sum(c['cost_usd'] or 0 for c in calls if c['transport']=='fixture'),
            'reserved_unknown_cost_usd':sum(c['reserved_usd'] for c in calls if c['cost_usd'] is None),
            'unknown_usage_requests':sum(c['usage'] is None for c in calls),
            'cost_notice':'按核对日峰值费率估算，服务商账单为准。失败/超时/取消/无usage保留预留费用，不视为免费。',
            'calls':calls,'authorizations':auths}


def outputs(store,task_id):
    with store.connect() as db:
        items=[dict(r) for r in db.execute('SELECT o.* FROM teaching_outputs o JOIN turns t ON t.id=o.turn_id WHERE t.task_id=? ORDER BY t.created_at',(task_id,))]
        summaries=[dict(r) for r in db.execute('SELECT * FROM summary_versions WHERE task_id=? ORDER BY created_at',(task_id,))]
        recognition=db.execute('SELECT value FROM source_recognition WHERE task_id=?',(task_id,)).fetchone()
        readings=[dict(r) for r in db.execute('''SELECT r.* FROM teaching_readings r JOIN turns t ON t.id=r.turn_id
            WHERE t.task_id=? AND t.status='failed' ORDER BY t.created_at''',(task_id,))]
    for item in items:
        item['payload']=json.loads(item['payload']); item['checks']=json.loads(item['checks'])
    for s in summaries: s['evidence_ids']=json.loads(s['evidence_ids'])
    return {'teaching_outputs':items,'summary_versions':summaries,'teaching_readings':readings,
            'source_recognition':json.loads(recognition[0]) if recognition else None}


def save_output(store,item):
    if item.get('structured') is None: return
    with store.connect() as db:
        _save_output(db,item)


def _save_output(db,item):
    if item.get('structured') is None: return
    values=(item['id'],item['context']['purpose'],encode(item['structured']),encode(item['fact_checks']))
    previous=db.execute('SELECT turn_id,kind,payload,checks FROM teaching_outputs WHERE turn_id=?',(item['id'],)).fetchone()
    if previous:
        if tuple(previous)!=values: raise Conflict('已保存的教学草稿内容不同，未覆盖。')
        return
    db.execute('INSERT INTO teaching_outputs VALUES(?,?,?,?,?,?,?)',(*values,'draft',None,None))


def finish_turn(store,item):
    """Persist a reply and its structured draft in one transaction; never repeat AI."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT * FROM turns WHERE id=?',(item['id'],)).fetchone()
        if not previous: raise Missing('辅导记录不存在。')
        values=(item['answer'],item['provider'],item['status'],item['error'],encode(item['suggested_tags']))
        if previous['status']!='running':
            if tuple(previous[k] for k in ('answer','provider','status','error','suggested_tags'))!=values:
                raise Conflict('辅导结果已保存，未覆盖既有记录。')
        else:
            db.execute('UPDATE turns SET answer=?,provider=?,status=?,error=?,suggested_tags=? WHERE id=?',(*values,item['id']))
        if item['status']=='completed': _save_output(db,item)
        if item.get('unverified_reading'):
            _save_reading(db,store,item)


def _save_reading(db,store,item):
    reading=item['unverified_reading']
    if item['status']!='failed' or item['answer'] or not item['error']:
        raise Conflict('未验证解释只能关联失败记录。')
    if (reading.get('state')!='unverified' or reading.get('source')!='model_unverified'
            or reading.get('validation_code')!='INCOMPATIBLE_RESPONSE'
            or not isinstance(reading.get('text'),str) or not reading['text'].strip() or len(reading['text'])>30000
            or not isinstance(reading.get('call_id'),str) or not re.fullmatch('[0-9a-f]{32}',reading['call_id'])):
        raise Conflict('未验证解释记录无效，未保存。')
    raw=(store.root/'ai_raw'/(reading['call_id']+'.json')).read_bytes()
    call=db.execute('SELECT state,error_code FROM ai_calls WHERE id=?',(reading['call_id'],)).fetchone()
    if (hashlib.sha256(raw).hexdigest()!=reading.get('raw_sha256') or not call
            or call['state']!='failed' or call['error_code']!='INCOMPATIBLE_RESPONSE'):
        raise Conflict('原始回复或失败调用校验不符，未保存解释。')
    values=(item['id'],*[reading[k] for k in ('state','source','text','validation_code','call_id','raw_sha256')])
    previous=db.execute('SELECT * FROM teaching_readings WHERE turn_id=?',(item['id'],)).fetchone()
    if previous:
        if tuple(previous)[:7]!=values:raise Conflict('未验证解释已保存，未覆盖。')
        return
    db.execute('INSERT INTO teaching_readings VALUES(?,?,?,?,?,?,?,?)',(*values,now()))


def decide(store,turn_id,decision):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        draft=db.execute('SELECT * FROM teaching_outputs WHERE turn_id=?',(turn_id,)).fetchone()
        turn=db.execute('SELECT * FROM turns WHERE id=?',(turn_id,)).fetchone()
        if turn and turn['status']=='failed':
            raise Conflict('这次回复校验失败，仅可阅读未验证文字，不能确认或保存为教学草稿。')
        if not draft or not turn: raise Missing('教学草稿不存在。')
        if draft['status']!='draft':
            if draft['status']==decision.action: return dict(draft)
            raise Conflict('草稿已处理；原决策保留。需要新分析时请主动重新生成。')
        payload=json.loads(draft['payload']); checks=json.loads(draft['checks'])
        if checks['conflicts'] and decision.action in {'accept','modified'}:
            raise Conflict('结构化事实与执行记录冲突，请拒绝或标记尚未确定，再核对原始证据。')
        if decision.action in {'accept','modified','uncertain'}:
            _check_saved_binding(db,turn,payload)
        sid=None; stamp=now()
        if decision.action in {'accept','modified','uncertain'} and draft['kind']=='analyze':
            sid=uid(); status={'accept':'user_confirmed','modified':'user_modified','uncertain':'uncertain'}[decision.action]
            reason=decision.text if decision.text is not None else payload['mechanism']
            observed=decision.observed if decision.observed is not None else payload['observed']
            if not reason.strip() or not observed.strip(): raise ValueError('原因和实际表现不能为空。')
            eid=next(iter(payload['evidence_refs']),None)
            mistake=db.execute('SELECT id FROM mistakes WHERE task_id=?',(turn['task_id'],)).fetchone()
            fields=(turn['version_id'],eid,observed,','.join(payload['knowledge'])[:80],reason,status)
            db.execute('INSERT INTO error_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(sid,turn['task_id'],mistake[0] if mistake else None,*fields,1,stamp,stamp))
            db.execute('INSERT INTO error_revisions VALUES(?,?,?,?,?,?,?,?,?,?)',(uid(),sid,1,*fields,stamp))
        if decision.action in {'accept','modified'} and draft['kind']=='summary':
            text=decision.text if decision.text is not None else '\n\n'.join(s['heading']+'\n'+s['text'] for s in payload['sections'])
            if not text.strip() or len(text)>10000: raise ValueError('总结须为1～10000字。')
            old=db.execute('SELECT * FROM mistakes WHERE task_id=?',(turn['task_id'],)).fetchone()
            latest=db.execute('SELECT text FROM summary_versions WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(turn['task_id'],)).fetchone()
            if old and old['summary'] and (not latest or latest['text']!=old['summary']):
                db.execute('INSERT INTO summary_versions VALUES(?,?,?,?,?,?,?)',(uid(),turn['task_id'],old['original_version_id'],None,old['summary'],'[]',stamp))
            sid=uid()
            db.execute('INSERT INTO summary_versions VALUES(?,?,?,?,?,?,?)',(sid,turn['task_id'],turn['version_id'],turn_id,text,encode(payload['evidence_refs']),stamp))
            mid=old['id'] if old else uid()
            db.execute('''INSERT INTO mistakes VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                summary=excluded.summary,updated_at=excluded.updated_at''',
                (mid,turn['task_id'],turn['version_id'],None,text,'[]',False,True,stamp,stamp))
            db.execute('UPDATE error_records SET mistake_id=? WHERE task_id=? AND mistake_id IS NULL',(mid,turn['task_id']))
        db.execute('UPDATE teaching_outputs SET status=?,saved_id=?,decided_at=? WHERE turn_id=?',(decision.action,sid,stamp,turn_id))
        return dict(db.execute('SELECT * FROM teaching_outputs WHERE turn_id=?',(turn_id,)).fetchone())


def _check_saved_binding(db,turn,payload):
    """A decision never turns a stale, foreign or hidden reference into evidence."""
    version=row(db.execute('SELECT * FROM versions WHERE id=?',(turn['version_id'],)).fetchone())
    context=json.loads(turn['context'])
    if (turn['status']!='completed' or not version or version['task_id']!=turn['task_id']
            or version['source_sha256']!=turn['source_sha256'] or digest(version['source'])!=turn['source_sha256']
            or context.get('version')!=version or context.get('task',{}).get('id')!=turn['task_id']):
        raise Conflict('教学草稿的代码版本不匹配，未保存。')
    shared={e['id']:e for e in context.get('evidence',[])}
    refs=payload.get('evidence_refs',[])
    if not isinstance(refs,list) or any(type(eid) is not str for eid in refs):
        raise Conflict('教学草稿的证据引用格式不正确，未保存。')
    selected={}
    for eid in refs:
        evidence=row(db.execute('SELECT * FROM evidence WHERE id=?',(eid,)).fetchone())
        if (not evidence or evidence['task_id']!=turn['task_id'] or evidence['version_id']!=turn['version_id']
                or evidence['source_sha256']!=turn['source_sha256'] or evidence['result'].get('hidden')
                or evidence!=shared.get(eid)):
            raise Conflict('教学草稿引用了未共享、其他版本或隐藏证据，未保存。')
        selected[eid]=evidence
    for fact in payload.get('facts',[]):
        evidence=selected.get(fact.get('evidence_id'))
        actual=evidence['result'].get(fact.get('field')) if evidence else None
        if actual is None or type(actual)!=type(fact.get('value')) or actual!=fact['value']:
            raise Conflict('教学草稿的事实与实际执行证据不符，未保存。')
