"""Tavily Web Search Tool

职责：
- 封装 tavily-python SDK 的异步调用
- 提供通用搜索（search）和公司专题搜索（search_company）
- 输出结构化 Pydantic 模型供下游消费

设计动机：
- 使用 tavily-python SDK 而非裸 httpx：
  · SDK 内置重试、超时、错误处理，减少重复代码
  · 提供 get_company_info 专用方法，直接获取公司结构化信息
  · 保持与官方 SDK 一致，降低维护成本
- 公司专题搜索：SDK 的 get_company_info + 多主题并发补充
  · 为 Job Extractor 的 CompanyInfo 补充外部数据
  · 部分查询失败时返回部分结果，不影响整体

API 兼容性：
- tavily-python AsyncTavilyClient
- search() → dict（标准搜索响应）
- get_company_info() → Sequence[dict]（公司信息）

潜在风险：
- API 超时：搜索请求可能较慢（5-60s）
  → 防御：SDK 内置超时，可配置 timeout 参数
- Rate limit：免费版有调用频率限制
  → 防御：捕获 SDK 异常，映射为 ExternalServiceError
- 部分失败：search_company 的多个子查询可能部分失败
  → 防御：asyncio.gather(return_exceptions=True)，失败的置 None
"""

from __future__ import annotations

import asyncio
from typing import Any

from tavily import AsyncTavilyClient

from app.core.exceptions import ExternalServiceError
from app.core.logger import logger
from app.core.settings import get_settings
from app.tools.retrieval.models import (
    CompanySearchResults,
    WebSearchResponse,
)

# ==================== 内部常量 ====================

# 默认超时时间（秒）
_DEFAULT_TIMEOUT: float = 30.0

# 公司搜索补充查询模板（与 get_company_info 互补）
_COMPANY_QUERY_TEMPLATES: dict[str, str] = {
    "culture": "{company} company culture work environment",
    "salary": "{company} salary compensation benefits",
}


# ==================== 公共 API ====================


class WebSearchTool:
    """Tavily Web Search Tool

    用法:
        tool = WebSearchTool()
        results = await tool.search_company("字节跳动")
        # results 是 CompanySearchResults 实例
        await tool.close()

    或使用异步上下文管理器:
        async with WebSearchTool() as tool:
            results = await tool.search_company("字节跳动")

    设计为可复用实例：内部使用 AsyncTavilyClient。
    """

    def __init__(
        self,
        tavily_client: AsyncTavilyClient | None = None,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """初始化工具

        Args:
            tavily_client: AsyncTavilyClient 实例。None 时自动创建。
            api_key: Tavily API Key。None 时从配置读取。
            timeout: 请求超时时间（秒）
        """
        settings = get_settings()
        self._api_key = api_key or settings.tavily_api_key
        self._timeout = timeout

        self._client = tavily_client or AsyncTavilyClient(
            api_key=self._api_key,
        )

        logger.info(
            "WebSearchTool 初始化完成 | timeout={}",
            self._timeout,
        )

    async def search(
        self,
        query: str,
        search_depth: str = "basic",
        max_results: int = 5,
        include_answer: bool = True,
        topic: str = "general",
    ) -> WebSearchResponse:
        """通用搜索

        Args:
            query: 搜索关键词
            search_depth: 搜索深度（basic/advanced/fast/ultra-fast）
            max_results: 最大结果数
            include_answer: 是否包含 AI 生成的摘要
            topic: 搜索主题（general/news/finance）

        Returns:
            WebSearchResponse 实例

        Raises:
            ExternalServiceError: API 调用失败
        """
        logger.info("Web 搜索开始 | query={} | depth={}", query, search_depth)

        try:
            data = await self._client.search(
                query=query,
                search_depth=search_depth,
                max_results=max_results,
                include_answer=include_answer,
                topic=topic,
                timeout=self._timeout,
            )
        except Exception as exc:
            logger.error("Tavily API 调用失败 | query={} | error={}", query, str(exc))
            raise ExternalServiceError(
                detail="Tavily API 调用失败",
                error_code="EXT_011",
                extra={"query": query, "error": str(exc)},
            ) from exc

        # 解析响应
        response = self._parse_response(data, query)

        logger.info(
            "Web 搜索完成 | query={} | results_count={} | has_answer={}",
            query,
            len(response.results),
            response.answer is not None,
        )

        return response

    async def search_company(
        self,
        company_name: str,
        extra_context: str | None = None,
    ) -> CompanySearchResults:
        """公司专题搜索

        使用 Tavily 的 get_company_info 获取公司基础信息，
        并发执行 culture/salary 补充查询。

        Args:
            company_name: 公司名称
            extra_context: 额外上下文（如行业、规模），追加到查询中

        Returns:
            CompanySearchResults 实例，失败的子查询对应字段为 None
        """
        logger.info("公司搜索开始 | company={}", company_name)

        # 构建并发任务：get_company_info + 补充查询
        tasks: dict[str, Any] = {}

        # 1. 使用 SDK 内置的 get_company_info
        tasks["reputation"] = self._get_company_info(company_name)

        # 2. 补充查询：culture / salary
        for topic, template in _COMPANY_QUERY_TEMPLATES.items():
            q = template.format(company=company_name)
            if extra_context:
                q = f"{q} {extra_context}"
            tasks[topic] = self.search(query=q, max_results=3, include_answer=True)

        # 并发执行，允许部分失败
        results_list = await asyncio.gather(
            *tasks.values(),
            return_exceptions=True,
        )

        # 聚合结果
        aggregated: dict[str, WebSearchResponse | None] = {}

        for topic, result in zip(tasks.keys(), results_list):
            if isinstance(result, Exception):
                logger.warning(
                    "公司搜索子查询失败 | company={} | topic={} | error={}",
                    company_name,
                    topic,
                    str(result),
                )
                aggregated[topic] = None
            else:
                aggregated[topic] = result

        # 构建搜索查询记录
        search_queries = [f"get_company_info({company_name})"]
        for template in _COMPANY_QUERY_TEMPLATES.values():
            q = template.format(company=company_name)
            if extra_context:
                q = f"{q} {extra_context}"
            search_queries.append(q)

        company_results = CompanySearchResults(
            company_name=company_name,
            search_queries=search_queries,
            reputation=aggregated.get("reputation"),
            culture=aggregated.get("culture"),
            salary=aggregated.get("salary"),
        )

        logger.info(
            "公司搜索完成 | company={} | topics_ok={}",
            company_name,
            [t for t, v in aggregated.items() if v is not None],
        )

        return company_results

    async def _get_company_info(self, company_name: str) -> WebSearchResponse:
        """调用 Tavily get_company_info

        Args:
            company_name: 公司名称

        Returns:
            WebSearchResponse 实例

        Raises:
            ExternalServiceError: API 调用失败
        """
        logger.info("Tavily get_company_info 开始 | company={}", company_name)

        try:
            results = await self._client.get_company_info(
                query=company_name,
                search_depth="advanced",
                max_results=5,
                timeout=self._timeout,
            )
        except Exception as exc:
            logger.error(
                "Tavily get_company_info 失败 | company={} | error={}",
                company_name,
                str(exc),
            )
            raise ExternalServiceError(
                detail="Tavily 公司信息查询失败",
                error_code="EXT_011",
                extra={"company": company_name, "error": str(exc)},
            ) from exc

        # get_company_info 返回 Sequence[dict]，转换为 WebSearchResponse
        # 每个 dict 包含 title/url/content/score 等字段
        response = self._parse_response(
            {"query": company_name, "results": list(results)},
            query=company_name,
        )

        logger.info(
            "Tavily get_company_info 完成 | company={} | results_count={}",
            company_name,
            len(response.results),
        )

        return response

    def _parse_response(
        self,
        data: dict[str, Any],
        query: str,
    ) -> WebSearchResponse:
        """解析 Tavily API 响应

        Args:
            data: API 响应字典
            query: 原始查询（用于填充 response.query）

        Returns:
            WebSearchResponse 实例

        Raises:
            ExternalServiceError: 响应校验失败
        """
        try:
            response = WebSearchResponse.model_validate(data)
            # 确保 query 字段一致
            response.query = query
            return response
        except Exception as exc:
            logger.error(
                "Tavily API 响应校验失败 | data={} | error={}",
                str(data)[:500],
                str(exc),
            )
            raise ExternalServiceError(
                detail="Tavily API 响应格式校验失败",
                error_code="EXT_014",
                extra={"data": str(data)[:500], "error": str(exc)},
            ) from exc

    async def close(self) -> None:
        """关闭 Tavily 客户端"""
        await self._client.close()
        logger.info("WebSearchTool 已关闭")

    async def __aenter__(self) -> WebSearchTool:
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口"""
        await self.close()
