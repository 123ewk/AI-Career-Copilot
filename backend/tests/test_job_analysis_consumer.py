"""Job Analysis Consumer 单元测试

职责：
- 测试 handle_job_analysis handler 的 5 步流水线
- Mock 所有外部依赖（DB session, JobService, Cache, TaskService, MQ Channel）
- 覆盖正常流程、Agent 失败、Job 不存在、缓存失败等场景

测试策略：
- Mock pg_session_factory 避免真实数据库
- Mock JobService（Consumer 直接 import 此类，而非 JobRepository/Agent）
- Mock RedisJobAnalysisCache 避免真实 Redis
- Mock rabbitmq_connection_factory 避免真实 MQ
- 验证 mark_running → run_analysis → mark_completed → publish event 顺序

关键修正（与旧测试的差异）：
- Consumer 实际 import 的是 JobService（不是 JobRepository / JobAnalysisAgent）
- JobService 内部封装了 JobRepository 和 JobAnalysisAgent
- 测试必须 patch JobService 而非其内部依赖
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.job.models import JobAnalysisResult, JobResponse
from app.infra.database.models.task import TaskStatus


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
    "business_id": f"analyze_jd:{SAMPLE_JOB_ID}",
}


def _make_job_response():
    """构造测试用 JobResponse DTO（Consumer 通过 JobService.get_job 获取）"""
    return JobResponse(
        id=SAMPLE_JOB_ID,
        title="Python 高级工程师",
        company="字节跳动",
        jd_text="Python 高级工程师 JD...",
        source="boss",
        source_url=None,
        salary_min=30,
        salary_max=50,
        location="北京",
        skills=["Python"],
        keywords=["后端开发"],
        seniority=None,
        difficulty=None,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )


# ==================== Tests ====================


class TestJobAnalysisConsumer:
    """Job Analysis Consumer 测试"""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """设置所有 mock

        Consumer 实际依赖：
        - pg_session_factory（DB session 工厂）
        - TaskService（任务状态管理）
        - JobService（业务编排：get_job + run_analysis）
        - RedisJobAnalysisCache（缓存读写，由 JobService 内部使用）
        - rabbitmq_connection_factory（发布完成事件）
        """
        self.mock_session = AsyncMock()

        self.mock_task_service = AsyncMock()
        # 默认 task 为 PENDING（待执行）
        from app.domain.task.dto import TaskDTO

        self.mock_task_service.get_task.return_value = TaskDTO(
            id=SAMPLE_TASK_ID,
            user_id=SAMPLE_USER_ID,
            session_id=uuid.uuid4(),
            business_id=f"analyze_jd:{SAMPLE_JOB_ID}",
            task_type="analyze_jd",
            status=TaskStatus.PENDING,
            input_data={"job_id": str(SAMPLE_JOB_ID)},
            result=None,
            error_message=None,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            updated_at=datetime(2026, 1, 1, 12, 0, 1),
        )

        self.mock_job_service = AsyncMock()
        self.mock_job_service.get_job.return_value = _make_job_response()
        self.mock_job_service.run_analysis.return_value = SAMPLE_ANALYSIS

        self.mock_cache = AsyncMock()

        self.mock_channel = AsyncMock()
        self.mock_mq_factory = AsyncMock()
        self.mock_mq_factory.get_channel.return_value = self.mock_channel

    async def test_normal_flow(self) -> None:
        """正常流程：5 步全部成功

        1. mark_running: PENDING → RUNNING
        2. run_analysis: JobService 执行 Agent + 落库 + 写缓存
        3. mark_completed: RUNNING → COMPLETED + result
        4. publish agent.event.completed
        """
        with (
            patch(
                "app.infra.message_queue.handlers.job_analysis.pg_session_factory"
            ) as mock_factory,
            patch(
                "app.infra.message_queue.handlers.job_analysis.TaskService",
                return_value=self.mock_task_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.JobService",
                return_value=self.mock_job_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache",
                return_value=self.mock_cache,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.rabbitmq_connection_factory",
                self.mock_mq_factory,
            ),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.infra.message_queue.handlers.job_analysis import (
                handle_job_analysis,
            )

            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_running 被调用（Step 1）
        self.mock_task_service.mark_running.assert_awaited_once_with(SAMPLE_TASK_ID)

        # 验证 JobService.get_job 被调用（Step 2 准备）
        self.mock_job_service.get_job.assert_awaited_once_with(SAMPLE_JOB_ID)

        # 验证 JobService.run_analysis 被调用（Step 2 执行）
        self.mock_job_service.run_analysis.assert_awaited_once()
        run_call = self.mock_job_service.run_analysis.call_args
        assert run_call.args[0] == SAMPLE_JOB_ID  # job_id 位置参数

        # 验证 mark_completed 被调用 + result 非空（Step 3）
        self.mock_task_service.mark_completed.assert_awaited_once()
        completed_call = self.mock_task_service.mark_completed.call_args
        assert completed_call.args == (SAMPLE_TASK_ID,)
        result = completed_call.kwargs.get("result")
        assert result is not None
        assert "skills" in result
        assert "Python" in result["skills"]

        # 验证完成事件已发布（Step 4）
        self.mock_mq_factory.get_channel.assert_awaited()

    async def test_job_not_found_marks_failed(self) -> None:
        """Job 不存在时标记任务失败 + 不重试

        JobService.get_job 抛 ResourceNotFoundError → Consumer 捕获并 mark_failed
        """
        from app.core.exceptions import ResourceNotFoundError

        self.mock_job_service.get_job.side_effect = ResourceNotFoundError(
            detail=f"岗位 {SAMPLE_JOB_ID} 不存在"
        )

        with (
            patch(
                "app.infra.message_queue.handlers.job_analysis.pg_session_factory"
            ) as mock_factory,
            patch(
                "app.infra.message_queue.handlers.job_analysis.TaskService",
                return_value=self.mock_task_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.JobService",
                return_value=self.mock_job_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache",
                return_value=self.mock_cache,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.rabbitmq_connection_factory",
                self.mock_mq_factory,
            ),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.infra.message_queue.handlers.job_analysis import (
                handle_job_analysis,
            )

            # 不应抛异常（业务错误被捕获）
            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_failed 被调用
        self.mock_task_service.mark_failed.assert_awaited_once()
        failed_call = self.mock_task_service.mark_failed.call_args
        assert "不存在" in failed_call.kwargs.get("error_message", "")

        # 验证 run_analysis 未被调用
        self.mock_job_service.run_analysis.assert_not_awaited()

        # 验证 mark_completed 未被调用
        self.mock_task_service.mark_completed.assert_not_awaited()

    async def test_run_analysis_failure_raises_for_retry(self) -> None:
        """run_analysis 失败时抛异常让 Consumer 基类重试

        JobService.run_analysis 抛异常 → Consumer 不在末次重试时 raise
        """
        self.mock_job_service.run_analysis.side_effect = Exception("LLM 调用超时")

        with (
            patch(
                "app.infra.message_queue.handlers.job_analysis.pg_session_factory"
            ) as mock_factory,
            patch(
                "app.infra.message_queue.handlers.job_analysis.TaskService",
                return_value=self.mock_task_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.JobService",
                return_value=self.mock_job_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache",
                return_value=self.mock_cache,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.rabbitmq_connection_factory",
                self.mock_mq_factory,
            ),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.infra.message_queue.handlers.job_analysis import (
                handle_job_analysis,
            )

            # 非末次重试时应抛异常让 Consumer 基类继续重试
            with pytest.raises(Exception, match="LLM 调用超时"):
                await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_completed 未被调用（执行失败）
        self.mock_task_service.mark_completed.assert_not_awaited()

    async def test_mark_running_failure_raises(self) -> None:
        """mark_running 失败时抛异常（让 consumer 基类处理重试）

        TaskService.mark_running 抛非 TaskStateError 异常 → Consumer 直接 raise
        """
        self.mock_task_service.mark_running.side_effect = Exception("DB 连接断开")

        with (
            patch(
                "app.infra.message_queue.handlers.job_analysis.pg_session_factory"
            ) as mock_factory,
            patch(
                "app.infra.message_queue.handlers.job_analysis.TaskService",
                return_value=self.mock_task_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.JobService",
                return_value=self.mock_job_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache",
                return_value=self.mock_cache,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.rabbitmq_connection_factory",
                self.mock_mq_factory,
            ),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.infra.message_queue.handlers.job_analysis import (
                handle_job_analysis,
            )

            with pytest.raises(Exception, match="DB 连接断开"):
                await handle_job_analysis(SAMPLE_BODY)

    async def test_publish_event_failure_does_not_fail_pipeline(self) -> None:
        """发布完成事件失败不影响主流程（任务已完成，事件可补偿）"""
        # 模拟 MQ channel 获取失败
        self.mock_mq_factory.get_channel.side_effect = ConnectionError("MQ 断连")

        with (
            patch(
                "app.infra.message_queue.handlers.job_analysis.pg_session_factory"
            ) as mock_factory,
            patch(
                "app.infra.message_queue.handlers.job_analysis.TaskService",
                return_value=self.mock_task_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.JobService",
                return_value=self.mock_job_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache",
                return_value=self.mock_cache,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.rabbitmq_connection_factory",
                self.mock_mq_factory,
            ),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.infra.message_queue.handlers.job_analysis import (
                handle_job_analysis,
            )

            # 不应抛异常（事件发布失败是 warning 不 raise）
            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_completed 仍被调用（主流程已完成）
        self.mock_task_service.mark_completed.assert_awaited_once()

    async def test_already_completed_task_skipped(self) -> None:
        """已完成任务被跳过（幂等）"""
        from app.domain.task.dto import TaskDTO

        # Task 已是 COMPLETED 状态
        self.mock_task_service.get_task.return_value = TaskDTO(
            id=SAMPLE_TASK_ID,
            user_id=SAMPLE_USER_ID,
            session_id=uuid.uuid4(),
            business_id=f"analyze_jd:{SAMPLE_JOB_ID}",
            task_type="analyze_jd",
            status=TaskStatus.COMPLETED,
            input_data={"job_id": str(SAMPLE_JOB_ID)},
            result={"skills": ["Python"]},
            error_message=None,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            updated_at=datetime(2026, 1, 1, 12, 0, 1),
        )

        with (
            patch(
                "app.infra.message_queue.handlers.job_analysis.pg_session_factory"
            ) as mock_factory,
            patch(
                "app.infra.message_queue.handlers.job_analysis.TaskService",
                return_value=self.mock_task_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.JobService",
                return_value=self.mock_job_service,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.RedisJobAnalysisCache",
                return_value=self.mock_cache,
            ),
            patch(
                "app.infra.message_queue.handlers.job_analysis.rabbitmq_connection_factory",
                self.mock_mq_factory,
            ),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=self.mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.infra.message_queue.handlers.job_analysis import (
                handle_job_analysis,
            )

            await handle_job_analysis(SAMPLE_BODY)

        # 验证 mark_running 未被调用（任务已完成）
        self.mock_task_service.mark_running.assert_not_awaited()

        # 验证 run_analysis 未被调用
        self.mock_job_service.run_analysis.assert_not_awaited()

        # 验证 mark_completed 未被调用
        self.mock_task_service.mark_completed.assert_not_awaited()
