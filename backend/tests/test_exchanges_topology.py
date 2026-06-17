"""消息队列拓扑声明测试

覆盖：
- declare_all 声明 retry exchange
- 每个主队列都有对应的重试队列
- 重试队列正确配置 DLX 和 DLX routing key
- 主队列保留原有的 DLX 配置
- 死信队列也被声明
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.infra.message_queue.exchanges import (
    EXCHANGE_DLX,
    EXCHANGE_RETRY,
    QUEUE_DLX_DEAD_LETTER,
    QUEUE_MATCH_COMPUTE,
    QUEUE_NOTIFICATION_EMAIL,
    QUEUE_NOTIFICATION_WEBHOOK,
    QUEUE_NOTIFICATION_WECHAT,
    QUEUE_RESUME_PARSE,
    QUEUE_TASK_COMPLETED,
    QUEUE_TASK_CREATED,
    QUEUE_TASK_FAILED,
    QUEUE_WORKFLOW_COMPLETED,
    QUEUE_WORKFLOW_FAILED,
    QUEUE_WORKFLOW_STARTED,
    RETRY_QUEUE_MAX_TTL_MS,
    RETRY_QUEUE_SUFFIX,
    ROUTING_MATCH_COMPUTE,
    ROUTING_NOTIFICATION_EMAIL,
    ROUTING_NOTIFICATION_WEBHOOK,
    ROUTING_NOTIFICATION_WECHAT,
    ROUTING_RESUME_PARSE,
    ROUTING_TASK_COMPLETED,
    ROUTING_TASK_CREATED,
    ROUTING_TASK_FAILED,
    ROUTING_WORKFLOW_COMPLETED,
    ROUTING_WORKFLOW_FAILED,
    ROUTING_WORKFLOW_STARTED,
    declare_all,
)


@pytest.fixture
def mock_channel() -> MagicMock:
    """构造 mock channel，declare_*/get_* 返回 mock 对象"""
    channel = MagicMock()
    channel.declare_exchange = AsyncMock(return_value=MagicMock())
    # declare_queue 返回的 mock 必须有 async bind（_declare_main_queue_with_retry 内部调用）
    queue_mock_from_declare = MagicMock()
    queue_mock_from_declare.bind = AsyncMock()
    channel.declare_queue = AsyncMock(return_value=queue_mock_from_declare)

    # get_queue 返回的对象也要有 bind
    queue_mock = MagicMock()
    queue_mock.bind = AsyncMock()
    channel.get_queue = AsyncMock(return_value=queue_mock)

    channel.get_exchange = AsyncMock(return_value=MagicMock())

    return channel


# ==================== 总体检查 ====================


async def test_declare_all_returns_without_error(mock_channel: MagicMock) -> None:
    """declare_all 应能正常执行完毕"""
    await declare_all(mock_channel)
    # 至少声明了 retry exchange + 各主队列
    assert mock_channel.declare_exchange.await_count > 0
    assert mock_channel.declare_queue.await_count > 0


# ==================== Retry Exchange ====================


async def test_retry_exchange_is_declared(mock_channel: MagicMock) -> None:
    """retry exchange 必须被声明"""
    await declare_all(mock_channel)

    # 找到所有 declare_exchange 的调用
    exchange_calls = [
        c.args[0] for c in mock_channel.declare_exchange.await_args_list
    ]
    assert EXCHANGE_RETRY in exchange_calls


async def test_retry_exchange_is_direct_and_durable(mock_channel: MagicMock) -> None:
    """retry exchange 应是 Direct + durable"""
    from aio_pika import ExchangeType

    await declare_all(mock_channel)

    # 找到 retry exchange 的声明调用
    retry_call = None
    for c in mock_channel.declare_exchange.await_args_list:
        if c.args[0] == EXCHANGE_RETRY:
            retry_call = c
            break

    assert retry_call is not None
    assert retry_call.args[1] == ExchangeType.DIRECT
    assert retry_call.kwargs.get("durable") is True


# ==================== 主队列 + 重试队列对应关系 ====================


async def test_every_main_queue_has_retry_queue(mock_channel: MagicMock) -> None:
    """每个主队列都应有一个对应的重试队列"""
    await declare_all(mock_channel)

    # 收集所有 declare_queue 的 queue_name
    declared_queues = {
        c.args[0] for c in mock_channel.declare_queue.await_args_list
    }

    # 主队列列表（业务相关）
    main_queues = [
        QUEUE_TASK_CREATED,
        QUEUE_TASK_COMPLETED,
        QUEUE_TASK_FAILED,
        QUEUE_WORKFLOW_STARTED,
        QUEUE_WORKFLOW_COMPLETED,
        QUEUE_WORKFLOW_FAILED,
        QUEUE_NOTIFICATION_EMAIL,
        QUEUE_NOTIFICATION_WECHAT,
        QUEUE_NOTIFICATION_WEBHOOK,
        QUEUE_RESUME_PARSE,
        QUEUE_MATCH_COMPUTE,
    ]

    for main_queue in main_queues:
        retry_queue = main_queue + RETRY_QUEUE_SUFFIX
        assert main_queue in declared_queues, f"主队列 {main_queue} 未声明"
        assert retry_queue in declared_queues, f"重试队列 {retry_queue} 未声明"


# ==================== 重试队列配置 ====================


async def test_retry_queue_has_dlx_to_main_exchange(mock_channel: MagicMock) -> None:
    """重试队列的 x-dead-letter-exchange 应指向主 exchange"""

    # 记录每次 declare_queue 的 (name, arguments)
    queue_args_map: dict[str, dict] = {}

    async def capture_declare_queue(name, **kwargs):
        queue_args_map[name] = kwargs.get("arguments", {})
        # 返回带 async bind 的 mock（_declare_main_queue_with_retry 会调 bind）
        q = MagicMock()
        q.bind = AsyncMock()
        return q

    mock_channel.declare_queue.side_effect = capture_declare_queue

    await declare_all(mock_channel)

    # 检查 copilot.task.created.retry 的 DLX 配置
    retry_queue_name = QUEUE_TASK_CREATED + RETRY_QUEUE_SUFFIX
    assert retry_queue_name in queue_args_map

    retry_args = queue_args_map[retry_queue_name]
    # DLX 应指向主 exchange
    assert retry_args["x-dead-letter-exchange"] == "copilot.task.exchange"
    # DLX routing key 应是原 routing key
    assert retry_args["x-dead-letter-routing-key"] == ROUTING_TASK_CREATED
    # 兜底 TTL
    assert retry_args["x-message-ttl"] == RETRY_QUEUE_MAX_TTL_MS


async def test_retry_queue_for_workflow_points_to_workflow_exchange(
    mock_channel: MagicMock,
) -> None:
    """工作流重试队列的 DLX 应指向工作流 exchange"""
    queue_args_map: dict[str, dict] = {}

    async def capture_declare_queue(name, **kwargs):
        queue_args_map[name] = kwargs.get("arguments", {})
        q = MagicMock()
        q.bind = AsyncMock()
        return q

    mock_channel.declare_queue.side_effect = capture_declare_queue

    await declare_all(mock_channel)

    for queue_name, expected_exchange, expected_routing_key in [
        (QUEUE_WORKFLOW_STARTED, "copilot.workflow.exchange", ROUTING_WORKFLOW_STARTED),
        (QUEUE_WORKFLOW_COMPLETED, "copilot.workflow.exchange", ROUTING_WORKFLOW_COMPLETED),
        (QUEUE_WORKFLOW_FAILED, "copilot.workflow.exchange", ROUTING_WORKFLOW_FAILED),
    ]:
        retry_name = queue_name + RETRY_QUEUE_SUFFIX
        retry_args = queue_args_map[retry_name]
        assert retry_args["x-dead-letter-exchange"] == expected_exchange
        assert retry_args["x-dead-letter-routing-key"] == expected_routing_key


async def test_retry_queue_for_notification(mock_channel: MagicMock) -> None:
    """通知重试队列的 DLX 应指向通知 exchange"""
    queue_args_map: dict[str, dict] = {}

    async def capture_declare_queue(name, **kwargs):
        queue_args_map[name] = kwargs.get("arguments", {})
        q = MagicMock()
        q.bind = AsyncMock()
        return q

    mock_channel.declare_queue.side_effect = capture_declare_queue

    await declare_all(mock_channel)

    for queue_name, expected_routing_key in [
        (QUEUE_NOTIFICATION_EMAIL, ROUTING_NOTIFICATION_EMAIL),
        (QUEUE_NOTIFICATION_WECHAT, ROUTING_NOTIFICATION_WECHAT),
        (QUEUE_NOTIFICATION_WEBHOOK, ROUTING_NOTIFICATION_WEBHOOK),
    ]:
        retry_name = queue_name + RETRY_QUEUE_SUFFIX
        retry_args = queue_args_map[retry_name]
        assert retry_args["x-dead-letter-exchange"] == "copilot.notification.exchange"
        assert retry_args["x-dead-letter-routing-key"] == expected_routing_key


# ==================== 主队列配置（未受影响） ====================


async def test_main_queue_keeps_dlx_to_dlx_exchange(mock_channel: MagicMock) -> None:
    """主队列的 x-dead-letter-exchange 应保持指向死信 exchange"""
    queue_args_map: dict[str, dict] = {}

    async def capture_declare_queue(name, **kwargs):
        queue_args_map[name] = kwargs.get("arguments", {})
        q = MagicMock()
        q.bind = AsyncMock()
        return q

    mock_channel.declare_queue.side_effect = capture_declare_queue

    await declare_all(mock_channel)

    # 主队列应继续指向 DLX（重试耗尽后进死信）
    for main_queue in [
        QUEUE_TASK_CREATED,
        QUEUE_NOTIFICATION_EMAIL,
        QUEUE_RESUME_PARSE,
        QUEUE_MATCH_COMPUTE,
    ]:
        main_args = queue_args_map[main_queue]
        assert main_args["x-dead-letter-exchange"] == EXCHANGE_DLX, (
            f"主队列 {main_queue} 的 DLX 被错误修改"
        )
        # 业务 TTL
        assert "x-message-ttl" in main_args


# ==================== 重试队列的 binding ====================


async def test_retry_queue_is_bound_to_retry_exchange(mock_channel: MagicMock) -> None:
    """重试队列应绑定到 retry exchange，routing_key = queue_name"""

    # declare_queue 返回的 mock 的 bind 方法需要被捕获
    # bind 本身是 async（被 await），所以用 AsyncMock
    queue_mock_from_declare = MagicMock()
    queue_mock_from_declare.bind = AsyncMock()
    mock_channel.declare_queue = AsyncMock(return_value=queue_mock_from_declare)

    await declare_all(mock_channel)

    # 找到所有重试队列的 bind（通过 routing_key 判断 = queue_name）
    bind_routing_keys = [
        call.kwargs.get("routing_key")
        for call in queue_mock_from_declare.bind.await_args_list
    ]

    # 11 个主队列，每个都应有 1 次重试队列的 bind（routing_key=queue_name）
    expected_routing_keys = {
        QUEUE_TASK_CREATED, QUEUE_TASK_COMPLETED, QUEUE_TASK_FAILED,
        QUEUE_WORKFLOW_STARTED, QUEUE_WORKFLOW_COMPLETED, QUEUE_WORKFLOW_FAILED,
        QUEUE_NOTIFICATION_EMAIL, QUEUE_NOTIFICATION_WECHAT, QUEUE_NOTIFICATION_WEBHOOK,
        QUEUE_RESUME_PARSE, QUEUE_MATCH_COMPUTE,
    }
    actual_retry_routing_keys = set(bind_routing_keys) & expected_routing_keys
    assert actual_retry_routing_keys == expected_routing_keys, (
        f"缺失重试队列 bind: {expected_routing_keys - actual_retry_routing_keys}"
    )


# ==================== 死信队列仍然被声明 ====================


async def test_dlq_is_still_declared(mock_channel: MagicMock) -> None:
    """死信队列必须仍然被声明（重试耗尽后进死信）"""
    await declare_all(mock_channel)

    declared_queues = {
        c.args[0] for c in mock_channel.declare_queue.await_args_list
    }
    assert QUEUE_DLX_DEAD_LETTER in declared_queues

    # DLX exchange 也被声明
    declared_exchanges = {
        c.args[0] for c in mock_channel.declare_exchange.await_args_list
    }
    assert EXCHANGE_DLX in declared_exchanges
