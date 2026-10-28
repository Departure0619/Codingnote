"""Cross-platform records over the existing Alpha transaction boundary.

Profiles scope local teaching preferences; this is not network authentication.
"""
import hashlib
import json


PREFERENCE_KEYS = ('guidance', 'hint', 'analysis', 'summary', 'association', 'frequency', 'difficulty', 'length')
PRESETS = {'light': (0,) * 8, 'standard': (1, 1, 1, 1, 1, 1, 0, 1), 'deep': (2,) * 8}
ERROR_STATUSES = {'ai_candidate', 'user_confirmed', 'user_modified', 'uncertain'}
ASSOCIATION_STATUSES = {'pending', 'confirmed', 'rejected', 'ignored', 'revoked'}


def _helpers():
    # Delayed import avoids a circular import with Store's mixin.
    from .store import Missing, Conflict, now, uid, encode, row
    return Missing, Conflict, now, uid, encode, row


def _source(item):
    if not item:
        return None
    result = dict(item)
    for key in ('license', 'knowledge_tags'):
        result[key] = json.loads(result[key])
    return result


class IntegrationStore:
    def source(self, task_id):
        with self.connect() as db:
            return _source(db.execute('SELECT * FROM task_sources WHERE task_id=?', (task_id,)).fetchone())

    def set_source(self, task_id, values, user_id='local'):
        Missing, Conflict, now, uid, encode, _ = _helpers()
        keys = ('source_platform', 'original_id', 'source_kind', 'source_description', 'license', 'bank_id',
                'question_id', 'content_version', 'content_digest', 'knowledge_primary', 'knowledge_tags', 'difficulty', 'import_status')
        with self.connect() as db:
            if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM tasks WHERE id=?', (task_id,)).fetchone():
                raise Missing('题目不存在。')
            owner = db.execute('SELECT user_id FROM task_profiles WHERE task_id=?', (task_id,)).fetchone()
            if owner and owner[0] != user_id:
                raise Conflict('本机学习档案与题目所属档案不一致。')
            db.execute('INSERT OR IGNORE INTO task_profiles VALUES(?,?)', (task_id, user_id))
            previous = _source(db.execute('SELECT * FROM task_sources WHERE task_id=?', (task_id,)).fetchone()) or {}
            data = {k: values.get(k, previous.get(k, [] if k == 'knowledge_tags' else {} if k == 'license' else 'manual' if k in {'source_kind', 'import_status'} else '')) for k in keys}
            for key in ('license', 'knowledge_tags'):
                data[key] = encode(data[key])
            columns = ','.join(keys)
            db.execute('INSERT INTO task_sources(task_id,' + columns + ') VALUES(' + ','.join('?' for _ in range(len(keys) + 1)) + ') ON CONFLICT(task_id) DO UPDATE SET ' + ','.join(k + '=excluded.' + k for k in keys), (task_id, *(data[k] for k in keys)))
            snapshot = _source(db.execute('SELECT * FROM task_sources WHERE task_id=?', (task_id,)).fetchone())
            revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM source_revisions WHERE task_id=?', (task_id,)).fetchone()[0]
            db.execute('INSERT INTO source_revisions VALUES(?,?,?,?,?)', (uid(), task_id, revision, encode(snapshot), now()))
        return self.source(task_id)

    def errors(self, task_id):
        with self.connect() as db:
            items = [dict(r) for r in db.execute('SELECT * FROM error_records WHERE task_id=? ORDER BY created_at DESC', (task_id,))]
            for item in items:
                item['revisions'] = [dict(r) for r in db.execute('SELECT * FROM error_revisions WHERE error_id=? ORDER BY revision', (item['id'],))]
        return items

    def save_error(self, task_id, values, error_id=None):
        Missing, Conflict, now, uid, _, _ = _helpers()
        status = values.get('status', 'uncertain')
        if status not in ERROR_STATUSES:
            raise ValueError('错误原因确认状态无效。')
        if not str(values.get('observed', '')).strip():
            raise ValueError('请记录实际表现。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('SELECT * FROM versions WHERE id=? AND task_id=?', (values['version_id'], task_id)).fetchone()
            if not version:
                raise Conflict('错误记录与代码版本不属于同一道题。')
            evidence_id = values.get('evidence_id') or None
            if evidence_id and not db.execute('SELECT id FROM evidence WHERE id=? AND task_id=? AND version_id=?', (evidence_id, task_id, version['id'])).fetchone():
                raise Conflict('错误记录与执行证据不属于同一道题或代码版本。')
            previous = db.execute('SELECT * FROM error_records WHERE id=? AND task_id=?', (error_id, task_id)).fetchone() if error_id else None
            if error_id and not previous:
                raise Missing('错误记录不存在。')
            if previous and values.get('revision', previous['revision']) != previous['revision']:
                raise Conflict('错误原因已在其他窗口修改，请刷新后核对。')
            eid, stamp = previous['id'] if previous else uid(), now()
            revision = previous['revision'] + 1 if previous else 1
            mistake = db.execute('SELECT id FROM mistakes WHERE task_id=?', (task_id,)).fetchone()
            fields = (values['version_id'], evidence_id, values['observed'], values.get('category', ''), values.get('reason', ''), status)
            if previous:
                db.execute('UPDATE error_records SET version_id=?,evidence_id=?,observed=?,category=?,reason=?,status=?,revision=?,updated_at=? WHERE id=?', (*fields, revision, stamp, eid))
            else:
                db.execute('INSERT INTO error_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (eid, task_id, mistake[0] if mistake else None, *fields, revision, stamp, stamp))
            db.execute('INSERT INTO error_revisions VALUES(?,?,?,?,?,?,?,?,?,?)', (uid(), eid, revision, *fields, stamp))
        return next(e for e in self.errors(task_id) if e['id'] == eid)

    @staticmethod
    def _basis(db, a_id, b_id):
        Missing, _, _, _, encode, _ = _helpers()
        result = []
        for eid in (a_id, b_id):
            item = db.execute('''SELECT e.*,t.current_version_id FROM error_records e
                JOIN tasks t ON t.id=e.task_id WHERE e.id=?''', (eid,)).fetchone()
            if not item:
                raise Missing('关联依赖的错误记录不存在。')
            # Membership in the mistake library is bookkeeping, not root-cause evidence.
            result.append({key: item[key] for key in ('id', 'task_id', 'version_id', 'evidence_id',
                'observed', 'category', 'reason', 'status', 'revision', 'current_version_id')})
        snapshot = encode(result)
        return hashlib.sha256(snapshot.encode('utf8')).hexdigest(), snapshot, result

    def create_association(self, error_a_id, error_b_id, reason, origin='user'):
        _, Conflict, now, uid, _, _ = _helpers()
        if not reason.strip():
            raise ValueError('请填写需要核对的关联依据。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            basis, snapshot, errors = self._basis(db, error_a_id, error_b_id)
            if errors[0]['task_id'] == errors[1]['task_id']:
                raise Conflict('跨题关联需要两道不同题目的错误记录。')
            item_id, stamp = uid(), now()
            db.execute('INSERT INTO associations VALUES(?,?,?,?,?,?,?,?,?,?)', (item_id, error_a_id, error_b_id, reason, origin, 'pending', basis, snapshot, stamp, stamp))
            db.execute('INSERT INTO association_history VALUES(?,?,?,?,?)', (uid(), item_id, None, 'pending', stamp))
        return self.association(item_id)

    def _association(self, db, item):
        result = dict(item)
        basis, _, errors = self._basis(db, result['error_a_id'], result['error_b_id'])
        result['stale'] = basis != result['basis_digest']
        result['basis_snapshot'] = json.loads(result['basis_snapshot'])
        result['task_a_id'], result['task_b_id'] = errors[0]['task_id'], errors[1]['task_id']
        result['history'] = [dict(r) for r in db.execute('SELECT * FROM association_history WHERE association_id=? ORDER BY created_at,id', (result['id'],))]
        return result

    def association(self, association_id):
        Missing, *_ = _helpers()
        with self.connect() as db:
            item = db.execute('SELECT * FROM associations WHERE id=?', (association_id,)).fetchone()
            if not item:
                raise Missing('跨题关联不存在。')
            return self._association(db, item)

    def associations(self, task_id=''):
        with self.connect() as db:
            query = 'SELECT a.* FROM associations a'
            args = ()
            if task_id:
                query += ' JOIN error_records x ON x.id=a.error_a_id JOIN error_records y ON y.id=a.error_b_id WHERE x.task_id=? OR y.task_id=?'
                args = (task_id, task_id)
            return [self._association(db, r) for r in db.execute(query + ' ORDER BY a.updated_at DESC', args).fetchall()]

    def decide_association(self, association_id, status):
        Missing, Conflict, now, uid, _, _ = _helpers()
        if status not in ASSOCIATION_STATUSES:
            raise ValueError('跨题关联状态无效。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            item = db.execute('SELECT * FROM associations WHERE id=?', (association_id,)).fetchone()
            if not item:
                raise Missing('跨题关联不存在。')
            if status == 'confirmed' and self._association(db, item)['stale']:
                raise Conflict('历史关联依据已变化，请依据当前错误记录重新建立候选。')
            stamp = now()
            db.execute('UPDATE associations SET status=?,updated_at=? WHERE id=?', (status, stamp, association_id))
            db.execute('INSERT INTO association_history VALUES(?,?,?,?,?)', (uid(), association_id, item['status'], status, stamp))
        return self.association(association_id)

    def preferences(self, user_id='local', task_id=None, value=None, reset=False):
        Missing, Conflict, now, _, encode, _ = _helpers()
        if not user_id or len(user_id) > 80:
            raise ValueError('本机学习档案标识无效。')
        scope = task_id or ''
        defaults = {'preset': 'standard', **dict(zip(PREFERENCE_KEYS, PRESETS['standard']))}
        with self.connect() as db:
            if task_id:
                if not db.execute('SELECT id FROM tasks WHERE id=?', (task_id,)).fetchone():
                    raise Missing('题目不存在。')
                owner = db.execute('SELECT user_id FROM task_profiles WHERE task_id=?', (task_id,)).fetchone()
                if owner and owner[0] != user_id or not owner and user_id != 'local':
                    raise Conflict('本机学习档案与题目所属档案不一致。')
            global_row = db.execute("SELECT value FROM learning_preferences WHERE user_id=? AND task_id=''", (user_id,)).fetchone()
            global_value = json.loads(global_row[0]) if global_row else defaults.copy()
            existing = db.execute('SELECT value FROM learning_preferences WHERE user_id=? AND task_id=?', (user_id, scope)).fetchone()
            current = json.loads(existing[0]) if existing else global_value.copy()
            if reset:
                if task_id:
                    db.execute('DELETE FROM learning_preferences WHERE user_id=? AND task_id=?', (user_id, scope))
                    return {**global_value, 'user_id': user_id, 'task_id': task_id, 'inherited': True}
                value = {'preset': 'standard'}
            if value is not None:
                unknown = set(value) - set(PREFERENCE_KEYS) - {'preset'}
                if unknown:
                    raise ValueError('未知学习偏好字段。')
                mode = value.get('preset')
                if mode and mode not in {*PRESETS, 'custom'}:
                    raise ValueError('学习模式无效。')
                if mode in PRESETS:
                    current = {'preset': mode, **dict(zip(PREFERENCE_KEYS, PRESETS[mode]))}
                changed_items = {k: v for k, v in value.items() if k in PREFERENCE_KEYS}
                if any(type(v) is not int or v not in {0, 1, 2} for v in changed_items.values()):
                    raise ValueError('单项学习偏好必须为 0、1 或 2。')
                if changed_items:
                    current.update(changed_items)
                    current['preset'] = 'custom'
                elif mode == 'custom':
                    current['preset'] = 'custom'
                db.execute('INSERT INTO learning_preferences VALUES(?,?,?,?) ON CONFLICT(user_id,task_id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at', (user_id, scope, encode(current), now()))
            return {**current, 'user_id': user_id, 'task_id': task_id, 'inherited': bool(task_id and not existing and value is None)}

    def integration_detail(self, task):
        task['source'] = self.source(task['id'])
        task['errors'] = self.errors(task['id'])
        task['associations'] = self.associations(task['id'])
        with self.connect() as db:
            task['task_revisions'] = [dict(r) for r in db.execute('SELECT * FROM task_revisions WHERE task_id=? ORDER BY revision', (task['id'],))]
            task['source_revisions'] = []
            for item in db.execute('SELECT * FROM source_revisions WHERE task_id=? ORDER BY revision', (task['id'],)):
                snapshot = dict(item)
                snapshot['source'] = json.loads(snapshot['source'])
                task['source_revisions'].append(snapshot)
        return task

    @staticmethod
    def record_task_revision(db, task_id):
        _, _, _, uid, _, _ = _helpers()
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        db.execute('INSERT INTO task_revisions VALUES(?,?,?,?,?,?,?)',
            (uid(), task_id, task['revision'], task['title'], task['statement'], task['source_url'], task['updated_at']))

    def mistakes(self, q='', language='', tag='', source='', primary='', secondary='', reason='',
                 since='', until='', review='', confirmation='', sort='recent'):
        _, _, _, _, _, row = _helpers()
        with self.connect() as db:
            items = [row(r) for r in db.execute('''SELECT m.*,t.title,t.statement,v.language,
                (SELECT MAX(created_at) FROM reviews WHERE mistake_id=m.id) AS last_review_at
                FROM mistakes m JOIN tasks t ON t.id=m.task_id JOIN versions v ON v.id=m.original_version_id''')]
            sources = {r['task_id']: _source(r) for r in db.execute('SELECT * FROM task_sources')}
            errors = {}
            for error in db.execute('SELECT * FROM error_records ORDER BY created_at DESC'):
                errors.setdefault(error['task_id'], []).append(dict(error))
            latest_reviews = {r['mistake_id']: r['outcome'] for r in db.execute('SELECT * FROM reviews ORDER BY created_at')}
        result = []
        for item in items:
            meta = sources.get(item['task_id']) or {}
            records = errors.get(item['task_id'], [])
            item.update({k: meta.get(k, [] if k == 'knowledge_tags' else '') for k in ('source_platform', 'original_id', 'source_kind', 'bank_id', 'question_id', 'knowledge_primary', 'knowledge_tags', 'difficulty')})
            item['source'] = meta
            item['errors'] = records
            item['primary_reason'] = records[0]['reason'] if records else item['summary']
            item['confirmation_status'] = records[0]['status'] if records else 'uncertain'
            item['last_error_at'] = max((e['created_at'] for e in records), default=item['created_at'])
            item['review_status'] = 'understood' if not item['needs_review'] else 'reviewing' if latest_reviews.get(item['id']) == 'retry' else 'due'
            haystack = ' '.join(str(item.get(k, '')) for k in ('id', 'task_id', 'title', 'statement', 'summary', 'original_id', 'question_id')) + ' '.join(item['tags'])
            if q and q.casefold() not in haystack.casefold():
                continue
            if language and item['language'] != language or tag and tag not in item['tags']:
                continue
            if source and (source == '__unknown' and item['source_platform'] or source != '__unknown' and item['source_platform'] != source):
                continue
            if primary and item['knowledge_primary'] != primary or secondary and secondary not in item['knowledge_tags']:
                continue
            if reason and not any(reason.casefold() in e['reason'].casefold() for e in records):
                continue
            if confirmation and not any(e['status'] == confirmation for e in records) and item['confirmation_status'] != confirmation:
                continue
            if since and item['last_error_at'][:len(since)] < since or until and item['last_error_at'][:len(until)] > until:
                continue
            if review and item['review_status'] != review:
                continue
            result.append(item)
        if sort in {'title', 'name'}:
            result.sort(key=lambda m: m['title'].casefold())
        elif sort in {'oldest', 'old'}:
            result.sort(key=lambda m: m['last_error_at'])
        elif sort in {'reviewed', 'review'}:
            result.sort(key=lambda m: m['last_review_at'] or '', reverse=True)
        elif sort == 'due':
            result.sort(key=lambda m: (m['review_status'] != 'due', m['last_error_at']))
        else:
            result.sort(key=lambda m: m['last_error_at'], reverse=True)
        return result
