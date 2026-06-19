"""Task 业务唯一键 + insert_idempotent 集成测试

覆盖：
- Task 模型 (user_id, business_id) 联合唯一约束在 ORM 层面定义正确
- insert_idempotent 配合 Task：首次成功 / 重复时抛 DuplicateMessageError
- 不同 user 相同 business_id 不冲突（多租户隔离）
- 重复 ID 被 _extract_business_id 正确提取

这些测试不连真实数据库，使用 SQLAlchemy 的 mock 模拟 IntegrityError。
真实数据库层级的 unique 约束由 Alembic migration 保证（见
migrations/versions/a1b2c3d4e5f6_add_business_unique_key_to_tasks_for_mq_.py）。

集成测试（真实 PG）应在 conftest.py 启用 db session 时跑 e2e。
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import DuplicateMessageError
from app.domain.common.idempotent import _extract_business_id, insert_idempotent
from app.infra.database.models.task import Task

# ==================== ORM Model 静态校验 ====================


def test_task_table_args_contains_business_unique_index() -> None:
    """Task 的 __table_args__ 必须包含 (user_id, business_id) 联合唯一索引

    这是 MQ 幂等消费的前置条件，缺失将导致：
    - Publisher Confirms 竞态时业务被重复执行
    - 重投消息触发业务逻辑（如重新调 LLM）造成资源浪费
    """
    indexes = Task.__table_args__
    # __table_args__ 是 Index 对象的 tuple，或包含 Index 的 dict
    flat = indexes if isinstance(indexes, tuple) else (indexes,)

    found = False
    for item in flat:
        # Index 对象有 .name / .unique / .columns
        if hasattr(item, "name") and item.name == "uq_tasks_user_business":
            assert item.unique is True, "uq_tasks_user_business 必须是 unique 索引"
            column_names = [c.name for c in item.columns]
            assert "user_id" in column_names
            assert "business_id" in column_names
            found = True
            break

    assert found, (
        f"Task.__table_args__ 必须包含 uq_tasks_user_business (user_id, business_id) "
        f"联合唯一索引，实际为：{[getattr(i, 'name', i) for i in flat]}"
    )


def test_task_business_id_is_not_nullable() -> None:
    """Task.business_id 必须是 NOT NULL（业务方必传）"""
    column = Task.__table__.columns["business_id"]
    assert column.nullable is False, (
        "Task.business_id 必须 NOT NULL，否则 unique 约束无法保证幂等性（NULL 不参与 unique）"
    )


def test_task_user_id_is_not_nullable() -> None:
    """Task.user_id 必须是 NOT NULL（联合唯一索引要求）"""
    column = Task.__table__.columns["user_id"]
    assert column.nullable is False, (
        "Task.user_id 必须 NOT NULL，否则 (user_id, business_id) 联合 unique 形同虚设"
    )


def test_task_business_id_length() -> None:
    """Task.business_id 长度上限合理（100 字符）"""
    column = Task.__table__.columns["business_id"]
    assert column.type.length == 100, "business_id 长度应为 100 字符"


# ==================== _extract_business_id 测试 ====================


def test_extract_business_id_prefers_business_id_field() -> None:
    """_extract_business_id 优先提取 business_id 字段（Task 推荐）"""
    values = {
        "id": str(uuid.uuid4()),
        "task_id": "task-1",
        "business_id": "analyze_jd:job-123",
    }
    assert _extract_business_id(values) == "analyze_jd:job-123"


def test_extract_business_id_falls_back_to_task_id() -> None:
    """无 business_id 时回退到 task_id"""
    values = {"id": "uuid-x", "task_id": "task-1"}
    assert _extract_business_id(values) == "task-1"


def test_extract_business_id_falls_back_to_id() -> None:
    """无 business_id/task_id 时回退到 id"""
    values = {"id": "uuid-x"}
    assert _extract_business_id(values) == "uuid-x"


def test_extract_business_id_unknown_when_all_empty() -> None:
    """所有 ID 字段都为空时返回 'unknown'"""
    assert _extract_business_id({}) == "unknown"
    assert _extract_business_id({"id": None, "task_id": "", "business_id": ""}) == "unknown"


# ==================== insert_idempotent 集成测试 ====================


def _make_session_mock_with_integrity_error() -> MagicMock:
    """构造一个 session mock，flush() 时抛 IntegrityError（模拟 unique 冲突）"""
    session = MagicMock()
    # flush 是 async，需要 AsyncMock
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            statement="INSERT INTO tasks ...",
            params={},
            orig=Exception("duplicate key value violates unique constraint"),
        )
    )
    session.rollback = AsyncMock()
    return session


async def test_insert_idempotent_task_first_call_succeeds() -> None:
    """Task 首次 INSERT：成功返回实例，不抛异常"""
    session = MagicMock()
    session.flush = AsyncMock()  # 第一次 flush 成功
    session.rollback = AsyncMock()

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    business_id = "analyze_jd:job-123"

    task = await insert_idempotent(
        session,
        Task,
        user_id=user_id,
        session_id=session_id,
        business_id=business_id,
        task_type="analyze_jd",
        status="PENDING",
    )

    assert task.business_id == business_id
    assert task.task_type == "analyze_jd"
    session.add.assert_called_once()
    session.flush.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_insert_idempotent_task_duplicate_raises_duplicate_error() -> None:
    """Task 重复 INSERT（同 business_id）：IntegrityError → DuplicateMessageError

    模拟 MQ 重投场景：业务方用相同 business_id 再次创建 Task，
    数据库 unique 约束拒绝，insert_idempotent 转换为 DuplicateMessageError，
    消费者基类识别后静默 ACK。
    """
    session = _make_session_mock_with_integrity_error()

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    business_id = "analyze_jd:job-123"

    with pytest.raises(DuplicateMessageError) as exc_info:
        await insert_idempotent(
            session,
            Task,
            user_id=user_id,
            session_id=session_id,
            business_id=business_id,
            task_type="analyze_jd",
            status="PENDING",
        )

    # DuplicateMessageError 携带正确的 business_id（用于日志排查）
    assert exc_info.value.message_id == business_id
    # 保留原异常 traceback
    assert exc_info.value.original_error is not None
    # 触发 rollback（保证 session 后续可用）
    session.rollback.assert_awaited_once()


async def test_insert_idempotent_task_different_users_same_business_id_ok() -> None:
    """不同 user 相同 business_id：联合唯一约束下应都能成功

    业务场景：用户 A 和用户 B 都对 "default-task" 这种简单 ID 创建任务，
    由于 unique 约束在 (user_id, business_id) 上，不会冲突。
    """
    session = MagicMock()
    session.flush = AsyncMock()  # 假设 DB 不会冲突
    session.rollback = AsyncMock()

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    session_id = uuid.uuid4()
    business_id = "default-task"

    # 用户 A 插入
    task_a = await insert_idempotent(
        session, Task,
        user_id=user_a, session_id=session_id,
        business_id=business_id, task_type="x", status="PENDING",
    )
    # 用户 B 插入（业务方提供相同 business_id，但 user_id 不同）
    task_b = await insert_idempotent(
        session, Task,
        user_id=user_b, session_id=session_id,
        business_id=business_id, task_type="x", status="PENDING",
    )

    assert task_a.user_id == user_a
    assert task_b.user_id == user_b
    assert task_a.business_id == task_b.business_id == business_id


async def test_insert_idempotent_non_integrity_error_not_caught() -> None:
    """非 IntegrityError（如 DB 断连）不应被转为 DuplicateMessageError

    insert_idempotent 只捕获 IntegrityError，其他错误向上传播由调用方处理
    （如重试、监控告警）。
    """
    session = MagicMock()
    session.flush = AsyncMock(side_effect=ConnectionError("DB unavailable"))
    session.rollback = AsyncMock()

    with pytest.raises(ConnectionError):
        await insert_idempotent(
            session, Task,
            user_id=uuid.uuid4(), session_id=uuid.uuid4(),
            business_id="analyze_jd:job-x", task_type="analyze_jd", status="PENDING",
        )

    # 非 IntegrityError 不会触发 rollback（保留原 session 状态供上层处理）
    session.rollback.assert_not_awaited()


# ==================== 端到端场景测试 ====================


async def test_mq_redelivery_simulation_acks_silently() -> None:
    """端到端模拟：MQ 重投同 business_id → 消费者静默 ACK

    完整流程：
    1. 第一次消费：INSERT Task 成功 → ACK
    2. MQ 重投同 message
    3. 第二次消费：INSERT Task 触发 unique 冲突 → DuplicateMessageError → 静默 ACK
    4. 不重试（重试也是重复）、不进死信
    """
    from app.infra.message_queue.consumer import MessageConsumer

    handle_calls: list[dict[str, Any]] = []

    class _TrackConsumer(MessageConsumer):
        async def handle_message(self, body: dict[str, Any]) -> None:
            handle_calls.append(body)
            # 模拟业务 INSERT 逻辑
            # 第一次：成功；第二次：模拟 unique 冲突
            if len(handle_calls) == 1:
                return
            raise DuplicateMessageError(message_id=body.get("business_id", "unknown"))

    consumer = _TrackConsumer(
        queue_name="tasks.analyze_jd", max_retries=3, retry_base_delay_ms=10,
    )
    msg1 = MagicMock()
    msg1.body = b'{"business_id": "analyze_jd:job-1"}'
    msg1.headers = {}
    msg1.message_id = "analyze_jd:job-1"
    msg1.ack = AsyncMock()
    msg1.nack = AsyncMock()

    msg2 = MagicMock()
    msg2.body = b'{"business_id": "analyze_jd:job-1"}'  # 同 business_id
    msg2.headers = {}
    msg2.message_id = "analyze_jd:job-1"
    msg2.ack = AsyncMock()
    msg2.nack = AsyncMock()

    # 第一次
    await consumer._on_message(msg1)
    msg1.ack.assert_awaited_once()
    msg1.nack.assert_not_awaited()

    # 第二次（重投）
    await consumer._on_message(msg2)
    msg2.ack.assert_awaited_once()  # 静默 ACK
    msg2.nack.assert_not_awaited()  # 不 NACK（不重试）

    # 业务逻辑被调用两次（handle_message 都会执行）
    # 但只有第一次真正 INSERT，第二次走 DuplicateMessageError 快速路径
    assert len(handle_calls) == 2
