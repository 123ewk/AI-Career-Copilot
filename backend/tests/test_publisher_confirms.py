"""消息发布者 Publisher Confirms 可靠性测试

覆盖（Q9 答案对应）：
- message_id 注入：业务侧传入 / 自动生成 UUID 兜底
- AMQP 协议级 message_id 字段 + headers["x-business-id"] 一致性
- Publisher Confirms 等待：成功路径（await 不抛即视为 Ack）
- Nack 路径：DeliveryError 走重试，重试耗尽后向上抛
- Confirm 超时：asyncio.TimeoutError 走重试，重试耗尽后向上抛
- publish_batch：全成功 / 部分失败 / 全失败
- BatchPublishResult 结构化字段（success / failed / has_failure / total / failure_rate）
- _extract_business_id 从 payload 多种 key 提取 + UUID 兜底
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aio_pika.exceptions import DeliveryError

from app.infra.message_queue.publisher import (
    BACKOFF_BASE_SECONDS,
    DEFAULT_CONFIRM_TIMEOUT_S,
    DEFAULT_PUBLISH_RETRIES,
    BatchPublishResult,
    MessagePublisher,
)

# ==================== Fixtures & Helpers ====================


@pytest.fixture(autouse=True)
def _patch_publisher_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 publisher 模块内的 sleep 替换为 0 秒（跳过指数退避）

    注意：monkeypatch.setattr(pub_mod.asyncio, "sleep", ...) 修改的是全局
    asyncio 模块本身（`import asyncio` 是引用同一对象），因此 _fast_sleep
    内部不能再用 asyncio.sleep —— 必须用保存的原始 sleep 引用。
    """
    import app.infra.message_queue.publisher as pub_mod
    # 在 patch 之前保存真实的 sleep，否则会递归到 _fast_sleep 自身
    real_sleep = asyncio.sleep

    async def _fast_sleep(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(pub_mod.asyncio, "sleep", _fast_sleep)


def _make_publisher(
    exchange: AsyncMock | None = None,
    *,
    confirm_timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S,
) -> tuple[MessagePublisher, MagicMock, AsyncMock]:
    """构造带 mock 的 MessagePublisher

    返回：(publisher, channel, exchange)
    """
    channel = MagicMock()
    channel.get_exchange = AsyncMock(return_value=exchange)
    publisher = MessagePublisher(channel, confirm_timeout_s=confirm_timeout_s)
    return publisher, channel, exchange  # type: ignore[return-value]


def _ok_exchange() -> AsyncMock:
    """构造一个始终成功的 exchange mock（Confirm Ack）"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"
    ex.publish = AsyncMock(return_value=None)
    return ex


def _nack_error(reason: str = "rejected") -> DeliveryError:
    """构造一个 DeliveryError（模拟 Broker Nack）

    aio-pika 9.6 DeliveryError 签名：
    __init__(self, message: DeliveredMessage|None, frame: Frame, *args)
    frame 参数不能为 None（mypy 严格），用 cast 绕过类型检查
    """
    # mypy 期望 frame 是 pamqp.Frame，运行时 aio-pika 对 None 也兼容
    fake_frame = cast(Any, MagicMock(name=f"frame[{reason}]"))
    return DeliveryError(None, fake_frame, reason)


# ==================== message_id 注入 ====================


async def test_publish_uses_explicit_message_id() -> None:
    """业务侧传入 message_id 时应原样使用"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    returned_id = await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="test.key",
        payload={"data": "x"},
        message_id="task-123",
    )

    assert returned_id == "task-123"
    # message 是 publish() 的位置参数
    sent_message = ex.publish.await_args.args[0]
    assert sent_message.message_id == "task-123"
    # x-business-id 注入到 headers
    assert sent_message.headers["x-business-id"] == "task-123"


async def test_publish_auto_generates_message_id_when_not_provided() -> None:
    """业务侧未传 message_id 时应自动生成 UUID 兜底"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    returned_id = await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="test.key",
        payload={"data": "x"},
    )

    # UUID4 字符串格式：36 字符，4 个连字符
    assert isinstance(returned_id, str)
    assert len(returned_id) == 36
    assert returned_id.count("-") == 4

    sent_message = ex.publish.await_args.args[0]
    assert sent_message.message_id == returned_id
    assert sent_message.headers["x-business-id"] == returned_id


async def test_publish_merges_user_headers_with_business_id() -> None:
    """业务侧 headers 应被保留，x-business-id 应被注入"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="test.key",
        payload={"data": "x"},
        message_id="task-9",
        headers={"trace_id": "abc-123", "user_id": "u-1"},
    )

    sent_message = ex.publish.await_args.args[0]
    assert sent_message.headers["trace_id"] == "abc-123"
    assert sent_message.headers["user_id"] == "u-1"
    assert sent_message.headers["x-business-id"] == "task-9"


async def test_publish_serializes_payload_as_json() -> None:
    """payload 应被 JSON 序列化，ensure_ascii=False 保留中文"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="test.key",
        payload={"name": "张三", "score": 95},
        message_id="t-1",
    )

    sent_message = ex.publish.await_args.args[0]
    decoded = json.loads(sent_message.body.decode("utf-8"))
    assert decoded == {"name": "张三", "score": 95}
    assert sent_message.content_type == "application/json"


async def test_publish_persistent_delivery_mode_by_default() -> None:
    """默认 persistent=True 应映射到 DeliveryMode.PERSISTENT"""
    from aio_pika import DeliveryMode

    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="k",
        payload={},
        message_id="t",
    )

    sent_message = ex.publish.await_args.args[0]
    assert sent_message.delivery_mode == DeliveryMode.PERSISTENT


async def test_publish_expiration_conversion_ms_to_seconds() -> None:
    """expiration_ms 是毫秒，应转换为秒（aio-pika 接口约定）"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="k",
        payload={},
        message_id="t",
        expiration_ms=5000,
    )

    sent_message = ex.publish.await_args.args[0]
    assert sent_message.expiration == 5.0


# ==================== Publisher Confirms 等待 ====================


async def test_publish_awaits_exchange_publish() -> None:
    """正常路径下 await exchange.publish 应被调用一次（Confirm 等待语义）

    aio-pika 9.6 RobustChannel 默认 publisher_confirms=True，
    await exchange.publish() 内部已等待 Broker Basic.Ack
    """
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="k",
        payload={"x": 1},
        message_id="t-1",
    )

    ex.publish.assert_awaited_once()
    call = ex.publish.await_args
    assert call.kwargs["routing_key"] == "k"


# ==================== Nack 路径（DeliveryError）===================


async def test_publish_retries_on_delivery_error() -> None:
    """Broker Nack（DeliveryError）应触发重试，最终成功"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"
    # 第一次 Nack，第二次成功
    ex.publish = AsyncMock(side_effect=[_nack_error("queue full"), None])
    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="k",
        payload={"x": 1},
        message_id="t-1",
        retries=2,  # 首次 + 2 次重试机会
    )

    # 1 次失败 + 1 次成功 = 2 次
    assert ex.publish.await_count == 2


async def test_publish_raises_after_max_retries_on_nack() -> None:
    """Nack 持续失败时，重试耗尽后抛出 DeliveryError"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"
    ex.publish = AsyncMock(side_effect=_nack_error("rejected"))

    publisher, _, _ = _make_publisher(ex)

    with pytest.raises(DeliveryError):
        await publisher.publish(
            exchange_name="copilot.test.exchange",
            routing_key="k",
            payload={},
            message_id="t-1",
            retries=2,
        )

    # retries=2 + 首次 = 3 次
    assert ex.publish.await_count == 3


# ==================== Confirm 超时路径 ====================


async def test_publish_raises_timeout_when_confirm_never_arrives() -> None:
    """Confirm 超时（Broker 假死）应抛 TimeoutError"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"

    async def _hang_forever(*_args: object, **_kwargs: object) -> None:
        # 用 Event.wait() 真实挂起：asyncio.sleep 被 fixture patch 成 0s，
        # 无法模拟 Broker 假死。Event.wait() 不走 sleep，wait_for 取消才返回
        await asyncio.Event().wait()

    ex.publish = AsyncMock(side_effect=_hang_forever)
    # 极短 Confirm 超时
    publisher, _, _ = _make_publisher(ex, confirm_timeout_s=0.05)

    with pytest.raises(asyncio.TimeoutError):
        await publisher.publish(
            exchange_name="copilot.test.exchange",
            routing_key="k",
            payload={},
            message_id="t-1",
            retries=0,  # 不重试，单纯看超时
        )


async def test_publish_retries_on_confirm_timeout() -> None:
    """Confirm 超时应走重试路径"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"

    call_count = {"n": 0}

    async def _hang_or_succeed(*_args: object, **_kwargs: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            await asyncio.Event().wait()  # 第一次真实 hang 触发 Confirm 超时
        # 第二次正常返回（不抛即视为 Ack）

    ex.publish = AsyncMock(side_effect=_hang_or_succeed)
    publisher, _, _ = _make_publisher(ex, confirm_timeout_s=0.05)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="k",
        payload={},
        message_id="t-1",
        retries=1,
    )

    # 超时 1 次 + 重试成功 1 次
    assert call_count["n"] == 2


# ==================== 批量发布 ====================


async def test_publish_batch_all_success() -> None:
    """批量发布全部成功时，返回的 failed 为空"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    result = await publisher.publish_batch(
        exchange_name="copilot.test.exchange",
        messages=[
            ("rk1", {"task_id": "t-1", "data": "a"}),
            ("rk2", {"task_id": "t-2", "data": "b"}),
            ("rk3", {"task_id": "t-3", "data": "c"}),
        ],
    )

    assert result.has_failure is False
    assert result.total == 3
    assert result.failure_rate == 0.0
    assert result.success == ["t-1", "t-2", "t-3"]
    assert result.failed == []
    assert ex.publish.await_count == 3


async def test_publish_batch_partial_failure_returns_structured_result() -> None:
    """批量部分失败时返回 BatchPublishResult，调用方可决定补偿"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"

    async def _maybe_fail(*args: object, **kwargs: object) -> None:
        # 识别 message_id 决定哪条失败
        sent = args[0]
        if getattr(sent, "message_id", None) == "t-2":
            raise _nack_error("nack")
        return None

    ex.publish = AsyncMock(side_effect=_maybe_fail)
    publisher, _, _ = _make_publisher(ex)

    result = await publisher.publish_batch(
        exchange_name="copilot.test.exchange",
        messages=[
            ("rk1", {"task_id": "t-1", "data": "a"}),
            ("rk2", {"task_id": "t-2", "data": "b"}),
            ("rk3", {"task_id": "t-3", "data": "c"}),
        ],
        retries=0,  # 不重试，简化测试
    )

    # 部分失败：t-2 失败，t-1/t-3 成功
    assert result.has_failure is True
    assert result.total == 3
    assert result.success == ["t-1", "t-3"]
    assert len(result.failed) == 1
    failed_id, failed_err = result.failed[0]
    assert failed_id == "t-2"
    assert isinstance(failed_err, DeliveryError)
    # 失败率 1/3
    assert abs(result.failure_rate - 1 / 3) < 1e-9


async def test_publish_batch_all_failure_does_not_raise() -> None:
    """批量全部失败时 publish_batch 也不抛异常（调用方拿结果判断）"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"
    ex.publish = AsyncMock(side_effect=_nack_error("down"))

    publisher, _, _ = _make_publisher(ex)

    result = await publisher.publish_batch(
        exchange_name="copilot.test.exchange",
        messages=[
            ("rk1", {"id": "n-1"}),
            ("rk2", {"id": "n-2"}),
        ],
        retries=0,
    )

    assert result.has_failure is True
    assert result.success == []
    assert len(result.failed) == 2
    assert result.failure_rate == 1.0


async def test_publish_batch_empty_messages_returns_empty_result() -> None:
    """空批量发布应返回空结果，不调用 exchange"""
    ex = _ok_exchange()
    publisher, _, _ = _make_publisher(ex)

    result = await publisher.publish_batch(
        exchange_name="copilot.test.exchange",
        messages=[],
    )

    assert result.total == 0
    assert result.has_failure is False
    assert result.failure_rate == 0.0
    ex.publish.assert_not_awaited()


# ==================== _extract_business_id ====================


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"id": "n-1"}, "n-1"),
        ({"task_id": "t-1"}, "t-1"),
        ({"notification_id": "noti-1"}, "noti-1"),
        ({"message_id": "m-1"}, "m-1"),
        # 优先级：id > task_id > notification_id > message_id
        ({"id": "first", "task_id": "second"}, "first"),
        ({"task_id": "second", "notification_id": "third"}, "second"),
        # 空值跳过
        ({"id": "", "task_id": "fallback"}, "fallback"),
        ({"id": None, "task_id": "ok"}, "ok"),
        ({"id": 0, "task_id": "ok"}, "ok"),  # 0 视为空，跳过
    ],
)
def test_extract_business_id_priority(payload: dict, expected: str) -> None:
    """按 _BATCH_ID_KEYS 顺序提取业务 ID，空值跳过"""
    assert MessagePublisher._extract_business_id(payload) == expected


def test_extract_business_id_falls_back_to_uuid() -> None:
    """payload 无业务 ID key 时应生成 UUID 兜底"""
    result = MessagePublisher._extract_business_id({"data": "x"})
    assert isinstance(result, str)
    assert len(result) == 36
    assert result.count("-") == 4


# ==================== BatchPublishResult dataclass ====================


def test_batch_publish_result_default_values() -> None:
    """默认值应为空列表"""
    r = BatchPublishResult()
    assert r.success == []
    assert r.failed == []
    assert r.has_failure is False
    assert r.total == 0
    assert r.failure_rate == 0.0


def test_batch_publish_result_is_frozen() -> None:
    """dataclass(frozen=True) 不允许修改字段"""
    r = BatchPublishResult()
    with pytest.raises(FrozenInstanceError):
        r.success = []  # type: ignore[misc]


def test_batch_publish_result_failure_rate() -> None:
    """failure_rate 计算正确"""
    r = BatchPublishResult(
        success=["a", "b"],
        failed=[("c", ValueError("x"))],
    )
    assert r.total == 3
    assert r.has_failure is True
    assert abs(r.failure_rate - 1 / 3) < 1e-9


# ==================== Exchange 缓存 ====================


async def test_exchange_is_cached_after_first_publish() -> None:
    """多次发布到同一 Exchange 时，get_exchange 只调用一次"""
    channel = MagicMock()
    ex = _ok_exchange()
    channel.get_exchange = AsyncMock(return_value=ex)
    publisher = MessagePublisher(channel)

    for i in range(3):
        await publisher.publish(
            exchange_name="copilot.test.exchange",
            routing_key=f"rk-{i}",
            payload={"i": i},
            message_id=f"t-{i}",
        )

    # 缓存生效：get_exchange 只调一次
    channel.get_exchange.assert_awaited_once()
    # 但 exchange.publish 调用 3 次
    assert ex.publish.await_count == 3


# ==================== BACKOFF_BASE_SECONDS sanity ====================


def test_backoff_base_is_one_second() -> None:
    """退避基础值是 1s，验证 1s → 2s → 4s 序列"""
    assert BACKOFF_BASE_SECONDS == 1.0


def test_default_publish_retries_is_three() -> None:
    """默认重试 3 次（与 Publisher Confirms 重试策略匹配）"""
    assert DEFAULT_PUBLISH_RETRIES == 3


# ==================== 网络异常（非 DeliveryError/TimeoutError）===================


async def test_publish_retries_on_generic_exception() -> None:
    """网络断开等非 DeliveryError/TimeoutError 也走重试"""
    ex = AsyncMock()
    ex.name = "copilot.test.exchange"
    ex.publish = AsyncMock(side_effect=[ConnectionError("network down"), None])

    publisher, _, _ = _make_publisher(ex)

    await publisher.publish(
        exchange_name="copilot.test.exchange",
        routing_key="k",
        payload={},
        message_id="t-1",
        retries=1,
    )

    assert ex.publish.await_count == 2


# ==================== confirm_timeout_s 默认值 ====================


def test_default_confirm_timeout_is_ten_seconds() -> None:
    """默认 Confirm 超时 10s"""
    assert DEFAULT_CONFIRM_TIMEOUT_S == 10.0
