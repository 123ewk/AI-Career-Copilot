"""限流中间件端到端集成测试（pytest 风格）

依赖：本地 Redis 服务在 localhost:6379 运行
- Redis 不可达时大部分用例会 skip 或 fail
- 测试间通过 reset_rate_limit_keys fixture 清理限流键，互不干扰
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.rate_limit import add_rate_limit_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.logger import setup_logging
from app.infra.database.redis import redis_client_factory

pytestmark = pytest.mark.asyncio


# ==================== Fixtures ====================

@pytest.fixture(scope="module", autouse=True)
def _setup_logging() -> None:
    """模块级：初始化日志（只跑一次）"""
    setup_logging()


@pytest.fixture(autouse=True)
async def _reset_redis_factory() -> AsyncGenerator[None, None]:
    """每个测试前重置 Redis 工厂，避免复用已关闭 event loop 上的连接池

    redis_client_factory 是模块级单例，首次创建后绑定到当时的 event loop。
    当 pytest-asyncio 为每个测试创建新 event loop 时，旧 pool 会抛出
    RuntimeError: Event loop is closed。重置工厂可让测试使用当前 loop。
    """
    redis_client_factory._pool = None  # type: ignore[attr-defined]
    redis_client_factory._client = None  # type: ignore[attr-defined]
    yield
    await redis_client_factory.close()


@pytest.fixture
def app() -> FastAPI:
    """构造测试应用

    中间件注册顺序按 main.py 约定（add_middleware LIFO）：
    最先注册的最内层，最后注册的最外层
    """
    application = FastAPI()
    add_rate_limit_middleware(application)
    add_exception_middleware(application)
    add_logging_middleware(application)
    add_request_id_middleware(application)

    @application.get("/ping")
    async def ping() -> dict[str, str]:
        return {"msg": "pong"}

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


async def _reset_keys() -> int:
    """清空限流键，测试间互不干扰"""
    client = redis_client_factory.client
    keys: list[str] = []
    async for k in client.scan_iter(match="rl:*"):
        keys.append(k)
    if keys:
        await client.delete(*keys)
    return len(keys)


@pytest.fixture
async def reset_rate_limit_keys() -> AsyncGenerator[None, None]:
    """每个测试前清空限流键"""
    await _reset_keys()
    yield
    # 测试结束再清一次，避免影响下一个测试
    await _reset_keys()


@pytest.fixture
async def ac(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """async HTTP 客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ==================== T1: 60 个请求全部通过 ====================

async def test_60_requests_all_pass(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T1: 60 个连续请求全部 200，配额逐次递减"""
    for i in range(60):
        r = await ac.get("/ping")
        assert r.status_code == 200, f"第 {i+1} 个请求失败: {r.status_code}"
        assert r.headers.get("x-ratelimit-limit") == "60"
        remaining = int(r.headers["x-ratelimit-remaining"])
        assert remaining == 60 - (i + 1), f"第 {i+1} 个剩余配额错误: {remaining}"


# ==================== T2: 第 61 个请求 429 ====================

async def test_61st_request_returns_429(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T2: 第 61 个请求触发 429 + RATE_001 + Retry-After"""
    # 先消耗 60 个配额
    for _ in range(60):
        await ac.get("/ping")

    r = await ac.get("/ping")
    assert r.status_code == 429
    body = r.json()
    assert body["error_code"] == "RATE_001"
    assert "request_id" in body
    assert int(r.headers["x-ratelimit-remaining"]) == 0
    assert int(r.headers["retry-after"]) >= 1
    assert "x-request-id" in {h.lower() for h in r.headers.keys()}


# ==================== T3: 健康检查不消耗配额 ====================

async def test_health_does_not_consume_quota(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T3: 健康检查路径不消耗配额"""
    for _ in range(5):
        r = await ac.get("/health")
        assert r.status_code == 200
        assert "x-ratelimit-limit" not in {h.lower() for h in r.headers.keys()}


# ==================== T4: 配额耗尽后 /ping 仍 429 ====================

async def test_ping_still_429_after_quota_exhausted(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T4: 配额耗尽后 /ping 仍返回 429"""
    for _ in range(60):
        await ac.get("/ping")
    r = await ac.get("/ping")
    assert r.status_code == 429


# ==================== T5: Redis 键存在且 TTL 正确 ====================

async def test_redis_key_exists_with_ttl(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T5: Redis 限流键存在且 TTL 在 (0, window] 范围"""
    await ac.get("/ping")

    client = redis_client_factory.client
    keys: list[str] = []
    async for k in client.scan_iter(match="rl:*"):
        keys.append(k)
    assert len(keys) >= 1, f"未发现限流键: {keys}"

    sample = keys[0]
    ttl = await client.ttl(sample)
    assert 0 < ttl <= 60, f"TTL 异常: {ttl}"


# ==================== T6: 限流响应包含 X-Request-ID ====================

async def test_429_response_has_x_request_id(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T6: 限流响应包含 X-Request-ID"""
    for _ in range(60):
        await ac.get("/ping")
    r = await ac.get("/ping")
    assert r.status_code == 429
    rid = r.headers.get("x-request-id")
    assert rid and rid != "-", f"X-Request-ID 缺失: {rid}"


# ==================== T7: 限流响应头字段完整 ====================

async def test_429_response_headers_complete(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T7: 限流响应头字段完整（含 x-ratelimit-* / retry-after / x-request-id）"""
    for _ in range(60):
        await ac.get("/ping")
    r = await ac.get("/ping")
    assert r.status_code == 429
    required = [
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "retry-after",
        "x-request-id",
    ]
    headers_lower = {k.lower() for k in r.headers.keys()}
    for h in required:
        assert h in headers_lower, f"缺少响应头: {h}"


# ==================== T8: 清空后窗口重置 ====================

async def test_reset_restores_quota(
    ac: AsyncClient, reset_rate_limit_keys
) -> None:
    """T8: 清空 Redis 后窗口重置，remaining 应为 59"""
    # 先消耗 60 个
    for _ in range(60):
        await ac.get("/ping")

    # 主动重置（fixture 会再清理一次，这是测试逻辑内的额外重置）
    await _reset_keys()

    r = await ac.get("/ping")
    assert r.status_code == 200, f"清空后仍 {r.status_code}: {r.text}"
    assert r.headers.get("x-ratelimit-remaining") == "59"
