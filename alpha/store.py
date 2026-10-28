"""Transactional product records; source and evidence are append-only."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from .integration_store import IntegrationStore
from .migrations import upgrade


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(source):
    return hashlib.sha256(source.encode('utf8')).hexdigest()


class Conflict(ValueError):
    pass


class Missing(ValueError):
    pass


DEFAULTS = {'help_level': 'step', 'review_intensity': 'standard', 'proactive': True}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, statement TEXT NOT NULL, source_url TEXT NOT NULL,
 revision INTEGER NOT NULL, current_version_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), number INTEGER NOT NULL,
 language TEXT NOT NULL, source TEXT NOT NULL, source_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,number));
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), version_id TEXT NOT NULL REFERENCES versions(id),
 source_sha256 TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, stdin TEXT NOT NULL,
 expected_output TEXT, result TEXT NOT NULL, request_id TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,request_id));
CREATE TABLE IF NOT EXISTS turns (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), version_id TEXT NOT NULL REFERENCES versions(id),
 source_sha256 TEXT NOT NULL, message TEXT NOT NULL, answer TEXT NOT NULL, provider TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT, context TEXT NOT NULL, suggested_tags TEXT NOT NULL,
 request_id TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,request_id));
CREATE TABLE IF NOT EXISTS mistakes (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
 original_version_id TEXT NOT NULL REFERENCES versions(id), repair_version_id TEXT REFERENCES versions(id),
 summary TEXT NOT NULL, tags TEXT NOT NULL, confirmed INTEGER NOT NULL, needs_review INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, mistake_id TEXT NOT NULL REFERENCES mistakes(id), outcome TEXT NOT NULL,
 notes TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recommendations (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, reason TEXT NOT NULL,
 mistake_ids TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_task ON evidence(task_id);
CREATE INDEX IF NOT EXISTS turns_task ON turns(task_id);
'''
JSON_COLUMNS = {'result', 'context', 'suggested_tags', 'tags', 'mistake_ids'}


def row(item):
    if item is None:
        return None
    result = dict(item)
    for key in JSON_COLUMNS & result.keys():
        result[key] = json.loads(result[key])
    for key in {'confirmed', 'needs_review'} & result.keys():
        result[key] = bool(result[key])
    return result


class Store(IntegrationStore):
    def __init__(self, root):
        self._transactions=threading.local()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'learning.sqlite3'
        existing = self.path.is_file() and self.path.stat().st_size > 0
        if existing:
            upgrade(self.path)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            if not existing:
                db.execute('PRAGMA user_version=1')
        if not existing:
            upgrade(self.path, backup_required=False)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO settings VALUES(1,?)', (encode(DEFAULTS),))
            db.execute("UPDATE turns SET status='failed',error=? WHERE status='running'",
                       ('服务曾中断，无法确认这次回复。不会自动重新调用；请核对记录后另发问题。',))
            for pending in db.execute("SELECT id,result FROM evidence WHERE kind='local'").fetchall():
                result = json.loads(pending['result'])
                if result.get('status') == 'running':
                    result.update(status='interrupted', executed=False, execution_unknown=True,
                                  error='服务曾中断，执行结果未确认。可手动重新运行。')
                    db.execute('UPDATE evidence SET result=? WHERE id=?', (encode(result), pending['id']))

    @contextmanager
    def connect(self):
        current=getattr(self._transactions,'connection',None)
        if current is not None:
            yield current
            return
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def atomic(self):
        """Compose existing short record operations without intermediate commits."""
        if getattr(self._transactions,'connection',None) is not None:
            raise RuntimeError('Nested application transactions are not supported')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._transactions.connection=db
            try:
                yield
            finally:
                del self._transactions.connection

    def list_tasks(self):
        with self.connect() as db:
            return [row(r) for r in db.execute('''SELECT t.*,v.language,
              EXISTS(SELECT 1 FROM mistakes m WHERE m.task_id=t.id) AS saved_mistake
              FROM tasks t JOIN versions v ON v.id=t.current_version_id ORDER BY updated_at DESC''')]

    def detail(self, task_id):
        with self.connect() as db:
            if not db.in_transaction: db.execute('BEGIN')
            task = row(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
            if task is None:
                raise Missing('未找到这道题，请返回学习首页。')
            for table, order in [('versions', 'number'), ('evidence', 'created_at'), ('turns', 'created_at')]:
                task[table] = [row(r) for r in db.execute(f'SELECT * FROM {table} WHERE task_id=? ORDER BY {order}', (task_id,))]
            task['mistake'] = row(db.execute('SELECT * FROM mistakes WHERE task_id=?', (task_id,)).fetchone())
            task['reviews'] = [] if not task['mistake'] else [row(r) for r in db.execute(
                'SELECT * FROM reviews WHERE mistake_id=? ORDER BY created_at', (task['mistake']['id'],))]
            self.validate_history(task)
            return self.integration_detail(task)

    @staticmethod
    def validate_history(task):
        versions = {v['id']: v for v in task['versions']}
        error = '历史代码与执行/辅导证据不匹配，已停止使用这些记录。请保留数据库备份以便检查。'
        if task['current_version_id'] not in versions:
            raise Conflict(error)
        for v in versions.values():
            if v['source_sha256'] != digest(v['source']):
                raise Conflict(error)
        for record in task['evidence'] + task['turns']:
            version = versions.get(record['version_id'])
            if not version or record['source_sha256'] != version['source_sha256']:
                raise Conflict(error)
        evidence = {e['id']: e for e in task['evidence']}
        turns = {t['id']: t for t in task['turns']}
        for turn in task['turns']:
            context = turn['context']
            if context.get('version') != versions[turn['version_id']] or context.get('task', {}).get('id') != task['id']:
                raise Conflict(error)
            for snapshot in context.get('evidence', []):
                if snapshot != evidence.get(snapshot.get('id')):
                    raise Conflict(error)
            for snapshot in context.get('history', []):
                original = turns.get(snapshot.get('id'))
                if not original or any(original.get(k) != value for k, value in snapshot.items()):
                    raise Conflict(error)
        mistake = task['mistake']
        if mistake and (mistake['original_version_id'] not in versions or
                        (mistake['repair_version_id'] and mistake['repair_version_id'] not in versions)):
            raise Conflict(error)

    def save_task(self, values, task_id=None):
        with self.connect() as db:
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            stamp = now()
            if task_id is None:
                task_id, version_id = uid(), uid()
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)', (task_id, values['title'], values['statement'],
                    values['source_url'], 1, version_id, stamp, stamp))
                number = 1
            else:
                task = row(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
                if task is None:
                    raise Missing('题目不存在。')
                if values['revision'] != task['revision']:
                    raise Conflict('题目已在其他窗口修改。请重新打开后核对，再保存；当前草稿仍在编辑区。')
                old = row(db.execute('SELECT * FROM versions WHERE id=?', (task['current_version_id'],)).fetchone())
                same = old['source'] == values['code'] and old['language'] == values['language']
                version_id = old['id'] if same else uid()
                number = None if same else old['number'] + 1
                db.execute('UPDATE tasks SET title=?,statement=?,source_url=?,revision=revision+1,current_version_id=?,updated_at=? WHERE id=?',
                    (values['title'], values['statement'], values['source_url'], version_id, stamp, task_id))
            if number is not None:
                db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?)', (version_id, task_id, number, values['language'],
                    values['code'], digest(values['code']), stamp))
            db.execute("INSERT OR IGNORE INTO task_profiles VALUES(?,'local')", (task_id,))
            self.record_task_revision(db, task_id)
        return self.detail(task_id)

    def version(self, task_id, version_id, current=False):
        detail = self.detail(task_id)
        version = next((v for v in detail['versions'] if v['id'] == version_id), None)
        if version is None or digest(version['source']) != version['source_sha256']:
            raise Conflict('代码版本与证据不匹配，未执行操作。请重新打开正确题目。')
        if current and detail['current_version_id'] != version_id:
            raise Conflict('当前代码版本已经改变。请保存并选择最新版本后重试。')
        return detail, version

    def prior_request(self, table, task_id, request_id, signature):
        assert table in {'evidence', 'turns'}
        with self.connect() as db:
            result = row(db.execute(f'SELECT * FROM {table} WHERE task_id=? AND request_id=?', (task_id, request_id)).fetchone())
        if result and result['signature'] != signature:
            raise Conflict('同一请求标识不能用于不同内容，请重新发起操作。')
        return result

    def add_evidence(self, task_id, version, kind, source, stdin, expected, result, request_id, signature):
        item = {'id': uid(), 'task_id': task_id, 'version_id': version['id'], 'source_sha256': version['source_sha256'],
                'kind': kind, 'source': source, 'stdin': stdin, 'expected_output': expected, 'result': result,
                'request_id': request_id, 'signature': signature, 'created_at': now()}
        with self.connect() as db:
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', tuple(encode(v) if k == 'result' else v for k, v in item.items()))
        return item

    def add_turn(self, item):
        with self.connect() as db:
            db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(
                encode(item[k]) if k in JSON_COLUMNS else item[k] for k in
                ['id', 'task_id', 'version_id', 'source_sha256', 'message', 'answer', 'provider', 'status', 'error',
                 'context', 'suggested_tags', 'request_id', 'signature', 'created_at']))

    def finish_turn(self, item):
        with self.connect() as db:
            db.execute('UPDATE turns SET answer=?,provider=?,status=?,error=?,suggested_tags=? WHERE id=?',
                       (item['answer'], item['provider'], item['status'], item['error'], encode(item['suggested_tags']), item['id']))

    def finish_evidence(self, item, result):
        with self.connect() as db:
            db.execute('UPDATE evidence SET result=? WHERE id=?', (encode(result), item['id']))
        return {**item, 'result': result}

    def save_mistake(self, task_id, values):
        self.version(task_id, values['original_version_id'])
        if values['repair_version_id']:
            self.version(task_id, values['repair_version_id'])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = row(db.execute('SELECT * FROM mistakes WHERE task_id=?', (task_id,)).fetchone())
            stamp = now()
            mid = existing['id'] if existing else uid()
            db.execute('''INSERT INTO mistakes VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                original_version_id=excluded.original_version_id,repair_version_id=excluded.repair_version_id,
                summary=excluded.summary,tags=excluded.tags,confirmed=excluded.confirmed,
                needs_review=excluded.needs_review,updated_at=excluded.updated_at''',
                (mid, task_id, values['original_version_id'], values['repair_version_id'], values['summary'], encode(values['tags']),
                 values['confirmed'], values['needs_review'], existing['created_at'] if existing else stamp, stamp))
            db.execute('UPDATE error_records SET mistake_id=? WHERE task_id=? AND mistake_id IS NULL', (mid, task_id))
        return self.detail(task_id)['mistake']

    def review(self, mid, outcome, notes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM mistakes WHERE id=?', (mid,)).fetchone():
                raise Missing('错题不存在。')
            result = {'id': uid(), 'mistake_id': mid, 'outcome': outcome, 'notes': notes, 'created_at': now()}
            db.execute('INSERT INTO reviews VALUES(?,?,?,?,?)', tuple(result.values()))
            db.execute('UPDATE mistakes SET needs_review=?,updated_at=? WHERE id=?', (outcome != 'understood', result['created_at'], mid))
        return result

    def settings(self, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('UPDATE settings SET value=? WHERE id=1', (encode(value),))
            return json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])

    def recommendations(self):
        from .learning_recommendations import _blocked_task_pairs, _same_confirmed_reason
        preferences = self.settings()
        if not preferences['proactive']:
            return []
        days = {'light': 7, 'standard': 3, 'intensive': 1}[preferences['review_intensity']]
        mistakes = self.mistakes()
        candidates = []
        groups = {}
        task_preferences = {}
        with self.connect() as db:
            explicit_scopes = {r['task_id'] for r in db.execute(
                "SELECT task_id FROM learning_preferences WHERE user_id='local'")}
            current_versions = {r['id']: r['current_version_id'] for r in db.execute(
                'SELECT id,current_version_id FROM tasks')}
            blocked = _blocked_task_pairs(db)
        for m in mistakes:
            pref = task_preferences[m['task_id']] = self.preferences('local', m['task_id'])
            interval = [7, 3, 1][pref['frequency']] if '' in explicit_scopes or m['task_id'] in explicit_scopes else days
            task_cutoff = datetime.now(timezone.utc) - timedelta(days=interval)
            recent = m['last_review_at'] and datetime.fromisoformat(m['last_review_at']) > task_cutoff
            if m['needs_review'] and not recent:
                candidates.append(('review:' + m['id'] + ':' + str(m['last_review_at']), 'review', '再试一次：' + m['title'],
                    '你标记了需要复习，且最近 ' + str(interval) + ' 天没有回顾。先独立尝试，再查看原记录。', [m['id']]))
            if m['confirmed'] and not recent:
                for tag in m['tags']:
                    groups.setdefault(tag, []).append(m)
        association_counts = {}
        for tag, members in groups.items():
            members.sort(key=lambda m: m['id'])
            for index, left in enumerate(members):
                for right in members[index + 1:]:
                    tids = (left['task_id'], right['task_id'])
                    if frozenset(tids) in blocked:
                        continue
                    if any(association_counts.get(tid, 0) >= [1, 3, 5][task_preferences[tid]['association']]
                           for tid in tids):
                        continue
                    scope = min(task_preferences[tid]['association'] for tid in tids)
                    if scope == 0:
                        left_errors = [{**e, 'current_version_id': current_versions[tids[0]]} for e in left['errors']]
                        right_errors = [{**e, 'current_version_id': current_versions[tids[1]]} for e in right['errors']]
                        if not any(_same_confirmed_reason(a, b) for a in left_errors for b in right_errors):
                            continue
                    ids = [left['id'], right['id']]
                    candidates.append(('pattern:' + tag + ':' + ','.join(ids), 'pattern', '一起回顾「' + tag + '」',
                        '你在这两道错题中确认了相同标签；共同标签不证明错误机制相同，仍需核对双方原因与执行证据。', ids))
                    for tid in tids:
                        association_counts[tid] = association_counts.get(tid, 0) + 1
        active = []
        with self.connect() as db:
            for key, kind, title, reason, ids in candidates:
                rid = digest(key)
                active.append(rid)
                db.execute('''INSERT INTO recommendations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,reason=excluded.reason,mistake_ids=excluded.mistake_ids''',
                    (rid, kind, title, reason, encode(ids), 'pending', now(), now()))
            items = [row(r) for r in db.execute("SELECT * FROM recommendations WHERE kind IN ('review','pattern') ORDER BY created_at DESC")]
        # A historical tag group must not carry a rejected/revoked pair back
        # into the overview. Independent review history can still be displayed.
        return [r for r in items if r['id'] in active or
                r['kind'] == 'review' and r['status'] in {'ignored', 'completed'}]

    def recommendation_status(self, rid, status):
        with self.connect() as db:
            if not db.execute('SELECT id FROM recommendations WHERE id=?', (rid,)).fetchone():
                raise Missing('建议不存在或已失效，请刷新。')
            db.execute('UPDATE recommendations SET status=?,updated_at=? WHERE id=?', (status, now(), rid))
            return row(db.execute('SELECT * FROM recommendations WHERE id=?', (rid,)).fetchone())

    def backup(self, destination, public_export=False):
        if Path(destination).resolve() == self.path.resolve():
            raise ValueError('备份目标不能是原学习数据库。')
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                if public_export:
                    from .public_records import public_records
                    target.execute('PRAGMA secure_delete=ON')
                    target.execute("UPDATE bank_definitions SET private_json='[]'")
                    target.execute("UPDATE submission_cases SET stdin='',expected_output='',result='{}'")
                    for table, column in [('evidence', 'result'), ('turns', 'context')]:
                        for record_id, value in target.execute(f'SELECT id,{column} FROM {table}').fetchall():
                            if table=='evidence' and json.loads(value).get('hidden'):
                                target.execute("UPDATE evidence SET stdin='',expected_output=NULL WHERE id=?",(record_id,))
                            target.execute(f'UPDATE {table} SET {column}=? WHERE id=?',
                                           (encode(public_records(json.loads(value))), record_id))
                    target.execute('CREATE TABLE export_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                    target.executemany('INSERT INTO export_metadata VALUES(?,?)', [
                        ('kind', 'learner_export'), ('redacted', 'true'), ('complete_snapshot', 'false'),
                        ('created_at', now()), ('notice', '脱敏学习记录导出；不包含隐藏测试，不是可完整恢复的数据库备份。')])
                    target.commit()
                    # Repack pages after erasing private cells: a raw SQLite download must
                    # not recover the original suite/result from freelist or overflow pages.
                    target.execute('VACUUM')
                    target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise sqlite3.DatabaseError('backup check failed')
                if list(target.execute('PRAGMA foreign_key_check')):
                    raise sqlite3.DatabaseError('backup foreign key check failed')
            finally:
                target.close()
