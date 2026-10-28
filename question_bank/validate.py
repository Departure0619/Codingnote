"""Generic public bank contract. Empty is valid; unauthorized assets fail closed.

No authored question, oracle, reference implementation or question-specific input
constraint is embedded here. Historical twenty-question checks stay quarantined.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def cases_digest(cases):
    return digest(json.dumps(cases, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8'))

def question_digest(question):
    content = {key: value for key, value in question.items() if key != 'validation'}
    return cases_digest(content)

def validate_bank(bank, root=ROOT):
    errors=[]
    if not isinstance(bank,dict): return ['bank must be an object']
    if bank.get('schema_version')!='1.0': errors.append('schema_version must be 1.0')
    if not isinstance(bank.get('bank_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',bank['bank_id']): errors.append('invalid bank_id')
    questions=bank.get('questions')
    if not isinstance(questions,list) or len(questions)>1000: return errors+['questions must be a bounded list (empty allowed)']
    seen=set()
    for index,q in enumerate(questions):
        prefix=f'question[{index}]'
        if not isinstance(q,dict): errors.append(prefix+': must be an object');continue
        identity=q.get('id')
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identity): errors.append(prefix+': invalid id')
        elif identity in seen: errors.append(prefix+': duplicate id')
        else: seen.add(identity)
        for name,limit in [('title',160),('statement',30000),('input_format',5000),('output_format',5000),('difficulty',80)]:
            value=q.get(name)
            if not isinstance(value,str) or not value.strip() or len(value)>limit: errors.append(prefix+': invalid '+name)
        knowledge=q.get('knowledge')
        if not isinstance(knowledge,dict) or not isinstance(knowledge.get('primary'),str) or not isinstance(knowledge.get('tags'),list): errors.append(prefix+': invalid knowledge')
        elif any(not isinstance(t,str) or not t.strip() or len(t)>80 for t in knowledge['tags']) or len(knowledge['tags'])>20: errors.append(prefix+': invalid tags')
        if not isinstance(q.get('source'),dict) or not isinstance(q['source'].get('attribution'),str) or not q['source']['attribution'].strip(): errors.append(prefix+': source attribution required')
        licence=q.get('license')
        if not isinstance(licence,dict) or licence.get('public_distribution_confirmed') is not True or any(not isinstance(licence.get(k),str) or not licence[k].strip() for k in ('statement','tests','reference')): errors.append(prefix+': explicit statement/tests/reference distribution permission required')
        constraints=q.get('constraints')
        if not isinstance(constraints,list) or any(not isinstance(c,str) or len(c)>5000 for c in constraints): errors.append(prefix+': invalid constraints')
        cases=q.get('test_cases');case_ids=set()
        if not isinstance(cases,list) or not 1<=len(cases)<=200: errors.append(prefix+': bounded nonempty test_cases required');cases=[]
        for case in cases:
            if not isinstance(case,dict): errors.append(prefix+': invalid test case');continue
            cid=case.get('id')
            if not isinstance(cid,str) or not cid or cid in case_ids: errors.append(prefix+': duplicate or invalid case id')
            else:case_ids.add(cid)
            for key in ('stdin','stdout'):
                value=case.get(key)
                if not isinstance(value,str) or len(value.encode('utf8'))>64000: errors.append(prefix+': invalid case '+key)
            if case.get('purpose') not in {'sample','boundary','normal','hidden'}: errors.append(prefix+': invalid case purpose')
        samples=q.get('samples')
        if not isinstance(samples,list) or not samples: errors.append(prefix+': samples required')
        else:
            for sample in samples:
                if not isinstance(sample,dict) or not any(c.get('purpose')=='sample' and c.get('stdin')==sample.get('stdin') and c.get('stdout')==sample.get('stdout') for c in cases if isinstance(c,dict)): errors.append(prefix+': sample must match a declared sample case')
        reference=q.get('reference')
        if reference is not None:
            try:
                path=(Path(root)/reference['path']).resolve()
                if not path.is_relative_to((Path(root)/'question_bank/references').resolve()) or digest(path.read_bytes())!=reference['sha256']: errors.append(prefix+': invalid reference path/hash')
            except (OSError,KeyError,TypeError): errors.append(prefix+': invalid reference')
    return errors

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank',type=Path,default=ROOT/'question_bank/bank.json')
    args=parser.parse_args()
    try:
        bank=json.loads(args.bank.read_text('utf8'));errors=validate_bank(bank)
    except (OSError,ValueError) as exc: bank={};errors=[str(exc)]
    print(json.dumps({'status':'failed' if errors else 'passed','scope':'generic_public_static_contract','questions':len(bank.get('questions',[])) if isinstance(bank.get('questions'),list) else None,'reference_execution':False,'errors':errors},ensure_ascii=False,indent=2))
    return bool(errors)

if __name__=='__main__':raise SystemExit(main())
