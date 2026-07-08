"""Match DTO / Schema（Pydantic v2）

职责：
- 定义匹配模块的 Pydantic Model，作为 Scorer 输入输出契约
- 描述「岗位 - 简历」匹配计算所需的字段与返回的分数结构
- 为后续 Step 1.7.3~1.7.12 的 Ranker / Strategy / Service / Router 提供统一数据契约

设计动机：
- DTO 与 ORM Model 分离：匹配计算不直接依赖数据库结构，只依赖岗位/简历分析后的结构化字段
- DTO 与 Scorer 解耦：Scorer 只接收 MatchInput，不感知 API / MQ / ORM 细节
- 权重可配置：MatchCalculateRequest 允许上层在单次调用中覆盖默认权重，便于 A/B 实验
- 分数明细透明：同时返回 bm25_score / semantic_score / combined_score，便于前端展示与问题定位

字段约束对齐：
- job_text / resume_text 最大 50_000 字符：与 Job / Resume DTO 保持一致，防止恶意长文本触发 OOM
- job_skills / resume_skills / job_keywords 元素长度复用 JOB_SKILL_ITEM_MAX_LENGTH / JOB_KEYWORD_ITEM_MAX_LENGTH / SKILL_ITEM_MAX_LENGTH
- 权重必须在 [0, 1] 之间且和为 1.0：保证融合分数有明确语义

安全设计：
- 输入文本只做长度校验，不做内容过滤；敏感词/黑名单由上层 Service 或 Agent 处理
- 不返回原始文本：MatchScoreDetail 只包含分数与元信息，避免泄露简历/岗位原文

潜在风险：
- 权重和校验：跨字段约束，用 model_validator 在对象构造后校验
- 超长 skills 数组：max_length 限制元素个数，field_validator 限制单元素长度
- 时间字段：scored_at 由 Scorer 自动填充，避免调用方伪造
"""

import uuid
from datetime import datetime, timezone
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 复用 Job / Resume DTO 的常量，保证跨模块约束一致
from app.domain.job.models import (
    JOB_JD_TEXT_MAX_LENGTH,
    JOB_KEYWORD_ITEM_MAX_LENGTH,
    JOB_KEYWORDS_MAX_LENGTH,
    JOB_SKILL_ITEM_MAX_LENGTH,
    JOB_SKILLS_MAX_LENGTH,
)
from app.domain.resume.models import (
    RESUME_RAW_TEXT_MAX_LENGTH,
    RESUME_SKILLS_MAX_LENGTH,
    SKILL_ITEM_MAX_LENGTH,
)

# ==================== 常量 ====================

# 匹配文本最大长度：取岗位 JD 与简历原文的较大上限作为统一兜底
MATCH_TEXT_MAX_LENGTH: Final[int] = max(
    JOB_JD_TEXT_MAX_LENGTH, RESUME_RAW_TEXT_MAX_LENGTH
)

# 技能/关键词数组最大长度：岗位侧与简历侧上限不一致，取较小值作为匹配输入上限
# 原因：匹配计算需要两边列表都可控，防止单侧注入超大数组拖垮 BM25
MATCH_SKILLS_MAX_LENGTH: Final[int] = min(
    JOB_SKILLS_MAX_LENGTH, RESUME_SKILLS_MAX_LENGTH
)
MATCH_KEYWORDS_MAX_LENGTH: Final[int] = JOB_KEYWORDS_MAX_LENGTH

# 默认权重：语义匹配占比更高，因为中文 JD 中同义词/近义表达更常见
# 加权求和：combined = 0.4 * bm25 + 0.6 * semantic
DEFAULT_BM25_WEIGHT: Final[float] = 0.4
DEFAULT_SEMANTIC_WEIGHT: Final[float] = 0.6

# 分数上限
MAX_SCORE: Final[float] = 100.0
MIN_SCORE: Final[float] = 0.0

# 工作年限边界：与 Resume DTO 对齐
EXPERIENCE_YEARS_MIN: Final[int] = 0
EXPERIENCE_YEARS_MAX: Final[int] = 50


# ==================== 子模型 ====================

class MatchInput(BaseModel):
    """匹配计算输入

    字段：
    - job_id / resume_id: 业务标识，仅用于结果回写与日志追踪，不参与分数计算
    - job_skills / job_keywords: 岗位侧结构化标签，作为 BM25 的 query 侧
    - job_text: 岗位 JD 原文，作为语义匹配的文本 A
    - resume_skills: 简历侧技能列表，作为 BM25 的 doc 侧补充
    - resume_text: 简历原文，作为语义匹配的文本 B
    - resume_experience_years: 工作年限（可选，为后续 strategy 扩展预留，当前 Scorer 不使用）

    设计：
    - skills 与 keywords 分离：skills 偏向「硬技能」（Python/MySQL），keywords 偏向「业务关键词」（RAG/Agent）
    - text 与 skills 共存：BM25 用结构化标签计算，语义模型用完整文本捕捉上下文
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID（仅用于结果回写与日志）",
    )
    resume_id: uuid.UUID = Field(
        ...,
        description="简历 ID（仅用于结果回写与日志）",
    )
    job_skills: list[str] = Field(
        default_factory=list,
        max_length=MATCH_SKILLS_MAX_LENGTH,
        description="岗位技能列表",
    )
    job_keywords: list[str] = Field(
        default_factory=list,
        max_length=MATCH_KEYWORDS_MAX_LENGTH,
        description="岗位关键词列表",
    )
    job_text: str = Field(
        default="",
        max_length=MATCH_TEXT_MAX_LENGTH,
        description="岗位 JD 原文",
    )
    resume_skills: list[str] = Field(
        default_factory=list,
        max_length=MATCH_SKILLS_MAX_LENGTH,
        description="简历技能列表",
    )
    resume_text: str = Field(
        default="",
        max_length=MATCH_TEXT_MAX_LENGTH,
        description="简历原文",
    )
    resume_experience_years: int | None = Field(
        default=None,
        ge=EXPERIENCE_YEARS_MIN,
        le=EXPERIENCE_YEARS_MAX,
        description="工作年限（为 strategy 预留，当前不参与计算）",
    )

    @field_validator("job_skills")
    @classmethod
    def _check_job_skills_items(cls, value: list[str]) -> list[str]:
        """岗位 skills 单元素长度校验"""
        for item in value:
            if len(item) > JOB_SKILL_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"job_skills 单个标签长度不能超过 {JOB_SKILL_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @field_validator("job_keywords")
    @classmethod
    def _check_job_keywords_items(cls, value: list[str]) -> list[str]:
        """岗位 keywords 单元素长度校验"""
        for item in value:
            if len(item) > JOB_KEYWORD_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"job_keywords 单个标签长度不能超过 {JOB_KEYWORD_ITEM_MAX_LENGTH} 字符"
                )
        return value

    @field_validator("resume_skills")
    @classmethod
    def _check_resume_skills_items(cls, value: list[str]) -> list[str]:
        """简历 skills 单元素长度校验"""
        for item in value:
            if len(item) > SKILL_ITEM_MAX_LENGTH:
                raise ValueError(
                    f"resume_skills 单个标签长度不能超过 {SKILL_ITEM_MAX_LENGTH} 字符"
                )
        return value


class MatchScoreDetail(BaseModel):
    """匹配分数明细（输出）

    字段：
    - bm25_score: 关键词匹配分数（0-100）
    - semantic_score: 语义相似度分数（0-100）
    - combined_score: 加权融合后的最终匹配度（0-100）
    - weight_bm25 / weight_semantic: 实际使用的权重，便于审计与复现
    - scored_at: 打分时间（UTC）

    设计：
    - 同时返回三个分数：前端可展示「综合匹配度 + 子维度」
    - 权重回显：当上层自定义权重时，响应中能看到实际生效的权重
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    resume_id: uuid.UUID = Field(
        ...,
        description="简历 ID",
    )
    bm25_score: float = Field(
        ...,
        ge=MIN_SCORE,
        le=MAX_SCORE,
        description="BM25 关键词匹配分数（0-100）",
    )
    semantic_score: float = Field(
        ...,
        ge=MIN_SCORE,
        le=MAX_SCORE,
        description="语义相似度分数（0-100）",
    )
    combined_score: float = Field(
        ...,
        ge=MIN_SCORE,
        le=MAX_SCORE,
        description="加权融合后的综合匹配度（0-100）",
    )
    weight_bm25: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="BM25 权重",
    )
    weight_semantic: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="语义相似度权重",
    )
    scored_at: datetime = Field(
        ...,
        description="打分时间（UTC）",
    )


class MatchCalculateRequest(BaseModel):
    """匹配计算请求（供 Service 层内部调用）

    在 MatchInput 基础上允许自定义权重，便于：
    - 不同业务场景（快速筛选 vs 深度匹配）使用不同权重
    - 后续 A/B 实验

    校验：
    - 两个权重都必须在 [0, 1]
    - 两个权重之和必须等于 1.0
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    match_input: MatchInput = Field(
        ...,
        description="匹配输入",
    )
    weight_bm25: float = Field(
        default=DEFAULT_BM25_WEIGHT,
        ge=0.0,
        le=1.0,
        description="BM25 权重",
    )
    weight_semantic: float = Field(
        default=DEFAULT_SEMANTIC_WEIGHT,
        ge=0.0,
        le=1.0,
        description="语义相似度权重",
    )

    @model_validator(mode="after")
    def _check_weights_sum(self) -> "MatchCalculateRequest":
        """校验权重和为 1.0

        为什么用 model_validator：
        - 跨字段约束，单字段 Field(ge=..., le=...) 无法表达「和为 1」
        - 浮点数比较：允许 1e-6 误差，避免浮点精度导致合法输入被误判
        """
        total = self.weight_bm25 + self.weight_semantic
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"BM25 权重 ({self.weight_bm25}) 与语义权重 ({self.weight_semantic}) "
                f"之和必须等于 1.0，当前为 {total}"
            )
        return self


class MatchComputeRequest(BaseModel):
    """匹配计算 API 请求"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    resume_id: uuid.UUID | None = Field(
        default=None,
        description="简历 ID，未传时使用用户当前活跃简历",
    )


class MatchResultResponse(BaseModel):
    """匹配计算 API 响应"""

    model_config = ConfigDict(
        extra="forbid",
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    resume_id: uuid.UUID = Field(
        ...,
        description="简历 ID",
    )
    score_detail: MatchScoreDetail = Field(
        ...,
        description="分数明细",
    )
    matched_skills: list[str] = Field(
        default_factory=list,
        description="命中技能",
    )
    missing_skills: list[str] = Field(
        default_factory=list,
        description="缺失技能",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="建议",
    )


def utc_now() -> datetime:
    """获取当前 UTC 时间

    为什么独立函数：
    - 方便单元测试 mock，避免测试依赖真实时间
    - 统一使用 timezone.utc，避免本地时间歧义
    """
    return datetime.now(timezone.utc)


__all__ = [
    # 常量
    "MATCH_TEXT_MAX_LENGTH",
    "MATCH_SKILLS_MAX_LENGTH",
    "MATCH_KEYWORDS_MAX_LENGTH",
    "DEFAULT_BM25_WEIGHT",
    "DEFAULT_SEMANTIC_WEIGHT",
    "MAX_SCORE",
    "MIN_SCORE",
    "EXPERIENCE_YEARS_MIN",
    "EXPERIENCE_YEARS_MAX",
    # 模型
    "MatchInput",
    "MatchScoreDetail",
    "MatchCalculateRequest",
    "MatchComputeRequest",
    "MatchResultResponse",
    # 工具函数
    "utc_now",
]
