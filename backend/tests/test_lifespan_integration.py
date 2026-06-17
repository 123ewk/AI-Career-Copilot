"""main.py lifespan 集成测试

覆盖：
- lifespan startup 触发 declare_all
- lifespan startup 触发 consumer_manager.start_all
- lifespan shutdown 触发 consumer_manager.stop_all
- lifespan shutdown 释放顺序：consumer → RabbitMQ → Redis → PostgreSQL
- 注册表为空时不报错

实现说明：
main.py 顶层 import 了所有 router，router 又依赖 python-multipart 等。
为避免启动整个应用图，我们在文件最顶部 mock 掉 routers 包，
再 import main（此时 main.py 能正常 import 所有 mock 的 router）。
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

# ==================== 必须在 import main 之前执行 ====================
# 把 routers 包和所有子模块替换成空 mock 模块，避免触发真实依赖
# main.py 顶层 `from app.api.routers import auth, user, ...` 必须能找到这些模块
_ROUTER_MODULES = [
    "app.api.routers",
    "app.api.routers.auth",
    "app.api.routers.user",
    "app.api.routers.resume",
    "app.api.routers.jobs",
    "app.api.routers.match",
    "app.api.routers.session",
    "app.api.routers.task",
    "app.api.routers.agent",
    "app.api.routers.workflow",
]
for _name in _ROUTER_MODULES:
    if _name in sys.modules:
        del sys.modules[_name]
    _mod = types.ModuleType(_name)
    _mod.router = MagicMock()  # type: ignore[attr-defined]
    sys.modules[_name] = _mod

# 现在可以安全 import main（main.py 顶层 import 的是 mock 后的 router）
import main as main_module  # noqa: E402
from main import create_app, lifespan  # noqa: E402


@pytest.fixture(autouse=True)
def _cleanup_router_mocks():
    """测试结束后清理 routers mock，让其他测试可正常 import 真实 routers"""
    yield
    for name in list(sys.modules):
        if name.startswith("app.api.routers"):
            del sys.modules[name]


# ==================== Mock 外部依赖 ====================


@pytest.fixture
def mock_factories(monkeypatch):
    """mock 所有外部依赖（PG/Redis/RabbitMQ/Consumer）"""
    # PostgreSQL
    pg_mock = MagicMock()
    pg_mock.engine = MagicMock()
    pg_mock.close = AsyncMock()
    monkeypatch.setattr(main_module, "pg_session_factory", pg_mock)

    # Redis
    redis_mock = MagicMock()
    redis_mock.client = MagicMock()
    redis_mock.close = AsyncMock()
    monkeypatch.setattr(main_module, "redis_client_factory", redis_mock)

    # RabbitMQ
    mq_channel = MagicMock()
    mq_channel.set_qos = AsyncMock()
    mq_channel.get_queue = AsyncMock(
        return_value=MagicMock(consume=AsyncMock(return_value="tag"))
    )
    mq_channel.get_exchange = AsyncMock(return_value=MagicMock())
    mq_channel.declare_exchange = AsyncMock(return_value=MagicMock())
    mq_channel.declare_queue = AsyncMock(return_value=MagicMock())
    mq_channel.basic_cancel = AsyncMock()

    mq_mock = MagicMock()
    mq_mock.get_channel = AsyncMock(return_value=mq_channel)
    mq_mock.connect = AsyncMock(return_value=MagicMock())
    mq_mock.close = AsyncMock()
    monkeypatch.setattr(main_module, "rabbitmq_connection_factory", mq_mock)

    # declare_all
    declare_all_mock = AsyncMock()
    monkeypatch.setattr(main_module, "declare_all", declare_all_mock)

    # consumer_manager
    cm_mock = MagicMock()
    cm_mock.start_all = AsyncMock()
    cm_mock.stop_all = AsyncMock()
    cm_mock.consumers = []  # 空注册表
    monkeypatch.setattr(main_module, "consumer_manager", cm_mock)

    return {
        "pg": pg_mock,
        "redis": redis_mock,
        "mq": mq_mock,
        "mq_channel": mq_channel,
        "declare_all": declare_all_mock,
        "consumer_manager": cm_mock,
    }


# ==================== Startup 阶段 ====================


async def test_lifespan_startup_declares_topology(mock_factories: dict) -> None:
    """启动时必须声明 RabbitMQ 拓扑"""
    app = FastAPI()
    async with lifespan(app):
        pass

    mock_factories["declare_all"].assert_awaited_once_with(
        mock_factories["mq_channel"]
    )


async def test_lifespan_startup_starts_consumers(mock_factories: dict) -> None:
    """启动时必须调用 consumer_manager.start_all"""
    app = FastAPI()
    async with lifespan(app):
        pass

    mock_factories["consumer_manager"].start_all.assert_awaited_once_with(
        mock_factories["mq_channel"]
    )


async def test_lifespan_startup_opens_mq_channel(mock_factories: dict) -> None:
    """启动时应获取 MQ channel（用于声明拓扑和启动消费者）"""
    app = FastAPI()
    async with lifespan(app):
        pass

    mock_factories["mq"].get_channel.assert_awaited_once()


async def test_lifespan_startup_with_empty_registry_works(
    mock_factories: dict,
) -> None:
    """空注册表时启动不应报错"""
    mock_factories["consumer_manager"].consumers = []
    app = FastAPI()

    # 不应抛异常
    async with lifespan(app):
        pass

    # start_all 仍被调用（空实现不报错）
    mock_factories["consumer_manager"].start_all.assert_awaited_once()


# ==================== Shutdown 阶段 ====================


async def test_lifespan_shutdown_stops_consumers_first(
    mock_factories: dict,
) -> None:
    """shutdown 必须先停消费者（保证业务处理完毕后再断连）"""
    app = FastAPI()

    call_order: list[str] = []

    async def record_stop():
        call_order.append("consumer.stop")

    async def record_mq_close():
        call_order.append("mq.close")

    async def record_redis_close():
        call_order.append("redis.close")

    async def record_pg_close():
        call_order.append("pg.close")

    mock_factories["consumer_manager"].stop_all = AsyncMock(side_effect=record_stop)
    mock_factories["mq"].close = AsyncMock(side_effect=record_mq_close)
    mock_factories["redis"].close = AsyncMock(side_effect=record_redis_close)
    mock_factories["pg"].close = AsyncMock(side_effect=record_pg_close)

    async with lifespan(app):
        pass

    # 顺序：consumer → mq → redis → pg
    assert call_order == ["consumer.stop", "mq.close", "redis.close", "pg.close"]


async def test_lifespan_shutdown_continues_on_consumer_failure(
    mock_factories: dict,
) -> None:
    """consumer.stop_all 失败不应阻塞其他资源释放"""
    mock_factories["consumer_manager"].stop_all = AsyncMock(
        side_effect=RuntimeError("stop 失败")
    )
    app = FastAPI()

    # 不应抛异常
    async with lifespan(app):
        pass

    # 其他资源仍被关闭
    mock_factories["mq"].close.assert_awaited_once()
    mock_factories["redis"].close.assert_awaited_once()
    mock_factories["pg"].close.assert_awaited_once()


async def test_lifespan_shutdown_continues_on_mq_failure(
    mock_factories: dict,
) -> None:
    """RabbitMQ 关闭失败不应阻塞 Redis/PostgreSQL 关闭"""
    mock_factories["mq"].close = AsyncMock(side_effect=RuntimeError("MQ 关闭失败"))
    app = FastAPI()

    async with lifespan(app):
        pass

    # Redis/PG 仍被关闭
    mock_factories["redis"].close.assert_awaited_once()
    mock_factories["pg"].close.assert_awaited_once()


# ==================== create_app 集成 ====================


def test_create_app_uses_lifespan() -> None:
    """create_app 创建的 app 必须使用 lifespan 上下文"""
    app = create_app()
    # FastAPI 内部用 app.router.lifespan_context 存储
    assert app.router.lifespan_context is not None
