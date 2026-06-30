"""Job DTO / Schema（Pydantic v2）

职责：
- 定义岗位域的 Pydantic Model，作为 API 层 ↔ Service 层之间的数据契约
- 入参（Request）做严格校验：创建岗位时强校验 JD 长度 / 薪资区间 / 来源平台
- 出参（Response）只暴露公开字段，绝不泄露其他用户的内部状态
- 分析结果（JobAnalysisResult）描述 Job Analysis Agent 的完整 LLM 产出
  （PRD §5.2），包含 ORM 不存的扩展字段（salary_range / company_info / hidden_requirements）

设计动机：
- DTO 与 ORM Model 分离：DTO 是 API 契约，ORM 是数据库映射
  · ORM 增字段不会自动泄露到前端（强边界）
  · DTO 可按场景裁剪字段（列表摘要 vs 详情）
- DTO 与 Validator 分离：DTO 描述「数据结构」，校验规则在字段内联
  · 简单长度/枚举校验内联为 field_validator，避免过度拆分
  · 跨方法复用的复杂规则（黑名单 / 敏感词等）才抽到 validator.py
- 分析结果是独立子模型（JobAnalysisResult）而非平铺到 JobResponse：
  · 「已分析」之前该字段为 None，避免响应里出现空字符串误导前端
  · 子模型可被 Agent / Cache / 前端独立引用（Step 1.6.12 缓存按 JobAnalysisResult 序列化）
- 异步分析响应（JobAnalyzeResponse）独立于同步 JobResponse：
  · 严格遵循 Step 1.6.10 「POST /analyze 返回 202 + {task_id}」契约
  · 避免把 task_id 混入同步响应造成字段语义混乱

字段约束对齐（与 ORM Model 保持一致，参考 app/infra/database/models/job.py）：
- title: 1-300 字符
- company: 1-300 字符
- jd_text: 1-50000 字符（Text 字段无 DB 长度限制，DTO 兜底防 OOM）
- source: boss/liepin/zhilian/shixisheng（枚举白名单）
- source_url: 0-1000 字符
- location: 0-200 字符
- skills/keywords: 数组，最多 100 个元素（覆盖实际场景 + 防注入式超大数组）
- salary: 0-1000 K（覆盖实际薪资区间，超出视为脏数据）

安全设计：
- 响应模型（JobResponse）只暴露白名单字段，不返回内部审计字段
- jd_text 在列表场景下裁剪为 jd_preview（前后 200 字符），详情场景才返回全文
  · 列表场景可能返回数十条岗位，全文会撑爆响应体
  · 客户端按需调 GET /api/jobs/{id} 拉全文
- 跨用户隔离由 Service 层按 user_id 过滤保证，DTO 不参与权限判断
- source 枚举白名单：避免脏数据污染数据库

潜在风险：
- 分析结果 schema 升级：DTO 用 Optional + 默认空结构，向后兼容
  → 防御：`extra="ignore"` 容忍额外字段；Agent 端 schema 升级不影响旧客户端
- 薪资单位混乱：DTO 强制 min <= max + 0-1000K 范围，超出拒绝
  → Service 层可补充「单位换算」（如 25K/月 vs 30万/年）
- 分析任务被重复触发：DTO 层无法防御（业务唯一性由 Step 1.16 业务幂等键保证）
  → DTO 只做格式校验，重复触发由 Service 配合 (user_id, business_id) 联合 unique 防御
- jd_text 长度边界：极长 JD（招聘网站复制整页）可能触发 OOM
  → 50000 字符兜底；超过应在前端截断
"""

import uuid
from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ==================== 常量 ====================

# JD 原文最大长度：50KB，覆盖 95% 岗位（正常 JD 1-10KB）
# 限制原因：Text 字段无 DB 长度限制，DTO 层兜底防止恶意上传触发 OOM
JOB_JD_TEXT_MAX_LENGTH: Final[int] = 50_000

# JD 预览长度（列表场景）：前后各 200 字符
JOB_JD_PREVIEW_LENGTH: Final[int] = 200

# 岗位名称/公司名称最大长度：与 ORM String(300) 对齐
JOB_TITLE_MAX_LENGTH: Final[int] = 300
JOB_COMPANY_MAX_LENGTH: Final[int] = 300

# 来源链接最大长度：与 ORM String(1000) 对齐
JOB_SOURCE_URL_MAX_LENGTH: Final[int] = 1000

# 工作地点最大长度：与 ORM String(200) 对齐
JOB_LOCATION_MAX_LENGTH: Final[int] = 200

# 薪资范围边界：单位 K（千），覆盖实际场景（0=实习/不限 → 1000K=顶级专家）
JOB_SALARY_MIN_K: Final[int] = 0
JOB_SALARY_MAX_K: Final[int] = 1000

# 技能/关键词数组最大长度：覆盖实际场景 + 防注入式超大数组
JOB_SKILLS_MAX_LENGTH: Final[int] = 100
JOB_KEYWORDS_MAX_LENGTH: Final[int] = 100
JOB_HIDDEN_REQUIREMENTS_MAX_LENGTH: Final[int] = 50

# 单个字符串元素最大长度：防止超长字符串（DoS + DB bloat）
JOB_SKILL_ITEM_MAX_LENGTH: Final[int] = 100
JOB_KEYWORD_ITEM_MAX_LENGTH: Final[int] = 100
JOB_HIDDEN_REQUIREMENT_ITEM_MAX_LENGTH: Final[int] = 200

# 公司信息字段最大长度
JOB_COMPANY_INDUSTRY_MAX_LENGTH: Final[int] = 100
JOB_COMPANY_SCALE_MAX_LENGTH: Final[int] = 100
JOB_COMPANY_STAGE_MAX_LENGTH: Final[int] = 100

# 来源平台枚举：与 ORM Job.source 字段对齐
# 设计：直接用字面量 Literal，避免引入新枚举类型与 ORM 解耦
_JOB_SOURCE_VALUES: Final[frozenset[str]] = frozenset(
    {"boss", "liepin", "zhilian", "shixiseng"}
)

# 资历等级枚举：与 PRD §5.2 对齐（与 core/constants.py SeniorityLevel 同步）
# 设计：DTO 不导入 core/constants.py 的 Enum，保持字面量与 ORM String 解耦
_SENIORITY_VALUES: Final[frozenset[str]] = frozenset(
    {"intern", "entry", "junior", "mid", "senior", "lead", "principal"}
)

# 难度等级枚举：与 PRD §5.2 对齐
_DIFFICULTY_VALUES: Final[frozenset[str]] = frozenset(
    {"easy", "medium", "hard", "expert"}
)


# ==================== 分析结果子模型 ====================

class SalaryRange(BaseModel):
    """薪资区间（PRD §5.2 JD 分析输出）

    字段：
    - min: 最低薪资（K）
    - max: 最高薪资（K）
    - unit: 单位（固定 'K'，预留扩展如 '万/月'）

    设计：
    - 与 ORM Job.salary_min / salary_max 字段语义对齐，但结构化输出便于前端展示
    - unit 字段预留扩展：未来支持「年薪/万」单位时无需改 DTO
    - min <= max 强约束：由 model_validator 校验
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    min: int = Field(
        ...,
        ge=JOB_SALARY_MIN_K,
        le=JOB_SALARY_MAX_K,
        description="最低薪资（K）",
    )
    max: int = Field(
        ...,
        ge=JOB_SALARY_MIN_K,
        le=JOB_SALARY_MAX_K,
        description="最高薪资（K）",
    )
    unit: str = Field(
        default="K",
        max_length=10,
        description="单位（默认 K，未来可扩展）",
    )

    @model_validator(mode="after")
    def _check_salary_range(self) -> "SalaryRange":
        """校验 min <= max：防止脏数据

        为什么用 model_validator 而非单个 field_validator：
        - 跨字段约束，单字段校验器无法表达
        """
        if self.min > self.max:
            raise ValueError(f"薪资下限 {self.min} 不能大于上限 {self.max}")
        return self


class CompanyInfo(BaseModel):
    """公司信息（PRD §5.2 JD 分析输出）

    字段：
    - industry: 行业（如 互联网/金融/制造业）
    - scale: 规模（如 100-500人/500-1000人）
    - stage: 融资阶段（如 A轮/B轮/已上市/未融资）

    设计：
    - 全部字段可选：LLM 抽取可能无法获取完整公司信息（如猎聘部分公司未填）
    - 不引入枚举：行业/规模/阶段的取值高度开放，强枚举反而限制扩展
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    industry: str | None = Field(
        default=None,
        max_length=JOB_COMPANY_INDUSTRY_MAX_LENGTH,
        description="行业",
    )
    scale: str | None = Field(
        default=None,
        max_length=JOB_COMPANY_SCALE_MAX_LENGTH,
        description="公司规模",
    )
    stage: str | None = Field(
        default=None,
        max_length=JOB_COMPANY_STAGE_MAX_LENGTH,
        description="融资阶段",
    )


class JobAnalysisResult(BaseModel):
    """JD 分析结果（PRD §5.2 Job Analysis Agent 输出）

    完整结构（与 PRD §5.2 一一对应）：
    {
        "skills": ["Python", "LangChain", "FastAPI", "PostgreSQL"],
        "keywords": ["AI应用开发", "RAG", "Agent", "大模型"],
        "seniority": "mid",
        "difficulty": "medium",
        "salary_range": {"min": 25, "max": 40, "unit": "K"},
        "company_info": {
            "industry": "互联网",
            "scale": "500-1000人",
            "stage": "B轮"
        },
        "hidden_requirements": ["可能需要oncall", "有竞业协议"]
    }

    字段层级：
    - 顶层必填（Agent 必须输出）：skills, keywords, seniority, difficulty
    - 顶层可选（LLM 可能未抽取）：salary_range, company_info, hidden_requirements
    - ORM 仅持久化部分字段（skills/keywords/seniority/difficulty/salary_min/salary_max）：
      salary_range / company_info / hidden_requirements 是 DTO 扩展字段，
      缓存或响应时可全量输出，落库时由 Service 层选择性持久化

    设计动机：
    - 与 ORM 解耦：ORM 只存「分析后稳定字段」，DTO 描述「Agent 完整输出」
    - 缓存友好：整个子模型可序列化/反序列化到 Redis（Step 1.6.12 JobAnalysisCache）
    - 客户端友好：前端拿到完整结构直接渲染，无需拼装
    """

    model_config = ConfigDict(
        # 允许从 ORM + 扩展字段（dict）混合创建
        from_attributes=True,
        # 容忍 Agent 输出额外字段：未来 schema 升级不影响旧客户端
        extra="ignore",
    )

    skills: list[str] = Field(
        default_factory=list,
        max_length=JOB_SKILLS_MAX_LENGTH,
        description="提取的技能列表",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=JOB_KEYWORDS_MAX_LENGTH,
        description="提取的关键词",
    )
    seniority: str | None = Field(
        default=None,
        description="资历要求（intern/entry/junior/mid/senior/lead/principal）",
    )
    difficulty: str | None = Field(
        default=None,
        description="难度评级（easy/medium/hard/expert）",
    )
    salary_range: SalaryRange | None = Field(
        default=None,
        description="薪资区间（K）",
    )
    company_info: CompanyInfo | None = Field(
        default=None,
        description="公司信息",
    )
    hidden_requirements: list[str] = Field(
        default_factory=list,
        max_length=JOB_HIDDEN_REQUIREMENTS_MAX_LENGTH,
        description="隐藏要求（如 oncall/竞业）",
    )

    @field_validator("skills")
    @classmethod
    def _check_skills_items(cls, value: list[str]) -> list[str]:
        """skills 元素长度校验：防止单元素超长"""
        for item in value:
            if len(item) > JOB_SKILL_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"skill 单个标签长度不能超过 {JOB_SKILL_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @field_validator("keywords")
    @classmethod
    def _check_keywords_items(cls, value: list[str]) -> list[str]:
        """keywords 元素长度校验：防止单元素超长"""
        for item in value:
            if len(item) > JOB_KEYWORD_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"keyword 单个标签长度不能超过 {JOB_KEYWORD_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @field_validator("hidden_requirements")
    @classmethod
    def _check_hidden_requirements_items(cls, value: list[str]) -> list[str]:
        """hidden_requirements 元素长度校验：防止单元素超长"""
        for item in value:
            if len(item) > JOB_HIDDEN_REQUIREMENT_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"hidden_requirement 单条长度不能超过 "
                    f"{JOB_HIDDEN_REQUIREMENT_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @field_validator("seniority")
    @classmethod
    def _check_seniority(cls, value: str | None) -> str | None:
        """seniority 枚举校验：白名单机制"""
        if value is not None and value not in _SENIORITY_VALUES:
            raise ValueError(
                f"seniority 必须是 {sorted(_SENIORITY_VALUES)} 之一"
            )
        return value

    @field_validator("difficulty")
    @classmethod
    def _check_difficulty(cls, value: str | None) -> str | None:
        """difficulty 枚举校验：白名单机制"""
        if value is not None and value not in _DIFFICULTY_VALUES:
            raise ValueError(
                f"difficulty 必须是 {sorted(_DIFFICULTY_VALUES)} 之一"
            )
        return value


# ==================== 入参 DTO ====================

class JobCreateRequest(BaseModel):
    """岗位创建请求（POST /api/jobs）

    字段：
    - title: 岗位名称（必填，1-300 字符）
    - company: 公司名称（必填，1-300 字符）
    - jd_text: JD 原文（必填，1-50000 字符）
    - source: 来源平台（必填，枚举：boss/liepin/zhilian/shixiseng）
    - source_url: 原始链接（可选，最大 1000 字符，ORM 唯一索引去重）
    - salary_min / salary_max: 薪资范围（可选，单位 K）
    - location: 工作地点（可选，最大 200 字符）
    - skills / keywords: 预提取的技能/关键词（可选，通常由 Agent 补全）
    - seniority / difficulty: 资历/难度（可选，通常由 Agent 补全）

    设计：
    - skills/keywords/seniority/difficulty 全部可选：用户手动录入岗位时可填，
      Extension 自动化场景可全空（由 Agent 分析后回填）
    - source_url 唯一性：依赖 ORM 唯一索引去重，DTO 不做查重（避免双层防御）
    - salary_min <= salary_max：model_validator 校验

    业务流：
    1. Extension 抓取 → POST /api/jobs 创建（含 jd_text）
    2. POST /api/jobs/{id}/analyze 触发 Agent 分析
    3. Agent 完成后落库 + 写缓存
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    title: str = Field(
        ...,
        min_length=1,
        max_length=JOB_TITLE_MAX_LENGTH,
        description="岗位名称",
        examples=["AI应用开发工程师"],
    )
    company: str = Field(
        ...,
        min_length=1,
        max_length=JOB_COMPANY_MAX_LENGTH,
        description="公司名称",
        examples=["某互联网公司"],
    )
    jd_text: str = Field(
        ...,
        min_length=1,
        max_length=JOB_JD_TEXT_MAX_LENGTH,
        description="JD 原文",
    )
    source: str = Field(
        ...,
        description="来源平台（boss/liepin/zhilian/shixiseng）",
        examples=["boss"],
    )
    source_url: str | None = Field(
        default=None,
        max_length=JOB_SOURCE_URL_MAX_LENGTH,
        description="原始链接（ORM 唯一约束去重）",
    )
    salary_min: int | None = Field(
        default=None,
        ge=JOB_SALARY_MIN_K,
        le=JOB_SALARY_MAX_K,
        description="最低薪资（K）",
    )
    salary_max: int | None = Field(
        default=None,
        ge=JOB_SALARY_MIN_K,
        le=JOB_SALARY_MAX_K,
        description="最高薪资（K）",
    )
    location: str | None = Field(
        default=None,
        max_length=JOB_LOCATION_MAX_LENGTH,
        description="工作地点",
    )
    skills: list[str] = Field(
        default_factory=list,
        max_length=JOB_SKILLS_MAX_LENGTH,
        description="预提取的技能列表（可选，通常由 Agent 补全）",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=JOB_KEYWORDS_MAX_LENGTH,
        description="预提取的关键词（可选）",
    )
    seniority: str | None = Field(
        default=None,
        description="资历要求（可选，intern/entry/junior/mid/senior/lead/principal）",
    )
    difficulty: str | None = Field(
        default=None,
        description="难度评级（可选，easy/medium/hard/expert）",
    )

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        """source 枚举校验：白名单机制，不在集合内直接拒绝

        不用 Enum：保持字符串字面量，与 ORM/前端/日志一致
        """
        if value not in _JOB_SOURCE_VALUES:
            raise ValueError(
                f"source 必须是 {sorted(_JOB_SOURCE_VALUES)} 之一"
            )
        return value

    @field_validator("seniority")
    @classmethod
    def _check_seniority(cls, value: str | None) -> str | None:
        """seniority 枚举校验"""
        if value is not None and value not in _SENIORITY_VALUES:
            raise ValueError(
                f"seniority 必须是 {sorted(_SENIORITY_VALUES)} 之一"
            )
        return value

    @field_validator("difficulty")
    @classmethod
    def _check_difficulty(cls, value: str | None) -> str | None:
        """difficulty 枚举校验"""
        if value is not None and value not in _DIFFICULTY_VALUES:
            raise ValueError(
                f"difficulty 必须是 {sorted(_DIFFICULTY_VALUES)} 之一"
            )
        return value

    @field_validator("skills")
    @classmethod
    def _check_skills_items(cls, value: list[str]) -> list[str]:
        """skills 元素长度校验"""
        for item in value:
            if len(item) > JOB_SKILL_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"skill 单个标签长度不能超过 {JOB_SKILL_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @field_validator("keywords")
    @classmethod
    def _check_keywords_items(cls, value: list[str]) -> list[str]:
        """keywords 元素长度校验"""
        for item in value:
            if len(item) > JOB_KEYWORD_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"keyword 单个标签长度不能超过 {JOB_KEYWORD_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @model_validator(mode="after")
    def _check_salary_range(self) -> "JobCreateRequest":
        """校验 salary_min <= salary_max + 至少一个值存在

        为什么允许「两个都为 None」：部分岗位（如 Boss直聘）不公开薪资
        """
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError(
                f"薪资下限 {self.salary_min} 不能大于上限 {self.salary_max}"
            )
        return self


class JobAnalyzeRequest(BaseModel):
    """触发 JD 分析请求（POST /api/jobs/analyze）

    字段：
    - job_id: 已入库的岗位 ID
    - session_id: 当前会话 ID（任务归属）

    设计：
    - 仅接受 job_id：避免「不入库直接分析」的孤儿任务
    - session_id 用于创建异步 Task，关联 tasks.session_id 外键
    - 业务唯一性由 Step 1.16 业务幂等键保证（service 层用 (user_id, business_id) 联合 unique）
    - 强类型 UUID：避免字符串误传
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    job_id: uuid.UUID = Field(
        ...,
        description="已入库的岗位 ID",
    )
    session_id: uuid.UUID = Field(
        ...,
        description="当前会话 ID（用于创建异步任务）",
    )


# ==================== 出参 DTO ====================

class JobSummary(BaseModel):
    """岗位摘要（列表用）

    用途：GET /api/jobs 列表返回
    设计：
    - 不包含 jd_text 全文（可能 50KB），仅返回 jd_preview
    - 不包含 company_info / hidden_requirements（Agent 输出的扩展字段，详情才返回）
    - 列表场景只需「标题/公司/薪资/地点/技能」即可定位/筛选
    - 减少响应体大小，提升列表接口性能
    """

    model_config = ConfigDict(
        # 支持从 ORM Model 创建：JobSummary.model_validate(orm_job)
        from_attributes=True,
        # 禁止额外字段透传：ORM 多了字段不会自动泄露（白名单机制）
        extra="ignore",
    )

    id: uuid.UUID = Field(
        ...,
        description="岗位 ID（UUID v4）",
    )
    title: str = Field(
        ...,
        description="岗位名称",
    )
    company: str = Field(
        ...,
        description="公司名称",
    )
    salary_min: int | None = Field(
        default=None,
        description="最低薪资（K）",
    )
    salary_max: int | None = Field(
        default=None,
        description="最高薪资（K）",
    )
    location: str | None = Field(
        default=None,
        description="工作地点",
    )
    source: str = Field(
        ...,
        description="来源平台",
    )
    skills: list[str] = Field(
        default_factory=list,
        max_length=JOB_SKILLS_MAX_LENGTH,
        description="技能列表",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=JOB_KEYWORDS_MAX_LENGTH,
        description="关键词",
    )
    seniority: str | None = Field(
        default=None,
        description="资历要求",
    )
    difficulty: str | None = Field(
        default=None,
        description="难度评级",
    )
    jd_preview: str = Field(
        default="",
        max_length=JOB_JD_PREVIEW_LENGTH * 2 + 10,
        description="JD 预览（前后各 200 字符，列表场景替代全文）",
    )
    created_at: datetime = Field(
        ...,
        description="创建时间（ISO 8601）",
    )


class JobListResponse(BaseModel):
    """岗位分页列表响应

    字段：
    - items: 当前页岗位摘要（不含 jd_text 全文 / company_info / hidden_requirements）
    - total: 该用户岗位总数（用于前端分页器计算总页数）
    - limit: 每页大小（回显给前端）
    - offset: 偏移量（回显给前端）

    设计动机：
    - 显式回显 limit/offset：避免前端"请求 limit=20，响应里看不到"导致误解
    - 总数 total 独立于 items：列表长度可能 < limit（末页）
    - 不缓存列表：列表数据频繁变化（创建/分析），缓存命中率低
    """

    model_config = {"extra": "forbid"}

    items: list[JobSummary] = Field(
        default_factory=list,
        description="岗位摘要列表",
    )
    total: int = Field(
        ...,
        ge=0,
        description="岗位总数",
    )
    limit: int = Field(
        ...,
        ge=1,
        description="每页大小（回显）",
    )
    offset: int = Field(
        ...,
        ge=0,
        description="偏移量（>=0）",
    )


class JobResponse(BaseModel):
    """岗位完整响应

    用途：
    - GET /api/jobs/{id} 详情返回
    - POST /api/jobs 创建成功返回
    - 内部 Service 层流转

    字段层级：
    - 基础字段（与 ORM 一一对应）：id/title/company/jd_text/salary/source/location/...
    - analysis: 完整分析结果（None 表示未分析）
    - company_info / hidden_requirements 在 analysis 内（详情场景才返回）

    设计：
    - `from_attributes=True`：支持从 ORM Job 对象直接构建
    - `extra="ignore"`：ORM 增字段不会自动透传
    - jd_text 必返回：Agent 匹配/优化建议阶段需要原文检索
    - analysis 字段：未分析时为 None，前端按需展示「待分析」标签
    """

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    id: uuid.UUID = Field(
        ...,
        description="岗位 ID（UUID v4）",
    )
    title: str = Field(
        ...,
        description="岗位名称",
    )
    company: str = Field(
        ...,
        description="公司名称",
    )
    salary_min: int | None = Field(
        default=None,
        description="最低薪资（K）",
    )
    salary_max: int | None = Field(
        default=None,
        description="最高薪资（K）",
    )
    jd_text: str = Field(
        ...,
        min_length=1,
        max_length=JOB_JD_TEXT_MAX_LENGTH,
        description="JD 原文",
    )
    source: str = Field(
        ...,
        description="来源平台",
    )
    source_url: str | None = Field(
        default=None,
        description="原始链接",
    )
    location: str | None = Field(
        default=None,
        description="工作地点",
    )
    skills: list[str] = Field(
        default_factory=list,
        max_length=JOB_SKILLS_MAX_LENGTH,
        description="技能列表",
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=JOB_KEYWORDS_MAX_LENGTH,
        description="关键词",
    )
    seniority: str | None = Field(
        default=None,
        description="资历要求",
    )
    difficulty: str | None = Field(
        default=None,
        description="难度评级",
    )
    analysis: JobAnalysisResult | None = Field(
        default=None,
        description="JD 分析结果（None 表示未分析）",
    )
    created_at: datetime = Field(
        ...,
        description="创建时间（ISO 8601）",
    )


class JobAnalyzeResponse(BaseModel):
    """触发 JD 分析响应（POST /api/jobs/{id}/analyze）

    字段：
    - job_id: 岗位 ID
    - task_id: 异步任务 ID（前端轮询用）。completed 时为 None
    - status: 任务状态（pending / completed）
    - analysis_result: 已完成的分析结果（仅在 completed 时有值）
    - cached: 是否来自缓存命中

    设计动机（Step 1.6.10 契约）：
    - 异步返回 202 + {task_id}，不是 200 + result
    · Agent 任务耗时 5s+ 必异步（项目规则：LLM 必异步）
    · 202 告知客户端"已接受，请轮询 GET /api/tasks/{task_id}"
    - 同步降级 / 缓存命中时直接返回 completed + analysis_result，减少一次轮询
    - status 字段为前端立即反馈「任务已接收/已完成」
    - 业务唯一性由 Service 层配合 (user_id, business_id) 联合 unique 保证
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    task_id: uuid.UUID | None = Field(
        default=None,
        description="异步任务 ID（pending 时非空，completed 时 None）",
    )
    status: Literal["pending", "completed"] = Field(
        ...,
        description="任务状态（pending: 已入队待处理 / completed: 已完成）",
    )
    analysis_result: JobAnalysisResult | None = Field(
        default=None,
        description="已完成的分析结果（status=completed 时返回）",
    )
    cached: bool = Field(
        default=False,
        description="是否来自缓存命中",
    )


# ==================== JD 解析结果 ====================

# JD 段落类型：基础三段式
# - responsibilities: 职位描述 / 工作内容 / 岗位职责
# - requirements: 任职要求 / 岗位要求 / 职位要求
# - benefits: 福利待遇 / 薪资福利 / 福利
JDSectionType = Literal["responsibilities", "requirements", "benefits", "other"]

# 段落标题映射（中文优先，英文备选）
# key: section_type, value: 可能的标题列表
JD_SECTION_TITLES: Final[dict[str, list[str]]] = {
    "responsibilities": [
        "职位描述", "工作内容", "岗位职责", "工作职责", "职责描述",
        "岗位描述", "工作描述", "职位简介", "岗位信息",
        "Responsibilities", "Job Description", "Description",
    ],
    "requirements": [
        "任职要求", "岗位要求", "职位要求", "任职资格", "要求",
        "岗位要求", "能力要求", "基本要求", "任职条件",
        "Requirements", "Qualifications", "Requirements",
    ],
    "benefits": [
        "福利待遇", "薪资福利", "福利", "薪酬福利", "薪资待遇",
        "待遇", "福利信息", "薪酬待遇",
        "Benefits", "Compensation", "Perks",
    ],
}


class JDParseResult(BaseModel):
    """JD 解析结果

    职责：
    - 存储 JD 文本的预处理和分段结果
    - 为 Job Analysis Agent 提供结构化输入

    字段：
    - raw_text: 原始 JD 文本（未处理）
    - cleaned_text: 预处理后的文本（去 HTML、规范化空白）
    - sections: 分段结果，key 为段落类型，value 为段落内容
    - metadata: 元数据（行数、字符数、段落数等）

    设计动机：
    - 与 JobAnalysisResult 解耦：Parser 负责文本预处理，Agent 负责语义分析
    - sections 用 dict 而非 list：便于按类型快速查找，无需遍历
    - metadata 存储统计信息：可用于日志、监控、调试
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    raw_text: str = Field(
        ...,
        min_length=1,
        max_length=JOB_JD_TEXT_MAX_LENGTH,
        description="原始 JD 文本",
    )
    cleaned_text: str = Field(
        ...,
        description="预处理后的文本",
    )
    sections: dict[str, str] = Field(
        default_factory=dict,
        description="分段结果 {section_type: content}",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="元数据（行数、字符数、段落数等）",
    )


__all__ = [
    # 常量
    "JOB_JD_TEXT_MAX_LENGTH",
    "JOB_JD_PREVIEW_LENGTH",
    "JOB_TITLE_MAX_LENGTH",
    "JOB_COMPANY_MAX_LENGTH",
    "JOB_SOURCE_URL_MAX_LENGTH",
    "JOB_LOCATION_MAX_LENGTH",
    "JOB_SALARY_MIN_K",
    "JOB_SALARY_MAX_K",
    "JOB_SKILLS_MAX_LENGTH",
    "JOB_KEYWORDS_MAX_LENGTH",
    "JOB_HIDDEN_REQUIREMENTS_MAX_LENGTH",
    "JOB_SKILL_ITEM_MAX_LENGTH",
    "JOB_KEYWORD_ITEM_MAX_LENGTH",
    "JOB_HIDDEN_REQUIREMENT_ITEM_MAX_LENGTH",
    "JOB_COMPANY_INDUSTRY_MAX_LENGTH",
    "JOB_COMPANY_SCALE_MAX_LENGTH",
    "JOB_COMPANY_STAGE_MAX_LENGTH",
    # JD 解析
    "JDSectionType",
    "JD_SECTION_TITLES",
    "JDParseResult",
    # 分析结果子模型
    "SalaryRange",
    "CompanyInfo",
    "JobAnalysisResult",
    # 入参
    "JobCreateRequest",
    "JobAnalyzeRequest",
    # 出参
    "JobSummary",
    "JobResponse",
    "JobListResponse",
    "JobAnalyzeResponse",
]
