"""Job Analysis Consumer 单元测试

职责：
- 测试 handle_job_analysis handler 的 5 步流水线
- Mock 所有外部依赖（DB session, Agent, Cache, TaskService, JobRepository）
- 覆盖正常流程、Agent 失败、Job 不存在等场景

测试策略：
- Mock pg_session_factory 避免真实数据库
- Mock JobAnalysisAgent 避免真实 LLM 调用
- Mock RedisJobAnalysisCache 避免真实 Redis
- 验证 mark_running → agent.run → update_analysis → mark_completed 顺序
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.agent.job_analysis_agent import AgentRunResult
from app.domain.job.models import JobAnalysisResult
from app.infra.database.models.task import Task, TaskStatus
from app.runtime.state.agent_state import AgentState


# ==================== 测试数据 ====================

SAMPLE_TASK_ID = uuid.uuid4()
SAMPLE_JOB_ID = uuid.uuid4()
SAMPLE_USER_ID = uuid.uuid4()

SAMPLE_ANALYSIS = JobAnalysisResult(
    skills=["Python", "FastAPI", "PostgreSQL"],
    keywords=["AI应用开发", "后端开发"],
    difficulty="hard",
    seniority="senior",
)

SAMPLE_BODY = {
    "task_id": str(SAMPLE_TASK_ID),
    "job_id": str(SAMPLE_JOB_ID),
    "user_id": str(SAMPLE_USER_ID),
}


def _make_job():
    """构造测试用 Job ORM 实例"""
    job = MagicMock()
    job.id = SAMPLE_JOB_ID
    job.jd_text = "Python 高级工程师 JD..."
    job.company = "字节跳动"
    job.analysis_result = None
    return job


def _make_task():
    """构造测试用 Task ORM 实例"""
    task = MagicMock()
    task.id = SAMPLE_TASK_ID
    task.status = TaskStatus.PENDING
    return task


# ==================== Tests ====================


class TestJobAnalysisConsumer:
    """Job Analysis Consumer 测试"""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """设置所有 mock"""
        self.mock_session = AsyncMock()
        self.mock_session_ctx = AsyncMock()
        self.mock_session_ctx.__aenter__ = AsyncMock(return_value=self.mock_session)
        self.mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        self.mock_task_service = AsyncMock()
        self.mock_job_repo = AsyncMock()
        self.mock_agent = AsyncMock()
        self.mock_cache = AsyncMock()

        # 默认返回值
        self.mock_job_repo.get_by_id.return_value = _make_job()
        self.mock_agent.run.return_value = AgentRunResult(
            analysis=SAMPLE_ANALYSIS,
            agent_state=AgentState.COMPLETED,
        )

    async def test_normal_flow(
        self,
    ) -> None:
        """正常流程：5 步全部成功"""
        with (
            patch("app.infra.message_queue.handlers.job_analysis.pg_session_factory") as mock_factory,
            patch("app.infra.message_queue.handlers.job_analysis.TaskService", return_value=self.mock_task_service),
            patch("app.infra.message_queue.handlers.job_analysis.JobRepository", return_value=self.mock_job_repo),
            patch("app.infra.message_queue.handlers.job_analysis.JobAnalysisAgent", return_value=self.mock_agent),
            patch("app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache", return_value=self.mock_cache),
        ):
            mock_factory.return_value = self.mock_session_ctx

            from app.infra.message_queue.handlers.job_analysis import handle_job_analysis

            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_running 被调用
        self.mock_task_service.mark_running.assert_awaited_once_with(SAMPLE_TASK_ID)

        # 验证 agent.run 被调用
        self.mock_agent.run.assert_awaited_once()

        # 验证 update_analysis 被调用
        self.mock_job_repo.update_analysis.assert_awaited_once()

        # 验证缓存写入
        self.mock_cache.set.assert_awaited_once_with(SAMPLE_JOB_ID, SAMPLE_ANALYSIS)

        # 验证 mark_completed 被调用
        self.mock_task_service.mark_completed.assert_awaited_once_with(
            SAMPLE_TASK_ID,
            result=SAMPLE_ANALYSIS.model_dump(),
        )

    async def test_job_not_found_marks_failed(
        self,
    ) -> None:
        """Job 不存在时标记任务失败"""
        self.mock_job_repo.get_by_id.return_value = None

        with (
            patch("app.infra.message_queue.handlers.job_analysis.pg_session_factory") as mock_factory,
            patch("app.infra.message_queue.handlers.job_analysis.TaskService", return_value=self.mock_task_service),
            patch("app.infra.message_queue.handlers.job_analysis.JobRepository", return_value=self.mock_job_repo),
            patch("app.infra.message_queue.handlers.job_analysis.JobAnalysisAgent", return_value=self.mock_agent),
            patch("app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache", return_value=self.mock_cache),
        ):
            mock_factory.return_value = self.mock_session_ctx

            from app.infra.message_queue.handlers.job_analysis import handle_job_analysis

            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_failed 被调用（不重试）
        self.mock_task_service.mark_failed.assert_awaited_once()
        call_kwargs = self.mock_task_service.mark_failed.call_args
        assert "不存在" in call_kwargs.kwargs.get("error_message", call_kwargs[1].get("error_message", ""))

        # 验证 agent.run 未被调用
        self.mock_agent.run.assert_not_awaited()

    async def test_agent_failure_marks_failed(
        self,
    ) -> None:
        """Agent 失败时标记任务失败"""
        self.mock_agent.run.return_value = AgentRunResult(
            analysis=None,
            agent_state=AgentState.FAILED,
            error="LLM 调用超时",
        )

        with (
            patch("app.infra.message_queue.handlers.job_analysis.pg_session_factory") as mock_factory,
            patch("app.infra.message_queue.handlers.job_analysis.TaskService", return_value=self.mock_task_service),
            patch("app.infra.message_queue.handlers.job_analysis.JobRepository", return_value=self.mock_job_repo),
            patch("app.infra.message_queue.handlers.job_analysis.JobAnalysisAgent", return_value=self.mock_agent),
            patch("app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache", return_value=self.mock_cache),
        ):
            mock_factory.return_value = self.mock_session_ctx

            from app.infra.message_queue.handlers.job_analysis import handle_job_analysis

            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_failed 被调用
        self.mock_task_service.mark_failed.assert_awaited_once()

        # 验证 mark_completed 未被调用
        self.mock_task_service.mark_completed.assert_not_awaited()

        # 验证缓存未写入
        self.mock_cache.set.assert_not_awaited()

    async def test_mark_running_failure_raises(
        self,
    ) -> None:
        """mark_running 失败时抛异常（让 consumer 基类处理重试）"""
        self.mock_task_service.mark_running.side_effect = Exception("DB 连接断开")

        with (
            patch("app.infra.message_queue.handlers.job_analysis.pg_session_factory") as mock_factory,
            patch("app.infra.message_queue.handlers.job_analysis.TaskService", return_value=self.mock_task_service),
            patch("app.infra.message_queue.handlers.job_analysis.JobRepository", return_value=self.mock_job_repo),
            patch("app.infra.message_queue.handlers.job_analysis.JobAnalysisAgent", return_value=self.mock_agent),
            patch("app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache", return_value=self.mock_cache),
        ):
            mock_factory.return_value = self.mock_session_ctx

            from app.infra.message_queue.handlers.job_analysis import handle_job_analysis

            with pytest.raises(Exception, match="DB 连接断开"):
                await handle_job_analysis(SAMPLE_BODY)

    async def test_cache_failure_does_not_fail_pipeline(
        self,
    ) -> None:
        """缓存写入失败不影响主流程"""
        self.mock_cache.set.side_effect = ConnectionError("Redis 连接断开")

        with (
            patch("app.infra.message_queue.handlers.job_analysis.pg_session_factory") as mock_factory,
            patch("app.infra.message_queue.handlers.job_analysis.TaskService", return_value=self.mock_task_service),
            patch("app.infra.message_queue.handlers.job_analysis.JobRepository", return_value=self.mock_job_repo),
            patch("app.infra.message_queue.handlers.job_analysis.JobAnalysisAgent", return_value=self.mock_agent),
            patch("app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache", return_value=self.mock_cache),
        ):
            mock_factory.return_value = self.mock_session_ctx

            from app.infra.message_queue.handlers.job_analysis import handle_job_analysis

            # 不应抛异常
            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_completed 仍被调用
        self.mock_task_service.mark_completed.assert_awaited_once()
