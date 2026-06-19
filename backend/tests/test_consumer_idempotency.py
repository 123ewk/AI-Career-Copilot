"""消费者幂等性测试

覆盖：
- MessageConsumer._on_message 捕获 DuplicateMessageError 静默 ACK（不重试）
- 业务层 insert_idempotent 辅助函数：成功 / 重复（IntegrityError → DuplicateMessageError）
- 重复消息不计入 retry_count、不进死信、不发重试队列
- 其他异常（非 DuplicateMessageError）继续走原有重试流程

场景对照 Q9 答案（at-least-once → exactly-effect）：
- Publisher Confirms 成功但 ACK 失败 → Broker 重投
- 业务表 unique 约束拒绝 → DuplicateMessageError → 静默 ACK
- 同一 message_id 多次消费仅第一次真正处理
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import DuplicateMessageError
from app.domain.common.idempotent import insert_idempotent
from app.infra.message_queue.consumer import MessageConsumer

# ==================== Fixtures ====================


class _StubMessage:
    """模拟 aio-pika AbstractIncomingMessage 的最小子集

    consumer.py 的 _on_message 只用到 body / headers / ack / nack / message_id
    """

    def __init__(self, body: dict[str, Any], headers: dict[str, Any] | None = None) -> None:
        self.body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.headers = headers or {}
        # aio-pika message.message_id 属性
        self.message_id = body.get("message_id") or body.get("task_id") or body.get("id")
        self.ack = AsyncMock()
        self.nack = AsyncMock()


class _StubConsumer(MessageConsumer):
    """最小可运行的消费者（仅用于测试 handle_message 被调用的次数）"""

    handle_call_count: ClassVar[int] = 0
    last_body: ClassVar[dict[str, Any] | None] = None

    async def handle_message(self, body: dict[str, Any]) -> None:
        # 默认实现：调用方可在测试中 patch
        _StubConsumer.handle_call_count += 1
        _StubConsumer.last_body = body


def _make_stub_consumer() -> MessageConsumer:
    """构造一个未 start() 的消费者实例（仅用于直接调用 _on_message）"""
    return _StubConsumer(queue_name="test.queue", max_retries=3, retry_base_delay_ms=10)


def _make_async_session_mock() -> MagicMock:
    """构造模拟 SQLAlchemy AsyncSession 的 MagicMock

    为什么用 MagicMock 而非 AsyncMock：
    - AsyncMock 的所有属性都会自动变成 AsyncMock
    - 但 session.add(instance) 是同步调用，不应返回 coroutine
    - 用 MagicMock 后，add 是普通 MagicMock，flush/rollback 单独设为 AsyncMock
    - 避免 "coroutine was never awaited" 警告
    """
    session = MagicMock()
    return session


# ==================== DuplicateMessageError 测试 ====================


def test_duplicate_message_error_attributes() -> None:
    """DuplicateMessageError 携带 message_id 与原异常"""
    original = IntegrityError("dup key", params={}, orig=Exception("unique violation"))
    e = DuplicateMessageError(message_id="task-123", original_error=original)

    assert e.message_id == "task-123"
    assert e.original_error is original
    assert "task-123" in str(e)


def test_duplicate_message_error_not_app_exception() -> None:
    """DuplicateMessageError 不是 AppException（不是 HTTP 错误）"""
    from app.core.exceptions import AppException

    e = DuplicateMessageError(message_id="x")
    assert not isinstance(e, AppException)


# ==================== MessageConsumer._on_message 幂等行为 ====================


async def test_consumer_acks_silently_on_duplicate_message() -> None:
    """handle_message 抛 DuplicateMessageError 时，消费者应 ACK 不重试"""
    consumer = _make_stub_consumer()
    msg = _StubMessage({"task_id": "task-1", "data": "x"})

    async def _raise_dup(_body: dict[str, Any]) -> None:
        raise DuplicateMessageError(message_id="task-1")

    with patch.object(_StubConsumer, "handle_message", side_effect=_raise_dup):
        await consumer._on_message(msg)

    # 静默 ACK
    msg.ack.assert_awaited_once()
    # 没有 NACK
    msg.nack.assert_not_awaited()


async def test_consumer_normal_flow_still_acks() -> None:
    """正常流程（handle_message 不抛）应 ACK"""
    consumer = _make_stub_consumer()
    msg = _StubMessage({"task_id": "task-2"})

    await consumer._on_message(msg)

    msg.ack.assert_awaited_once()
    msg.nack.assert_not_awaited()


async def test_consumer_other_exception_goes_to_retry() -> None:
    """非 DuplicateMessageError 的异常走原有重试流程（不静默 ACK）"""
    consumer = _make_stub_consumer()
    msg = _StubMessage({"task_id": "task-3"})

    async def _raise_other(_body: dict[str, Any]) -> None:
        raise ValueError("some business error")

    # _handle_retry 会被调用，发到重试 exchange；mock 掉避免真实 publish
    with patch.object(consumer, "_handle_retry", new=AsyncMock()) as mock_retry:
        with patch.object(_StubConsumer, "handle_message", side_effect=_raise_other):
            await consumer._on_message(msg)

        # 走重试流程
        mock_retry.assert_awaited_once()
    # 没有 ACK
    msg.ack.assert_not_awaited()
    # 没有 NACK（在重试流程内部可能调，但不在这个测试路径）
    msg.nack.assert_not_awaited()


async def test_consumer_json_decode_error_nacks_to_dead_letter() -> None:
    """JSON 解码失败直接 NACK 进死信"""
    consumer = _make_stub_consumer()

    # 构造一个非 JSON body
    msg = MagicMock()
    msg.body = b"not-json{"
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()

    await consumer._on_message(msg)

    # NACK 不重入队 → 进死信
    msg.nack.assert_awaited_once()
    assert msg.nack.await_args.kwargs["requeue"] is False
    msg.ack.assert_not_awaited()


async def test_consumer_in_flight_decremented_on_duplicate() -> None:
    """重复消息处理后 _in_flight 应归零（优雅关闭可正常退出）"""
    consumer = _make_stub_consumer()
    msg = _StubMessage({"task_id": "task-4"})

    async def _raise_dup(_body: dict[str, Any]) -> None:
        raise DuplicateMessageError(message_id="task-4")

    assert consumer._in_flight == 0
    with patch.object(_StubConsumer, "handle_message", side_effect=_raise_dup):
        await consumer._on_message(msg)

    assert consumer._in_flight == 0  # 已归零


# ==================== insert_idempotent 辅助函数 ====================


class _FakeModel:
    """模拟 ORM Model：构造时存 values"""

    instances: ClassVar[list[_FakeModel]] = []

    def __init__(self, **values: Any) -> None:
        self.values = values
        _FakeModel.instances.append(self)


async def test_insert_idempotent_success_returns_instance() -> None:
    """INSERT 成功时返回 instance，不抛异常"""
    session = _make_async_session_mock()
    session.flush = AsyncMock()  # 正常返回

    instance = await insert_idempotent(session, _FakeModel, task_id="t-1", data="x")

    assert instance is not None
    assert instance.values == {"task_id": "t-1", "data": "x"}
    session.add.assert_called_once_with(instance)
    session.flush.assert_awaited_once()


async def test_insert_idempotent_duplicate_raises_duplicate_message_error() -> None:
    """INSERT IntegrityError 时抛 DuplicateMessageError，附带 message_id"""
    session = _make_async_session_mock()

    # 模拟 flush 抛 IntegrityError（duplicate key）
    integrity_error = IntegrityError(
        "INSERT INTO t ... duplicate key value violates unique constraint",
        params={},
        orig=Exception("duplicate key"),
    )

    async def _raise_integrity() -> None:
        raise integrity_error

    session.flush = AsyncMock(side_effect=_raise_integrity)
    session.rollback = AsyncMock()

    with pytest.raises(DuplicateMessageError) as exc_info:
        await insert_idempotent(session, _FakeModel, task_id="t-dup", data="x")

    # 业务 ID 正确提取
    assert exc_info.value.message_id == "t-dup"
    # 触发回滚
    session.rollback.assert_awaited_once()
    # 原异常保留在 original_error
    assert exc_info.value.original_error is integrity_error
    # 原异常也保留在 __cause__（raise from 语义）
    assert exc_info.value.__cause__ is integrity_error


async def test_insert_idempotent_extracts_id_field() -> None:
    """_extract_business_id 优先用 id 字段"""
    session = _make_async_session_mock()
    integrity_error = IntegrityError("dup", params={}, orig=Exception("x"))

    async def _raise() -> None:
        raise integrity_error

    session.flush = AsyncMock(side_effect=_raise)
    session.rollback = AsyncMock()

    with pytest.raises(DuplicateMessageError) as exc_info:
        await insert_idempotent(session, _FakeModel, id="n-1", data="y")

    assert exc_info.value.message_id == "n-1"


async def test_insert_idempotent_extracts_notification_id_field() -> None:
    """_extract_business_id 也支持 notification_id"""
    session = _make_async_session_mock()
    integrity_error = IntegrityError("dup", params={}, orig=Exception("x"))

    async def _raise() -> None:
        raise integrity_error

    session.flush = AsyncMock(side_effect=_raise)
    session.rollback = AsyncMock()

    with pytest.raises(DuplicateMessageError) as exc_info:
        await insert_idempotent(session, _FakeModel, notification_id="noti-1")

    assert exc_info.value.message_id == "noti-1"


async def test_insert_idempotent_no_business_id_falls_back_to_unknown() -> None:
    """无业务 ID 字段时退化为 "unknown"（不阻断主流程）"""
    session = _make_async_session_mock()
    integrity_error = IntegrityError("dup", params={}, orig=Exception("x"))

    async def _raise() -> None:
        raise integrity_error

    session.flush = AsyncMock(side_effect=_raise)
    session.rollback = AsyncMock()

    with pytest.raises(DuplicateMessageError) as exc_info:
        await insert_idempotent(session, _FakeModel, data="z", score=42)

    # 提取不到业务 ID，标为 unknown
    assert exc_info.value.message_id == "unknown"


async def test_insert_idempotent_non_integrity_error_propagates() -> None:
    """非 IntegrityError（如连接错误）应向上抛（让重试逻辑处理）"""
    session = _make_async_session_mock()
    other_error = ConnectionError("db down")

    async def _raise() -> None:
        raise other_error

    session.flush = AsyncMock(side_effect=_raise)
    session.rollback = AsyncMock()

    # 应向上传播原始异常，不是 DuplicateMessageError
    with pytest.raises(ConnectionError) as exc_info:
        await insert_idempotent(session, _FakeModel, task_id="t-1")

    assert exc_info.value is other_error


# ==================== 集成场景 ====================


async def test_duplicate_message_does_not_trigger_retry_publish() -> None:
    """重复消息不应触发 _handle_retry（不发到 retry exchange）"""
    consumer = _make_stub_consumer()
    msg = _StubMessage({"task_id": "task-dup"})

    async def _raise_dup(_body: dict[str, Any]) -> None:
        raise DuplicateMessageError(message_id="task-dup")

    with patch.object(consumer, "_handle_retry", new=AsyncMock()) as mock_retry:
        with patch.object(_StubConsumer, "handle_message", side_effect=_raise_dup):
            await consumer._on_message(msg)

        # 关键断言：没有走重试流程
        mock_retry.assert_not_awaited()
    # 静默 ACK
    msg.ack.assert_awaited_once()


async def test_duplicate_message_idempotent_across_redeliveries() -> None:
    """同一 message_id 多次重投：3 次都 ACK，不进重试"""
    consumer = _make_stub_consumer()
    msg = _StubMessage({"task_id": "task-redeliver-1"})

    call_count = 0

    async def _simulate_business(_body: dict[str, Any]) -> None:
        nonlocal call_count
        call_count += 1
        # 模拟业务表 unique 约束拒绝（重复消息）
        if call_count == 1:
            raise DuplicateMessageError(message_id="task-redeliver-1")
        # 后续重投都走相同路径

    with patch.object(consumer, "_handle_retry", new=AsyncMock()) as mock_retry:
        with patch.object(_StubConsumer, "handle_message", side_effect=_simulate_business):
            # 模拟 Broker 重投 3 次
            for _ in range(3):
                await consumer._on_message(msg)

        # 关键断言：3 次都 ACK，不进重试
        assert msg.ack.await_count == 3
        assert mock_retry.await_count == 0
