"""Task Repository 单元测试

职责：
- 测试 TaskRepository 的数据访问层行为
- Mock AsyncSession 避免真实数据库依赖
- 覆盖 CRUD、状态更新、分页查询

测试策略：
- 使用 AsyncMock(spec=AsyncSession) 模拟 session
- 验证 flush 调用（不 commit，由 Service 控制事务）
- 验证 ORM 属性赋值正确性
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.database.models.task import Task, TaskStatus


# ==================== Fixtures ====================


@pytest.fixture
def mock_session() -> AsyncMock:
    """模拟 AsyncSession"""
    session = AsyncMock(spec=AsyncSession)
    # execute 返回值需要链式调用
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one_or_none.return_value = None
    mock_result.scalar_one.return_value = 0
    session.execute.return_value = mock_result
    return session


@pytest.fixture
def repo(mock_session: AsyncMock):
    """TaskRepository 实例"""
    from app.infra.repositories.task_repo import TaskRepository

    return TaskRepository(mock_session)


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


# ==================== Create ====================


class TestTaskRepoCreate:
    """创建任务测试"""

    async def test_create_basic(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """基本创建：flush 被调用，返回 Task 实例"""
        task = await repo.create(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            business_id="analyze_jd:job-123",
            task_type="analyze_jd",
            input_data={"job_id": "job-123"},
        )

        assert isinstance(task, Task)
        assert task.user_id == SAMPLE_USER_ID
        assert task.session_id == SAMPLE_SESSION_ID
        assert task.business_id == "analyze_jd:job-123"
        assert task.task_type == "analyze_jd"
        assert task.status == TaskStatus.PENDING
        mock_session.add.assert_called_once()
        mock_session.flush.assert_awaited_once()

    async def test_create_default_status(
        self,
        repo,
    ) -> None:
        """默认状态为 PENDING"""
        task = await repo.create(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            business_id="test:1",
            task_type="test",
        )

        assert task.status == TaskStatus.PENDING

    async def test_create_with_input_data(
        self,
        repo,
    ) -> None:
        """带 input_data 创建"""
        input_data = {"job_id": "uuid-123", "jd_text": "test jd"}
        task = await repo.create(
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            business_id="analyze_jd:uuid-123",
            task_type="analyze_jd",
            input_data=input_data,
        )

        assert task.input_data == input_data


# ==================== Read ====================


class TestTaskRepoRead:
    """查询任务测试"""

    async def test_get_by_id_found(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """按 ID 查询：找到返回 Task"""
        expected = _make_task()
        mock_session.get.return_value = expected

        result = await repo.get_by_id(SAMPLE_TASK_ID)

        assert result == expected
        mock_session.get.assert_awaited_once_with(Task, SAMPLE_TASK_ID)

    async def test_get_by_id_not_found(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """按 ID 查询：未找到返回 None"""
        mock_session.get.return_value = None

        result = await repo.get_by_id(SAMPLE_TASK_ID)

        assert result is None

    async def test_list_default(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """默认分页查询"""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [_make_task()]
        mock_session.execute.return_value = mock_result

        result = await repo.list_by_user(SAMPLE_USER_ID)

        assert len(result) == 1
        mock_session.execute.assert_awaited_once()

    async def test_list_with_status_filter(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """带状态过滤的查询"""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await repo.list_by_user(
            SAMPLE_USER_ID,
            status=TaskStatus.RUNNING,
        )

        assert len(result) == 0

    async def test_list_with_pagination(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """分页参数传递"""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        await repo.list_by_user(SAMPLE_USER_ID, limit=10, offset=20)

        mock_session.execute.assert_awaited_once()

    async def test_count(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """统计任务总数"""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5
        mock_session.execute.return_value = mock_result

        result = await repo.count_by_user(SAMPLE_USER_ID)

        assert result == 5


# ==================== Update ====================


class TestTaskRepoUpdate:
    """更新任务状态测试"""

    async def test_mark_running(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """标记为 RUNNING"""
        task = _make_task(status=TaskStatus.PENDING)

        await repo.mark_running(task)

        assert task.status == TaskStatus.RUNNING
        mock_session.flush.assert_awaited()

    async def test_mark_completed(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """标记为 COMPLETED，带结果"""
        task = _make_task(status=TaskStatus.RUNNING)
        result = {"skills": ["Python"], "difficulty": "medium"}

        await repo.mark_completed(task, result=result)

        assert task.status == TaskStatus.COMPLETED
        assert task.result == result
        mock_session.flush.assert_awaited()

    async def test_mark_failed(
        self,
        repo,
        mock_session: AsyncMock,
    ) -> None:
        """标记为 FAILED，带错误信息"""
        task = _make_task(status=TaskStatus.RUNNING)
        error_msg = "LLM 调用超时"

        await repo.mark_failed(task, error_message=error_msg)

        assert task.status == TaskStatus.FAILED
        assert task.error_message == error_msg
        mock_session.flush.assert_awaited()
