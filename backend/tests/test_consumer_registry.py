"""消费者注册中心测试

覆盖：
- @register 装饰器正确填充 CONSUMER_REGISTRY
- 装饰器参数校验（queue_name、prefetch_count、max_retries、retry_base_delay_ms）
- 装饰器拒绝非 async 函数
- ConsumerManager.build_consumers 支持 async 函数和 ABC 子类
- ConsumerManager.start_all 用 asyncio.create_task 拉起
- ConsumerManager.stop_all 优雅关闭
- clear_registry 测试辅助
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.infra.message_queue.consumer import (
    DEFAULT_MAX_RETRIES,
    FunctionConsumer,
    MessageConsumer,
)
from app.infra.message_queue.registry import (
    CONSUMER_REGISTRY,
    ConsumerManager,
    ConsumerSpec,
    clear_registry,
    register,
)

# ==================== Fixture ====================

@pytest.fixture(autouse=True)
def _reset_registry():
    """每个测试前后清空注册表，避免测试间污染"""
    clear_registry()
    yield
    clear_registry()


# ==================== 装饰器基本行为 ====================


def test_register_collects_to_global_registry() -> None:
    """@register 应填充 CONSUMER_REGISTRY"""

    @register("test.queue.a")
    async def handler_a(body: dict) -> None:
        pass

    @register("test.queue.b")
    async def handler_b(body: dict) -> None:
        pass

    assert len(CONSUMER_REGISTRY) == 2

    spec_a = CONSUMER_REGISTRY[0]
    assert isinstance(spec_a, ConsumerSpec)
    assert spec_a.queue_name == "test.queue.a"
    assert spec_a.handler is handler_a
    # 默认值
    assert spec_a.prefetch_count == 10
    assert spec_a.max_retries == DEFAULT_MAX_RETRIES
    assert spec_a.retry_base_delay_ms == 5_000
    assert spec_a.retry_routing_key is None  # None 时使用 queue_name

    spec_b = CONSUMER_REGISTRY[1]
    assert spec_b.queue_name == "test.queue.b"
    assert spec_b.handler is handler_b


def test_register_returns_original_function() -> None:
    """装饰器应返回原函数（业务代码可正常调用）"""

    @register("test.queue")
    async def handler(body: dict) -> None:
        return None

    # 函数应可正常调用
    assert asyncio.iscoroutinefunction(handler)

    async def use_it() -> dict:
        return {"called": True}

    # 注册后的 handler 仍是原函数对象
    assert handler.__name__ == "handler"


def test_register_with_custom_params() -> None:
    """@register 自定义参数应正确保存"""

    @register(
        "test.queue",
        prefetch_count=20,
        max_retries=5,
        retry_base_delay_ms=10_000,
        retry_routing_key="custom.routing.key",
    )
    async def handler(body: dict) -> None:
        pass

    spec = CONSUMER_REGISTRY[0]
    assert spec.prefetch_count == 20
    assert spec.max_retries == 5
    assert spec.retry_base_delay_ms == 10_000
    assert spec.retry_routing_key == "custom.routing.key"


def test_register_rejects_sync_function() -> None:
    """@register 必须拒绝非 async 函数"""
    with pytest.raises(TypeError, match="必须是 async 函数"):

        @register("test.queue")  # type: ignore[arg-type]
        def sync_handler(body: dict) -> None:
            pass


# ==================== 装饰器参数校验 ====================


def test_register_rejects_empty_queue_name() -> None:
    """空字符串 queue_name 应抛 ValueError"""
    with pytest.raises(ValueError, match="queue_name 必须是非空字符串"):
        register("")


def test_register_rejects_invalid_prefetch_count() -> None:
    """prefetch_count < 1 应抛 ValueError"""
    with pytest.raises(ValueError, match="prefetch_count 必须 >= 1"):
        register("test.queue", prefetch_count=0)


def test_register_rejects_negative_max_retries() -> None:
    """max_retries < 0 应抛 ValueError（0 是允许的，表示不重试）"""
    with pytest.raises(ValueError, match="max_retries 必须 >= 0"):
        register("test.queue", max_retries=-1)


def test_register_rejects_small_retry_delay() -> None:
    """retry_base_delay_ms < 100 应抛 ValueError（防止过频重试）"""
    with pytest.raises(ValueError, match="retry_base_delay_ms 必须 >= 100ms"):
        register("test.queue", retry_base_delay_ms=50)


# ==================== ConsumerManager ====================


def test_build_consumers_from_async_functions() -> None:
    """async 函数应被包装为 FunctionConsumer"""

    @register("test.queue.a", prefetch_count=5)
    async def handler_a(body: dict) -> None:
        pass

    @register("test.queue.b", max_retries=10)
    async def handler_b(body: dict) -> None:
        pass

    mgr = ConsumerManager()
    consumers = mgr._build_consumers()

    assert len(consumers) == 2
    # 全部是 FunctionConsumer
    assert all(isinstance(c, FunctionConsumer) for c in consumers)
    assert all(isinstance(c, MessageConsumer) for c in consumers)

    # 配置正确传递
    assert consumers[0]._queue_name == "test.queue.a"
    assert consumers[0]._prefetch_count == 5
    assert consumers[1]._queue_name == "test.queue.b"
    assert consumers[1]._max_retries == 10


def test_build_consumers_supports_abc_subclass() -> None:
    """ABC 子类实例应直接使用，不被包装"""

    class MyCustomConsumer(MessageConsumer):
        def __init__(self) -> None:
            super().__init__(queue_name="custom.queue")
            self.call_count = 0

        async def handle_message(self, body: dict) -> None:
            self.call_count += 1

    custom = MyCustomConsumer()
    # 把 ABC 实例当作 handler 注入
    CONSUMER_REGISTRY.append(
        ConsumerSpec(
            queue_name="ignored",  # 实际用实例的 queue_name
            handler=custom,
        )
    )

    mgr = ConsumerManager()
    consumers = mgr._build_consumers()

    assert len(consumers) == 1
    # 直接使用原实例（不是 FunctionConsumer）
    assert consumers[0] is custom
    assert not isinstance(consumers[0], FunctionConsumer)


def test_build_consumers_empty_registry() -> None:
    """空注册表应返回空列表"""
    mgr = ConsumerManager()
    assert mgr._build_consumers() == []


# ==================== start_all / stop_all ====================


async def test_start_all_with_empty_registry() -> None:
    """空注册表调用 start_all 不应报错"""
    mgr = ConsumerManager()
    fake_channel = MagicMock()

    await mgr.start_all(fake_channel)

    assert mgr._started is True
    # 没有消费者被启动
    assert mgr.consumers == []


async def test_start_all_invokes_create_task() -> None:
    """start_all 应使用 asyncio.create_task 拉起消费者"""
    captured_tasks: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def tracking_create_task(coro, *, name=None):
        task = original_create_task(coro, name=name)
        captured_tasks.append(task)
        return task

    @register("test.queue")
    async def handler(body: dict) -> None:
        pass

    mgr = ConsumerManager()

    # 模拟 channel：让 channel.get_queue 返回一个 mock queue，
    # queue.consume 返回 consumer_tag
    mock_queue = MagicMock()
    mock_queue.consume = AsyncMock(return_value="consumer-tag-1")
    mock_channel = MagicMock()
    mock_channel.set_qos = AsyncMock()
    mock_channel.get_queue = AsyncMock(return_value=mock_queue)
    mock_channel.get_exchange = AsyncMock(return_value=MagicMock())

    # patch asyncio.create_task
    import unittest.mock
    with unittest.mock.patch(
        "asyncio.create_task", side_effect=tracking_create_task
    ):
        await mgr.start_all(mock_channel)

    # 验证：每个 consumer 都被 create_task 拉起
    assert len(captured_tasks) == 1
    # task 名字应包含 queue 名
    assert captured_tasks[0].get_name() == "consumer-start-test.queue"

    # 验证：consumer.start 被调用（set_qos + get_queue + get_exchange）
    mock_channel.set_qos.assert_awaited_once_with(prefetch_count=10)
    mock_channel.get_queue.assert_awaited_once_with("test.queue")
    mock_channel.get_exchange.assert_awaited_once()

    # 验证：started 标记
    assert mgr._started is True
    # 等待 task 完成（start 内部是 await channel.get_queue 等）
    await asyncio.gather(*captured_tasks, return_exceptions=True)


async def test_start_all_called_twice_warns() -> None:
    """重复调用 start_all 应记录警告并跳过"""
    @register("test.queue")
    async def handler(body: dict) -> None:
        pass

    mgr = ConsumerManager()
    mock_queue = MagicMock()
    mock_queue.consume = AsyncMock(return_value="tag")
    mock_channel = MagicMock()
    mock_channel.set_qos = AsyncMock()
    mock_channel.get_queue = AsyncMock(return_value=mock_queue)
    mock_channel.get_exchange = AsyncMock(return_value=MagicMock())

    await mgr.start_all(mock_channel)
    # 第二次应被跳过
    await mgr.start_all(mock_channel)

    # set_qos 只调用一次（说明第二次没真的启动）
    assert mock_channel.set_qos.await_count == 1


async def test_stop_all_when_not_started() -> None:
    """未启动时调用 stop_all 应为 no-op"""
    mgr = ConsumerManager()
    # 不应抛异常
    await mgr.stop_all()


async def test_stop_all_closes_all_consumers() -> None:
    """stop_all 应并发停止所有消费者"""
    @register("test.queue.a")
    async def handler_a(body: dict) -> None:
        pass

    @register("test.queue.b")
    async def handler_b(body: dict) -> None:
        pass

    mgr = ConsumerManager()
    mock_queue = MagicMock()
    mock_queue.consume = AsyncMock(return_value="tag")
    mock_queue.cancel = AsyncMock()
    mock_channel = MagicMock()
    mock_channel.set_qos = AsyncMock()
    mock_channel.get_queue = AsyncMock(return_value=mock_queue)
    mock_channel.get_exchange = AsyncMock(return_value=MagicMock())

    await mgr.start_all(mock_channel)
    # 等待 start 任务完成
    await asyncio.gather(*mgr._start_tasks, return_exceptions=True)

    await mgr.stop_all()

    # queue.cancel 每个 consumer 都被调用（aio-pika 推荐方式）
    assert mock_queue.cancel.await_count == 2
    # started 重置
    assert mgr._started is False


async def test_start_all_continues_on_individual_failure() -> None:
    """某个消费者启动失败不应影响其他消费者"""
    @register("test.queue.a")
    async def handler_a(body: dict) -> None:
        pass

    @register("test.queue.b")
    async def handler_b(body: dict) -> None:
        pass

    mgr = ConsumerManager()

    # 构造 channel：第一次 get_queue 抛异常，第二次成功
    good_queue = MagicMock()
    good_queue.consume = AsyncMock(return_value="tag")
    call_count = {"n": 0}

    async def get_queue(name):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("模拟启动失败")
        return good_queue

    mock_channel = MagicMock()
    mock_channel.set_qos = AsyncMock()
    mock_channel.get_queue = AsyncMock(side_effect=get_queue)
    mock_channel.get_exchange = AsyncMock(return_value=MagicMock())

    # 不应抛出异常（gather return_exceptions=True）
    await mgr.start_all(mock_channel)
    await asyncio.gather(*mgr._start_tasks, return_exceptions=True)

    # 两个 consumer 都被构建
    assert len(mgr.consumers) == 2
    # 第二个 consumer 启动成功（set_qos 被调用两次 = 两个 start 都跑到 set_qos）
    assert mock_channel.set_qos.await_count == 2
