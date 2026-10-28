"""Server-side providers reuse the frozen protocol and the web evidence adapter."""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
import urllib.error
import urllib.request

# Legacy experiment adapters are imported only when explicitly used. Alpha uses
# AIConfig/chat_body/http_transport and must not load legacy question fixtures.

ERROR_MESSAGES = {
    'AUTH_FAILED': 'API Key 无效或无权访问，请打开 AI 设置核对凭据。',
    'INSUFFICIENT_BALANCE': 'API 余额不足，本次未生成建议；请自行核对服务商账户，不要反复重试。',
    'MODEL_UNAVAILABLE': 'Model 不存在或不可用，或 API 路径/请求格式不兼容。',
    'TIMEOUT': '连接超时，请稍后手动重试。',
    'URL_UNREACHABLE': 'Base URL 无法访问或发生了不允许的重定向。',
    'NETWORK_ERROR': '网络连接失败，请检查 Base URL 和网络。',
    'INCOMPATIBLE_RESPONSE': 'Provider 返回格式不兼容，请确认支持 Chat Completions 与 JSON 输出。',
    'PROVIDER_ERROR': 'Provider 未完成请求，请检查服务状态后手动重试。',
    'UNKNOWN_ERROR': '连接测试失败，请核对配置后手动重试。',
    'ACCEPTANCE_BUDGET_EXHAUSTED': '本轮真实 API 验收请求预算已用完，已停止外部请求。',
    'ACCEPTANCE_ENDPOINT_REJECTED': '本轮验收仅允许 DeepSeek 官方 HTTPS Chat Completions 与指定模型。',
    'ACCEPTANCE_STOPPED': '此前出现凭据或余额问题，本轮已停止真实请求，请保留记录。',
}


class AIUnavailable(RuntimeError):
    def __init__(self, message, *, code='PROVIDER_ERROR', http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class AIProvider(Protocol):
    provenance: str
    def __call__(self, feature: str, public: dict, artifact: Path) -> dict: ...
    def public_config(self) -> dict: ...


def chat_completions_url(base):
    validate_base_url(base)
    base = base.rstrip('/')
    return base if base.endswith('/chat/completions') else base + '/chat/completions'


def is_official_deepseek(config):
    return urlsplit(config.base_url).hostname == 'api.deepseek.com'


def chat_body(config, messages, parameters):
    body = {'model': config.model, 'messages': messages,
            'response_format': {'type': 'json_object'}, **parameters}
    if is_official_deepseek(config):
        body.update(thinking={'type': 'disabled'}, stream=False)
        body.setdefault('max_tokens', 2048)
    return body


def json_format_example(feature):
    # Shape only, never a prewritten diagnosis or repair for the current code.
    value = {'interpretation': '说明学生表达的理解。', 'answer_quotes': [],
             'evidence_sufficiency': 'partial', 'next_action': 'clarify_meaning',
             'student_message': '请说明你观察到的当前版本运行结果。',
             'decision_basis': '仅依据当前请求；格式示例不代表本题事实。',
             'uncertainties': ['尚不确定学生的理解。'], 'evidence_refs': [], 'execution_requests': []}
    if feature != 'F01':
        value.update(question_intent='澄清当前理解', stop_questioning=False, stop_reason='not_stopping')
    return '\nJSON FORMAT EXAMPLE (shape only; adapt to the actual input, do not copy facts):\n' + json.dumps(value, ensure_ascii=False)


def validate_base_url(base):
    try:
        parsed=urlsplit(base)
        local=parsed.hostname in {'localhost','127.0.0.1','::1'}
        valid=(parsed.hostname and parsed.scheme in {'http','https'} and
               (parsed.scheme!='http' or local) and not parsed.username and not parsed.password and
               not parsed.query and not parsed.fragment)
        # Accessing port also rejects malformed/invalid port numbers.
        parsed.port
        if not valid:raise ValueError()
    except ValueError:
        raise ValueError('Base URL 必须是合法 HTTPS 地址；仅本机允许 HTTP，URL 不得携带凭据/查询参数。') from None


@dataclass(frozen=True)
class AIConfig:
    provider: str = 'codex_cli'
    base_url: str = ''
    model: str = ''
    api_key: str = field(default='',repr=False)
    timeout: float = 60
    parameters: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, env):
        provider=env.get('AIC_AI_PROVIDER','codex_cli').strip()
        if provider not in {'codex_cli','api','disabled'}:
            raise ValueError('AIC_AI_PROVIDER 必须为 codex_cli、api 或 disabled')
        base=env.get('AIC_AI_BASE_URL','').strip().rstrip('/')
        model=env.get('AIC_AI_MODEL','').strip()
        key=env.get('AIC_AI_API_KEY','')
        try:
            timeout=float(env.get('AIC_AI_TIMEOUT_SECONDS','60'))
            parameters=json.loads(env.get('AIC_AI_GENERATION_PARAMS','{}'))
            if not math.isfinite(timeout) or not 1<=timeout<=1200 or not isinstance(parameters,dict):raise ValueError()
            ranges={'temperature':(0,2),'top_p':(0,1),'max_tokens':(1,8192)}
            for name,value in parameters.items():
                if name not in ranges or type(value) not in (int,float) or not math.isfinite(value) or not ranges[name][0]<=value<=ranges[name][1]:raise ValueError()
                if name=='max_tokens' and type(value) is not int:raise ValueError()
        except (ValueError,TypeError):
            raise ValueError('AI 超时或生成参数无效；允许 temperature、top_p、max_tokens') from None
        if provider=='api':
            if not model or not key:
                raise ValueError('api 需要 Model、服务器 API Key 和 HTTPS Base URL；仅本机允许 HTTP，URL 不得携带凭据/查询参数')
            validate_base_url(base)
            if any(ch in key for ch in '\r\n') or len(key)>4096 or len(model)>200:
                raise ValueError('AI 密钥或 Model 格式无效')
        return cls(provider,base,model,key,timeout,parameters)

    @classmethod
    def from_env(cls, env_file=None):
        values={}
        if env_file and Path(env_file).exists():
            for line in Path(env_file).read_text(encoding='utf-8-sig').splitlines():
                line=line.strip()
                if not line or line.startswith('#'):continue
                if '=' not in line:raise ValueError('.env 需要 KEY=VALUE 格式')
                name,value=line.split('=',1);value=value.strip()
                if value[:1] in {'"',"'"} and value[-1:]==value[:1]:value=value[1:-1]
                values[name.strip()]=value
        values.update({k:v for k,v in os.environ.items() if k.startswith('AIC_AI_')})
        return cls.from_mapping(values)

    def public(self):
        from . import teaching
        # Endpoint/key remain entirely server-side; no HTTP credentials in exports.
        return {'provider':self.provider,'model':self.model if self.provider=='api' else
                teaching.f01.MODEL if self.provider=='codex_cli' else None,
                'timeout_seconds':self.timeout if self.provider=='api' else teaching.f01.TIMEOUT,
                'generation_parameters':self.parameters if self.provider=='api' else {},
                'key_configured':bool(self.api_key) if self.provider=='api' else False}


class CodexCLIProvider:
    provenance='real_official_Codex_CLI'
    def __init__(self, config):
        from . import teaching
        self.config=config;self.delegate=teaching.OfficialModel()
    def public_config(self):return self.config.public()
    def __call__(self,feature,public,artifact):
        result=self.delegate(feature,public,artifact)
        result['metadata']={**(result.get('metadata') or {}),'provider':'codex_cli',
                            'provider_success':bool((result.get('metadata') or {}).get('CLI_success'))}
        return result


class DisabledProvider:
    provenance='AI_disabled'
    def __init__(self, config):self.config=config
    def public_config(self):return self.config.public()
    def __call__(self,feature,public,artifact):
        raise AIUnavailable('AI 当前已关闭。请打开顶部 AI 设置并保存配置，再手动重试；也可返回首页使用离线固定提示。')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise AIUnavailable('AI API 重定向被拒绝', code='URL_UNREACHABLE')


def http_transport(url,headers,body,timeout):
    request=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode('utf8'),headers=headers,method='POST')
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=timeout) as response:
            raw=response.read(1048577)
            if len(raw)>1048576:raise AIUnavailable('AI API 响应超出 1 MiB 限制',code='INCOMPATIBLE_RESPONSE')
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        code = 'AUTH_FAILED' if exc.code in {401,403} else 'INSUFFICIENT_BALANCE' if exc.code == 402 else 'MODEL_UNAVAILABLE' if exc.code in {400,404,422} else 'PROVIDER_ERROR'
        raise AIUnavailable('AI API HTTP '+str(exc.code),code=code,http_status=exc.code) from None
    except TimeoutError:
        raise AIUnavailable('AI API 网络、超时或响应格式错误',code='TIMEOUT') from None
    except urllib.error.URLError as exc:
        code='TIMEOUT' if isinstance(exc.reason,TimeoutError) else 'NETWORK_ERROR'
        raise AIUnavailable('AI API 网络、超时或响应格式错误',code=code) from None
    except OSError:
        raise AIUnavailable('AI API 网络、超时或响应格式错误',code='NETWORK_ERROR') from None
    except ValueError:
        raise AIUnavailable('AI API 网络、超时或响应格式错误',code='INCOMPATIBLE_RESPONSE') from None


class APIProvider:
    provenance='real_external_AI_API'
    def __init__(self,config,transport=None):
        self.config=config;self.transport=transport or http_transport
    def public_config(self):return self.config.public()
    def redact(self,value):
        # Even an upstream response that echoes the exact key cannot export it.
        if isinstance(value,str):return value.replace(self.config.api_key,'[REDACTED]')
        if isinstance(value,list):return [self.redact(v) for v in value]
        if isinstance(value,dict):return {self.redact(k):self.redact(v) for k,v in value.items()}
        return value
    def __call__(self,feature,public,artifact):
        artifact=Path(artifact);artifact.mkdir(parents=True,exist_ok=False)
        from . import teaching
        public=self.redact(public)
        engine=teaching.f01 if feature=='F01' else teaching.f02
        prompt=teaching.model_prompt(feature,public)
        schema=json.loads((engine.ROOT/'schema.json').read_text(encoding='utf8'))
        messages=[]
        if public.get('execution_context') is not None:
            messages.append({'role':'system','content':teaching.EXECUTION_BOUNDARY})
        instructions = prompt+'\nReturn only JSON matching this schema:\n'+json.dumps(schema,ensure_ascii=False)
        if is_official_deepseek(self.config):
            instructions += json_format_example(feature)
        messages.append({'role':'user','content':instructions})
        body=chat_body(self.config, messages, self.config.parameters)
        try:
            response=self.transport(chat_completions_url(self.config.base_url),
                {'Content-Type':'application/json','Authorization':'Bearer '+self.config.api_key},body,self.config.timeout)
            choice=response['choices'][0]
            if is_official_deepseek(self.config) and choice.get('finish_reason') not in {None, 'stop'}:
                raise ValueError('Incomplete model response')
            content=self.redact(choice['message']['content'])
            output=json.loads(content)
        except AIUnavailable as exc:
            # No exception text, body or request headers is logged by this boundary.
            code = exc.code if exc.code in ERROR_MESSAGES else 'PROVIDER_ERROR'
            http_status = exc.http_status if type(exc.http_status) is int and 100 <= exc.http_status <= 599 else None
            raise AIUnavailable(ERROR_MESSAGES[code] + ' 未生成教学建议。', code=code, http_status=http_status) from None
        except Exception:
            raise AIUnavailable(ERROR_MESSAGES['INCOMPATIBLE_RESPONSE'] + ' 未生成教学建议。', code='INCOMPATIBLE_RESPONSE') from None
        checks=teaching.validate_reply(feature,output,public)
        metadata={'provider':'api','provider_success':True,'actual_model_call':True,
                  'model_requested':self.config.model,'usage':self.redact(response.get('usage')),
                  'provenance':self.provenance,'CLI_success':False}
        if is_official_deepseek(self.config):
            metadata.update(model_returned=self.redact(response.get('model')), finish_reason=choice.get('finish_reason'),
                            thinking_type='disabled', max_tokens=body['max_tokens'])
        for name,value in [('raw_response.json',output),('validation.json',checks),('metadata.json',metadata)]:
            (artifact/name).write_text(json.dumps(self.redact(value),ensure_ascii=False,indent=2),encoding='utf8')
        return {'output':output,'checks':checks,'metadata':metadata}


def make_provider(config=None, *, transport=None):
    config=config or AIConfig.from_env(Path(__file__).resolve().parent.parent/'.env')
    if config.provider == 'api' and transport is not None:
        return APIProvider(config, transport=transport)
    return {'codex_cli':CodexCLIProvider,'api':APIProvider,'disabled':DisabledProvider}[config.provider](config)
