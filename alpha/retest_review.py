"""Server-owned review scope for one explicitly approved round-8 retest stage."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
PLAN_SHA256='a08223c06a680d00d776ebf28bc1274c6be36d0c256299b3149731d03ea3a92e'
RAW4_SHA256='4d25cdb533fe336e326f6853485b933fef5ed8cafd5afd8ac443678725d47804'
FAILURE_ID='c55e02fa9a644a9582473cb56212acf4'
ORIGINAL_CALLS_SHA256='f4bb85d5ba6637993ec977b6d56281c9366d669af38a8a5bb6480e721cf60395'
ACCEPTANCE_PATH=ROOT/'output/round8/final-gate/offline-acceptance.json'
REVIEW_FILES=('alpha/round8_budget.py','alpha/tutor.py','alpha/app.py','alpha/models.py',
              'alpha/retest_review.py','alpha/teaching_protocol.py','alpha/static/round67.js','alpha/ai_models.json')


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()


def code_revision():
    return {'base_commit':'099470fd285dde0fc6dbb5c7112cac294367287b',
            'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in REVIEW_FILES}}


def trusted_review(*,acceptance_path=None):
    """No request parameter can choose a model, cap, failure, or evidence path."""
    path=Path(acceptance_path) if acceptance_path is not None else ACCEPTANCE_PATH
    try:
        acceptance_bytes=path.read_bytes();acceptance=json.loads(acceptance_bytes)
        revision=code_revision()
        if acceptance.get('passed') is not True or acceptance.get('ready_for_local_confirmation') is not True:
            raise ValueError('离线验收尚未通过，不能提供受控复验批准。')
        if acceptance.get('source_revision')!=revision or acceptance.get('plan_sha256')!=PLAN_SHA256:
            raise ValueError('当前修订与已审查版本不一致，须重新核对后批准。')
        plan=ROOT/'output/round8/controlled-retest/retest-plan.json'
        raw=ROOT/'ROUND8_RAW_MODEL_RESULTS/real/request-04-response.json'
        if hashlib.sha256(plan.read_bytes()).hexdigest()!=PLAN_SHA256 or hashlib.sha256(raw.read_bytes()).hexdigest()!=RAW4_SHA256:
            raise ValueError('冻结计划或原失败证据校验不符，未启用复验。')
        from .teaching_protocol import Analysis,contract_prompt
        if Analysis.model_json_schema()['properties']['history_candidates'].get('maxItems')!=0:
            raise ValueError('历史候选协议未修补，未启用复验。')
        if 'history_candidates必须为[]' not in contract_prompt({'purpose':'analyze'}):
            raise ValueError('历史候选提示与协议不一致，未启用复验。')
        catalog=json.loads((ROOT/'alpha/ai_models.json').read_text('utf8'))
        if catalog['base_url']!='https://api.deepseek.com' or 'deepseek-flash' not in catalog['models']:
            raise ValueError('本轮官方Flash配置不符，未启用复验。')
    except (OSError,KeyError,TypeError,json.JSONDecodeError):
        raise ValueError('受控复验审查依据缺失或无效，暂不能批准。') from None
    review={'model':'deepseek-flash','base_url':'https://api.deepseek.com',
        'max_new_requests':4,'cumulative_max_requests':8,'cumulative_budget_usd':.50,
        'frozen_plan_sha256':PLAN_SHA256,'original_failure_id':FAILURE_ID,
        'original_stop_reason':'INCOMPATIBLE_RESPONSE','original_calls_sha256':ORIGINAL_CALLS_SHA256,
        'code_revision':revision,'repair_summary':'Schema将history_candidates限制为[]，Prompt与解析器一致；非空及未知候选继续拒绝。',
        'review_basis':[{'path':str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else 'isolated-review',
                        'sha256':hashlib.sha256(acceptance_bytes).hexdigest(),'checks':acceptance.get('checks',{})},
                       {'path':'ROUND8_RAW_MODEL_RESULTS/real/request-04-response.json','sha256':RAW4_SHA256},
                       {'path':'output/round8/controlled-retest/retest-plan.json','sha256':PLAN_SHA256}]}
    review['review_fingerprint']=_digest(review)
    return review
