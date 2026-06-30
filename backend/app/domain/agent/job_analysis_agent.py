"""Job Analysis Agent

职责：
- 编排 JD 分析的完整流水线：解析 → 提取 → 搜索补充 → 完成
- 纯计算函数，被 Consumer 调用（不直接暴露给 API）
- 管理 AgentState 状态转换，输出 AgentRunResult

设计动机：
- LangGraph 风格的状态机：每个阶段有明确的输入/输出和状态转换
- 纯函数设计：不依赖 session/DB/cache，所有依赖通过构造函数注入
  → 易于测试（mock 依赖）和复用（被 Consumer / 测试 / CLI 调用）
- Web 搜索降级：search_company 失败不影响主流程（返回 None）

状态流转：
    PARSING → EXTRACTING → ANALYZING → COMPLETED
                                ↓ (异常)
                             FAILED

输入/输出：
- 输入：jd_text (str), company (str | None)
- 输出：AgentRunResult (analysis + agent_state + company_info + error)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logger import logger
from app.domain.job.extractor import JobExtractor
from app.domain.job.models import JobAnalysisResult, JDParseResult
from app.domain.job.parser import JDParser
from app.runtime.state.agent_state import AgentState, validate_transition
from app.tools.retrieval.models import CompanySearchResults
from app.tools.retrieval.web_search import WebSearchTool


@dataclass(frozen=True)
class AgentRunResult:
    """Agent 运行结果

    Attributes:
        analysis: LLM 提取的结构化分析结果
        agent_state: Agent 最终状态（COMPLETED 或 FAILED）
        company_info: Web 搜索获取的公司信息（可选，降级时为 None）
        error: 错误信息（成功时为 None）
    """

    analysis: JobAnalysisResult | None = None
    agent_state: AgentState = AgentState.COMPLETED
    company_info: CompanySearchResults | None = None
    error: str | None = None


class JobAnalysisAgent:
    """Job Analysis Agent

    用法：
        agent = JobAnalysisAgent()  # 使用默认依赖
        result = await agent.run(jd_text="...", company="字节跳动")
        if result.agent_state == AgentState.COMPLETED:
            print(result.analysis.skills)

    设计原则：
    - 依赖注入：parser/extractor/web_search 通过构造函数传入
    - 默认依赖：不传时自动创建（生产环境用默认，测试时注入 mock）
    - 状态机驱动：每阶段前校验状态转换合法性
    - 异常隔离：Web 搜索失败降级，Parser/Extractor 失败终止
    """

    def __init__(
        self,
        parser: JDParser | None = None,
        extractor: JobExtractor | None = None,
        web_search: WebSearchTool | None = None,
    ) -> None:
        """初始化 Agent

        Args:
            parser: JD 文本解析器。None 时自动创建。
            extractor: JD 信息提取器。None 时自动创建。
            web_search: Web 搜索工具。None 时自动创建。
        """
        self._parser = parser or JDParser()
        self._extractor = extractor or JobExtractor()
        self._web_search = web_search or WebSearchTool()
        self._state: AgentState = AgentState.PARSING

    async def run(
        self,
        jd_text: str,
        company: str | None = None,
    ) -> AgentRunResult:
        """执行 JD 分析流水线

        Args:
            jd_text: JD 原始文本
            company: 公司名称（可选，用于 Web 搜索补充信息）

        Returns:
            AgentRunResult: 分析结果 + 状态 + 公司信息 + 错误信息
        """
        logger.info("Job Analysis Agent 启动 | text_len={} | company={}", len(jd_text), company)

        # ---- 阶段 1: PARSING（初始状态，无需转换）----
        try:
            parse_result = await self._parser.parse(jd_text)
            logger.info("JD 解析完成 | sections={}", list(parse_result.sections.keys()))
        except Exception as exc:
            return self._fail(f"JD 解析失败: {exc}")

        # ---- 阶段 2: EXTRACTING ----
        try:
            self._transition_to(AgentState.EXTRACTING)
            analysis = await self._extractor.extract(jd_text)
            logger.info(
                "JD 提取完成 | skills_count={} | difficulty={}",
                len(analysis.skills),
                analysis.difficulty,
            )
        except Exception as exc:
            return self._fail(f"JD 提取失败: {exc}")

        # ---- 阶段 3: ANALYZING（Web 搜索，降级容忍）----
        company_info = None
        try:
            self._transition_to(AgentState.ANALYZING)
            if company:
                search_result = await self._web_search.search_company(company)
                company_info = search_result
                logger.info("公司信息搜索完成 | company={}", company)
            else:
                logger.info("未提供公司名，跳过 Web 搜索")
        except Exception as exc:
            # Web 搜索失败是降级场景，不影响主流程
            logger.warning("公司信息搜索失败（降级） | company={} | exc={}", company, exc)

        # ---- 阶段 4: COMPLETED ----
        self._transition_to(AgentState.COMPLETED)

        logger.info(
            "Job Analysis Agent 完成 | skills_count={} | difficulty={} | seniority={}",
            len(analysis.skills),
            analysis.difficulty,
            analysis.seniority,
        )

        return AgentRunResult(
            analysis=analysis,
            agent_state=AgentState.COMPLETED,
            company_info=company_info,
            error=None,
        )

    def _transition_to(self, next_state: AgentState) -> None:
        """执行状态转换（含合法性校验）

        Args:
            next_state: 目标状态

        Raises:
            ValueError: 非法状态转换
        """
        validate_transition(self._state, next_state)
        self._state = next_state

    def _fail(self, error_message: str) -> AgentRunResult:
        """将 Agent 标记为 FAILED 并返回结果

        Args:
            error_message: 错误描述

        Returns:
            AgentRunResult: 失败状态的结果
        """
        logger.error("Job Analysis Agent 失败 | error={}", error_message)
        try:
            self._transition_to(AgentState.FAILED)
        except ValueError:
            # 如果当前状态不允许转到 FAILED（已经是终态），直接设置
            self._state = AgentState.FAILED

        return AgentRunResult(
            analysis=None,
            agent_state=AgentState.FAILED,
            company_info=None,
            error=error_message,
        )


__all__ = ["AgentRunResult", "JobAnalysisAgent"]
