"""Task Service 单元测试

职责：
- 测试 TaskService 的业务编排逻辑
- Mock TaskRepository 避免真实数据库依赖
- Mock insert_idempotent 避免 AsyncSession 默认值问题
- 覆盖任务生命周期：创建、状态更新、查询、状态机非法转换

测试策略：
- Mock TaskRepository：验证 Service 正确调用 repo 方法
- Mock insert_idempotent：验证 Service 正确构造 Task 参数
- 验证 commit 时机：只有写操作才 commit
- 验证异常翻译：ResourceNotFoundError / TaskStateError 正确抛出
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ConflictError, ResourceNotFoundError, TaskStateError
from app.domain.task.dto import TaskDTO, TaskListResponse
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
SAMPLE_CREATED_AT = datetime(2026, 1, 1, 12, 0, 0)
SAMPLE_UPDATED_AT = datetime(2026, 1, 1, 12, 0, 1)


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
        "created_at": SAMPLE_CREATED_AT,
        "updated_at": SAMPLE_UPDATED_AT,
    }
    defaults.update(overrides)
    return Task(**defaults)


# ==================== Create Task ====================


class TestTaskServiceCreate:
    """创建任务测试"""

    @patch("app.domain.task.service.insert_idempotent")
    async def test_create_task_success(
        self,
        mock_insert: MagicMock,
        service: TaskService,
        mock_session: AsyncMock,
    ) -> None:
        """创建任务成功：调用 insert_idempotent + commit + 返回 TaskDTO"""
        expected_task = _make_task()
        mock_insert.return_value = expected_task

        result = await service.create_task(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            task_type="analyze_jd",
            business_id="analyze_jd:job-123",
            input_data={"job_id": "job-123"},
        )

        assert isinstance(result, TaskDTO)
        assert result.id == SAMPLE_TASK_ID
        assert result.status == TaskStatus.PENDING
        mock_insert.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    @patch("app.domain.task.service.insert_idempotent")
    async def test_create_task_duplicate(
        self,
        mock_insert: MagicMock,
        service: TaskService,
    ) -> None:
        """business_id 重复时抛 ConflictError"""
        from app.core.exceptions import DuplicateMessageError

        mock_insert.side_effect = DuplicateMessageError(
            message_id="analyze_jd:job-123",
        )

        with pytest.raises(ConflictError):
            await service.create_task(
                user_id=SAMPLE_USER_ID,
                session_id=SAMPLE_SESSION_ID,
                task_type="analyze_jd",
                business_id="analyze_jd:job-123",
            )

    @patch("app.domain.task.service.insert_idempotent")
    async def test_create_task_passes_all_params(
        self,
        mock_insert: MagicMock,
    ) -> None:
        """参数正确传递到 insert_idempotent"""
        mock_insert.return_value = _make_task()
        session = AsyncMock()
        svc = TaskService(session)

        await svc.create_task(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            task_type="match_resume",
            business_id="match:resume-456",
            input_data={"job_id": "j1", "resume_id": "r1"},
        )

        call_args = mock_insert.call_args
        assert call_args.args[0] is session  # 第一个位置参数是 session
        assert call_args.args[1] is Task     # 第二个位置参数是 Task Model
        kwargs = call_args.kwargs
        assert kwargs["user_id"] == SAMPLE_USER_ID
        assert kwargs["session_id"] == SAMPLE_SESSION_ID
        assert kwargs["task_type"] == "match_resume"
        assert kwargs["business_id"] == "match:resume-456"
        assert kwargs["status"] == TaskStatus.PENDING


# ==================== Mark Running ====================


class TestTaskServiceMarkRunning:
    """标记任务运行中测试"""

    async def test_mark_running_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """标记运行中：调用 repo + commit + 返回 TaskDTO"""
        task = _make_task()
        mock_repo.get_by_id.return_value = task
        mock_repo.mark_running.return_value = task

        result = await service.mark_running(SAMPLE_TASK_ID)

        assert isinstance(result, TaskDTO)
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

    async def test_mark_running_invalid_state(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """非 PENDING 任务不能 mark_running"""
        mock_repo.get_by_id.return_value = _make_task(status=TaskStatus.COMPLETED)

        with pytest.raises(TaskStateError):
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
        """标记完成：调用 repo + commit + 返回 TaskDTO"""
        task = _make_task(status=TaskStatus.RUNNING)
        mock_repo.get_by_id.return_value = task
        mock_repo.mark_completed.return_value = task
        result_data = {"skills": ["Python"], "difficulty": "medium"}

        result = await service.mark_completed(SAMPLE_TASK_ID, result=result_data)

        assert isinstance(result, TaskDTO)
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

    async def test_mark_completed_invalid_state(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """非 RUNNING 任务不能 mark_completed"""
        mock_repo.get_by_id.return_value = _make_task(status=TaskStatus.PENDING)

        with pytest.raises(TaskStateError):
            await service.mark_completed(SAMPLE_TASK_ID, result={})


# ==================== Mark Failed ====================


class TestTaskServiceMarkFailed:
    """标记任务失败测试"""

    async def test_mark_failed_success_from_running(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """RUNNING → FAILED：执行中失败（如 Agent 异常 / 超时）"""
        task = _make_task(status=TaskStatus.RUNNING)
        mock_repo.get_by_id.return_value = task
        mock_repo.mark_failed.return_value = task
        error_msg = "LLM 调用超时"

        result = await service.mark_failed(SAMPLE_TASK_ID, error_message=error_msg)

        assert isinstance(result, TaskDTO)
        mock_repo.mark_failed.assert_awaited_once_with(task, error_message=error_msg)
        mock_session.commit.assert_awaited_once()

    async def test_mark_failed_success_from_pending(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """PENDING → FAILED：未开始即失败（如 MQ 投递失败 / Publisher 未注入）

        覆盖 JobService.analyze_job 中 sync_fallback=False 时直接 mark_failed 的场景。
        修复前 _VALID_TRANSITIONS 不允许此转换，会抛 TaskStateError；
        修复后允许 PENDING → FAILED，避免 API 层错误吞掉。
        """
        task = _make_task(status=TaskStatus.PENDING)
        mock_repo.get_by_id.return_value = task
        mock_repo.mark_failed.return_value = task
        error_msg = "MQ 发布失败"

        result = await service.mark_failed(SAMPLE_TASK_ID, error_message=error_msg)

        assert isinstance(result, TaskDTO)
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

    async def test_mark_failed_invalid_state(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """COMPLETED / FAILED / CANCELLED 状态不能 mark_failed"""
        mock_repo.get_by_id.return_value = _make_task(status=TaskStatus.COMPLETED)

        with pytest.raises(TaskStateError):
            await service.mark_failed(SAMPLE_TASK_ID, error_message="error")


# ==================== Get Task ====================


class TestTaskServiceGet:
    """查询任务测试"""

    async def test_get_task_success(
        self,
        service: TaskService,
        mock_repo: AsyncMock,
    ) -> None:
        """查询成功返回 TaskDTO"""
        expected = _make_task()
        mock_repo.get_by_id.return_value = expected

        result = await service.get_task(SAMPLE_TASK_ID)

        assert isinstance(result, TaskDTO)
        assert result.id == SAMPLE_TASK_ID

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
        """默认分页查询返回 TaskListResponse"""
        mock_repo.list_by_user.return_value = [_make_task()]
        mock_repo.count_by_user.return_value = 1

        result = await service.list_tasks(user_id=SAMPLE_USER_ID)

        assert isinstance(result, TaskListResponse)
        assert result.total == 1
        assert len(result.items) == 1
        assert isinstance(result.items[0], TaskDTO)
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

        assert result.total == 0
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
