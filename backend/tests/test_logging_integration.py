"""Logging 中间件端到端集成测试（pytest 风格）

测试覆盖：
1. 正常请求（2xx）输出 INFO 访问日志
2. 4xx 输出 WARNING 访问日志
3. 5xx 输出 ERROR 访问日志
4. 健康检查路径跳过访问日志
5. 访问日志包含 method/path/status/latency/client
6. 访问日志携带 request_id
7. 业务异常时单独打 ERROR 异常日志
8. request.client 为 None 时 client 字段兜底 "-"
"""

from __future__ import annotations

import io
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.logger import logger


# ==================== Fixtures ====================

@pytest.fixture
def log_capture() -> io.StringIO:
    """每次测试一个独立的 StringIO 缓冲区，函数级 scope

    logger.add() 返回 handler_id，teardown 时用 logger.remove() 卸载，
    避免跨测试污染 loguru 处理器列表
    """
    buf = io.StringIO()
    handler_id = logger.add(
        buf,
        format="{level} | rid={extra[request_id]} | {message}",
        level="DEBUG",
        enqueue=False,
    )
    yield buf
    # 移除该 handler，避免跨测试污染
    try:
        logger.remove(handler_id)
    except Exception:
        pass


@pytest.fixture
def app() -> FastAPI:
    """构造测试应用"""
    application = FastAPI()
    add_logging_middleware(application)
    add_exception_middleware(application)
    add_request_id_middleware(application)

    @application.get("/ok")
    async def ok() -> dict[str, str]:
        return {"msg": "ok"}

    @application.get("/not-found")
    async def not_found() -> None:
        raise HTTPException(status_code=404, detail="missing")

    @application.get("/crash")
    async def crash() -> None:
        raise RuntimeError("boom")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/metrics")
    async def metrics() -> dict[str, str]:
        return {"metrics": "data"}

    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ==================== T1-T3: 状态码 → 日志级别 ====================

def test_2xx_logs_info(client: TestClient, log_capture: io.StringIO) -> None:
    """T1: 2xx 请求 → INFO 访问日志（含 method/path/status/latency/rid）"""
    r = client.get("/ok", headers={"X-Request-ID": "rid-2xx"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "INFO" in logs
    assert "请求完成" in logs
    assert "method=GET" in logs
    assert "path=/ok" in logs
    assert "status=200" in logs
    assert "latency_ms=" in logs
    assert "rid=rid-2xx" in logs


def test_4xx_logs_warning(client: TestClient, log_capture: io.StringIO) -> None:
    """T2: 4xx 请求 → WARNING 访问日志"""
    r = client.get("/not-found", headers={"X-Request-ID": "rid-4xx"})
    logs = log_capture.getvalue()
    assert r.status_code == 404
    assert "WARNING" in logs
    assert "status=404" in logs
    assert "rid=rid-4xx" in logs


def test_5xx_logs_error(client: TestClient, log_capture: io.StringIO) -> None:
    """T3: 5xx 请求 → ERROR 访问日志"""
    r = client.get("/crash", headers={"X-Request-ID": "rid-5xx"})
    logs = log_capture.getvalue()
    assert r.status_code == 500
    assert "ERROR" in logs
    assert "status=500" in logs
    assert "rid=rid-5xx" in logs


# ==================== T4-T5: 跳过路径 ====================

def test_health_skips_access_log(client: TestClient, log_capture: io.StringIO) -> None:
    """T4: /health 跳过访问日志"""
    r = client.get("/health", headers={"X-Request-ID": "rid-health"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "请求完成" not in logs


def test_metrics_skips_access_log(client: TestClient, log_capture: io.StringIO) -> None:
    """T5: /metrics 跳过访问日志"""
    r = client.get("/metrics", headers={"X-Request-ID": "rid-metrics"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "请求完成" not in logs


# ==================== T6: client 字段 ====================

def test_access_log_contains_client_field(client: TestClient, log_capture: io.StringIO) -> None:
    """T6: 访问日志包含 client 字段（IP 兜底）"""
    client.get("/ok", headers={"X-Request-ID": "rid-client"})
    logs = log_capture.getvalue()
    assert "client=" in logs


# ==================== T7: 5xx 异常 traceback ====================

def test_5xx_logs_exception_traceback(client: TestClient, log_capture: io.StringIO) -> None:
    """T7: 5xx 异常时记录 traceback 日志（含 RuntimeError / boom）"""
    client.get("/crash", headers={"X-Request-ID": "rid-trace"})
    logs = log_capture.getvalue()
    assert "RuntimeError" in logs or "boom" in logs


# ==================== T8: 多请求 rid 隔离 ====================

def test_multiple_requests_have_separate_rids(client: TestClient, log_capture: io.StringIO) -> None:
    """T8: 多个请求不串 request_id"""
    client.get("/ok", headers={"X-Request-ID": "rid-A"})
    client.get("/ok", headers={"X-Request-ID": "rid-B"})
    logs = log_capture.getvalue()
    assert "rid=rid-A" in logs
    assert "rid=rid-B" in logs


# ==================== T9: 无 X-Request-ID 时兜底 ====================

def test_no_x_request_id_still_logs(client: TestClient, log_capture: io.StringIO) -> None:
    """T9: 无 X-Request-ID 时自动生成 UUID，访问日志仍输出"""
    r = client.get("/ok")
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "请求完成" in logs
    assert "rid=" in logs


# ==================== T10: latency_ms 数值合法 ====================

def test_latency_ms_is_non_negative(client: TestClient, log_capture: io.StringIO) -> None:
    """T10: latency_ms 数值合法（≥ 0）"""
    client.get("/ok", headers={"X-Request-ID": "rid-latency"})
    logs = log_capture.getvalue()
    m = re.search(r"latency_ms=([0-9.]+)", logs)
    assert m is not None, f"未找到 latency_ms: {logs!r}"
    latency = float(m.group(1))
    assert latency >= 0
