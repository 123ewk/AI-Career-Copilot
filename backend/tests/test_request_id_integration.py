"""Test 2: RequestIDMiddleware 集成测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import io
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.middleware.request_id import add_request_id_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.exception import add_exception_middleware
from app.core.logger import logger, get_request_id

# 捕获日志,验证 rid 注入
log_capture = io.StringIO()
logger.add(log_capture, format="rid={extra[request_id]} | {message}", level="DEBUG", enqueue=False)

app = FastAPI()
add_logging_middleware(app)        # logging 在 request_id 之后注册
add_exception_middleware(app)
add_request_id_middleware(app)     # request_id 最后注册 -> 最外层

@app.get("/inspect")
def inspect(request: Request):
    """在路由层读 request.state.request_id / contextvars"""
    return {
        "state_rid": request.state.request_id,
        "ctx_rid": get_request_id(),
    }

@app.get("/log")
def log_endpoint():
    """业务日志验证 rid 自动注入"""
    logger.info("业务层日志")
    return {"ok": True}

@app.get("/not-found")
def not_found():
    raise HTTPException(status_code=404, detail="missing")

@app.get("/crash")
def crash():
    raise RuntimeError("boom")

# raise_server_exceptions=False 模拟生产模式
client = TestClient(app, raise_server_exceptions=False)


print("======== Test 1: 无 X-Request-ID 时自动生成 UUID ========")
log_capture.truncate(0); log_capture.seek(0)
r = client.get("/inspect")
data = r.json()
print("STATUS=", r.status_code)
print("HEADER=", r.headers.get("x-request-id"))
print("STATE_RID=", data["state_rid"])
print("CTX_RID=", data["ctx_rid"])
assert r.status_code == 200
assert re.match(r"^[0-9a-f]{8}-", r.headers.get("x-request-id"))
assert data["state_rid"] == r.headers.get("x-request-id"), "state 应等于 header"
assert data["ctx_rid"] == r.headers.get("x-request-id"), "ctx 应等于 header"
print("PASS")


print()
print("======== Test 2: 客户端传入合法的 X-Request-ID 透传 ========")
log_capture.truncate(0); log_capture.seek(0)
r = client.get("/inspect", headers={"X-Request-ID": "client-rid-12345"})
data = r.json()
print("HEADER=", r.headers.get("x-request-id"))
print("STATE_RID=", data["state_rid"])
assert r.headers.get("x-request-id") == "client-rid-12345"
assert data["state_rid"] == "client-rid-12345"
assert data["ctx_rid"] == "client-rid-12345"
# 业务日志也带上
logs = log_capture.getvalue()
assert "rid=client-rid-12345" in logs
print("PASS")


print()
print("======== Test 3: 非法 X-Request-ID 被拒绝,回退 UUID ========")
r = client.get("/inspect", headers={"X-Request-ID": "evil\nINJECT"})
data = r.json()
print("HEADER=", r.headers.get("x-request-id")[:12] + "...")
assert "\n" not in r.headers.get("x-request-id")
assert re.match(r"^[0-9a-f]{8}-", r.headers.get("x-request-id"))
assert data["state_rid"] == r.headers.get("x-request-id")
print("PASS")


print()
print("======== Test 4: 业务日志自动带上 rid ========")
log_capture.truncate(0); log_capture.seek(0)
r = client.get("/log", headers={"X-Request-ID": "log-trace"})
logs = log_capture.getvalue()
print("STATUS=", r.status_code)
print("LOG SNIPPET:")
for line in logs.strip().split("\n")[-3:]:
    print(" ", line)
assert r.status_code == 200
assert "业务层日志" in logs
assert "rid=log-trace" in logs
print("PASS")


print()
print("======== Test 5: 4xx 响应也有 X-Request-ID ========")
r = client.get("/not-found", headers={"X-Request-ID": "err-trace"})
print("STATUS=", r.status_code)
print("X-Request-ID=", r.headers.get("x-request-id"))
assert r.status_code == 404
assert r.headers.get("x-request-id") == "err-trace"
print("PASS")


print()
print("======== Test 6: 5xx 异常响应也带 X-Request-ID(纯 ASGI 优势) ========")
r = client.get("/crash", headers={"X-Request-ID": "crash-trace"})
print("STATUS=", r.status_code)
print("X-Request-ID=", r.headers.get("x-request-id"))
print("BODY=", r.json())
# 5xx 响应体的 request_id 字段(由 exception 中间件写入)
assert r.status_code == 500
# 关键验证: 纯 ASGI 包装的 send 让异常响应也带 X-Request-ID header
assert r.headers.get("x-request-id") == "crash-trace", "5xx 异常响应头应有 X-Request-ID"
# 响应体中的 request_id 与 header 一致
assert r.json()["request_id"] == "crash-trace"
print("PASS")


print()
print("======== Test 7: 多次请求 rid 互不污染 ========")
r1 = client.get("/inspect", headers={"X-Request-ID": "rid-A"})
r2 = client.get("/inspect", headers={"X-Request-ID": "rid-B"})
assert r1.json()["state_rid"] == "rid-A"
assert r2.json()["state_rid"] == "rid-B"
print("PASS")


print()
print("======== Test 8: request.state 写入不影响原有 state ========")
@app.get("/check-state")
def check_state(request: Request):
    # 确保 request.state 已有内容(由其他中间件)时不被覆盖
    request.state.user_id = 42
    return {
        "request_id": request.state.request_id,
        "user_id": request.state.user_id,
    }
r = client.get("/check-state", headers={"X-Request-ID": "state-test"})
data = r.json()
print("STATE=", data)
assert data["request_id"] == "state-test"
assert data["user_id"] == 42, "原有 state 字段不应被覆盖"
print("PASS")


print()
print("======== Test 9: 兼容 X-Correlation-ID ========")
r = client.get("/inspect", headers={"X-Correlation-ID": "corr-001"})
assert r.json()["state_rid"] == "corr-001"
print("PASS")


print()
print("======== Test 10: 兼容 X-Trace-ID ========")
r = client.get("/inspect", headers={"X-Trace-ID": "trace-001"})
assert r.json()["state_rid"] == "trace-001"
print("PASS")


print()
print("======== Test 11: 多个候选 header 存在时,按列表顺序取第一个 ========")
r = client.get(
    "/inspect",
    headers={
        "X-Request-ID": "primary",
        "X-Correlation-ID": "secondary",
    },
)
data = r.json()
print("STATE_RID=", data["state_rid"])
# ASGI headers 顺序由 client 决定,testclient 保持传入顺序
# 所以应该是 primary
assert data["state_rid"] == "primary"
print("PASS")


print()
print("======== Test 12: request_id 不会泄露到下一个请求 ========")
@app.get("/leak-check")
def leak_check():
    rid = get_request_id()
    return {"rid": rid}

# 第一次请求带 rid
r1 = client.get("/leak-check", headers={"X-Request-ID": "leak-test-1"})
# 第二次请求不带
r2 = client.get("/leak-check")
print("R1=", r1.json()["rid"])
print("R2=", r2.json()["rid"])
assert r1.json()["rid"] == "leak-test-1"
assert r2.json()["rid"] != "leak-test-1", "第二次请求不应残留上一次的值"
assert re.match(r"^[0-9a-f]{8}-", r2.json()["rid"])
print("PASS")


print()
print("ALL_INTEGRATION_TESTS_OK")
