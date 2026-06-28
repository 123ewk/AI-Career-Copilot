"""Task Service 单元测试

职责：
- 测试 TaskService 的业务编排逻辑
- Mock TaskRepository 避免真实数据库依赖
- 覆盖任务生命周期：创建、状态更新、查询

测试策略：
- Mock TaskRepository：验证 Service 正确调用 repo 方法
- 验证 commit 时机：只有写操作才 commit
- 验证异常翻译：ResourceNotFoundError 正确抛出
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import ResourceNotFoundError
from app.domain.task.service import TaskService
from app.infra.database.models.task import Task, TaskStatus


# ==================== Fixtures ====================


@pytest.fixture
def mock_session() -> AsyncMock:
    """模拟 AsyncSession"""
    return AsyncMock()


@pytest.fixture
def mock_repo() -> AsyncMock:
    """模拟 TaskRepository"""
    return AsyncMock()


@pytest.fixture
def service(mock_session: AsyncMock, mock_repo: AsyncMock) -> TaskService:
    """TaskService 实例（通过构造函数注入 mock repo）"""
    return TaskService(mock_session, repo=mock_repo)


# ==================== 测试数据 ====================

SAMPLE_USER_ID = uuid.uuid4()
SAMPLE_SESSION_ID = uuid.uuid4()
SAMPLE_TASK_ID = uuid.uuid4()


def _make_task(**overrides) -> Task:
    """构造测试用 Task ORM 实例"""
    defaults = {
        "id": SAMPLE_TASK_ID,
        "user_id": SAMPLE_USER_ID,
        "session_id": SAMPLE_SESSION_ID,
        "business_id": "analyze_jd:job-123",
        "task_type": "analyze_jd",
        "status": TaskStatus.PENDING,
        "input_data": {"job_id": "job-123"},
        "result": None,
        "error_message": None,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "updated_at": datetime(2026, 1, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    return Task(**defaults)


# ==================== Create Task ====================


class TestTaskServiceCreate:
    """创建任务测试"""

    async def test_create_task_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """创建任务成功：调用 repo + commit"""
        expected = _make_task()
        mock_repo.create.return_value = expected

        result = await service.create_task(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            task_type="analyze_jd",
            business_id="analyze_jd:job-123",
            input_data={"job_id": "job-123"},
        )

        assert result == expected
        mock_repo.create.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    async def test_create_task_passes_all_params(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """参数正确传递到 repo"""
        mock_repo.create.return_value = _make_task()

        await service.create_task(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            task_type="match_resume",
            business_id="match:resume-456",
            input_data={"job_id": "j1", "resume_id": "r1"},
        )

        call_kwargs = mock_repo.create.call_args.kwargs
        assert call_kwargs["user_id"] == SAMPLE_USER_ID
        assert call_kwargs["session_id"] == SAMPLE_SESSION_ID
        assert call_kwargs["task_type"] == "match_resume"
        assert call_kwargs["business_id"] == "match:resume-456"


# ==================== Mark Running ====================


class TestTaskServiceMarkRunning:
    """标记任务运行中测试"""

    async def test_mark_running_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """标记运行中：调用 repo + commit"""
        task = _make_task()
        mock_repo.get_by_id.return_value = task

        await service.mark_running(SAMPLE_TASK_ID)

        mock_repo.mark_running.assert_awaited_once_with(task)
        mock_session.commit.assert_awaited_once()

    async def test_mark_running_not_found(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """任务不存在抛 ResourceNotFoundError"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.mark_running(SAMPLE_TASK_ID)


# ==================== Mark Completed ====================


class TestTaskServiceMarkCompleted:
    """标记任务完成测试"""

    async def test_mark_completed_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """标记完成：调用 repo + commit"""
        task = _make_task(status=TaskStatus.RUNNING)
        mock_repo.get_by_id.return_value = task
        result_data = {"skills": ["Python"], "difficulty": "medium"}

        await service.mark_completed(SAMPLE_TASK_ID, result=result_data)

        mock_repo.mark_completed.assert_awaited_once_with(task, result=result_data)
        mock_session.commit.assert_awaited_once()

    async def test_mark_completed_not_found(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """任务不存在抛 ResourceNotFoundError"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.mark_completed(SAMPLE_TASK_ID, result={})


# ==================== Mark Failed ====================


class TestTaskServiceMarkFailed:
    """标记任务失败测试"""

    async def test_mark_failed_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """标记失败：调用 repo + commit"""
        task = _make_task(status=TaskStatus.RUNNING)
        mock_repo.get_by_id.return_value = task
        error_msg = "LLM 调用超时"

        await service.mark_failed(SAMPLE_TASK_ID, error_message=error_msg)

        mock_repo.mark_failed.assert_awaited_once_with(task, error_message=error_msg)
        mock_session.commit.assert_awaited_once()

    async def test_mark_failed_not_found(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """任务不存在抛 ResourceNotFoundError"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.mark_failed(SAMPLE_TASK_ID, error_message="error")


# ==================== Get Task ====================


class TestTaskServiceGet:
    """查询任务测试"""

    async def test_get_task_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """查询成功返回 Task"""
        expected = _make_task()
        mock_repo.get_by_id.return_value = expected

        result = await service.get_task(SAMPLE_TASK_ID)

        assert result == expected

    async def test_get_task_not_found(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """任务不存在抛 ResourceNotFoundError"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.get_task(SAMPLE_TASK_ID)


# ==================== List Tasks ====================


class TestTaskServiceList:
    """列表查询测试"""

    async def test_list_tasks_default(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """默认分页查询"""
        mock_repo.list_by_user.return_value = [_make_task()]
        mock_repo.count_by_user.return_value = 1

        result = await service.list_tasks(user_id=SAMPLE_USER_ID)

        assert result["total"] == 1
        assert len(result["items"]) == 1
        mock_repo.list_by_user.assert_awaited_once_with(
            SAMPLE_USER_ID, status=None, limit=20, offset=0,
        )

    async def test_list_tasks_with_status_filter(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """带状态过滤"""
        mock_repo.list_by_user.return_value = []
        mock_repo.count_by_user.return_value = 0

        result = await service.list_tasks(
            user_id=SAMPLE_USER_ID,
            status=TaskStatus.RUNNING,
        )

        assert result["total"] == 0
        mock_repo.list_by_user.assert_awaited_once_with(
            SAMPLE_USER_ID, status=TaskStatus.RUNNING, limit=20, offset=0,
        )

    async def test_list_tasks_with_pagination(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """分页参数传递"""
        mock_repo.list_by_user.return_value = []
        mock_repo.count_by_user.return_value = 0

        await service.list_tasks(
            user_id=SAMPLE_USER_ID,
            limit=10,
            offset=20,
        )

        mock_repo.list_by_user.assert_awaited_once_with(
            SAMPLE_USER_ID, status=None, limit=10, offset=20,
        )
