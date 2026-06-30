"""Agent 领域服务 Facade

职责：
- 对外统一暴露 Agent 能力，避免调用方直接依赖 domain/agent/job_analysis_agent.py
- 当前主要提供 Job Analysis Agent 的入口
- 未来可在此扩展 Resume Agent、Communication Agent、Strategy Agent 等

设计动机：
- 与 TaskService / JobService 保持一致：调用方 import app.domain.agent.service
- 隔离具体 Agent 实现，便于后续替换为 LangGraph 编排或多 Agent 协调
"""

from __future__ import annotations

from app.domain.agent.job_analysis_agent import AgentRunResult, JobAnalysisAgent


class AgentService:
    """Agent 领域服务

    用法：
        service = AgentService()
        result = await service.analyze_job(jd_text="...", company="字节跳动")
    """

    def __init__(self, job_analysis_agent: JobAnalysisAgent | None = None) -> None:
        """初始化

        Args:
            job_analysis_agent: Job Analysis Agent 实例。None 时创建默认实例。
        """
        self._job_analysis_agent = job_analysis_agent or JobAnalysisAgent()

    async def analyze_job(
        self,
        jd_text: str,
        company: str | None = None,
    ) -> AgentRunResult:
        """执行 Job Analysis Agent

        Args:
            jd_text: JD 原始文本
            company: 公司名称（可选）

        Returns:
            AgentRunResult
        """
        return await self._job_analysis_agent.run(jd_text=jd_text, company=company)


__all__ = ["AgentService", "AgentRunResult", "JobAnalysisAgent"]
