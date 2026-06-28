"""Web Search Tool 单元测试

职责：
- 测试 Tavily API 搜索功能（通用搜索 + 公司专题搜索）
- 使用 unittest.mock 模拟 AsyncTavilyClient
- 覆盖正常流程、边界条件、异常流程

测试策略：
- Mock SDK：使用 unittest.mock 模拟 AsyncTavilyClient，避免真实 API 调用
- 正常流程：搜索返回、公司聚合
- 边界条件：空结果、缺失字段、额外字段、部分失败
- 异常流程：SDK 异常映射为 ExternalServiceError
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import ExternalServiceError
from app.tools.retrieval.models import CompanySearchResults, WebSearchResponse
from app.tools.retrieval.web_search import WebSearchTool

# ==================== Fixtures ====================


@pytest.fixture
def mock_tavily_client() -> AsyncMock:
    """模拟 AsyncTavilyClient"""
    client = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def tool(mock_tavily_client: AsyncMock) -> WebSearchTool:
    """WebSearchTool 实例（使用模拟 Tavily 客户端）"""
    return WebSearchTool(
        tavily_client=mock_tavily_client,
        api_key="test-api-key",
    )


# ==================== 测试数据 ====================

# 标准搜索查询
SAMPLE_QUERY = "字节跳动 company reputation"

# Tavily 模拟搜索响应
MOCK_SEARCH_RESPONSE = {
    "query": SAMPLE_QUERY,
    "answer": "ByteDance is a multinational technology company...",
    "results": [
        {
            "title": "ByteDance - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/ByteDance",
            "content": "ByteDance Ltd. is a Chinese internet technology company...",
            "score": 0.95,
            "raw_content": None,
        },
        {
            "title": "ByteDance Company Profile",
            "url": "https://www.byteDance.com/about",
            "content": "ByteDance is a global technology company...",
            "score": 0.87,
            "raw_content": None,
        },
    ],
    "response_time": 1.23,
}

# Tavily 模拟公司信息响应（get_company_info 返回列表）
MOCK_COMPANY_INFO_RESPONSE = [
    {
        "title": "ByteDance - About",
        "url": "https://www.bytedance.com/about",
        "content": "ByteDance is a technology company...",
        "score": 0.92,
    },
    {
        "title": "ByteDance - Crunchbase",
        "url": "https://www.crunchbase.com/organization/bytedance",
        "content": "ByteDance develops mobile apps...",
        "score": 0.85,
    },
]


# ==================== 正常流程 ====================


class TestWebSearchToolNormalFlow:
    """正常流程测试"""

    async def test_search_returns_valid_response(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """搜索返回有效响应"""
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        result = await tool.search(SAMPLE_QUERY)

        assert isinstance(result, WebSearchResponse)
        assert result.query == SAMPLE_QUERY
        assert result.answer is not None
        assert len(result.results) == 2
        assert result.results[0].title == "ByteDance - Wikipedia"
        assert result.results[0].score == 0.95

    async def test_search_with_answer(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """搜索包含 AI 摘要"""
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        result = await tool.search(SAMPLE_QUERY, include_answer=True)

        assert result.answer is not None
        assert "ByteDance" in result.answer

    async def test_search_without_answer(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """搜索不含 AI 摘要"""
        response_data = {**MOCK_SEARCH_RESPONSE, "answer": None}
        mock_tavily_client.search.return_value = response_data

        result = await tool.search(SAMPLE_QUERY, include_answer=False)

        assert result.answer is None

    async def test_search_parameters_passed_correctly(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """搜索参数正确传递到 SDK"""
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        await tool.search(
            query=SAMPLE_QUERY,
            search_depth="advanced",
            max_results=10,
            include_answer=True,
            topic="news",
        )

        call_args = mock_tavily_client.search.call_args
        assert call_args.kwargs["query"] == SAMPLE_QUERY
        assert call_args.kwargs["search_depth"] == "advanced"
        assert call_args.kwargs["max_results"] == 10
        assert call_args.kwargs["include_answer"] is True
        assert call_args.kwargs["topic"] == "news"

    async def test_search_company_aggregates_results(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """公司搜索聚合多个查询结果"""
        mock_tavily_client.get_company_info.return_value = MOCK_COMPANY_INFO_RESPONSE
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        result = await tool.search_company("字节跳动")

        assert isinstance(result, CompanySearchResults)
        assert result.company_name == "字节跳动"
        assert len(result.search_queries) == 3
        assert result.reputation is not None
        assert result.culture is not None
        assert result.salary is not None

    async def test_search_company_with_extra_context(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """公司搜索带额外上下文"""
        mock_tavily_client.get_company_info.return_value = MOCK_COMPANY_INFO_RESPONSE
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        result = await tool.search_company("字节跳动", extra_context="互联网")

        assert result.company_name == "字节跳动"
        # 验证补充查询中包含额外上下文
        for query in result.search_queries[1:]:  # 跳过 get_company_info 查询
            assert "互联网" in query

    async def test_search_company_uses_get_company_info(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """公司搜索使用 SDK 内置的 get_company_info"""
        mock_tavily_client.get_company_info.return_value = MOCK_COMPANY_INFO_RESPONSE
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        await tool.search_company("字节跳动")

        # 验证调用了 get_company_info
        mock_tavily_client.get_company_info.assert_called_once()
        call_args = mock_tavily_client.get_company_info.call_args
        assert call_args.kwargs["query"] == "字节跳动"
        assert call_args.kwargs["search_depth"] == "advanced"

    async def test_search_company_concurrent_calls(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """公司搜索并发调用（get_company_info + 2 个 search）"""
        mock_tavily_client.get_company_info.return_value = MOCK_COMPANY_INFO_RESPONSE
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        await tool.search_company("字节跳动")

        # get_company_info 调用 1 次，search 调用 2 次（culture + salary）
        assert mock_tavily_client.get_company_info.call_count == 1
        assert mock_tavily_client.search.call_count == 2


# ==================== 边界条件 ====================


class TestWebSearchToolEdgeCases:
    """边界条件测试"""

    async def test_search_empty_results(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """搜索返回空结果"""
        response_data = {**MOCK_SEARCH_RESPONSE, "results": []}
        mock_tavily_client.search.return_value = response_data

        result = await tool.search("nonexistent company")

        assert result.results == []
        assert len(result.results) == 0

    async def test_search_missing_answer_field(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """Tavily 响应缺少 answer 字段"""
        response_data = {k: v for k, v in MOCK_SEARCH_RESPONSE.items() if k != "answer"}
        mock_tavily_client.search.return_value = response_data

        result = await tool.search(SAMPLE_QUERY)

        assert result.answer is None

    async def test_search_extra_fields_ignored(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """Tavily 返回额外字段时正常处理"""
        response_data = {**MOCK_SEARCH_RESPONSE, "extra_field": "ignored"}
        mock_tavily_client.search.return_value = response_data

        result = await tool.search(SAMPLE_QUERY)

        assert len(result.results) == 2

    async def test_search_company_partial_failure(
        self,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """公司搜索部分子查询失败时返回部分结果"""
        # get_company_info 成功，culture search 失败，salary search 成功
        mock_tavily_client.get_company_info.return_value = MOCK_COMPANY_INFO_RESPONSE
        mock_tavily_client.search.side_effect = [
            RuntimeError("culture search failed"),  # culture 查询失败
            MOCK_SEARCH_RESPONSE,  # salary 查询成功
        ]

        search_tool = WebSearchTool(
            tavily_client=mock_tavily_client,
            api_key="test-api-key",
        )
        result = await search_tool.search_company("字节跳动")

        # 应该返回部分结果，不抛异常
        assert isinstance(result, CompanySearchResults)
        assert result.company_name == "字节跳动"
        # reputation 和 salary 应该有值，culture 应该是 None
        assert result.reputation is not None
        assert result.culture is None
        assert result.salary is not None


# ==================== 异常流程 ====================


class TestWebSearchToolExceptions:
    """异常流程测试"""

    async def test_search_api_exception(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """SDK 抛出异常时映射为 ExternalServiceError"""
        mock_tavily_client.search.side_effect = Exception("API connection failed")

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.search(SAMPLE_QUERY)

        assert exc_info.value.error_code == "EXT_011"

    async def test_search_api_timeout(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """SDK 超时时映射为 ExternalServiceError"""
        mock_tavily_client.search.side_effect = TimeoutError("Request timed out")

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.search(SAMPLE_QUERY)

        assert exc_info.value.error_code == "EXT_011"

    async def test_search_api_rate_limit(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """SDK 返回 rate limit 错误时映射为 ExternalServiceError"""
        mock_tavily_client.search.side_effect = Exception("Rate limit exceeded")

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.search(SAMPLE_QUERY)

        assert exc_info.value.error_code == "EXT_011"

    async def test_search_validation_error(
        self,
        tool: WebSearchTool,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """SDK 返回的数据不符合 Pydantic schema"""
        invalid_response = {
            "query": SAMPLE_QUERY,
            "results": "not_a_list",  # 应该是列表
        }
        mock_tavily_client.search.return_value = invalid_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.search(SAMPLE_QUERY)

        assert exc_info.value.error_code == "EXT_014"

    async def test_search_company_company_info_failure(
        self,
        mock_tavily_client: AsyncMock,
    ) -> None:
        """get_company_info 失败时返回部分结果"""
        mock_tavily_client.get_company_info.side_effect = Exception("API error")
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        search_tool = WebSearchTool(
            tavily_client=mock_tavily_client,
            api_key="test-api-key",
        )
        result = await search_tool.search_company("字节跳动")

        # reputation 应该是 None（get_company_info 失败），其他有值
        assert result.reputation is None
        assert result.culture is not None
        assert result.salary is not None


# ==================== 异步上下文管理器 ====================


class TestWebSearchToolContextManager:
    """异步上下文管理器测试"""

    async def test_async_context_manager(self, mock_tavily_client: AsyncMock) -> None:
        """异步上下文管理器正常工作"""
        mock_tavily_client.search.return_value = MOCK_SEARCH_RESPONSE

        async with WebSearchTool(
            tavily_client=mock_tavily_client,
            api_key="test-key",
        ) as search_tool:
            result = await search_tool.search(SAMPLE_QUERY)
            assert result.query == SAMPLE_QUERY

    async def test_close_method(self, mock_tavily_client: AsyncMock) -> None:
        """close 方法调用 tavily_client.close"""
        search_tool = WebSearchTool(
            tavily_client=mock_tavily_client,
            api_key="test-key",
        )
        await search_tool.close()
        mock_tavily_client.close.assert_called_once()
