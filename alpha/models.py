from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, ConfigDict, field_validator

Language = Literal['c', 'cpp', 'python', 'java', 'javascript']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)


class Task(Model):
    title: str = Field(min_length=1, max_length=160)
    statement: str = Field(default='', max_length=30000)
    source_url: str = Field(default='', max_length=2000)
    language: Language = 'python'
    code: str = Field(default='', max_length=80000)
    source_platform: str | None = Field(default=None, max_length=120)
    original_id: str | None = Field(default=None, max_length=160)
    knowledge_primary: str | None = Field(default=None, max_length=80)
    knowledge_tags: list[str] | None = Field(default=None, max_length=20)
    import_status: Literal['manual', 'pending_link', 'pending_ocr', 'builtin'] | None = None
    source_candidate: dict | None = None
    source_confirmed: bool = False

    @field_validator('knowledge_tags')
    @classmethod
    def knowledge_tags_bounded(cls, value):
        if value is not None and any(not t.strip() or len(t) > 80 for t in value):
            raise ValueError('知识标签无效')
        return list(dict.fromkeys(t.strip() for t in value)) if value is not None else None

    @field_validator('title')
    @classmethod
    def title_nonempty(cls, value):
        if not value.strip():
            raise ValueError('请输入题目名称')
        return value.strip()

    @field_validator('source_url')
    @classmethod
    def safe_url(cls, value):
        value = value.strip()
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('来源链接请使用 http 或 https 地址')
        return value


class SaveTask(Task):
    revision: int = Field(ge=0)


class Run(Model):
    version_id: str = Field(min_length=1, max_length=64)
    stdin: str = Field(default='', max_length=16000)
    expected_output: str | None = Field(default=None, max_length=16000)
    request_id: str = Field(min_length=1, max_length=100)


class External(Model):
    version_id: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=120)
    verdict: str = Field(min_length=1, max_length=120)
    notes: str = Field(default='', max_length=10000)

    @field_validator('source', 'verdict')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('请填写平台和判题结果')
        return value.strip()


class Chat(Model):
    version_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=1, max_length=100)
    purpose: Literal['chat','analyze','summary'] = 'chat'
    evidence_ids: list[str] | None = Field(default=None, max_length=6)
    expected_source_sha256: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')
    explicit_direct: bool = False

    @field_validator('message')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('请输入想问的问题')
        return value.strip()


class Mistake(Model):
    original_version_id: str = Field(min_length=1, max_length=64)
    repair_version_id: str | None = Field(default=None, max_length=64)
    summary: str = Field(default='', max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    confirmed: bool = False
    needs_review: bool = True

    @field_validator('tags')
    @classmethod
    def clean_tags(cls, values):
        result = list(dict.fromkeys(s.strip() for s in values if s.strip()))
        if any(len(s) > 40 for s in result):
            raise ValueError('每个标签最多 40 字')
        return result


class Review(Model):
    outcome: Literal['understood', 'unsure', 'retry']
    notes: str = Field(default='', max_length=5000)


class Settings(Model):
    help_level: Literal['hint', 'step', 'direct'] = 'step'
    review_intensity: Literal['light', 'standard', 'intensive'] = 'standard'
    proactive: bool = True


class AISettings(Model):
    mode: Literal['disabled', 'demo', 'deepseek']
    api_key: str = Field(default='', max_length=4096, repr=False)
    approved_requests: int = Field(default=0, ge=0, le=100)
    approve_paid_calls: bool = False
    model: Literal['deepseek-flash','deepseek-v4-pro'] = 'deepseek-flash'
    budget_usd: float = Field(default=0.5, gt=0, le=100, allow_inf_nan=False)
    timeout: int = Field(default=45, ge=1, le=120)


class RetestApproval(Model):
    api_key: str = Field(min_length=1,max_length=4096,repr=False)
    challenge_id: str = Field(min_length=16,max_length=128)
    confirmation_token: str = Field(min_length=20,max_length=128,repr=False)
    review_fingerprint: str = Field(pattern='^[0-9a-f]{64}$')
    confirm_scope: bool = Field(strict=True)
    approve_paid_calls: bool = Field(strict=True)


class TeachingDecision(Model):
    action: Literal['accept','modified','rejected','ignored','uncertain']
    text: str | None = Field(default=None,max_length=10000)
    observed: str | None = Field(default=None,max_length=5000)


class SourceRequest(Model):
    url: str = Field(default='',max_length=2000)
    text: str = Field(default='',max_length=30000)


class OCRRequest(Model):
    image_base64: str = Field(min_length=1,max_length=7000000)


class RecommendationStatus(Model):
    status: Literal['accepted', 'ignored', 'completed']


class Practice(Model):
    language: Language
    new_task: bool = False
    request_id: str = Field(min_length=1, max_length=100)


class Submission(Model):
    version_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=100)


class ErrorRecord(Model):
    version_id: str = Field(min_length=1, max_length=64)
    evidence_id: str | None = Field(default=None, max_length=64)
    observed: str = Field(min_length=1, max_length=5000)
    category: str = Field(default='', max_length=80)
    reason: str = Field(default='', max_length=5000)
    status: Literal['ai_candidate', 'user_confirmed', 'user_modified', 'uncertain'] = 'uncertain'
    revision: int | None = Field(default=None, ge=1)


class Association(Model):
    left_error_id: str = Field(min_length=1, max_length=64)
    right_error_id: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1, max_length=5000)


class AssociationStatus(Model):
    status: Literal['pending', 'confirmed', 'rejected', 'ignored', 'revoked']


class Preferences(Model):
    preset: Literal['light', 'standard', 'deep', 'custom'] | None = None
    guidance: int | None = Field(default=None, ge=0, le=2, strict=True)
    hint: int | None = Field(default=None, ge=0, le=2, strict=True)
    analysis: int | None = Field(default=None, ge=0, le=2, strict=True)
    summary: int | None = Field(default=None, ge=0, le=2, strict=True)
    association: int | None = Field(default=None, ge=0, le=2, strict=True)
    frequency: int | None = Field(default=None, ge=0, le=2, strict=True)
    difficulty: int | None = Field(default=None, ge=0, le=2, strict=True)
    length: int | None = Field(default=None, ge=0, le=2, strict=True)
