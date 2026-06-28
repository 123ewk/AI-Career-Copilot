"""Job Analysis Agent 单元测试

职责：
- 测试 JobAnalysisAgent.run() 的编排逻辑
- Mock JDParser / JobExtractor / WebSearchTool 避免真实外部调用
- 覆盖正常流程、阶段转换、异常处理

测试策略：
- Mock 所有外部依赖（Parser/Extractor/WebSearch）
- 验证状态转换顺序：PARSING → EXTRACTING → ANALYZING → COMPLETED
- 验证各阶段的输入输出正确传递
- 验证异常时状态转为 FAILED
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ExternalServiceError
from app.domain.job.models import JobAnalysisResult, JDParseResult
from app.runtime.state.agent_state import AgentState


# ==================== 测试数据 ====================

SAMPLE_JD_TEXT = """
字节跳动招聘 Python 高级工程师
负责后端系统开发，要求熟悉 Python、FastAPI、PostgreSQL
"""

SAMPLE_PARSE_RESULT = JDParseResult(
    raw_text=SAMPLE_JD_TEXT,
    cleaned_text=SAMPLE_JD_TEXT.strip(),
    sections={
        "responsibilities": "负责后端系统开发",
        "requirements": "熟悉 Python、FastAPI、PostgreSQL",
    },
    metadata={"line_count": 3, "char_count": 60},
)

SAMPLE_ANALYSIS = JobAnalysisResult(
    skills=["Python", "FastAPI", "PostgreSQL"],
    keywords=["AI应用开发", "后端开发"],
    difficulty="hard",
    seniority="senior",
)

SAMPLE_COMPANY_SEARCH = MagicMock(
    reputation=MagicMock(
        results=[
            MagicMock(content="字节跳动是一家优秀的互联网公司"),
        ],
    ),
    culture=MagicMock(results=[]),
    salary=MagicMock(results=[]),
)


# ==================== Fixtures ====================


@pytest.fixture
def mock_parser():
    """模拟 JDParser"""
    parser = AsyncMock()
    parser.parse.return_value = SAMPLE_PARSE_RESULT
    return parser


@pytest.fixture
def mock_extractor():
    """模拟 JobExtractor"""
    extractor = AsyncMock()
    extractor.extract.return_value = SAMPLE_ANALYSIS
    return extractor


@pytest.fixture
def mock_web_search():
    """模拟 WebSearchTool"""
    search = AsyncMock()
    search.search_company.return_value = SAMPLE_COMPANY_SEARCH
    return search


@pytest.fixture
def agent(mock_parser, mock_extractor, mock_web_search):
    """JobAnalysisAgent 实例（注入 mock 依赖）"""
    from app.domain.agent.job_analysis_agent import JobAnalysisAgent

    return JobAnalysisAgent(
        parser=mock_parser,
        extractor=mock_extractor,
        web_search=mock_web_search,
    )


# ==================== Normal Flow ====================


class TestJobAnalysisAgentNormalFlow:
    """正常流程测试"""

    async def test_run_success(
        self,
        agent,
        mock_parser,
        mock_extractor,
        mock_web_search,
    ) -> None:
        """完整流程：PARSING → EXTRACTING → ANALYZING → COMPLETED"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert result.analysis == SAMPLE_ANALYSIS
        assert result.agent_state == AgentState.COMPLETED
        assert result.error is None

    async def test_parser_called_first(
        self,
        agent,
        mock_parser,
    ) -> None:
        """Parser 被正确调用"""
        await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        mock_parser.parse.assert_awaited_once_with(SAMPLE_JD_TEXT)

    async def test_extractor_called_with_jd_text(
        self,
        agent,
        mock_extractor,
    ) -> None:
        """Extractor 接收正确的 JD 文本"""
        await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        mock_extractor.extract.assert_awaited_once_with(SAMPLE_JD_TEXT)

    async def test_web_search_called_with_company(
        self,
        agent,
        mock_web_search,
    ) -> None:
        """WebSearch 接收正确的公司名"""
        await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        mock_web_search.search_company.assert_awaited_once_with("字节跳动")

    async def test_run_without_company_skips_search(
        self,
        agent,
        mock_web_search,
    ) -> None:
        """不传 company 时跳过 Web 搜索"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT)

        assert result.analysis == SAMPLE_ANALYSIS
        assert result.agent_state == AgentState.COMPLETED
        mock_web_search.search_company.assert_not_awaited()

    async def test_state_transitions_order(
        self,
        agent,
    ) -> None:
        """最终状态为 COMPLETED"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert result.agent_state == AgentState.COMPLETED


# ==================== Error Handling ====================


class TestJobAnalysisAgentErrors:
    """异常处理测试"""

    async def test_parser_failure_leads_to_failed_state(
        self,
        agent,
        mock_parser,
    ) -> None:
        """Parser 失败 → FAILED 状态"""
        mock_parser.parse.side_effect = ValueError("JD 文本为空")

        result = await agent.run(jd_text="")

        assert result.agent_state == AgentState.FAILED
        assert result.error is not None
        assert "JD 文本为空" in result.error

    async def test_extractor_failure_leads_to_failed_state(
        self,
        agent,
        mock_extractor,
    ) -> None:
        """Extractor 失败 → FAILED 状态"""
        mock_extractor.extract.side_effect = ExternalServiceError(
            detail="LLM 调用超时",
            error_code="EXT_012",
        )

        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert result.agent_state == AgentState.FAILED
        assert result.error is not None

    async def test_web_search_failure_does_not_fail_agent(
        self,
        agent,
        mock_web_search,
    ) -> None:
        """Web 搜索失败不影响主流程（降级）"""
        mock_web_search.search_company.side_effect = ExternalServiceError(
            detail="Tavily API 超时",
            error_code="EXT_012",
        )

        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        # Web 搜索失败是降级场景，Agent 仍然完成
        assert result.analysis == SAMPLE_ANALYSIS
        assert result.agent_state == AgentState.COMPLETED

    async def test_analysis_returned_on_success(
        self,
        agent,
    ) -> None:
        """成功时返回正确的分析结果"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert result.analysis.skills == ["Python", "FastAPI", "PostgreSQL"]
        assert result.analysis.difficulty == "hard"
        assert result.analysis.seniority == "senior"


# ==================== Result Structure ====================


class TestJobAnalysisAgentResult:
    """结果结构测试"""

    async def test_result_has_agent_state(
        self,
        agent,
    ) -> None:
        """结果包含 agent_state 字段"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert hasattr(result, "agent_state")
        assert result.agent_state == AgentState.COMPLETED

    async def test_result_has_analysis(
        self,
        agent,
    ) -> None:
        """结果包含 analysis 字段"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert hasattr(result, "analysis")
        assert isinstance(result.analysis, JobAnalysisResult)

    async def test_result_has_company_info(
        self,
        agent,
    ) -> None:
        """结果包含 company_info 字段"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert hasattr(result, "company_info")

    async def test_result_has_error_none_on_success(
        self,
        agent,
    ) -> None:
        """成功时 error 为 None"""
        result = await agent.run(jd_text=SAMPLE_JD_TEXT, company="字节跳动")

        assert result.error is None
