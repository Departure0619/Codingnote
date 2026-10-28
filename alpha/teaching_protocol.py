"""Bounded, untrusted learning data and strictly validated teaching contracts."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from .store import Conflict


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Fact(Contract):
    evidence_id: StrictStr
    field: Literal['status', 'stdout', 'stderr', 'executed', 'matches_expected']
    value: str | bool


class Analysis(Contract):
    observed: StrictStr = Field(min_length=1, max_length=5000)
    mechanism: StrictStr = Field(min_length=1, max_length=5000)
    evidence_refs: list[StrictStr] = Field(max_length=6)
    knowledge: list[StrictStr] = Field(max_length=8)
    repair: StrictStr = Field(max_length=5000)
    uncertainties: list[StrictStr] = Field(min_length=1, max_length=8)
    cognitive_hypothesis: StrictStr = Field(max_length=2000)
    facts: list[Fact] = Field(max_length=12)
    history_candidates: list[StrictStr] = Field(max_length=0)


class Section(Contract):
    heading: StrictStr = Field(min_length=1, max_length=100)
    text: StrictStr = Field(min_length=1, max_length=6000)


class Summary(Contract):
    sections: list[Section] = Field(min_length=2, max_length=8)
    evidence_refs: list[StrictStr] = Field(max_length=6)
    uncertainties: list[StrictStr] = Field(min_length=1, max_length=8)


class AnalysisNarrative(Contract):
    mechanism: StrictStr = Field(min_length=1, max_length=5000)
    knowledge: list[StrictStr] = Field(max_length=8)
    repair: StrictStr = Field(max_length=5000)
    uncertainties: list[StrictStr] = Field(min_length=1, max_length=8)
    cognitive_hypothesis: StrictStr = Field(max_length=2000)


class SummaryNarrative(Contract):
    sections: list[Section] = Field(min_length=2, max_length=8)
    uncertainties: list[StrictStr] = Field(min_length=1, max_length=8)


class NarrativeEnvelope(Contract):
    protocol_version: Literal[2]
    answer: StrictStr = Field(min_length=1, max_length=30000)
    suggested_tags: list[StrictStr] = Field(max_length=5)


class AnalysisEnvelope(NarrativeEnvelope):
    analysis: AnalysisNarrative


class SummaryEnvelope(NarrativeEnvelope):
    summary: SummaryNarrative


SUMMARY_HEADINGS = [
    ['错误表现', '修复要点'],
    ['错误表现', '可能原因', '知识点', '修复过程', '预防建议'],
    ['错误表现', '详细原因', '相关概念', '修复前后区别', '回顾建议', '延伸练习']]


def policy(context):
    p = context['learning_preferences']
    guidance = ['优先给线索，不主动提供完整解答。', '分步解释关键步骤，优先引导理解。',
                '详细解释代码行为、概念和修改理由，可按请求给出代码。'][p['guidance']]
    if context.get('explicit_direct'):
        guidance = '学生本轮主动选择直接讲解，允许完整解释和修复代码，不强制追问。'
    return (guidance + '\n提示强度：' + ['只给一个小提示', '适量具体修改建议', '充分提示并举例'][p['hint']] +
            '\n原因分析：' + ['简要线索', '分析错误机制与依据', '深入解释机制、概念及不确定性'][p['analysis']] +
            '\n回答长度：' + ['简短，约150字', '标准，约500字', '详细，约1000字'][p['length']] +
            '\n总结必须包含章节：' + '、'.join(SUMMARY_HEADINGS[p['summary']]) +
            '\n可以直接回答；不要求学生每轮先回答反问。')


def reference_schema(contract, context):
    """Match model-facing reference choices to the parser's shared evidence scope."""
    schema = contract.model_json_schema()
    evidence_ids = [e['id'] for e in context.get('evidence', [])[:6]]
    references = schema['properties']['evidence_refs']
    if evidence_ids:
        references['items']['enum'] = evidence_ids
        if contract is Analysis:
            schema['$defs']['Fact']['properties']['evidence_id']['enum'] = evidence_ids
    else:
        references['maxItems'] = 0
        if contract is Analysis:
            schema['properties']['facts']['maxItems'] = 0
    return schema


def contract_prompt(context):
    purpose = context.get('purpose', 'chat')
    text = '只输出JSON对象，字段answer（非空中文字符串）、suggested_tags（最多5个短标签）。'
    if purpose == 'analyze':
        text += '\n额外必须有analysis对象，严格符合以下JSON Schema：' + json.dumps(reference_schema(Analysis, context), ensure_ascii=False)
        text += '\n区分程序事实、推断机制、认知假设。facts只能逐字引用提供的证据字段，不允许重写执行结果。认知假设始终待确认。'
        text += '\n本轮未提供历史候选，history_candidates必须为[]；不得填入猜测的问题、历史记录或未共享的候选ID。'
    if purpose == 'summary':
        text += '\n额外必须有summary对象，严格符合以下JSON Schema：' + json.dumps(reference_schema(Summary, context), ensure_ascii=False)
        text += '\n总结是草稿。历史未执行修复时不得声称修复已验证。'
    if purpose in ('analyze', 'summary'):
        text += '\nevidence_refs只能填写本轮共享执行证据的id；不得填入代码版本ID、题目ID、对话ID。没有共享执行证据时evidence_refs必须为[]。'
    if purpose == 'analyze':
        text += '\nfacts.evidence_id同样只能填写本轮共享执行证据的id；没有共享执行证据时facts必须为[]。'
    return text


def narrative_prompt(context):
    """Production model contract: explanations only; backend supplies bindings."""
    purpose=context.get('purpose','chat')
    envelope={'analyze':AnalysisEnvelope,'summary':SummaryEnvelope}.get(purpose,NarrativeEnvelope)
    text='只输出以下JSON对象。protocol_version固定为2。模型只填写解释、建议及待确认候选；证据关联和运行事实由服务端生成。JSON Schema：'
    text+=json.dumps(envelope.model_json_schema(),ensure_ascii=False)
    text+='\n不要输出任何内部标识、执行事实对象、历史关联或用户确认状态。解释文字不得改写真实运行结果，不得虚构执行。认知假设始终待确认。'
    if purpose=='summary':
        text+='\n总结是草稿。历史未执行修复时不得声称修复已验证。章节顺序必须为：'+'、'.join(SUMMARY_HEADINGS[context['learning_preferences']['summary']])
    return text


def context_for(store, task, version, value):
    available = {e['id']:e for e in task['evidence'] if e['version_id']==version['id']
                 and e['result'].get('status') != 'running'}
    selected = value.evidence_ids
    if selected is not None and any(i not in available for i in selected):
        raise Conflict('选择的执行证据不属于当前题目、当前版本或尚未完成。')
    evidence = [available[i] for i in selected] if selected is not None else list(available.values())[-6:]
    if any(e['source_sha256']!=version['source_sha256'] for e in evidence):
        raise Conflict('执行证据与当前版本不匹配。')
    if value.expected_source_sha256 and value.expected_source_sha256!=version['source_sha256']:
        raise Conflict('代码校验值已变化，请重新核对当前版本。')
    history = [{k:t[k] for k in ('id','version_id','source_sha256','message','answer','provider')}
               for t in task['turns'] if t['status']=='completed'][-12:]
    ctx={'task':{k:task[k] for k in ('id','title','statement','source_url','revision')},
         'version':version,'evidence':evidence,'history':history,'help_level':store.settings()['help_level'],
         'learning_preferences':store.preferences('local',task['id']), 'purpose':value.purpose,
         'explicit_direct':value.explicit_direct,'version_changed':bool(history and history[-1]['version_id']!=version['id']),
         'context_limits':'最近12轮成功对话和最多6条用户共享证据；历史消息所述旧执行不证明当前代码。'}
    if value.purpose=='summary':
        ctx['code_history']=[{k:v[k] for k in ('id','number','language','source','source_sha256')} for v in task['versions'][-4:]]
        ctx['error_history']=[{k:e[k] for k in ('id','version_id','evidence_id','reason','status','revision')} for e in task['errors'][:8]]
    return ctx


def model_context(context):
    """Only deliberate public fields, never nested runner metadata or private cases."""
    def fields(value, allowed):
        return {k:value[k] for k in allowed if k in value}
    version_fields=('id','number','language','source','source_sha256')
    result = {k:context[k] for k in ('purpose','explicit_direct','version_changed','context_limits','help_level') if k in context}
    result['task']=fields(context['task'],('id','title','statement','source_url','revision'))
    result['version']=fields(context['version'],version_fields)
    result['learning_preferences']=fields(context['learning_preferences'],('guidance','hint','analysis','length','summary'))
    result['history']=[fields(t,('id','version_id','source_sha256','message','answer','provider')) for t in context['history'][-12:]]
    if 'code_history' in context:
        result['code_history']=[fields(v,version_fields) for v in context['code_history'][-4:]]
    if 'error_history' in context:
        result['error_history']=[fields(e,('id','version_id','evidence_id','reason','status','revision')) for e in context['error_history'][:8]]
    result['evidence']=[]
    for e in context['evidence'][:6]:
        r=e['result']; hidden=bool(r.get('hidden'))
        item={k:e[k] for k in ('id','version_id','source_sha256','kind')}
        item['result']={k:r[k] for k in ('status','executed','matches_expected') if k in r}
        item['result']['hidden']=hidden
        if not hidden:
            item['stdin']=e['stdin'][:16000]; item['expected_output']=e['expected_output']
            item['result'].update({k:str(r[k])[:16000] for k in ('stdout','stderr','error','notes') if r.get(k) is not None})
        result['evidence'].append(item)
    return result


def parse_output(output, context):
    purpose=context.get('purpose','chat')
    expected={'answer','suggested_tags'} | ({'analysis'} if purpose=='analyze' else {'summary'} if purpose=='summary' else set())
    if not isinstance(output,dict) or set(output)!=expected:
        raise ValueError('invalid envelope')
    answer=output.get('answer'); tags=output.get('suggested_tags',[])
    if not isinstance(answer,str) or not answer.strip() or len(answer)>30000:
        raise ValueError('invalid answer')
    if not isinstance(tags,list) or len(tags)>5 or any(type(t) is not str or not t.strip() or len(t)>40 for t in tags):
        raise ValueError('invalid tags')
    parsed=None
    if purpose=='analyze': parsed=Analysis.model_validate(output.get('analysis')).model_dump()
    if purpose=='summary':
        parsed=Summary.model_validate(output.get('summary')).model_dump()
        if [s['heading'] for s in parsed['sections']] != SUMMARY_HEADINGS[context['learning_preferences']['summary']]:
            raise ValueError('summary sections mismatch')
        if len('\n\n'.join(s['heading']+'\n'+s['text'] for s in parsed['sections']))>10000:
            raise ValueError('summary exceeds editable storage contract')
    checks={'scope':'仅核对结构化事实与原始证据字段；自由解释未经全面事实验证。','conflicts':[], 'verified_facts':0}
    if parsed is not None:
        refs={e['id']:e for e in model_context(context)['evidence']}
        if any(eid not in refs for eid in parsed['evidence_refs']): raise ValueError('unknown evidence reference')
        for fact in parsed.get('facts',[]):
            e=refs.get(fact['evidence_id'])
            actual=e['result'].get(fact['field']) if e else None
            if actual is None or type(actual)!=type(fact['value']) or actual!=fact['value']:
                checks['conflicts'].append(fact)
            else: checks['verified_facts']+=1
        # No historical candidate corpus is shared with this bounded contract.
        if parsed.get('history_candidates'): raise ValueError('unknown historical candidate')
    return {'answer':answer,'suggested_tags':tags,'structured':parsed,'fact_checks':checks}


def _backend_evidence(context):
    """Validate server context before deriving a trusted, non-hidden binding."""
    task=context.get('task');version=context.get('version');evidence=context.get('evidence',[])
    if not isinstance(task,dict) or not isinstance(version,dict):raise ValueError('invalid binding context')
    task_id=task.get('id');version_id=version.get('id');source=version.get('source');digest=version.get('source_sha256')
    if (type(task_id) is not str or not task_id or type(version_id) is not str or not version_id
            or version.get('task_id')!=task_id or type(source) is not str or type(digest) is not str
            or hashlib.sha256(source.encode('utf8')).hexdigest()!=digest):
        raise ValueError('invalid version binding')
    if type(evidence) is not list or len(evidence)>6:raise ValueError('invalid evidence scope')
    visible=[];hidden=set();seen=set()
    for item in evidence:
        if not isinstance(item,dict):raise ValueError('invalid evidence binding')
        eid=item.get('id');result=item.get('result')
        if (type(eid) is not str or not eid or eid in seen or item.get('task_id')!=task_id
                or item.get('version_id')!=version_id or item.get('source_sha256')!=digest
                or not isinstance(result,dict) or type(result.get('status')) is not str
                or not result['status'] or result['status']=='running'):
            raise ValueError('invalid evidence binding')
        seen.add(eid)
        if result.get('hidden'):hidden.add(eid)
        else:visible.append(item)
    binding={'source':'backend','task_id':task_id,'version_id':version_id,'source_sha256':digest,
        'evidence_ids':[item['id'] for item in visible]}
    return visible,hidden,binding


def _backend_facts(evidence):
    facts=[]
    for item in evidence:
        for field in ('status','executed','stdout','stderr','matches_expected'):
            value=item['result'].get(field)
            if value is None:continue
            expected=bool if field in ('executed','matches_expected') else str
            if type(value) is not expected:raise ValueError('invalid backend evidence field')
            facts.append({'evidence_id':item['id'],'field':field,'value':value})
    # Preserve the existing canonical draft limit; omitted fields are not claims.
    return facts[:12]


def _backend_observed(evidence):
    if not evidence:return '没有本轮可绑定的公开执行证据；不能据此认定程序实际输出或修复已执行。'
    records=[{'evidence_id':item['id'],'result':{field:item['result'][field]
        for field in ('status','executed','stdout','stderr','matches_expected') if field in item['result']}}
        for item in evidence]
    text='后端当前版本公开证据记录（外部状态仍是用户录入，不等同本机执行）：\n'+json.dumps(records,ensure_ascii=False)
    return text if len(text)<=5000 else text[:4960]+'\n（展示已截断；完整运行记录为准。）'


def parse_model_output(output, context):
    """Keep legacy failures strict; derive v2 facts independently of the model."""
    if not isinstance(output,dict):raise ValueError('invalid envelope')
    purpose=context.get('purpose','chat')
    if 'protocol_version' not in output:
        # This call remains the legacy validation boundary and cancellation hook.
        parsed=parse_output(output,context)
        if parsed['structured'] is not None:
            _,hidden,binding=_backend_evidence(context)
            claimed=set(parsed['structured']['evidence_refs'])|{fact['evidence_id'] for fact in parsed['structured'].get('facts',[])}
            if claimed&hidden:raise ValueError('hidden evidence reference')
            parsed['evidence_binding']=binding
        return parsed
    if type(output['protocol_version']) is not int or output['protocol_version']!=2:
        raise ValueError('invalid narrative version')
    envelope={'analyze':AnalysisEnvelope,'summary':SummaryEnvelope}.get(purpose,NarrativeEnvelope)
    data=envelope.model_validate(output).model_dump()
    if not data['answer'].strip() or any(not tag.strip() or len(tag)>40 for tag in data['suggested_tags']):
        raise ValueError('invalid answer or tags')
    evidence,_,binding=_backend_evidence(context)
    structured=None;facts=[]
    if purpose=='analyze':
        facts=_backend_facts(evidence)
        structured=Analysis.model_validate({**data['analysis'],'observed':_backend_observed(evidence),
            'evidence_refs':binding['evidence_ids'],'facts':facts,'history_candidates':[]}).model_dump()
    if purpose=='summary':
        structured=Summary.model_validate({**data['summary'],'evidence_refs':binding['evidence_ids']}).model_dump()
        if [section['heading'] for section in structured['sections']]!=SUMMARY_HEADINGS[context['learning_preferences']['summary']]:
            raise ValueError('summary sections mismatch')
        if len('\n\n'.join(section['heading']+'\n'+section['text'] for section in structured['sections']))>10000:
            raise ValueError('summary exceeds editable storage contract')
    return {'answer':data['answer'],'suggested_tags':data['suggested_tags'],'structured':structured,
        'fact_checks':{'source':'backend','scope':'证据关联和结构化事实由后端当前公开记录生成；模型解释、建议及认知假设仍待核对。',
            'conflicts':[],'verified_facts':len(facts)},'evidence_binding':binding}
