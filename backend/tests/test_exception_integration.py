"""Exception 中间件端到端集成测试

测试覆盖：
1. AppException（业务/基础设施）走自定义 handler
2. RequestValidationError 拍平为统一格式
3. StarletteHTTPException 走 HTTP handler
4. 兜底 Exception 走 500 + SYS_000
5. dev 环境响应体附加 debug 字段
6. 4xx 响应携带 X-Request-ID 头
7. 5xx 响应携带 X-Request-ID 头
8. 错误响应不暴露敏感信息（traceback / 原始 detail）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.core.exceptions import (
    AppException,
    ValidationError,
    AuthenticationError,
    DatabaseError,
)


# ==================== 测试应用 ====================

app = FastAPI()
add_exception_middleware(app)
add_request_id_middleware(app)  # 注入 X-Request-ID


class _UserIn(BaseModel):
    name: str
    age: int


@app.get("/ok")
async def ok():
    """正常端点"""
    return {"msg": "ok"}


@app.get("/biz-4xx")
async def biz_4xx():
    """业务异常 400"""
    raise ValidationError(detail="邮箱格式错误", extra={"field": "email"})


@app.get("/biz-401")
async def biz_401():
    """业务异常 401"""
    raise AuthenticationError(detail="Token 已过期")


@app.get("/infra-500")
async def infra_500():
    """基础设施异常 500"""
    raise DatabaseError(detail="数据库连接失败", extra={"host": "pg-1"})


@app.get("/http-404")
async def http_404():
    """原生 HTTPException 404"""
    raise HTTPException(status_code=404, detail="资源不见了")


@app.get("/http-401-direct")
async def http_401_direct():
    """原生 HTTPException 401（映射表 AUTH_001）"""
    raise HTTPException(status_code=401, detail="请登录")


@app.post("/validate")
async def validate(data: _UserIn):
    """Pydantic 校验失败"""
    return {"name": data.name, "age": data.age}


@app.get("/crash")
async def crash():
    """未处理异常"""
    raise RuntimeError("boom")


# raise_server_exceptions=False 模拟生产模式
client = TestClient(app, raise_server_exceptions=False)


# ==================== 测试 ====================

def main() -> int:
    print("=" * 60)
    print("exception 中间件端到端集成测试")
    print("=" * 60)

    # ---- T1: 正常请求 200 ----
    print("\n[T1] 正常 GET /ok -> 200")
    r = client.get("/ok")
    assert r.status_code == 200, r.text
    assert r.json() == {"msg": "ok"}
    print("  PASS")

    # ---- T2: 业务异常 400 (ValidationError) ----
    print("\n[T2] ValidationError -> 400 | error_code=VAL_001")
    r = client.get("/biz-4xx")
    body = r.json()
    assert r.status_code == 400, body
    assert body["error_code"] == "VAL_001", body
    assert body["detail"] == "邮箱格式错误", body
    assert "request_id" in body, body
    # 响应体不应包含 extra（防止敏感调试信息泄露）
    assert "extra" not in body, body
    print(f"  PASS - 400 | error_code={body['error_code']} | detail={body['detail']!r}")

    # ---- T3: 业务异常 401 (AuthenticationError) ----
    print("\n[T3] AuthenticationError -> 401 | error_code=AUTH_001")
    r = client.get("/biz-401")
    body = r.json()
    assert r.status_code == 401, body
    assert body["error_code"] == "AUTH_001", body
    assert body["detail"] == "Token 已过期", body
    print(f"  PASS - 401 | error_code={body['error_code']}")

    # ---- T4: 基础设施异常 500 (DatabaseError) ----
    print("\n[T4] DatabaseError -> 500 | error_code=DB_001")
    r = client.get("/infra-500")
    body = r.json()
    assert r.status_code == 500, body
    assert body["error_code"] == "DB_001", body
    # AppException handler 直接使用 exc.detail（调用方传入的值）
    assert body["detail"] == "数据库连接失败", body
    # dev 模式带 extra 字段
    assert "debug" in body, body
    assert body["debug"]["extra"] == {"host": "pg-1"}, body
    print(f"  PASS - 500 | error_code={body['error_code']} | detail={body['detail']!r}")

    # ---- T5: Pydantic 校验失败 422 ----
    print("\n[T5] Pydantic 校验失败 -> 422 | error_code=VAL_001")
    # name 缺失 + age 类型错误
    r = client.post("/validate", json={"age": "not-a-number"})
    body = r.json()
    assert r.status_code == 422, body
    assert body["error_code"] == "VAL_001", body
    # detail 应包含首条错误 + 错误总数
    assert "项错误" in body["detail"], body
    assert "request_id" in body, body
    print(f"  PASS - 422 | error_code={body['error_code']} | detail={body['detail']!r}")

    # ---- T6: HTTPException 404 (映射表 RES_001) ----
    print("\n[T6] HTTPException(404) -> 404 | error_code=RES_001")
    r = client.get("/http-404")
    body = r.json()
    assert r.status_code == 404, body
    assert body["error_code"] == "RES_001", body
    assert body["detail"] == "资源不见了", body
    print(f"  PASS - 404 | error_code={body['error_code']}")

    # ---- T7: HTTPException 401 (映射表 AUTH_001) ----
    print("\n[T7] HTTPException(401) -> 401 | error_code=AUTH_001")
    r = client.get("/http-401-direct")
    body = r.json()
    assert r.status_code == 401, body
    assert body["error_code"] == "AUTH_001", body
    assert body["detail"] == "请登录", body
    print(f"  PASS - 401 | error_code={body['error_code']}")

    # ---- T8: HTTPException 405 (映射表 REQ_002) ----
    print("\n[T8] HTTPException(405) -> 405 | error_code=REQ_002")
    r = client.request("PATCH", "/http-404")
    body = r.json()
    assert r.status_code == 405, body
    assert body["error_code"] == "REQ_002", body
    print(f"  PASS - 405 | error_code={body['error_code']}")

    # ---- T9: 兜底未处理异常 -> 500 | SYS_000 ----
    print("\n[T9] RuntimeError -> 500 | error_code=SYS_000")
    r = client.get("/crash")
    body = r.json()
    assert r.status_code == 500, body
    assert body["error_code"] == "SYS_000", body
    assert body["detail"] == "服务内部错误", body
    # 不应泄露原始异常信息
    assert "boom" not in body["detail"], body
    assert "request_id" in body, body
    print(f"  PASS - 500 | error_code={body['error_code']} | detail={body['detail']!r}")

    # ---- T10: 异常响应携带 X-Request-ID ----
    print("\n[T10] 5xx 异常响应携带 X-Request-ID")
    custom_rid = "test-exception-rid-aaaa-bbbb"
    r = client.get("/crash", headers={"X-Request-ID": custom_rid})
    assert r.status_code == 500
    assert r.headers.get("x-request-id") == custom_rid, f"got {r.headers.get('x-request-id')!r}"
    assert r.json()["request_id"] == custom_rid
    print(f"  PASS - X-Request-ID={custom_rid}")

    # ---- T11: 4xx 响应也携带 X-Request-ID ----
    print("\n[T11] 4xx 响应也携带 X-Request-ID")
    r = client.get("/biz-401", headers={"X-Request-ID": "4xx-rid"})
    assert r.status_code == 401
    assert r.headers.get("x-request-id") == "4xx-rid"
    print(f"  PASS - X-Request-ID=4xx-rid")

    # ---- T12: dev 环境响应体包含 debug 字段 ----
    print("\n[T12] dev 环境响应体包含 debug 字段")
    r = client.get("/biz-4xx")
    body = r.json()
    # dev 模式下，AppException 应附加 debug
    assert "debug" in body, body
    assert body["debug"]["exc_type"] == "ValidationError", body
    assert body["debug"]["extra"] == {"field": "email"}, body
    print(f"  PASS - debug={body['debug']!r}")

    # ---- T13: dev 环境 5xx debug 字段含 traceback ----
    print("\n[T13] dev 环境 5xx 响应 debug.traceback 存在")
    r = client.get("/crash")
    body = r.json()
    assert "debug" in body, body
    assert "traceback" in body["debug"], body
    assert "RuntimeError" in body["debug"]["traceback"], body
    print(f"  PASS - traceback 长度={len(body['debug']['traceback'])}")

    # ---- T14: dev 环境 422 校验响应 debug.errors 存在 ----
    print("\n[T14] dev 环境 422 响应 debug.errors 存在")
    r = client.post("/validate", json={})
    body = r.json()
    assert "debug" in body, body
    assert "errors" in body["debug"], body
    assert isinstance(body["debug"]["errors"], list), body
    print(f"  PASS - errors 数量={len(body['debug']['errors'])}")

    # ---- T15: 错误响应无 WWW-Authenticate 头（auth 中间件的事）----
    print("\n[T15] exception handler 不注入 WWW-Authenticate（仅 auth 401 才需要）")
    r = client.get("/biz-401")
    assert "www-authenticate" not in {h.lower() for h in r.headers.keys()}
    print(f"  PASS")

    # ---- T16: AppException 子类（自定义 status_code）走子类的 status_code ----
    print("\n[T16] 自定义 AppException 子类映射到自定义状态码")

    class _CustomError(AppException):
        status_code = 418  # I'm a teapot
        error_code = "TEA_001"
        detail = "我是茶壶"

    @app.get("/custom-app-exc")
    async def custom_app_exc():
        raise _CustomError()

    r = client.get("/custom-app-exc")
    body = r.json()
    assert r.status_code == 418, body
    assert body["error_code"] == "TEA_001", body
    assert body["detail"] == "我是茶壶", body
    print(f"  PASS - 418 | error_code={body['error_code']}")

    print("\n" + "=" * 60)
    print("ALL_TESTS_OK")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
