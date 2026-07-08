"""Health Router 集成测试

测试覆盖：
- /health 返回 200 和 {status: ok}
- 健康检查端点不依赖数据库/缓存/MQ，可在独立 FastAPI 应用中测试
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routers import health

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app() -> FastAPI:
    """挂载 health router 的独立应用"""
    application = FastAPI()
    application.include_router(health.router)
    return application


@pytest.fixture
async def ac(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """async HTTP 客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_health_endpoint_returns_ok(ac: AsyncClient) -> None:
    """T1: /health 返回 200 和固定 JSON"""
    response = await ac.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
