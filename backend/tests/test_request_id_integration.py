"""RequestIDMiddleware 集成测试（pytest 风格）

测试覆盖：
- 无 X-Request-ID 时自动生成 UUID，state / ctx / header 三者一致
- 客户端传入合法的 X-Request-ID 透传
- 非法 X-Request-ID 被拒绝，回退 UUID
- 业务日志自动带上 rid
- 4xx/5xx 响应也带 X-Request-ID
- 多次请求 rid 互不污染
- request.state 写入不影响原有 state
- 兼容 X-Correlation-ID / X-Trace-ID
- 多个候选 header 存在时按列表顺序取第一个
- request_id 不会泄露到下一个请求
"""

from __future__ import annotations

import io
import re

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.logger import get_request_id, logger


# ==================== Fixtures ====================

@pytest.fixture
def log_capture() -> io.StringIO:
    """函数级 StringIO 缓冲区，teardown 时移除 handler"""
    buf = io.StringIO()
    handler_id = logger.add(
        buf,
        format="rid={extra[request_id]} | {message}",
        level="DEBUG",
        enqueue=False,
    )
    yield buf
    try:
        logger.remove(handler_id)
    except Exception:
        pass


@pytest.fixture
def app() -> FastAPI:
    """构造测试应用：logging + exception + request_id 中间件"""
    application = FastAPI()
    add_logging_middleware(application)
    add_exception_middleware(application)
    add_request_id_middleware(application)

    @application.get("/inspect")
    def inspect(request: Request) -> dict[str, str | None]:
        return {
            "state_rid": request.state.request_id,
            "ctx_rid": get_request_id(),
        }

    @application.get("/log")
    def log_endpoint() -> dict[str, bool]:
        logger.info("业务层日志")
        return {"ok": True}

    @application.get("/not-found")
    def not_found() -> None:
        raise HTTPException(status_code=404, detail="missing")

    @application.get("/crash")
    def crash() -> None:
        raise RuntimeError("boom")

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ==================== T1: 无 X-Request-ID → UUID ====================

def test_no_x_request_id_generates_uuid(
    client: TestClient, log_capture: io.StringIO
) -> None:
    """T1: 无 X-Request-ID 时自动生成 UUID，state/ctx/header 三者一致"""
    r = client.get("/inspect")
    data = r.json()
    assert r.status_code == 200
    assert re.match(r"^[0-9a-f]{8}-", r.headers.get("x-request-id", ""))
    assert data["state_rid"] == r.headers.get("x-request-id")
    assert data["ctx_rid"] == r.headers.get("x-request-id")


# ==================== T2: 合法 X-Request-ID 透传 ====================

def test_valid_x_request_id_passthrough(
    client: TestClient, log_capture: io.StringIO
) -> None:
    """T2: 合法 X-Request-ID 透传（state/ctx/header/logs 都带）"""
    r = client.get("/inspect", headers={"X-Request-ID": "client-rid-12345"})
    data = r.json()
    assert r.headers.get("x-request-id") == "client-rid-12345"
    assert data["state_rid"] == "client-rid-12345"
    assert data["ctx_rid"] == "client-rid-12345"
    # 业务日志也带上
    assert "rid=client-rid-12345" in log_capture.getvalue()


# ==================== T3: 非法 X-Request-ID 被拒绝 ====================

def test_invalid_x_request_id_rejected(client: TestClient) -> None:
    """T3: 非法 X-Request-ID（含换行符）被拒绝，回退 UUID"""
    r = client.get("/inspect", headers={"X-Request-ID": "evil\nINJECT"})
    data = r.json()
    assert "\n" not in (r.headers.get("x-request-id") or "")
    assert re.match(r"^[0-9a-f]{8}-", r.headers.get("x-request-id") or "")
    assert data["state_rid"] == r.headers.get("x-request-id")


# ==================== T4: 业务日志自动带上 rid ====================

def test_business_log_includes_rid(
    client: TestClient, log_capture: io.StringIO
) -> None:
    """T4: 业务日志自动带上 rid"""
    r = client.get("/log", headers={"X-Request-ID": "log-trace"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "业务层日志" in logs
    assert "rid=log-trace" in logs


# ==================== T5: 4xx 响应也带 X-Request-ID ====================

def test_4xx_response_has_x_request_id(client: TestClient) -> None:
    """T5: 4xx 响应也带 X-Request-ID"""
    r = client.get("/not-found", headers={"X-Request-ID": "err-trace"})
    assert r.status_code == 404
    assert r.headers.get("x-request-id") == "err-trace"


# ==================== T6: 5xx 异常响应也带 X-Request-ID ====================

def test_5xx_response_has_x_request_id(client: TestClient) -> None:
    """T6: 5xx 异常响应也带 X-Request-ID（纯 ASGI 优势）"""
    r = client.get("/crash", headers={"X-Request-ID": "crash-trace"})
    assert r.status_code == 500
    # 关键验证：异常响应头也带 X-Request-ID
    assert r.headers.get("x-request-id") == "crash-trace"
    # 响应体中的 request_id 与 header 一致
    assert r.json()["request_id"] == "crash-trace"


# ==================== T7: 多次请求 rid 互不污染 ====================

def test_multiple_requests_have_separate_rids(client: TestClient) -> None:
    """T7: 多次请求 rid 互不污染"""
    r1 = client.get("/inspect", headers={"X-Request-ID": "rid-A"})
    r2 = client.get("/inspect", headers={"X-Request-ID": "rid-B"})
    assert r1.json()["state_rid"] == "rid-A"
    assert r2.json()["state_rid"] == "rid-B"


# ==================== T8: request.state 写入不影响原有 state ====================

def test_state_setdefault_preserves_existing(client: TestClient, app: FastAPI) -> None:
    """T8: request.state 已有字段不被覆盖"""

    @app.get("/check-state")
    def check_state(request: Request) -> dict[str, str | int]:
        request.state.user_id = 42
        return {
            "request_id": request.state.request_id,
            "user_id": request.state.user_id,
        }

    r = client.get("/check-state", headers={"X-Request-ID": "state-test"})
    data = r.json()
    assert data["request_id"] == "state-test"
    assert data["user_id"] == 42, "原有 state 字段不应被覆盖"


# ==================== T9: 兼容 X-Correlation-ID ====================

def test_x_correlation_id_supported(client: TestClient) -> None:
    """T9: 兼容 X-Correlation-ID"""
    r = client.get("/inspect", headers={"X-Correlation-ID": "corr-001"})
    assert r.json()["state_rid"] == "corr-001"


# ==================== T10: 兼容 X-Trace-ID ====================

def test_x_trace_id_supported(client: TestClient) -> None:
    """T10: 兼容 X-Trace-ID"""
    r = client.get("/inspect", headers={"X-Trace-ID": "trace-001"})
    assert r.json()["state_rid"] == "trace-001"


# ==================== T11: 多个候选 header 优先级 ====================

def test_header_priority(client: TestClient) -> None:
    """T11: 多个候选 header 存在时，按列表顺序取第一个（primary）"""
    r = client.get(
        "/inspect",
        headers={
            "X-Request-ID": "primary",
            "X-Correlation-ID": "secondary",
        },
    )
    data = r.json()
    # ASGI headers 顺序由 client 决定，TestClient 保持传入顺序
    # 所以应该是 primary
    assert data["state_rid"] == "primary"


# ==================== T12: request_id 不泄露 ====================

def test_request_id_does_not_leak(client: TestClient, app: FastAPI) -> None:
    """T12: request_id 不会泄露到下一个请求"""

    @app.get("/leak-check")
    def leak_check() -> dict[str, str | None]:
        return {"rid": get_request_id()}

    # 第一次请求带 rid
    r1 = client.get("/leak-check", headers={"X-Request-ID": "leak-test-1"})
    # 第二次请求不带
    r2 = client.get("/leak-check")
    assert r1.json()["rid"] == "leak-test-1"
    assert r2.json()["rid"] != "leak-test-1", "第二次请求不应残留上一次的值"
    assert re.match(r"^[0-9a-f]{8}-", r2.json()["rid"] or "")
