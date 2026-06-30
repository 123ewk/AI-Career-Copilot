"""消费者重试逻辑测试

覆盖：
- _compute_retry_ttl_ms 指数退避计算
- _get_retry_count 从 header 读取
- _handle_retry 成功路径：publish 到 retry exchange + ack
- _handle_retry 失败路径：超过 max_retries → nack(false) → 死信
- _handle_retry 异常路径：publish 失败 → 降级 nack(requeue=True)
- _on_message 端到端：成功 / 失败重试 / 重试耗尽
- 关闭中收到消息：nack(requeue=True)
- shutdown_event 触发后停止接收新消息
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.infra.message_queue.consumer import (
    DEFAULT_MAX_RETRIES,
    MAX_RETRY_DELAY_MS,
    FunctionConsumer,
    MessageConsumer,
)

# ==================== TTL 退避计算 ====================


def test_compute_retry_ttl_exponential() -> None:
    """TTL 应该是 base * 2^(count-1)"""
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=AsyncMock(),
        retry_base_delay_ms=1_000,
    )

    assert consumer._compute_retry_ttl_ms(1) == 1_000  # base
    assert consumer._compute_retry_ttl_ms(2) == 2_000  # base * 2
    assert consumer._compute_retry_ttl_ms(3) == 4_000  # base * 4
    assert consumer._compute_retry_ttl_ms(4) == 8_000  # base * 8


def test_compute_retry_ttl_capped_at_max() -> None:
    """TTL 超过 MAX_RETRY_DELAY_MS 时应被截断"""
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=AsyncMock(),
        retry_base_delay_ms=10_000,
    )

    # 10s * 2^9 = 5120s ≈ 85 分钟，超过 5 分钟上限
    assert consumer._compute_retry_ttl_ms(10) == MAX_RETRY_DELAY_MS
    # 极端情况
    assert consumer._compute_retry_ttl_ms(20) == MAX_RETRY_DELAY_MS


def test_compute_retry_ttl_handles_invalid_count() -> None:
    """retry_count < 1 时退化为 base_delay"""
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=AsyncMock(),
        retry_base_delay_ms=1_000,
    )

    assert consumer._compute_retry_ttl_ms(0) == 1_000
    assert consumer._compute_retry_ttl_ms(-1) == 1_000


# ==================== Header 解析 ====================


def test_get_retry_count_from_message() -> None:
    """应正确从 message.headers 读取 x-retry-count"""
    # 无 headers
    msg1 = MagicMock()
    msg1.headers = None
    assert MessageConsumer._get_retry_count(msg1) == 0

    # 空 headers
    msg2 = MagicMock()
    msg2.headers = {}
    assert MessageConsumer._get_retry_count(msg2) == 0

    # 有 x-retry-count
    msg3 = MagicMock()
    msg3.headers = {"x-retry-count": 2}
    assert MessageConsumer._get_retry_count(msg3) == 2

    # x-retry-count 是字符串时也能解析
    msg4 = MagicMock()
    msg4.headers = {"x-retry-count": "3"}
    assert MessageConsumer._get_retry_count(msg4) == 3


# ==================== 端到端：成功路径 ====================


async def test_on_message_success_acks() -> None:
    """消息处理成功应 ACK"""
    handler = AsyncMock()
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
    )
    consumer._retry_exchange_obj = MagicMock()  # 模拟 start 已初始化

    msg = MagicMock()
    msg.body = json.dumps({"key": "value"}).encode("utf-8")
    msg.headers = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()

    await consumer._on_message(msg)

    # consumer 基类会注入 __mq_meta__ 元数据，供 handler 感知重试状态
    handler.assert_awaited_once_with(
        {
            "key": "value",
            "__mq_meta__": {
                "retry_count": 0,
                "max_retries": 3,
                "queue": "test.queue",
            },
        }
    )
    msg.ack.assert_awaited_once()
    msg.nack.assert_not_awaited()
    assert consumer._in_flight == 0  # 正常递减


# ==================== 端到端：重试路径 ====================


async def test_on_message_failure_publishes_to_retry_exchange() -> None:
    """业务异常时应 publish 到 retry exchange（不是 nack requeue）"""
    handler = AsyncMock(side_effect=ValueError("业务失败"))
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
        max_retries=3,
        retry_base_delay_ms=1_000,
    )

    retry_exchange = MagicMock()
    retry_exchange.publish = AsyncMock()
    consumer._retry_exchange_obj = retry_exchange

    msg = MagicMock()
    msg.body = json.dumps({"task_id": "123"}).encode("utf-8")
    msg.headers = {"x-retry-count": 1}  # 模拟已经是第 1 次重试
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.content_type = "application/json"

    await consumer._on_message(msg)

    # handler 被调用；consumer 基类注入 __mq_meta__ 元数据
    handler.assert_awaited_once_with(
        {
            "task_id": "123",
            "__mq_meta__": {
                "retry_count": 1,
                "max_retries": 3,
                "queue": "test.queue",
            },
        }
    )

    # publish 到 retry exchange（不是 nack requeue）
    retry_exchange.publish.assert_awaited_once()
    publish_call = retry_exchange.publish.await_args
    retry_msg = publish_call.kwargs.get("message") or publish_call.args[0]
    assert publish_call.kwargs["routing_key"] == "test.queue"

    # publish 成功后 ack 原消息
    msg.ack.assert_awaited_once()
    # 关键：不应 nack(requeue=True)
    msg.nack.assert_not_awaited()

    # header 校验：x-retry-count 应递增到 2
    assert retry_msg.headers["x-retry-count"] == 2
    assert retry_msg.headers["x-original-queue"] == "test.queue"
    assert "业务失败" in retry_msg.headers["x-last-error"]


async def test_retry_count_increments_correctly() -> None:
    """重试计数应严格递增，不会因 nack requeue 而丢失"""
    handler = AsyncMock(side_effect=RuntimeError("always fail"))
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
        max_retries=5,
        retry_base_delay_ms=1_000,
    )

    retry_exchange = MagicMock()
    retry_exchange.publish = AsyncMock()
    consumer._retry_exchange_obj = retry_exchange

    # 模拟三次连续失败（每次都从主队列来）
    for expected_count in [1, 2, 3]:
        msg = MagicMock()
        msg.body = json.dumps({"n": expected_count}).encode("utf-8")
        msg.headers = {"x-retry-count": expected_count - 1}  # 每次都是上一次的结果
        msg.ack = AsyncMock()
        msg.nack = AsyncMock()
        msg.content_type = "application/json"

        await consumer._on_message(msg)

        retry_msg = retry_exchange.publish.await_args.args[0]
        # x-retry-count 必须严格递增
        assert retry_msg.headers["x-retry-count"] == expected_count


# ==================== 端到端：超过最大重试 ====================


async def test_on_message_exceeds_max_retries_goes_to_dlq() -> None:
    """超过 max_retries 时应 nack(requeue=False) 让消息进死信"""
    handler = AsyncMock(side_effect=RuntimeError("permanent fail"))
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
        max_retries=2,  # 最多 2 次重试
    )

    retry_exchange = MagicMock()
    retry_exchange.publish = AsyncMock()
    consumer._retry_exchange_obj = retry_exchange

    msg = MagicMock()
    msg.body = json.dumps({"id": 1}).encode("utf-8")
    # 已经是第 2 次失败（header 中 x-retry-count=2），本次失败后会变 3
    msg.headers = {"x-retry-count": 2}
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.content_type = "application/json"

    await consumer._on_message(msg)

    # 关键：应 nack(requeue=False) 进死信
    msg.nack.assert_awaited_once_with(requeue=False)
    # 不应 publish 到 retry exchange
    retry_exchange.publish.assert_not_awaited()
    # 不应 ack
    msg.ack.assert_not_awaited()


# ==================== 端到端：publish 失败降级 ====================


async def test_handle_retry_publish_failure_falls_back_to_nack() -> None:
    """publish 到 retry exchange 失败时应降级为 nack(requeue=True)"""
    handler = AsyncMock(side_effect=ValueError("业务失败"))
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
        max_retries=3,
    )

    retry_exchange = MagicMock()
    retry_exchange.publish = AsyncMock(side_effect=ConnectionError("retry exchange 不可用"))
    consumer._retry_exchange_obj = retry_exchange

    msg = MagicMock()
    msg.body = json.dumps({}).encode("utf-8")
    msg.headers = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.content_type = "application/json"

    await consumer._on_message(msg)

    # publish 失败：降级为 nack(requeue=True)
    msg.nack.assert_awaited_once_with(requeue=True)
    # 不应 ack（避免消息丢失）
    msg.ack.assert_not_awaited()


# ==================== 端到端：JSON 解码失败 ====================


async def test_on_message_invalid_json_goes_to_dlq() -> None:
    """消息体不是合法 JSON 时直接进死信（重试无意义）"""
    handler = AsyncMock()
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
    )
    consumer._retry_exchange_obj = MagicMock()

    msg = MagicMock()
    msg.body = b"not a json {"
    msg.headers = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()

    await consumer._on_message(msg)

    # handler 不应被调用
    handler.assert_not_awaited()
    # nack(requeue=False) 进死信
    msg.nack.assert_awaited_once_with(requeue=False)
    # in_flight 必须归零（finally 块）
    assert consumer._in_flight == 0


# ==================== 关闭中收到消息 ====================


async def test_on_message_nacks_during_shutdown() -> None:
    """shutdown 标志设置后，新消息应 nack(requeue=True) 让其他消费者处理"""
    handler = AsyncMock()
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
    )
    consumer._retry_exchange_obj = MagicMock()

    msg = MagicMock()
    msg.body = json.dumps({}).encode("utf-8")
    msg.headers = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()

    # 模拟关闭中
    consumer._shutdown_event.set()

    await consumer._on_message(msg)

    # handler 不应被调用
    handler.assert_not_awaited()
    # nack(requeue=True) 让消息回到队列
    msg.nack.assert_awaited_once_with(requeue=True)


# ==================== 优雅关闭 ====================


async def test_stop_waits_for_in_flight_messages() -> None:
    """stop() 应等待 in_flight 消息完成"""
    started = asyncio.Event()
    can_finish = asyncio.Event()

    async def slow_handler(body: dict) -> None:
        started.set()
        await can_finish.wait()

    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=slow_handler,
    )
    consumer._consumer_tag = "tag"
    consumer._channel = MagicMock()
    # 模拟 aio-pika queue 对象（start() 时会被赋值给 self._queue_obj）
    consumer._queue_obj = MagicMock()
    consumer._queue_obj.cancel = AsyncMock()

    msg = MagicMock()
    msg.body = json.dumps({}).encode("utf-8")
    msg.headers = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()

    # 启动一个 in_flight 的消息处理
    task = asyncio.create_task(consumer._on_message(msg))
    await started.wait()
    assert consumer._in_flight == 1

    # 启动 stop 任务
    stop_task = asyncio.create_task(consumer.stop())

    # stop 应该在等 in_flight
    await asyncio.sleep(0.1)
    assert not stop_task.done()

    # 让消息处理完成
    can_finish.set()
    await task
    await stop_task

    # queue.cancel 被调用（aio-pika 推荐方式）
    consumer._queue_obj.cancel.assert_awaited_once_with("tag")


# ==================== 边界：max_retries=0 ====================


async def test_max_retries_zero_goes_to_dlq_on_first_failure() -> None:
    """max_retries=0 时第一次失败就进死信"""
    handler = AsyncMock(side_effect=RuntimeError("fail"))
    consumer = FunctionConsumer(
        queue_name="test.queue",
        handler=handler,
        max_retries=0,
    )
    consumer._retry_exchange_obj = MagicMock()

    msg = MagicMock()
    msg.body = json.dumps({}).encode("utf-8")
    msg.headers = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    msg.content_type = "application/json"

    await consumer._on_message(msg)

    # retry_count=0 + 1 = 1 > max_retries=0 → 直接进死信
    msg.nack.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
