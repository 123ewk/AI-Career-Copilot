"""Auth 中间件端到端集成测试（pytest 风格）

测试覆盖（与脚本版一致）：
- 白名单路径无需 Token
- 受保护路径的 401 系列（缺失/格式/签名/过期/类型/sub 缺失）
- 有效 Token 注入 user_id
- 大小写宽松的 Bearer 前缀
- 401/200 响应 X-Request-ID 透传
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import jwt
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.middleware.auth import add_auth_middleware
from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.rate_limit import add_rate_limit_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.logger import setup_logging

if TYPE_CHECKING:
    from app.core.settings import Settings

pytestmark = pytest.mark.asyncio


# ==================== Fixtures ====================

@pytest.fixture(scope="module", autouse=True)
def _setup_logging() -> None:
    """模块级：初始化日志（避免每个测试都重置）"""
    setup_logging()


@pytest.fixture
def app() -> FastAPI:
    """每个测试独立的 FastAPI 应用实例

    中间件注册顺序按 main.py 约定（add_middleware 是 LIFO：
    最后注册的最外层，最先进入请求）
    """
    application = FastAPI()

    add_auth_middleware(application)
    add_rate_limit_middleware(application)
    add_exception_middleware(application)
    add_logging_middleware(application)
    add_request_id_middleware(application)

    @application.get("/api/auth/login")
    async def login() -> dict[str, str]:
        return {"msg": "login ok"}

    @application.post("/api/auth/refresh")
    async def refresh() -> dict[str, str]:
        return {"msg": "refresh ok"}

    @application.get("/api/me")
    async def me(request: Request) -> dict[str, str | None]:
        return {
            "user_id": getattr(request.state, "user_id", None),
            "sub_in_payload": request.state.user_payload.get("sub")
            if hasattr(request.state, "user_payload")
            else None,
        }

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


@pytest.fixture
async def ac(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """async HTTP 客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ==================== T1-T3: 白名单 / 健康检查 ====================

async def test_login_whitelist(ac: AsyncClient) -> None:
    """T1: /api/auth/login 白名单，无需 Token"""
    r = await ac.get("/api/auth/login")
    assert r.status_code == 200


async def test_refresh_whitelist(ac: AsyncClient) -> None:
    """T2: /api/auth/refresh 白名单，无需 Token"""
    r = await ac.post("/api/auth/refresh")
    assert r.status_code == 200


async def test_health_whitelist(ac: AsyncClient) -> None:
    """T3: /health 精确白名单"""
    r = await ac.get("/health")
    assert r.status_code == 200


# ==================== T4-T5: 受保护路径 401 场景 ====================

async def test_protected_no_token_returns_401(ac: AsyncClient) -> None:
    """T4: /api/me 无 Token → 401 + AUTH_001 + WWW-Authenticate + X-Request-ID"""
    r = await ac.get("/api/me")
    assert r.status_code == 401
    body = r.json()
    assert body["error_code"] == "AUTH_001"
    assert "request_id" in body
    assert r.headers.get("www-authenticate") == "Bearer"
    assert r.headers.get("x-request-id") is not None


async def test_protected_bad_authorization_format(ac: AsyncClient) -> None:
    """T5: Authorization 头缺 Bearer 前缀 → 401"""
    r = await ac.get("/api/me", headers={"Authorization": "abcdef.token.hijkl"})
    assert r.status_code == 401


# ==================== T6-T8: Token 校验失败 ====================

async def test_wrong_signature_token(ac: AsyncClient, make_jwt) -> None:
    """T6: 错误签名 → 401 + AUTH_001"""
    bad_token = make_jwt(secret="WRONG-SECRET-NOT-THE-REAL-ONE")
    r = await ac.get("/api/me", headers={"Authorization": f"Bearer {bad_token}"})
    assert r.status_code == 401
    assert r.json()["error_code"] == "AUTH_001"


async def test_expired_token(ac: AsyncClient, make_jwt) -> None:
    """T7: 过期 Token → 401 + detail='Token 已过期'"""
    expired_token = make_jwt(exp_delta=-60)
    r = await ac.get("/api/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Token 已过期"


async def test_refresh_token_rejected(ac: AsyncClient, make_jwt) -> None:
    """T8: refresh token 用于普通 API → 401"""
    refresh_token = make_jwt(token_type="refresh")
    r = await ac.get("/api/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert r.status_code == 401


# ==================== T9-T10: 有效 Token ====================

async def test_valid_access_token(ac: AsyncClient, make_jwt) -> None:
    """T9: 有效 access token → 200 + user_id 注入"""
    valid_token = make_jwt(sub="user-42")
    r = await ac.get("/api/me", headers={"Authorization": f"Bearer {valid_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user-42"
    assert body["sub_in_payload"] == "user-42"


async def test_bearer_lowercase(ac: AsyncClient, make_jwt) -> None:
    """T10: 'bearer xxx' 小写前缀也能通过"""
    valid_token = make_jwt(sub="user-42")
    r = await ac.get("/api/me", headers={"Authorization": f"bearer {valid_token}"})
    assert r.status_code == 200


# ==================== T11: X-Request-ID 透传 ====================

async def test_401_x_request_id_passthrough(ac: AsyncClient) -> None:
    """T11: 401 响应的 X-Request-ID 与请求头一致"""
    custom_rid = "test-correlation-id-aaaa-bbbb-cccc"
    r = await ac.get(
        "/api/me",
        headers={"Authorization": "Bearer x", "X-Request-ID": custom_rid},
    )
    assert r.status_code == 401
    assert r.headers.get("x-request-id") == custom_rid


# ==================== T12: 缺 sub 声明 ====================

async def test_token_missing_sub(ac: AsyncClient, test_settings: "Settings") -> None:
    """T12: Token 缺 sub 声明 → 401"""
    token_no_sub = jwt.encode(
        {"type": "access", "exp": 9_999_999_999},
        test_settings.jwt_secret_key,
        algorithm=test_settings.jwt_algorithm,
    )
    r = await ac.get("/api/me", headers={"Authorization": f"Bearer {token_no_sub}"})
    assert r.status_code == 401
