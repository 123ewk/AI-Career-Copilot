"""Resume DTO / Schema（Pydantic v2）

职责：
- 定义简历域的 Pydantic Model，作为 API 层 ↔ Service 层之间的数据契约
- 入参（Request）做严格校验：上传时强校验技能/年限/活跃标记等
- 出参（Response）只暴露公开字段，绝不泄露其他用户的简历内容
- 简历原文（raw_text）可能很长：DTO 层做 size 兜底，防止恶意上传触发 OOM

设计动机：
- DTO 与 ORM Model 分离：DTO 是 API 契约，ORM 是数据库映射
  · 防止 ORM 字段变动直接暴露给前端（强边界）
  · DTO 可按场景裁剪字段（上传响应 vs 列表摘要 vs 详情）
- DTO 与 Validator 分离：DTO 描述「数据结构」，Validator 描述「校验规则」
  · Service 层切换活跃简历等场景可复用同一套校验
- 结构化数据（education/experience/projects）拆成独立子模型：
  · 字段约束集中维护，子模型可被 Agent / 前端独立引用
  · `extra="ignore"` 容忍解析器输出额外字段，向后兼容

字段约束对齐（与 ORM Model 保持一致，参考 app/infra/database/models/resume.py）：
- raw_text: 1-50000 字符（PDF/DOCX 解析后纯文本）
- skills: 数组，最多 200 个元素
- experience_years: 0-50 年
- is_active: bool（数据库有部分唯一索引兜底，DTO 仅描述字段）

安全设计：
- 响应模型（ResumeResponse）只暴露白名单字段
- 跨用户隔离由 Service 层按 user_id 过滤保证，DTO 不参与权限判断
- raw_text max_length 50000：限制单次请求体大小，防止恶意上传触发 OOM
- skills 去重/去空在 Service 层处理：DTO 层只校验长度，不做归一化
  · 避免 DTO 与 Service 行为重复，也避免 DTO 误删有效输入

潜在风险：
- 结构化数据格式可能随解析器升级而变：DTO 用 Optional + 默认空列表
  → 防御：`extra="ignore"` 容忍额外字段；子模型全部字段可选
- is_active=True 唯一性：DTO 不强制，由数据库部分唯一索引兜底
  → Service 层「切换活跃」需事务化：先取消旧活跃，再设新活跃
- 解析异步化：上传成功但解析失败时，前端需可识别
  → ResumeUploadResponse.parse_status 显式区分 PARSED/PARSING/FAILED
"""

import uuid
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ==================== 常量 ====================

# 简历原文最大长度：50KB，覆盖 95% 简历（正常简历 5-20KB）
# 限制原因：PDF/DOCX 解析后纯文本可达数 MB，DTO 层兜底防止恶意上传
RESUME_RAW_TEXT_MAX_LENGTH: Final[int] = 50_000

# 技能列表最大长度：覆盖实际场景 + 防注入式超大数组
RESUME_SKILLS_MAX_LENGTH: Final[int] = 200

# 单个技能字符串最大长度：防止超长字符串（DoS + DB bloat）
SKILL_ITEM_MAX_LENGTH: Final[int] = 100

# 工作年限边界：0-50，应届生填 0 也合法
EXPERIENCE_YEARS_MIN: Final[int] = 0
EXPERIENCE_YEARS_MAX: Final[int] = 50

# 简历文本字段最大长度（教育/工作/项目描述等）
EDUCATION_DESCRIPTION_MAX_LENGTH: Final[int] = 2_000
EXPERIENCE_DESCRIPTION_MAX_LENGTH: Final[int] = 5_000
PROJECT_DESCRIPTION_MAX_LENGTH: Final[int] = 5_000

# 解析状态枚举：客户端根据状态决定是否展示「解析中」loading
PARSE_STATUS_PARSED: Final[str] = "PARSED"
PARSE_STATUS_PARSING: Final[str] = "PARSING"
PARSE_STATUS_FAILED: Final[str] = "FAILED"
_VALID_PARSE_STATUSES: Final[frozenset[str]] = frozenset(
    {PARSE_STATUS_PARSED, PARSE_STATUS_PARSING, PARSE_STATUS_FAILED}
)


# ==================== 结构化数据子模型 ====================

class EducationItem(BaseModel):
    """教育经历条目

    字段：
    - school: 学校名称
    - degree: 学位（如 本科/硕士/博士）
    - major: 专业
    - start_date / end_date: 起止日期（YYYY-MM 格式或自由文本）
    - description: 补充描述（GPA、荣誉、核心课程等）

    所有字段除 school 外均可选：解析器对历史/在读简历的字段补全能力不一
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    school: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="学校名称",
    )
    degree: str | None = Field(
        default=None,
        max_length=100,
        description="学位（如 本科/硕士/博士）",
    )
    major: str | None = Field(
        default=None,
        max_length=100,
        description="专业",
    )
    start_date: str | None = Field(
        default=None,
        max_length=50,
        description="开始日期（YYYY-MM 或自由文本）",
    )
    end_date: str | None = Field(
        default=None,
        max_length=50,
        description="结束日期（YYYY-MM 或自由文本）",
    )
    description: str | None = Field(
        default=None,
        max_length=EDUCATION_DESCRIPTION_MAX_LENGTH,
        description="补充描述",
    )


class ExperienceItem(BaseModel):
    """工作经历条目

    字段：
    - company: 公司名称
    - position: 职位
    - start_date / end_date: 起止日期
    - description: 工作描述（职责、业绩）
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    company: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="公司名称",
    )
    position: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="职位",
    )
    start_date: str | None = Field(
        default=None,
        max_length=50,
        description="开始日期",
    )
    end_date: str | None = Field(
        default=None,
        max_length=50,
        description="结束日期（'至今' 视为合法值）",
    )
    description: str | None = Field(
        default=None,
        max_length=EXPERIENCE_DESCRIPTION_MAX_LENGTH,
        description="工作描述",
    )


class ProjectItem(BaseModel):
    """项目经历条目

    字段：
    - name: 项目名称
    - role: 担任角色
    - start_date / end_date: 起止日期
    - description: 项目描述（背景、目标、个人贡献）
    - tech_stack: 技术栈标签
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="项目名称",
    )
    role: str | None = Field(
        default=None,
        max_length=100,
        description="担任角色",
    )
    start_date: str | None = Field(
        default=None,
        max_length=50,
        description="开始日期",
    )
    end_date: str | None = Field(
        default=None,
        max_length=50,
        description="结束日期",
    )
    description: str | None = Field(
        default=None,
        max_length=PROJECT_DESCRIPTION_MAX_LENGTH,
        description="项目描述",
    )
    tech_stack: list[str] = Field(
        default_factory=list,
        max_length=RESUME_SKILLS_MAX_LENGTH,
        description="技术栈标签",
    )

    @field_validator("tech_stack")
    @classmethod
    def _check_tech_stack_items(cls, value: list[str]) -> list[str]:
        """tech_stack 元素长度校验：防止单元素超长

        设计：DTO 不做去重/去空，仅做长度校验
        - 去重/去空由 Service 层负责
        - 避免 DTO 误删 Service 期望的「原始输入」
        """
        for item in value:
            if len(item) > SKILL_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"tech_stack 单个标签长度不能超过 {SKILL_ITEM_MAX_LENGTH} 字符"
                )
        return value


class ResumeStructuredData(BaseModel):
    """简历结构化数据

    典型结构：
    {
        "education": [EducationItem, ...],
        "experience": [ExperienceItem, ...],
        "projects": [ProjectItem, ...]
    }

    设计：
    - 三个核心字段全部默认空列表：保证下游 Agent 拿到的结构稳定
    - `extra="ignore"` 容忍解析器输出额外字段（如 certificates/languages）
      这些字段暂不建模，保留扩展空间，未来可平滑升级
    """

    model_config = ConfigDict(
        extra="ignore",
    )

    education: list[EducationItem] = Field(
        default_factory=list,
        description="教育经历",
    )
    experience: list[ExperienceItem] = Field(
        default_factory=list,
        description="工作经历",
    )
    projects: list[ProjectItem] = Field(
        default_factory=list,
        description="项目经历",
    )


# ==================== 出参 DTO ====================

class ResumeSummary(BaseModel):
    """简历摘要（列表用）

    用途：GET /api/resume 列表返回
    设计：不包含 raw_text（可能 50KB），不包含 structured_data
    · 列表场景只需「技能 + 年限 + 活跃标记 + 上传时间」即可定位/筛选
    · 减少响应体大小，提升列表接口性能
    """

    model_config = ConfigDict(
        # 支持从 ORM Model 创建：ResumeSummary.model_validate(orm_resume)
        from_attributes=True,
        # 禁止额外字段透传：ORM 多了字段也不会泄露（白名单机制）
        extra="ignore",
    )

    id: uuid.UUID = Field(
        ...,
        description="简历 ID（UUID v4）",
    )
    skills: list[str] = Field(
        default_factory=list,
        max_length=RESUME_SKILLS_MAX_LENGTH,
        description="技能列表",
    )
    experience_years: int | None = Field(
        default=None,
        ge=EXPERIENCE_YEARS_MIN,
        le=EXPERIENCE_YEARS_MAX,
        description="工作年限",
    )
    is_active: bool = Field(
        ...,
        description="是否为当前活跃简历",
    )
    created_at: datetime = Field(
        ...,
        description="创建时间（ISO 8601）",
    )


class ResumeResponse(BaseModel):
    """简历完整响应

    用途：
    - GET /api/resume/{id} 详情返回
    - 简历解析完成后内部流转
    - 上传/更新成功后由 Service 层包装为 ResumeUploadResponse 返回

    设计：
    - `from_attributes=True`：支持从 ORM Resume 对象直接构建
    - `extra="ignore"`：ORM 增字段不会自动透传，必须显式同步到 DTO
    - raw_text 必返回：Agent 匹配/优化建议阶段需要原文检索
    - structured_data 必返回：前端可展示结构化卡片视图
    """

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",
    )

    id: uuid.UUID = Field(
        ...,
        description="简历 ID（UUID v4）",
    )
    user_id: uuid.UUID = Field(
        ...,
        description="所属用户 ID",
    )
    raw_text: str = Field(
        ...,
        min_length=1,
        max_length=RESUME_RAW_TEXT_MAX_LENGTH,
        description="简历原文（PDF/DOCX 解析后纯文本）",
    )
    structured_data: ResumeStructuredData = Field(
        ...,
        description="结构化数据（教育/工作/项目）",
    )
    skills: list[str] = Field(
        default_factory=list,
        max_length=RESUME_SKILLS_MAX_LENGTH,
        description="技能列表",
    )
    experience_years: int | None = Field(
        default=None,
        ge=EXPERIENCE_YEARS_MIN,
        le=EXPERIENCE_YEARS_MAX,
        description="工作年限",
    )
    is_active: bool = Field(
        ...,
        description="是否为当前活跃简历",
    )
    created_at: datetime = Field(
        ...,
        description="创建时间（ISO 8601）",
    )


class ResumeUploadResponse(BaseModel):
    """简历上传响应

    字段：
    - resume: 完整简历信息
    - parse_status: 解析状态
      · PARSED: 同步解析成功，structured_data 已就绪
      · PARSING: 异步解析中，structured_data 可能为空，客户端可轮询
      · FAILED: 解析失败，raw_text 已保存但结构化数据不可用
    - message: 解析失败时的错误信息（成功时为 null）

    设计动机：
    - 解析是耗时操作（LLM 抽取可能 5-10s）：支持同步+异步两种模式
    - 客户端根据 parse_status 决定 UI 展示：
      · PARSED: 直接展示结构化卡片
      · PARSING: 展示「解析中」骨架屏，轮询 GET /api/resume/{id}
      · FAILED: 展示「解析失败」+ message，允许用户重传或编辑
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    resume: ResumeResponse = Field(
        ...,
        description="简历信息",
    )
    parse_status: str = Field(
        default=PARSE_STATUS_PARSED,
        description="解析状态（PARSED/PARSING/FAILED）",
    )
    message: str | None = Field(
        default=None,
        max_length=500,
        description="解析失败时的错误信息",
    )

    @field_validator("parse_status")
    @classmethod
    def _check_parse_status(cls, value: str) -> str:
        """解析状态枚举校验：白名单机制，不在集合内直接拒绝

        不直接用 Enum：保持字符串字面量，与前端 / 日志 / 文档保持一致
        """
        if value not in _VALID_PARSE_STATUSES:
            raise ValueError(
                f"parse_status 必须是 {sorted(_VALID_PARSE_STATUSES)} 之一"
            )
        return value


# ==================== 入参 DTO ====================

class ResumeUpdateRequest(BaseModel):
    """简历更新请求

    用途：PATCH /api/resume/{id}
    典型场景：切换活跃简历（用户上传新简历后，把旧的 is_active 置 False）

    设计：
    - 所有字段可选：只传需要修改的字段，未传字段保持不变
    - 当前只暴露 is_active：结构化数据 / 原文不允许 API 直接修改
      · 原文修改等同于「重新上传」，由 POST /api/resume/upload 处理
      · 结构化数据由解析器生成，人工编辑走单独的「编辑简历」接口（未来）
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    is_active: bool | None = Field(
        default=None,
        description="是否为当前活跃简历（每用户最多一条 is_active=True）",
    )


__all__ = [
    # 结构化数据子模型
    "EducationItem",
    "ExperienceItem",
    "ProjectItem",
    "ResumeStructuredData",
    # 出参
    "ResumeSummary",
    "ResumeResponse",
    "ResumeUploadResponse",
    # 入参
    "ResumeUpdateRequest",
    # 常量（供 Service / Router 复用）
    "RESUME_RAW_TEXT_MAX_LENGTH",
    "RESUME_SKILLS_MAX_LENGTH",
    "SKILL_ITEM_MAX_LENGTH",
    "EXPERIENCE_YEARS_MIN",
    "EXPERIENCE_YEARS_MAX",
    "EDUCATION_DESCRIPTION_MAX_LENGTH",
    "EXPERIENCE_DESCRIPTION_MAX_LENGTH",
    "PROJECT_DESCRIPTION_MAX_LENGTH",
    "PARSE_STATUS_PARSED",
    "PARSE_STATUS_PARSING",
    "PARSE_STATUS_FAILED",
]
