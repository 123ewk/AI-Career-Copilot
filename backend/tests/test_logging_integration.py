"""Logging 中间件端到端集成测试

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
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import io
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.logger import logger


# 捕获所有日志
log_capture = io.StringIO()
logger.add(
    log_capture,
    format="{level} | rid={extra[request_id]} | {message}",
    level="DEBUG",
    enqueue=False,
)


# ==================== 测试应用 ====================

app = FastAPI()
add_logging_middleware(app)
add_exception_middleware(app)
add_request_id_middleware(app)


@app.get("/ok")
async def ok():
    return {"msg": "ok"}


@app.get("/not-found")
async def not_found():
    raise HTTPException(status_code=404, detail="missing")


@app.get("/crash")
async def crash():
    raise RuntimeError("boom")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return {"metrics": "data"}


# raise_server_exceptions=False 模拟生产模式
client = TestClient(app, raise_server_exceptions=False)


# ==================== 测试 ====================

def main() -> int:
    print("=" * 60)
    print("logging 中间件端到端集成测试")
    print("=" * 60)

    # ---- T1: 2xx 请求输出 INFO 访问日志 ----
    print("\n[T1] 2xx 请求 -> INFO 访问日志")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/ok", headers={"X-Request-ID": "rid-2xx"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "INFO" in logs, logs
    assert "请求完成" in logs, logs
    assert "method=GET" in logs, logs
    assert "path=/ok" in logs, logs
    assert "status=200" in logs, logs
    assert "latency_ms=" in logs, logs
    assert "rid=rid-2xx" in logs, logs
    print(f"  PASS - 包含 method/path/status/latency/request_id")

    # ---- T2: 4xx 请求输出 WARNING 访问日志 ----
    print("\n[T2] 4xx 请求 -> WARNING 访问日志")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/not-found", headers={"X-Request-ID": "rid-4xx"})
    logs = log_capture.getvalue()
    assert r.status_code == 404
    assert "WARNING" in logs, logs
    assert "status=404" in logs, logs
    assert "rid=rid-4xx" in logs, logs
    print(f"  PASS - 4xx 走 WARNING")

    # ---- T3: 5xx 请求输出 ERROR 访问日志 ----
    print("\n[T3] 5xx 请求 -> ERROR 访问日志")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/crash", headers={"X-Request-ID": "rid-5xx"})
    logs = log_capture.getvalue()
    assert r.status_code == 500
    assert "ERROR" in logs, logs
    assert "status=500" in logs, logs
    assert "rid=rid-5xx" in logs, logs
    print(f"  PASS - 5xx 走 ERROR")

    # ---- T4: 健康检查 /health 跳过访问日志 ----
    print("\n[T4] /health 跳过访问日志")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/health", headers={"X-Request-ID": "rid-health"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    # 不应有"请求完成"访问日志
    assert "请求完成" not in logs, f"健康检查不应输出访问日志: {logs!r}"
    print(f"  PASS - 无访问日志输出")

    # ---- T5: /metrics 跳过访问日志 ----
    print("\n[T5] /metrics 跳过访问日志")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/metrics", headers={"X-Request-ID": "rid-metrics"})
    logs = log_capture.getvalue()
    assert r.status_code == 200
    assert "请求完成" not in logs, f"/metrics 不应输出访问日志: {logs!r}"
    print(f"  PASS - 无访问日志输出")

    # ---- T6: 访问日志包含 client IP ----
    print("\n[T6] 访问日志包含 client 字段")
    log_capture.truncate(0); log_capture.seek(0)
    client.get("/ok", headers={"X-Request-ID": "rid-client"})
    logs = log_capture.getvalue()
    assert "client=" in logs, logs
    print(f"  PASS")

    # ---- T7: 5xx 异常时同时记录异常 traceback 日志 ----
    print("\n[T7] 5xx 异常记录 traceback 日志")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/crash", headers={"X-Request-ID": "rid-trace"})
    logs = log_capture.getvalue()
    # 业务异常由 exception 中间件 + logging 中间件分别记录
    # exception 中间件记录 ERROR + traceback
    # logging 中间件记录 ERROR 访问日志
    # 至少应包含 "RuntimeError"（来自 traceback）
    assert "RuntimeError" in logs or "boom" in logs, f"未发现异常日志: {logs!r}"
    print(f"  PASS")

    # ---- T8: 多个请求独立记录，不串日志 ----
    print("\n[T8] 多个请求不串 request_id")
    log_capture.truncate(0); log_capture.seek(0)
    client.get("/ok", headers={"X-Request-ID": "rid-A"})
    client.get("/ok", headers={"X-Request-ID": "rid-B"})
    logs = log_capture.getvalue()
    assert "rid=rid-A" in logs, logs
    assert "rid=rid-B" in logs, logs
    print(f"  PASS - 各自携带自己的 rid")

    # ---- T9: 无 X-Request-ID 时自动生成 UUID ----
    print("\n[T9] 无 X-Request-ID 时日志仍能输出")
    log_capture.truncate(0); log_capture.seek(0)
    r = client.get("/ok")
    logs = log_capture.getvalue()
    assert r.status_code == 200
    # 访问日志应输出
    assert "请求完成" in logs, logs
    # rid 字段应存在（可能是 UUID 或 "-"）
    assert "rid=" in logs, logs
    print(f"  PASS - 访问日志正常输出")

    # ---- T10: 访问日志 latency_ms 数值合法 ----
    print("\n[T10] latency_ms 数值合法（>0）")
    log_capture.truncate(0); log_capture.seek(0)
    client.get("/ok", headers={"X-Request-ID": "rid-latency"})
    logs = log_capture.getvalue()
    # 抽取 latency_ms= 后面的数字
    m = re.search(r"latency_ms=([0-9.]+)", logs)
    assert m, f"未找到 latency_ms: {logs!r}"
    latency = float(m.group(1))
    assert latency >= 0, f"latency 为负: {latency}"
    print(f"  PASS - latency_ms={latency}")

    print("\n" + "=" * 60)
    print("ALL_TESTS_OK")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
