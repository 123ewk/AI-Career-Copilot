"""Web Search 数据模型

职责：
- 定义 Tavily API 响应的 Pydantic 模型
- 定义公司搜索聚合结果模型，供下游 Job Extractor 消费

设计动机：
- WebSearchResult/WebSearchResponse：映射 Tavily API 原始响应
- CompanySearchResults：聚合多次搜索结果，按主题分类（reputation/culture/salary）
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ==================== 常量 ====================

# 搜索结果最大数量
MAX_SEARCH_RESULTS: int = 20

# 公司查询主题
COMPANY_TOPICS: tuple[str, ...] = ("reputation", "culture", "salary")


# ==================== Tavily API 响应模型 ====================


class WebSearchResult(BaseModel):
    """单条搜索结果"""

    title: str = Field(..., max_length=500)
    url: str = Field(..., max_length=2000)
    content: str = Field(default="", max_length=5000)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_content: str | None = None


class WebSearchResponse(BaseModel):
    """Tavily 搜索响应"""

    model_config = {"extra": "ignore"}

    query: str
    answer: str | None = None
    results: list[WebSearchResult] = Field(default_factory=list)
    response_time: float | None = None


# ==================== 公司搜索聚合模型 ====================


class CompanySearchResults(BaseModel):
    """公司搜索聚合结果

    按主题分类存储搜索结果，供 Job Extractor 补充 CompanyInfo。
    每个主题字段可为 None（对应查询失败时）。
    """

    model_config = {"extra": "ignore"}

    company_name: str
    search_queries: list[str] = Field(default_factory=list)
    reputation: WebSearchResponse | None = None
    culture: WebSearchResponse | None = None
    salary: WebSearchResponse | None = None
    general: WebSearchResponse | None = None
