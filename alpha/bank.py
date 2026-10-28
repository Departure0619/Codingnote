"""Immutable original-bank snapshots and judging through the existing runner.

Private suites never cross the ordinary HTTP or tutoring boundary. A saved
evidence row contains a digest-based projection; the private case row retains
the complete execution for local administrative backup and auditing.
"""
import copy
import json
from pathlib import Path
import threading
from .store import Conflict, Missing, uid, now, encode, digest
from .languages import LANGUAGES, public_metadata
from .public_records import public_records
from question_bank.validate import question_digest, cases_digest, validate_bank

BANK_SCHEMA = '''
CREATE TABLE IF NOT EXISTS bank_definitions (
 id TEXT PRIMARY KEY, bank_id TEXT NOT NULL, question_id TEXT NOT NULL,
 content_digest TEXT NOT NULL, content_version INTEGER NOT NULL,
 test_digest TEXT NOT NULL, public_json TEXT NOT NULL, private_json TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(bank_id,question_id,content_digest),
 UNIQUE(bank_id,question_id,content_version));
CREATE TABLE IF NOT EXISTS practice_links (
 definition_id TEXT NOT NULL REFERENCES bank_definitions(id), language TEXT NOT NULL,
 slot TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
 request_id TEXT NOT NULL, signature TEXT NOT NULL,
 PRIMARY KEY(definition_id,language,slot));
CREATE TABLE IF NOT EXISTS practice_requests (
 request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS submissions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 version_id TEXT NOT NULL REFERENCES versions(id), definition_id TEXT NOT NULL REFERENCES bank_definitions(id),
 source_sha256 TEXT NOT NULL, language TEXT NOT NULL, mode TEXT NOT NULL,
 test_digest TEXT NOT NULL, status TEXT NOT NULL, passed INTEGER NOT NULL DEFAULT 0,
 total INTEGER NOT NULL, request_id TEXT NOT NULL, signature TEXT NOT NULL,
 created_at TEXT NOT NULL, finished_at TEXT, UNIQUE(task_id,request_id));
CREATE TABLE IF NOT EXISTS submission_cases (
 submission_id TEXT NOT NULL REFERENCES submissions(id), case_id TEXT NOT NULL,
 evidence_id TEXT NOT NULL UNIQUE REFERENCES evidence(id), stdin TEXT NOT NULL,
 expected_output TEXT NOT NULL, result TEXT NOT NULL, matches_expected INTEGER NOT NULL,
 PRIMARY KEY(submission_id,case_id));
CREATE INDEX IF NOT EXISTS bank_lookup ON bank_definitions(bank_id,question_id,content_version);
CREATE INDEX IF NOT EXISTS submissions_task ON submissions(task_id,created_at);
'''

# UI templates are scaffolds, not reference solutions. Toolchain commands remain
# exclusively in the already accepted language registry and image.
TEMPLATES = {
    'c': '#include <stdio.h>\n\nint main(void) {\n    /* 读取输入并实现题目 */\n    return 0;\n}\n',
    'cpp': '#include <iostream>\nusing namespace std;\n\nint main() {\n    // 读取输入并实现题目\n    return 0;\n}\n',
    'python': 'import sys\n\ndef main():\n    data = sys.stdin.read()\n    # 根据题意读取输入并输出结果\n\nif __name__ == "__main__":\n    main()\n',
    'java': 'import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner in = new Scanner(System.in);\n        // 读取输入并实现题目\n    }\n}\n',
    'javascript': "'use strict';\nconst fs = require('fs');\nconst input = fs.readFileSync(0, 'utf8');\n// 根据题意读取输入并输出结果\n",
}


def languages():
    return {key: {**public_metadata(key), 'template': TEMPLATES[key],
                  'limitations': '仅单文件、预置标准库；不支持多文件项目、外部依赖或构建工具。'} for key in LANGUAGES}


def public_question(question):
    # Deliberate allowlist: never expose references, tests or receipt paths.
    return {key: copy.deepcopy(question[key]) for key in (
        'id', 'title', 'statement', 'difficulty', 'knowledge', 'input_format',
        'output_format', 'samples', 'constraints', 'source', 'license')}


class Bank:
    def __init__(self, store):
        self.store = store
        self.import_receipt = self.import_asset(self.load_asset())
        with store.connect() as db:
            db.execute("UPDATE submissions SET status='interrupted',finished_at=? WHERE status='running'", (now(),))

    @staticmethod
    def load_asset():
        return json.loads((Path(__file__).resolve().parents[1] / 'question_bank' / 'bank.json').read_text(encoding='utf8'))

    def import_asset(self, asset):
        errors = validate_bank(asset)
        if errors:
            raise ValueError("Invalid public question bank: " + "; ".join(errors))
        inserted = 0
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for question in asset['questions']:
                content_digest = question_digest(question)
                if db.execute('SELECT id FROM bank_definitions WHERE bank_id=? AND question_id=? AND content_digest=?',
                              (asset['bank_id'], question['id'], content_digest)).fetchone():
                    continue
                version = db.execute('SELECT COALESCE(MAX(content_version),0)+1 FROM bank_definitions WHERE bank_id=? AND question_id=?',
                                     (asset['bank_id'], question['id'])).fetchone()[0]
                db.execute('INSERT INTO bank_definitions VALUES(?,?,?,?,?,?,?,?,?)',
                    (uid(), asset['bank_id'], question['id'], content_digest, version, cases_digest(question['test_cases']),
                     encode(public_question(question)), encode(question['test_cases']), now()))
                inserted += 1
        return {'bank_id': asset['bank_id'], 'inserted': inserted, 'idempotency': 'bank_id + question_id + question_digest',
                'active_questions': len(asset['questions'])}

    def definition(self, question_id=None, definition_id=None):
        with self.store.connect() as db:
            if definition_id:
                item = db.execute('SELECT * FROM bank_definitions WHERE id=?', (definition_id,)).fetchone()
            else:
                item = db.execute('SELECT * FROM bank_definitions WHERE question_id=? ORDER BY content_version DESC LIMIT 1', (question_id,)).fetchone()
            if item is None:
                raise Missing('题库中没有这道题。')
            return dict(item)

    def public(self, definition):
        result = json.loads(definition['public_json'])
        result.update(bank_id=definition['bank_id'], content_version=definition['content_version'],
                      content_digest=definition['content_digest'], test_digest=definition['test_digest'])
        with self.store.connect() as db:
            result['practices'] = [dict(r) for r in db.execute('SELECT p.task_id,p.language,t.updated_at FROM practice_links p JOIN tasks t ON t.id=p.task_id WHERE definition_id=? ORDER BY t.updated_at DESC', (definition['id'],))]
        return result

    def listing(self, q='', primary='', tag='', difficulty='', id=''):
        with self.store.connect() as db:
            rows = db.execute('SELECT d.* FROM bank_definitions d WHERE content_version=(SELECT MAX(content_version) FROM bank_definitions x WHERE x.bank_id=d.bank_id AND x.question_id=d.question_id) ORDER BY question_id').fetchall()
        items = [self.public(dict(r)) for r in rows]
        facets = {'primary': sorted({v['knowledge']['primary'] for v in items}),
                  'tags': sorted({tag for v in items for tag in v['knowledge']['tags']}),
                  'difficulty': sorted({v['difficulty'] for v in items})}
        filtered = [v for v in items if (not q or q.casefold() in (v['id'] + v['title'] + v['statement']).casefold())
                    and (not primary or primary == v['knowledge']['primary'])
                    and (not tag or tag in v['knowledge']['tags'])
                    and (not difficulty or difficulty == v['difficulty']) and (not id or id.upper() == v['id'])]
        return {'bank_id': rows[0]['bank_id'] if rows else '', 'total': len(items), 'matched': len(filtered), 'items': filtered, 'facets': facets}

    def practice(self, question_id, language, new_task, request_id):
        if language not in LANGUAGES:
            raise Conflict('不支持该语言。')
        definition = self.definition(question_id)
        public = json.loads(definition['public_json'])
        signature = digest(encode([definition['id'], language, new_task]))
        # Single SQLite transaction makes creation and idempotency indivisible.
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT * FROM practice_requests WHERE request_id=?', (request_id,)).fetchone()
            if prior:
                if prior['signature'] != signature:
                    raise Conflict('同一练习请求标识不能用于不同题目或语言。')
                task_id = prior['task_id']
            else:
                slot = 'new:' + request_id if new_task else 'resume'
                existing = None if new_task else db.execute('''SELECT p.task_id FROM practice_links p
                    JOIN tasks t ON t.id=p.task_id JOIN versions v ON v.id=t.current_version_id
                    WHERE p.definition_id=? AND v.language=? ORDER BY t.updated_at DESC LIMIT 1''',
                    (definition['id'], language)).fetchone()
                if existing:
                    task_id = existing[0]
                else:
                    if db.execute('SELECT 1 FROM practice_links WHERE definition_id=? AND language=? AND slot=?',
                                  (definition['id'], language, slot)).fetchone():
                        slot += ':' + uid()
                    task_id, version_id, stamp = uid(), uid(), now()
                    statement = (public['statement'] + '\n\n输入格式\n' + public['input_format'] + '\n\n输出格式\n'
                                 + public['output_format'] + '\n\n约束\n' + '\n'.join(public['constraints']))
                    db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',
                               (task_id, public['title'], statement, '', 1, version_id, stamp, stamp))
                    source = TEMPLATES[language]
                    db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?)', (version_id, task_id, 1, language, source, digest(source), stamp))
                    db.execute('INSERT INTO task_profiles VALUES(?,?)', (task_id, 'local'))
                    db.execute('INSERT INTO task_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (task_id, '内置原创题库', '', 'project_original', public['source']['attribution'], encode(public['license']),
                         definition['bank_id'], public['id'], str(definition['content_version']), definition['content_digest'],
                         public['knowledge']['primary'], encode(public['knowledge']['tags']), public['difficulty'], 'builtin'))
                    self.store.record_task_revision(db, task_id)
                    snapshot = dict(db.execute('SELECT * FROM task_sources WHERE task_id=?', (task_id,)).fetchone())
                    snapshot['license'] = json.loads(snapshot['license'])
                    snapshot['knowledge_tags'] = json.loads(snapshot['knowledge_tags'])
                    db.execute('INSERT INTO source_revisions VALUES(?,?,?,?,?)', (uid(), task_id, 1, encode(snapshot), stamp))
                    db.execute('INSERT INTO practice_links VALUES(?,?,?,?,?,?)',
                               (definition['id'], language, slot, task_id, request_id, signature))
                db.execute('INSERT INTO practice_requests VALUES(?,?,?)', (request_id, signature, task_id))
        return self.detail(task_id)

    def task_definition(self, task_id):
        with self.store.connect() as db:
            link = db.execute('SELECT definition_id FROM practice_links WHERE task_id=?', (task_id,)).fetchone()
        return self.definition(definition_id=link[0]) if link else None

    def detail(self, task_id):
        task = self.store.detail(task_id)
        definition = self.task_definition(task_id)
        task['bank_question'] = self.public(definition) if definition else None
        task['submissions'] = self.submissions(task_id)
        return public_records(task)

    def submission(self, submission_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM submissions WHERE id=?', (submission_id,)).fetchone()
            if row is None:
                raise Missing('提交不存在。')
            item = dict(row)
            cases = db.execute('SELECT c.*,e.result AS public_result FROM submission_cases c JOIN evidence e ON e.id=c.evidence_id WHERE submission_id=? ORDER BY c.case_id', (submission_id,)).fetchall()
        item.pop('signature', None)
        item['cases'] = []
        for case in cases:
            public = json.loads(case['public_result'])
            value = {'case_id': case['case_id'], 'evidence_id': case['evidence_id'],
                     'status': public['status'], 'matches_expected': bool(case['matches_expected'])}
            if item['mode'] == 'samples':
                value.update(stdin=case['stdin'], expected_output=case['expected_output'], result=json.loads(case['result']))
            item['cases'].append(value)
        return item

    def submissions(self, task_id):
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM submissions WHERE task_id=? ORDER BY created_at', (task_id,))]
        return [self.submission(value) for value in ids]

    def judge(self, task_id, version_id, request_id, mode, execution, cancel_event=None):
        signature = digest(encode([version_id, mode]))
        with self.store.connect() as db:
            prior = db.execute('SELECT * FROM submissions WHERE task_id=? AND request_id=?', (task_id, request_id)).fetchone()
        if prior:
            if prior['signature'] != signature:
                raise Conflict('同一提交请求标识不能复用于不同版本或测试模式。')
            if prior['status'] == 'running':
                raise Conflict('上次提交尚未确认，不会重复执行。')
            return self.submission(prior['id'])
        _, version = self.store.version(task_id, version_id, current=True)
        if not version['source'].strip():
            raise Conflict('请先保存非空代码。')
        definition = self.task_definition(task_id)
        if definition is None:
            raise Conflict('这道手工题没有正式题库测试；请使用自定义输入运行。')
        cases = json.loads(definition['private_json'])
        if mode == 'samples':
            samples = json.loads(definition['public_json'])['samples']
            cases = [{'id': 'sample-' + str(i + 1), **value} for i, value in enumerate(samples)]
        submission_id = uid()
        with self.store.connect() as db:
            db.execute('INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (submission_id, task_id, version_id, definition['id'], version['source_sha256'], version['language'], mode,
                 definition['test_digest'], 'running', 0, len(cases), request_id, signature, now(), None))
        passed, failure, event = 0, None, cancel_event or threading.Event()
        for case in cases:
            if event.is_set():
                failure = 'cancelled'
                break
            try:
                options = {'cancel_event': event} if getattr(execution, 'supports_cancellation', False) else {}
                result = execution.run(version['language'], version['source'], case['stdin'], **options)
            except Exception:
                result = {'status': 'service_error', 'executed': False, 'cleaned': False,
                          'stdout': '', 'stderr': '', 'error': '执行服务中断，未回退到主机执行。'}
            trusted = (result.get('status') == 'completed' and result.get('executed')
                       and result.get('cleaned', result.get('cleanup_confirmed', False))
                       and result.get('compile_status') == 'passed')
            result.update(stdin_sha256=digest(case['stdin']), stdout_sha256=digest(result.get('stdout', '')))
            matches = bool(trusted and result.get('stdout') == case['stdout'])
            passed += int(matches)
            projection = dict(result) if mode == 'samples' else {
                key: result[key] for key in ('status', 'executed', 'compile_status', 'cleaned', 'execution_unknown',
                    'elapsed_seconds', 'run_elapsed_seconds', 'image_id', 'policy_sha256', 'engine_fingerprint',
                    'resource_reason') if key in result}
            projection.update(submission_id=submission_id, case_id=case['id'], test_digest=definition['test_digest'],
                              language=version['language'], matches_expected=matches,
                              hidden=mode != 'samples')
            # The old evidence and context contracts see only the public projection.
            evidence = {'id': uid(), 'task_id': task_id, 'version_id': version_id,
                'source_sha256': version['source_sha256'], 'kind': 'local',
                'source': '公开样例' if mode == 'samples' else '正式题库测试（案例内容保密）',
                'stdin': case['stdin'] if mode == 'samples' else '',
                'expected_output': case['stdout'] if mode == 'samples' else None,
                'result': projection, 'request_id': 'bank:' + submission_id + ':' + case['id'],
                'signature': signature, 'created_at': now()}
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    tuple(encode(v) if k == 'result' else v for k, v in evidence.items()))
                db.execute('INSERT INTO submission_cases VALUES(?,?,?,?,?,?,?)',
                    (submission_id, case['id'], evidence['id'], case['stdin'], case['stdout'], encode(result), matches))
            if result.get('status') in {'unavailable', 'service_error', 'cancelled'} or not result.get('cleaned', result.get('cleanup_confirmed', False)):
                failure = result.get('status', 'service_error')
                break
        status = failure or ('passed' if passed == len(cases) else 'wrong_answer')
        with self.store.connect() as db:
            db.execute('UPDATE submissions SET status=?,passed=?,finished_at=? WHERE id=?', (status, passed, now(), submission_id))
        return self.submission(submission_id)
