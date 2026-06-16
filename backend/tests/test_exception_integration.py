"""Exception 中间件端到端集成测试（pytest 风格）

测试覆盖：
1. AppException（业务/基础设施）走自定义 handler
2. RequestValidationError 拍平为统一格式
3. StarletteHTTPException 走 HTTP handler
4. 兜底 Exception 走 500 + SYS_000
5. dev 环境响应体附加 debug 字段
6. 4xx/5xx 响应携带 X-Request-ID 头
7. 错误响应不暴露敏感信息（traceback / 原始 detail）
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.exceptions import (
    AppException,
    AuthenticationError,
    DatabaseError,
    ValidationError,
)


# ==================== Fixtures ====================

@pytest.fixture
def app() -> FastAPI:
    """构造测试应用：注册 exception + request_id 中间件，定义 8 个端点"""
    application = FastAPI()
    add_exception_middleware(application)
    add_request_id_middleware(application)

    class _UserIn(BaseModel):
        name: str
        age: int

    @application.get("/ok")
    async def ok() -> dict[str, str]:
        return {"msg": "ok"}

    @application.get("/biz-4xx")
    async def biz_4xx() -> None:
        raise ValidationError(detail="邮箱格式错误", extra={"field": "email"})

    @application.get("/biz-401")
    async def biz_401() -> None:
        raise AuthenticationError(detail="Token 已过期")

    @application.get("/infra-500")
    async def infra_500() -> None:
        raise DatabaseError(detail="数据库连接失败", extra={"host": "pg-1"})

    @application.get("/http-404")
    async def http_404() -> None:
        raise HTTPException(status_code=404, detail="资源不见了")

    @application.get("/http-401-direct")
    async def http_401_direct() -> None:
        raise HTTPException(status_code=401, detail="请登录")

    @application.post("/validate")
    async def validate(data: _UserIn) -> dict[str, Any]:
        return {"name": data.name, "age": data.age}

    @application.get("/crash")
    async def crash() -> None:
        raise RuntimeError("boom")

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """raise_server_exceptions=False 模拟生产模式"""
    return TestClient(app, raise_server_exceptions=False)


# ==================== T1: 正常请求 ====================

def test_ok_endpoint(client: TestClient) -> None:
    """T1: 正常 GET /ok → 200"""
    r = client.get("/ok")
    assert r.status_code == 200
    assert r.json() == {"msg": "ok"}


# ==================== T2-T4: 业务/基础设施异常 ====================

def test_validation_error_returns_400(client: TestClient) -> None:
    """T2: ValidationError → 400 | error_code=VAL_001"""
    r = client.get("/biz-4xx")
    body = r.json()
    assert r.status_code == 400
    assert body["error_code"] == "VAL_001"
    assert body["detail"] == "邮箱格式错误"
    assert "request_id" in body
    # extra 不写入响应（防泄露）
    assert "extra" not in body


def test_authentication_error_returns_401(client: TestClient) -> None:
    """T3: AuthenticationError → 401 | error_code=AUTH_001"""
    r = client.get("/biz-401")
    body = r.json()
    assert r.status_code == 401
    assert body["error_code"] == "AUTH_001"
    assert body["detail"] == "Token 已过期"


def test_database_error_returns_500(client: TestClient) -> None:
    """T4: DatabaseError → 500 | error_code=DB_001 + dev extra"""
    r = client.get("/infra-500")
    body = r.json()
    assert r.status_code == 500
    assert body["error_code"] == "DB_001"
    assert body["detail"] == "数据库连接失败"
    # dev 模式带 extra 字段
    assert "debug" in body
    assert body["debug"]["extra"] == {"host": "pg-1"}


# ==================== T5: Pydantic 校验失败 ====================

def test_pydantic_validation_returns_422(client: TestClient) -> None:
    """T5: Pydantic 校验失败 → 422 | error_code=VAL_001"""
    r = client.post("/validate", json={"age": "not-a-number"})
    body = r.json()
    assert r.status_code == 422
    assert body["error_code"] == "VAL_001"
    assert "项错误" in body["detail"]
    assert "request_id" in body


# ==================== T6-T8: HTTPException 映射 ====================

def test_http_exception_404_maps_to_res_001(client: TestClient) -> None:
    """T6: HTTPException(404) → 404 | error_code=RES_001"""
    r = client.get("/http-404")
    body = r.json()
    assert r.status_code == 404
    assert body["error_code"] == "RES_001"
    assert body["detail"] == "资源不见了"


def test_http_exception_401_maps_to_auth_001(client: TestClient) -> None:
    """T7: HTTPException(401) → 401 | error_code=AUTH_001"""
    r = client.get("/http-401-direct")
    body = r.json()
    assert r.status_code == 401
    assert body["error_code"] == "AUTH_001"
    assert body["detail"] == "请登录"


def test_http_exception_405_maps_to_req_002(client: TestClient) -> None:
    """T8: HTTPException(405) → 405 | error_code=REQ_002"""
    r = client.request("PATCH", "/http-404")
    body = r.json()
    assert r.status_code == 405
    assert body["error_code"] == "REQ_002"


# ==================== T9: 兜底异常 ====================

def test_unhandled_exception_returns_500_sys_000(client: TestClient) -> None:
    """T9: RuntimeError → 500 | error_code=SYS_000 | 不泄露原始异常"""
    r = client.get("/crash")
    body = r.json()
    assert r.status_code == 500
    assert body["error_code"] == "SYS_000"
    assert body["detail"] == "服务内部错误"
    # 不应泄露原始异常信息
    assert "boom" not in body["detail"]
    assert "request_id" in body


# ==================== T10-T11: X-Request-ID 透传 ====================

def test_5xx_response_has_x_request_id(client: TestClient) -> None:
    """T10: 5xx 异常响应携带 X-Request-ID（与请求头透传一致）"""
    custom_rid = "test-exception-rid-aaaa-bbbb"
    r = client.get("/crash", headers={"X-Request-ID": custom_rid})
    assert r.status_code == 500
    assert r.headers.get("x-request-id") == custom_rid
    assert r.json()["request_id"] == custom_rid


def test_4xx_response_has_x_request_id(client: TestClient) -> None:
    """T11: 4xx 响应也携带 X-Request-ID"""
    r = client.get("/biz-401", headers={"X-Request-ID": "4xx-rid"})
    assert r.status_code == 401
    assert r.headers.get("x-request-id") == "4xx-rid"


# ==================== T12-T14: dev 环境 debug 字段 ====================

def test_dev_response_contains_debug_for_biz_exception(client: TestClient) -> None:
    """T12: dev 环境响应体包含 debug 字段（业务异常）"""
    r = client.get("/biz-4xx")
    body = r.json()
    assert "debug" in body
    assert body["debug"]["exc_type"] == "ValidationError"
    assert body["debug"]["extra"] == {"field": "email"}


def test_dev_5xx_response_contains_traceback(client: TestClient) -> None:
    """T13: dev 环境 5xx 响应 debug.traceback 存在"""
    r = client.get("/crash")
    body = r.json()
    assert "debug" in body
    assert "traceback" in body["debug"]
    assert "RuntimeError" in body["debug"]["traceback"]


def test_dev_422_response_contains_errors(client: TestClient) -> None:
    """T14: dev 环境 422 响应 debug.errors 存在"""
    r = client.post("/validate", json={})
    body = r.json()
    assert "debug" in body
    assert "errors" in body["debug"]
    assert isinstance(body["debug"]["errors"], list)


# ==================== T15: WWW-Authenticate 头 ====================

def test_biz_401_response_has_no_www_authenticate(client: TestClient) -> None:
    """T15: exception handler 不注入 WWW-Authenticate（仅 auth 401 才需要）"""
    r = client.get("/biz-401")
    assert "www-authenticate" not in {h.lower() for h in r.headers.keys()}


# ==================== T16: 自定义 AppException 子类 ====================

def test_custom_app_exception_subclass(client: TestClient, app: FastAPI) -> None:
    """T16: 自定义 AppException 子类映射到自定义 status_code / error_code"""

    class _CustomError(AppException):
        status_code = 418  # I'm a teapot
        error_code = "TEA_001"
        detail = "我是茶壶"

    @app.get("/custom-app-exc")
    async def custom_app_exc() -> None:
        raise _CustomError()

    r = client.get("/custom-app-exc")
    body = r.json()
    assert r.status_code == 418
    assert body["error_code"] == "TEA_001"
    assert body["detail"] == "我是茶壶"
