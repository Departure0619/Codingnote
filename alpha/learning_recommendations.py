"""Bounded deterministic candidates; neither tags nor matching prose prove a cause."""
import json
from .store import digest, encode, now


DIFFICULTY_LEVELS = ('beginner', 'intermediate', 'advanced')


def _blocked_task_pairs(db):
    # Automatic discovery cannot bypass a decision with a different error record
    # or by falling back to the pair's common knowledge tags.
    return {frozenset((r['task_a_id'], r['task_b_id'])) for r in db.execute('''
        SELECT x.task_id AS task_a_id,y.task_id AS task_b_id FROM associations a
        JOIN error_records x ON x.id=a.error_a_id JOIN error_records y ON y.id=a.error_b_id
        WHERE a.status IN ('rejected','revoked','ignored')''')}


def _same_confirmed_reason(a, b):
    return (all(e['status'] in {'user_confirmed', 'user_modified'} and
                e['version_id'] == e['current_version_id'] for e in (a, b)) and
            bool(a['reason'].strip()) and a['reason'].strip() == b['reason'].strip())


def _save_candidate(store, rid, kind, title, reason, mistake_ids, basis):
    stamp = now()
    with store.connect() as db:
        db.execute('''INSERT INTO recommendations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,reason=excluded.reason,mistake_ids=excluded.mistake_ids''',
            (rid, kind, title, reason, encode(mistake_ids), 'pending', stamp, stamp))
        item = dict(db.execute('SELECT * FROM recommendations WHERE id=?', (rid,)).fetchone())
    item['mistake_ids'] = json.loads(item['mistake_ids'])
    item['basis'] = basis
    return item


def build(store, bank, task_id=''):
    questions = bank.listing()['items']
    difficulties = {q['difficulty'] for q in questions}
    multiple = len(difficulties) > 1 and difficulties <= set(DIFFICULTY_LEVELS)
    result = {'items': [], 'multi_difficulty_supported': multiple,
              'available_difficulties': sorted(difficulties), 'available_question_count': len(questions),
              'notice': '确定性规则仅提出待核对候选，不评估掌握率或学习成绩；最多以20道已存错题为来源，'
                        '候选计算每题最多比较8条自身错误记录和80条其他错误记录。'}
    if not store.settings()['proactive']:
        return result
    tasks = ([store.detail(task_id)] if task_id else
             [store.detail(m['task_id']) for m in store.mistakes()[:20]])
    with store.connect() as db:
        blocked = _blocked_task_pairs(db)
    preferences = {}
    emitted_ids = set()

    def preference(tid):
        if tid not in preferences:
            preferences[tid] = store.preferences('local', tid)
        return preferences[tid]

    for task in tasks:
        pref = preference(task['id'])
        source = task['source'] or {}
        tags, primary = set(source.get('knowledge_tags', [])), source.get('knowledge_primary', '')
        current = next(v for v in task['versions'] if v['id'] == task['current_version_id'])
        source_difficulty = source.get('difficulty', '')
        target = None
        if multiple and source_difficulty in DIFFICULTY_LEVELS:
            target = DIFFICULTY_LEVELS[max(0, min(2,
                DIFFICULTY_LEVELS.index(source_difficulty) + pref['difficulty'] - 1))]
        if len(difficulties) < 2:
            difficulty_note = '现有题库仅一种难度，按知识重合度安排巩固/迁移顺序，不能提供更高难度匹配。'
        elif target is None:
            difficulty_note = '来源或题库缺少可比较的难度标签，按知识相关度推荐，不能确认难度匹配。'
        else:
            difficulty_note = '按来源题与题库明确标注的难度排序，目标为 ' + target + '；不据此推断个人掌握程度。'
        candidates = []
        for question in questions:
            if question['id'] == source.get('question_id'):
                continue
            shared = sorted(tags & set(question['knowledge']['tags']))
            same = bool(primary and primary == question['knowledge']['primary'])
            if not shared and not same:
                continue
            score = len(shared) * 3 + int(same) * 2
            distance = (abs(DIFFICULTY_LEVELS.index(question['difficulty']) -
                            DIFFICULTY_LEVELS.index(target)) if target else 0)
            # Breadth may reorder one-tier practice, but never relabels a task.
            overlap = score if pref['difficulty'] == 2 and target is None else -score
            candidates.append(((distance, overlap, question['id']), question, shared, same))
        candidates.sort(key=lambda candidate: candidate[0])
        for _, question, shared, same in candidates[:[2, 4, 6][pref['frequency']]]:
            basis = {'relationship': 'knowledge_related', 'task_id': task['id'], 'task_title': task['title'],
                     'question_id': question['id'], 'shared_knowledge': shared or ([primary] if same else []),
                     'language': current['language'], 'difficulty': question['difficulty'],
                     'difficulty_preference': pref['difficulty'], 'difficulty_target': target,
                     'difficulty_note': difficulty_note,
                     'evidence_requirement': '共同知识点仅作为练习候选，不证明错误机制相同。'}
            reason = ('与「' + task['title'] + '」共有知识点：' + '、'.join(basis['shared_knowledge']) + '。' +
                      basis['evidence_requirement'])
            item = _save_candidate(store, digest('practice:' + task['id'] + ':' + question['id']),
                'practice', '相关练习：' + question['title'], reason,
                [task['mistake']['id']] if task['mistake'] else [], basis)
            item['question_id'] = question['id']
            result['items'].append(item)

        with store.connect() as db:
            other = [dict(r) for r in db.execute('''SELECT e.*,t.current_version_id,
                s.knowledge_primary,s.knowledge_tags FROM error_records e
                JOIN tasks t ON t.id=e.task_id JOIN task_sources s ON s.task_id=e.task_id
                JOIN task_profiles p ON p.task_id=e.task_id AND p.user_id='local'
                WHERE e.task_id<>? ORDER BY e.created_at DESC,e.id LIMIT 80''', (task['id'],))]
        pairs = []
        for own in task['errors'][:8]:
            a = {**own, 'current_version_id': current['id']}
            for b in other:
                if frozenset((a['task_id'], b['task_id'])) in blocked:
                    continue
                same_reason = _same_confirmed_reason(a, b)
                shared = sorted(tags & set(json.loads(b['knowledge_tags'])))
                same_primary = bool(primary and primary == b['knowledge_primary'])
                scope = min(pref['association'], preference(b['task_id'])['association'])
                if scope == 0 and not same_reason:
                    continue
                if not same_reason and not shared and not same_primary:
                    continue
                relation = 'similar_mechanism_candidate' if same_reason else 'knowledge_related'
                # Acceptance belongs to these revisions. Canonical order also
                # keeps A→B and B→A from becoming two separate suggestions.
                snapshots = sorted([(e['id'], e['revision'], e['version_id'], e['current_version_id'])
                                    for e in (a, b)])
                rid = digest('error-candidate:' + encode([snapshots, relation]))
                basis = {'relationship': relation, 'task_id': task['id'], 'other_task_id': b['task_id'],
                         'left_error_id': a['id'], 'right_error_id': b['id'],
                         'shared_knowledge': shared or ([primary] if same_primary else []),
                         'left_revision': a['revision'], 'right_revision': b['revision'],
                         'left_version_id': a['version_id'], 'right_version_id': b['version_id'],
                         'left_status': a['status'], 'right_status': b['status'],
                         'left_current_version_id': current['id'], 'right_current_version_id': b['current_version_id'],
                         'left_evidence_id': a['evidence_id'], 'right_evidence_id': b['evidence_id'],
                         'association_preference': scope,
                         'evidence_requirement': '共同标签不证明错误机制相同；相似原因描述仍需核对双方执行证据，'
                                                 '不会自动创建或确认关联。'}
                pairs.append((not same_reason, -len(shared), rid, basis))
        pairs.sort(key=lambda pair: pair[:3])
        emitted = 0
        for _, _, rid, basis in pairs:
            if emitted >= [1, 3, 5][pref['association']]:
                break
            if rid in emitted_ids:
                continue
            reason = ('双方当前版本已确认原因文字相同，提出待核对候选。' if
                      basis['relationship'] == 'similar_mechanism_candidate' else '知识点相关，错误机制尚无充分证据。')
            item = _save_candidate(store, rid, 'association', '核对相关错题', reason, [], basis)
            result['items'].append(item)
            emitted_ids.add(rid)
            emitted += 1
    return result
