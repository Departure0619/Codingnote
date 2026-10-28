"""Persistent guard for the explicitly bounded round 8 authorization only.

No credentials, learner text, or request headers enter this ledger. There is no
reset endpoint: another round needs a newly reviewed authorization scope.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid

from web_lab.ai_provider import AIUnavailable


ROUND_ID = 'round8'
MODEL = 'deepseek-flash'
MAX_REQUESTS = 8
MAX_BUDGET_USD = .5
DEFAULT_LEDGER = Path(__file__).resolve().parent.parent / '.local' / 'round8-ai-budget.sqlite3'
STOP_CODES = {'AUTH_FAILED', 'INSUFFICIENT_BALANCE', 'INCOMPATIBLE_RESPONSE', 'MODEL_UNAVAILABLE'}


def validate_scope(model, requests, budget):
    if (model != MODEL or type(requests) is not int or not 0 <= requests <= MAX_REQUESTS
            or type(budget) not in (int, float) or not math.isfinite(budget)
            or not 0 < budget <= MAX_BUDGET_USD):
        raise ValueError('第8轮仅授权 deepseek-flash、累计最多8次且总预算不超过US$0.50；保存设置不能扩大本轮授权。')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode('utf8')).hexdigest()


def _has_table(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _audit_rows(db, table):
    return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] if _has_table(db, table) else []


def _phase_rows(db):
    stages = _audit_rows(db, 'round_retest_stages')
    events = _audit_rows(db, 'round_stop_events')
    return stages, events, stages[-1] if stages else None


def _known_usage(value):
    try:
        usage = json.loads(value) if isinstance(value, str) else value
        fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
        return (isinstance(usage, dict) and all(type(usage.get(k)) is int
                and 0 <= usage[k] <= 10000000 for k in fields)
                and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'])
    except (ValueError, TypeError):
        return False


def _finite_cost(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _original_calls(db):
    return [dict(row) for row in db.execute('''SELECT * FROM round_attempts a WHERE NOT EXISTS(
        SELECT 1 FROM round_stage_attempts m WHERE m.attempt_id=a.id) ORDER BY created_at,id''')]


def _assert_mutable_attempt(db, cid):
    if _has_table(db, 'round_retest_stages') and db.execute('SELECT 1 FROM round_retest_stages').fetchone():
        if (db.execute('SELECT 1 FROM round_attempts WHERE id=?', (cid,)).fetchone()
                and not db.execute('SELECT 1 FROM round_stage_attempts WHERE attempt_id=?', (cid,)).fetchone()):
            raise ValueError('原始四次调用属于不可更改的历史证据。')


def _effective_stop(auth, stage, events):
    if not stage:
        return events[-1]['stop_reason'] if events else auth['stop_reason']
    reviewed = next((i for i, event in enumerate(events)
                     if event['id'] == stage['supersedes_stop_event_id']), None)
    if reviewed is None:
        return 'INVALID_STAGE_AUDIT'
    return events[-1]['stop_reason'] if len(events) > reviewed + 1 else None


def _new_stop(db, auth, stage, reason, cid=None):
    db.execute('INSERT INTO round_stop_events VALUES(?,?,?,?,?,?,?)',
               (uuid.uuid4().hex, auth['id'], stage['id'] if stage else None,
                cid, reason, 'stage' if stage else 'legacy_import', datetime.now(timezone.utc).isoformat()))


def _stage_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS round_stop_events(
        id TEXT PRIMARY KEY,authorization_id TEXT NOT NULL,stage_id TEXT,
        attempt_id TEXT,stop_reason TEXT NOT NULL,source TEXT NOT NULL,created_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS round_retest_stages(
        id TEXT PRIMARY KEY,authorization_id TEXT NOT NULL UNIQUE,
        supersedes_stop_event_id TEXT NOT NULL UNIQUE,transport TEXT NOT NULL,model TEXT NOT NULL,
        max_new_requests INTEGER NOT NULL,cumulative_max_requests INTEGER NOT NULL,
        cumulative_budget_usd REAL NOT NULL,review_fingerprint TEXT NOT NULL,
        original_failure_id TEXT NOT NULL,original_calls_sha256 TEXT NOT NULL,
        scope_json TEXT NOT NULL,approval_json TEXT NOT NULL,approved_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS round_stage_attempts(
        attempt_id TEXT PRIMARY KEY,stage_id TEXT NOT NULL,created_at TEXT NOT NULL)''')
    # Approval history is append-only, including reservations rolled back locally
    # before dispatch. Quota counts mapping rows joined to actual attempts.
    for table in ('round_stop_events', 'round_retest_stages', 'round_stage_attempts'):
        for operation in ('UPDATE', 'DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS ' + table + '_no_' + operation.lower()
                       + ' BEFORE ' + operation + ' ON ' + table
                       + " BEGIN SELECT RAISE(ABORT,'immutable retest audit'); END")


class RoundBudget:
    def __init__(self, path, transport):
        self.path = Path(path)
        self.transport = transport

    @contextmanager
    def connect(self, *, readonly=False):
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path.resolve().as_uri() + '?mode=ro' if readonly else str(self.path)
        db = sqlite3.connect(target, timeout=10, uri=readonly)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def configure(self, model, requests, budget):
        validate_scope(model, requests, budget)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS round_authorization(
                id TEXT PRIMARY KEY,round_id TEXT NOT NULL,transport TEXT NOT NULL,
                model TEXT NOT NULL,max_requests INTEGER NOT NULL,budget_usd REAL NOT NULL,
                stop_reason TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS round_attempts(
                id TEXT PRIMARY KEY,state TEXT NOT NULL,reserved_usd REAL NOT NULL,
                cost_usd REAL,usage_json TEXT,error_code TEXT,in_flight INTEGER NOT NULL,
                dispatched INTEGER NOT NULL,created_at TEXT NOT NULL)''')
            row = db.execute('SELECT * FROM round_authorization').fetchone()
            if row:
                if row['round_id'] != ROUND_ID or row['transport'] != self.transport or row['model'] != MODEL:
                    raise ValueError('本轮授权账本范围不匹配，未启用请求。')
                # A stopped authorization remains historical evidence. A later
                # reviewed stage is append-only; settings never edit this row.
                if not row['stop_reason']:
                    db.execute('UPDATE round_authorization SET max_requests=MIN(max_requests,?),budget_usd=MIN(budget_usd,?)',
                               (requests, budget))
            else:
                db.execute('INSERT INTO round_authorization VALUES(?,?,?,?,?,?,NULL)',
                           (uuid.uuid4().hex, ROUND_ID, self.transport, MODEL, requests, budget))
        return self.snapshot()

    def snapshot(self):
        empty = {'round_id': ROUND_ID, 'transport': self.transport, 'model': MODEL,
                 'authorized': False, 'max_requests': 0, 'budget_usd': 0,
                 'attempts': 0, 'dispatched_requests': 0, 'remaining_requests': 0,
                 'known_cost_usd': 0, 'reserved_unknown_cost_usd': 0,
                 'unknown_usage_requests': 0, 'upstream_pending': False,
                 'stop_reason': None, 'historical_stop_reason': None, 'effective_stop_reason': None,
                 'stages': [], 'current_stage': None, 'stop_events': [], 'phase_remaining': 0,
                 'historical_attempts': 0, 'stage_attempts': 0, 'calls': [],
                 'notice': '跨学习库的第8轮累计账本；发送尝试不等于确认收费。保存设置、重启或换库均不恢复额度。'}
        if not self.path.exists():
            return empty
        with self.connect(readonly=True) as db:
            db.execute('BEGIN')
            auth = db.execute('SELECT * FROM round_authorization').fetchone()
            calls = [dict(r) for r in db.execute('SELECT * FROM round_attempts ORDER BY created_at,id')]
            stages, events, stage = _phase_rows(db)
            stage_count = db.execute('''SELECT COUNT(*) FROM round_stage_attempts m
                JOIN round_attempts a ON a.id=m.attempt_id WHERE m.stage_id=?''', (stage['id'],)).fetchone()[0] if stage else 0
        if not auth:
            return empty
        for call in calls:
            call['usage'] = json.loads(call.pop('usage_json')) if call['usage_json'] else None
        effective = _effective_stop(auth, stage, events)
        remaining = max(0, auth['max_requests'] - len(calls))
        phase_remaining = max(0, min(stage['max_new_requests'] - stage_count,
            stage['cumulative_max_requests'] - len(calls), remaining)) if stage else 0
        if stage:
            remaining = phase_remaining
            spent = sum(c['cost_usd'] if c['cost_usd'] is not None else c['reserved_usd'] for c in calls)
            if spent >= min(auth['budget_usd'], stage['cumulative_budget_usd'], MAX_BUDGET_USD):
                remaining = 0
        remaining = 0 if effective else remaining
        return {**empty, 'authorized': True, 'authorization_id': auth['id'],
                'max_requests': auth['max_requests'], 'budget_usd': auth['budget_usd'],
                'attempts': len(calls), 'dispatched_requests': sum(c['dispatched'] for c in calls),
                'remaining_requests': remaining,
                'known_cost_usd': sum(c['cost_usd'] or 0 for c in calls),
                'reserved_unknown_cost_usd': sum(c['reserved_usd'] for c in calls if c['cost_usd'] is None),
                'unknown_usage_requests': sum(c['usage'] is None for c in calls),
                'upstream_pending': any(c['in_flight'] or c['state'] in {'reserved', 'running'} for c in calls),
                'stop_reason': auth['stop_reason'], 'historical_stop_reason': auth['stop_reason'],
                'effective_stop_reason': effective, 'stages': stages, 'current_stage': stage,
                'stop_events': events, 'phase_remaining': 0 if effective else phase_remaining,
                'historical_attempts': len(calls) - stage_count, 'stage_attempts': stage_count, 'calls': calls}

    def _review_state(self, db, review):
        reasons = []
        review = review if isinstance(review, dict) else {}
        expected = {'max_new_requests': 4, 'cumulative_max_requests': MAX_REQUESTS,
                    'cumulative_budget_usd': MAX_BUDGET_USD, 'model': MODEL}
        if any(type(review.get(k)) not in ((int,) if k.endswith('requests') else (int, float)
                   if k == 'cumulative_budget_usd' else (str,)) or review.get(k) != v
               for k, v in expected.items()):
            reasons.append('INVALID_REVIEW_SCOPE')
        if review.get('stop_reason', review.get('original_stop_reason')) != 'INCOMPATIBLE_RESPONSE':
            reasons.append('UNREVIEWED_STOP_REASON')
        for key in ('review_fingerprint', 'frozen_plan_sha256', 'original_calls_sha256'):
            value = review.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                reasons.append('INVALID_' + key.upper())
        if not isinstance(review.get('code_revision'), (str, dict)) or not review.get('code_revision'):
            reasons.append('MISSING_CODE_REVISION')
        if not isinstance(review.get('review_basis'), (str, list, dict)) or not review.get('review_basis'):
            reasons.append('MISSING_REVIEW_BASIS')
        try:
            _json(review)
        except (ValueError, TypeError):
            reasons.append('INVALID_REVIEW_DOCUMENT')
        auth = db.execute('SELECT * FROM round_authorization').fetchone()
        calls = [dict(row) for row in db.execute('SELECT * FROM round_attempts ORDER BY created_at,id')]
        stages, events, stage = _phase_rows(db)
        if stages:
            reasons.append('STAGE_ALREADY_GRANTED')
        elif events:
            reasons.append('UNREVIEWED_STOP_EVENT')
        if not auth:
            reasons.append('NO_AUTHORIZATION')
        elif (auth['round_id'] != ROUND_ID or auth['transport'] != self.transport or auth['model'] != MODEL):
            reasons.append('AUTHORIZATION_SCOPE_MISMATCH')
        elif auth['stop_reason'] != 'INCOMPATIBLE_RESPONSE':
            reasons.append('UNREVIEWED_HISTORICAL_STOP')
        if len(calls) != 4:
            reasons.append('ORIGINAL_ATTEMPTS_MUST_BE_FOUR')
        if any(c['in_flight'] or c['state'] in {'reserved', 'running'} for c in calls):
            reasons.append('REQUEST_IN_PROGRESS')
        if any(not _finite_cost(c['cost_usd']) or not _finite_cost(c['reserved_usd'])
               or not _known_usage(c['usage_json']) for c in calls):
            reasons.append('UNKNOWN_USAGE')
        if calls:
            if (calls[-1]['id'] != review.get('original_failure_id')
                    or calls[-1]['error_code'] != 'INCOMPATIBLE_RESPONSE'
                    or any(c['error_code'] or not c['dispatched']
                           or c['state'] not in {'completed', 'success'} for c in calls[:-1])
                    or not calls[-1]['dispatched'] or calls[-1]['state'] != 'failed'):
                reasons.append('ORIGINAL_FAILURE_MISMATCH')
        else:
            reasons.append('ORIGINAL_FAILURE_MISMATCH')
        try:
            digest_matches = _sha(calls) == review.get('original_calls_sha256')
        except (ValueError, TypeError):
            digest_matches = False
        if not digest_matches:
            reasons.append('ORIGINAL_CALLS_CHANGED')
        if auth and (auth['max_requests'] <= len(calls) or
                     sum(c['cost_usd'] if _finite_cost(c['cost_usd']) else c['reserved_usd'] for c in calls)
                     >= min(auth['budget_usd'], MAX_BUDGET_USD)):
            reasons.append('BUDGET_EXHAUSTED')
        return {'eligible': not reasons, 'pending': not reasons,
                'blocked_reasons': list(dict.fromkeys(reasons)), 'review_fingerprint': review.get('review_fingerprint'),
                'current_stage': stage, 'stage_exists': bool(stage)}

    def review_state(self, review):
        """Read-only eligibility; the caller supplies the server's trusted review."""
        if not self.path.exists():
            return {'eligible': False, 'pending': False, 'blocked_reasons': ['NO_AUTHORIZATION'],
                    'stage_exists': False, 'current_stage': None}
        with self.connect(readonly=True) as db:
            db.execute('BEGIN')
            return self._review_state(db, review)

    def approve_stage(self, review, approval):
        """Append one bounded stage after the local settings confirmation."""
        allowed_approval_fields = {'source', 'confirmed', 'challenge_id', 'approved_at',
                                   'server_time', 'review_fingerprint'}
        if not isinstance(approval, dict) or set(approval) - allowed_approval_fields:
            raise ValueError('复验批准仅接受固定确认字段，不能包含凭据或其他设置参数。')
        if ('review_fingerprint' in approval and
                (not isinstance(review, dict) or approval['review_fingerprint'] != review.get('review_fingerprint'))):
            raise ValueError('批准时的审查指纹已改变，请重新核对后确认。')
        if (not isinstance(approval, dict) or approval.get('source') != 'local_settings_confirmation'
                or approval.get('confirmed') is not True or not isinstance(approval.get('challenge_id'), str)
                or not approval['challenge_id'].strip()):
            raise ValueError('复验需要本机设置页的新明确批准，保存设置不构成批准。')
        approved_at = approval.get('approved_at', approval.get('server_time'))
        try:
            parsed_at = datetime.fromisoformat(approved_at)
            if parsed_at.tzinfo is None or abs((datetime.now(timezone.utc) - parsed_at).total_seconds()) > 300:
                raise ValueError('expired approval')
            _json(approval)
        except (TypeError, ValueError):
            raise ValueError('复验批准缺少有效服务器时间。') from None
        if not self.path.exists():
            raise ValueError('缺少待审查的本轮账本。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._review_state(db, review)
            if not state['eligible']:
                raise ValueError('当前账本不符合受控复验条件：' + ','.join(state['blocked_reasons']))
            auth = db.execute('SELECT * FROM round_authorization').fetchone()
            _stage_schema(db)
            _new_stop(db, auth, None, auth['stop_reason'], review['original_failure_id'])
            event = db.execute('SELECT id FROM round_stop_events ORDER BY rowid DESC LIMIT 1').fetchone()
            db.execute('INSERT INTO round_retest_stages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, auth['id'], event['id'], self.transport, MODEL, 4, MAX_REQUESTS,
                        MAX_BUDGET_USD, review['review_fingerprint'], review['original_failure_id'],
                        review['original_calls_sha256'], _json(review), _json(approval), approved_at))
        return self.snapshot()

    def reserve(self, cid, reserved, review_fingerprint=None):
        if type(reserved) not in (int, float) or not math.isfinite(reserved) or reserved < 0:
            raise ValueError('费用预留必须为有限非负数。')
        rejected_review = False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            auth = db.execute('SELECT * FROM round_authorization').fetchone()
            stages, events, stage = _phase_rows(db)
            if (not auth or auth['round_id'] != ROUND_ID or auth['transport'] != self.transport
                    or auth['model'] != MODEL or _effective_stop(auth, stage, events)):
                raise AIUnavailable('本轮授权已停止，需核对账本并获得新的明确授权；保存设置不会恢复。',
                                    code='ROUND_AUTHORIZATION_STOPPED')
            stage_changed = None
            if stage:
                if review_fingerprint != stage['review_fingerprint']:
                    stage_changed = 'REVIEW_CHANGED'
                elif _sha(_original_calls(db)) != stage['original_calls_sha256']:
                    stage_changed = 'ORIGINAL_CALLS_CHANGED'
            if stage_changed:
                _new_stop(db, auth, stage, stage_changed)
                rejected_review = True
            elif db.execute("SELECT 1 FROM round_attempts WHERE in_flight=1 OR state IN ('reserved','running')").fetchone():
                raise AIUnavailable('本轮仍有上游请求或结果核对未结束，本次未发送。', code='REQUEST_IN_PROGRESS')
            if not rejected_review:
                count, spent = db.execute('SELECT COUNT(*),COALESCE(SUM(COALESCE(cost_usd,reserved_usd)),0) FROM round_attempts').fetchone()
                phase_count = db.execute('''SELECT COUNT(*) FROM round_stage_attempts m
                    JOIN round_attempts a ON a.id=m.attempt_id WHERE m.stage_id=?''', (stage['id'],)).fetchone()[0] if stage else 0
                max_count = min(auth['max_requests'], MAX_REQUESTS)
                max_budget = min(auth['budget_usd'], MAX_BUDGET_USD)
                if stage:
                    max_count = min(max_count, stage['cumulative_max_requests'])
                    max_budget = min(max_budget, stage['cumulative_budget_usd'])
                if (count >= max_count or spent + reserved > max_budget
                        or stage and phase_count >= min(stage['max_new_requests'], 4)):
                    raise AIUnavailable('本轮累计次数或费用预留已达授权上限，本次未发送。', code='BUDGET_EXHAUSTED')
                created_at = datetime.now(timezone.utc).isoformat()
                db.execute('INSERT INTO round_attempts VALUES(?,?,?,?,?,?,?,?,?)',
                           (cid, 'reserved', reserved, None, None, None, 1, 0, created_at))
                if stage:
                    db.execute('INSERT INTO round_stage_attempts VALUES(?,?,?)', (cid, stage['id'], created_at))
        if rejected_review:
            raise AIUnavailable('代码或审查范围已改变，复验已停止；本次未发送。', code='ROUND_AUTHORIZATION_STOPPED')

    def abort_before_send(self, cid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _assert_mutable_attempt(db, cid)
            db.execute('DELETE FROM round_attempts WHERE id=? AND dispatched=0', (cid,))

    def dispatched(self, cid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _assert_mutable_attempt(db, cid)
            db.execute("UPDATE round_attempts SET dispatched=1,state='running' WHERE id=?", (cid,))

    def transport_returned(self, cid):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _assert_mutable_attempt(db, cid)
            db.execute('UPDATE round_attempts SET in_flight=0 WHERE id=?', (cid,))

    def finish(self, cid, state, usage, cost, code):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            _assert_mutable_attempt(db, cid)
            if not db.execute('SELECT 1 FROM round_attempts WHERE id=?', (cid,)).fetchone():
                return
            stages, events, stage = _phase_rows(db)
            db.execute('''UPDATE round_attempts SET state=?,usage_json=COALESCE(?,usage_json),
                cost_usd=COALESCE(?,cost_usd),error_code=? WHERE id=?''',
                       (state, json.dumps(usage) if usage else None, cost, code, cid))
            # Unknown use requires a review even if late usage subsequently arrives.
            stop = code if code in STOP_CODES else 'UNKNOWN_USAGE' if cost is None else None
            if stage:
                stop = (code if code in STOP_CODES else 'UNKNOWN_USAGE'
                        if not _finite_cost(cost) or not _known_usage(usage)
                        else code or ('PROVIDER_ERROR' if state == 'failed' else None))
            if stop:
                auth = db.execute('SELECT * FROM round_authorization').fetchone()
                if stage:
                    # A late known-usage update cannot clear an earlier stop.
                    if not _effective_stop(auth, stage, events):
                        _new_stop(db, auth, stage, stop, cid)
                else:
                    db.execute('UPDATE round_authorization SET stop_reason=COALESCE(stop_reason,?)', (stop,))
