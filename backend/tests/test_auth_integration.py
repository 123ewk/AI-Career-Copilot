"""Auth 中间件端到端集成测试

依赖：
- PyJWT 2.x（项目 .venv 已装）
- 项目 .env 中应配置 JWT_SECRET_KEY / JWT_ALGORITHM
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt
import asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.middleware.auth import add_auth_middleware
from app.api.middleware.rate_limit import add_rate_limit_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.exception import add_exception_middleware
from app.core.logger import setup_logging
from app.core.settings import get_settings

setup_logging()

# ---- 构造测试应用 ----
app = FastAPI()
# 注册顺序按 main.py 约定
# FastAPI add_middleware 是 LIFO：最后注册的最外层
add_auth_middleware(app)          # 第一个注册 = 最内层
add_rate_limit_middleware(app)
add_exception_middleware(app)
add_logging_middleware(app)
add_request_id_middleware(app)    # 最后注册 = 最外层


# 三个端点：
# /api/auth/login - 白名单
# /api/auth/refresh - 白名单
# /api/me - 需鉴权
# /health - 精确白名单
@app.get("/api/auth/login")
async def login():
    return {"msg": "login ok"}


@app.post("/api/auth/refresh")
async def refresh():
    return {"msg": "refresh ok"}


@app.get("/api/me")
async def me(request: Request):
    # 业务层从 request.state 取用户
    return {
        "user_id": getattr(request.state, "user_id", None),
        "sub_in_payload": request.state.user_payload.get("sub") if hasattr(request.state, "user_payload") else None,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---- 构造测试 JWT ----
SETTINGS = get_settings()
SECRET = SETTINGS.jwt_secret_key
ALG = SETTINGS.jwt_algorithm


def make_token(*, sub: str = "user-123", token_type: str = "access",
               exp_delta: int = 3600, secret: str = None) -> str:
    """构造一个测试 JWT

    Args:
        sub: 主题（用户 ID）
        token_type: access / refresh
        exp_delta: 距当前时间的过期偏移（秒），负数表示已过期
        secret: 签名密钥，默认用 settings.jwt_secret_key
    """
    payload = {
        "sub": sub,
        "type": token_type,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_delta,
    }
    return jwt.encode(payload, secret or SECRET, algorithm=ALG)


# ==================== 测试用例 ====================

async def main() -> int:
    print("=" * 60)
    print("auth 中间件端到端集成测试")
    print("=" * 60)
    print(f"  ALG = {ALG}")
    print(f"  SECRET (前 8 字符) = {SECRET[:8]!r}...")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # ---- T1: 白名单路径（login）无需 token ----
        print("\n[T1] 白名单 /api/auth/login 无需 Token")
        r = await ac.get("/api/auth/login")
        assert r.status_code == 200, f"白名单应 200，实际 {r.status_code}"
        print(f"  PASS - status={r.status_code}")

        # ---- T2: 白名单路径（refresh）无需 token ----
        print("\n[T2] 白名单 /api/auth/refresh 无需 Token")
        r = await ac.post("/api/auth/refresh")
        assert r.status_code == 200
        print(f"  PASS - status={r.status_code}")

        # ---- T3: 精确白名单（/health） ----
        print("\n[T3] 健康检查 /health 无需 Token")
        r = await ac.get("/health")
        assert r.status_code == 200
        print(f"  PASS - status={r.status_code}")

        # ---- T4: 受保护路径无 token → 401 ----
        print("\n[T4] /api/me 无 Token → 401")
        r = await ac.get("/api/me")
        assert r.status_code == 401, f"应为 401，实际 {r.status_code}"
        body = r.json()
        assert body["error_code"] == "AUTH_001", body
        assert "request_id" in body, body
        assert r.headers.get("www-authenticate") == "Bearer"
        assert r.headers.get("x-request-id") is not None
        print(f"  PASS - 401 | error_code=AUTH_001 | WWW-Authenticate=Bearer")

        # ---- T5: Authorization 头格式错误 → 401 ----
        print("\n[T5] 错误格式 Authorization（缺 Bearer 前缀）→ 401")
        r = await ac.get("/api/me", headers={"Authorization": "abcdef.token.hijkl"})
        assert r.status_code == 401
        print(f"  PASS - 401 | detail={r.json()['detail']!r}")

        # ---- T6: token 签名错误 → 401 ----
        print("\n[T6] 错误签名 Token → 401")
        bad_token = make_token(secret="WRONG-SECRET-NOT-THE-REAL-ONE")
        r = await ac.get("/api/me", headers={"Authorization": f"Bearer {bad_token}"})
        assert r.status_code == 401
        body = r.json()
        assert body["error_code"] == "AUTH_001"
        print(f"  PASS - 401 | detail={body['detail']!r}")

        # ---- T7: token 过期 → 401 + 明确 detail ----
        print("\n[T7] 过期 Token → 401 | detail='Token 已过期'")
        expired_token = make_token(exp_delta=-60)  # 60 秒前就过期了
        r = await ac.get("/api/me", headers={"Authorization": f"Bearer {expired_token}"})
        assert r.status_code == 401
        body = r.json()
        assert body["detail"] == "Token 已过期", body
        print(f"  PASS - 401 | detail={body['detail']!r}")

        # ---- T8: refresh token 误用 → 401 ----
        print("\n[T8] refresh Token 用于普通 API → 401")
        refresh_token = make_token(token_type="refresh")
        r = await ac.get("/api/me", headers={"Authorization": f"Bearer {refresh_token}"})
        assert r.status_code == 401
        print(f"  PASS - 401 | detail={r.json()['detail']!r}")

        # ---- T9: 有效 access token → 200 + user_id 注入 ----
        print("\n[T9] 有效 Token → 200 + request.state.user_id 注入")
        valid_token = make_token(sub="user-42")
        r = await ac.get("/api/me", headers={"Authorization": f"Bearer {valid_token}"})
        assert r.status_code == 200, f"应为 200，实际 {r.status_code}: {r.text}"
        body = r.json()
        assert body["user_id"] == "user-42", body
        assert body["sub_in_payload"] == "user-42", body
        print(f"  PASS - 200 | user_id={body['user_id']!r}")

        # ---- T10: 大小写宽松：bearer 前缀 ----
        print("\n[T10] 大小写宽松：'bearer xxx' 也能通过")
        r = await ac.get("/api/me", headers={"Authorization": f"bearer {valid_token}"})
        assert r.status_code == 200, f"bearer 小写应通过，实际 {r.status_code}"
        print(f"  PASS - 200")

        # ---- T11: 401 响应包含 X-Request-ID ----
        print("\n[T11] 401 响应 X-Request-ID 与请求头透传一致")
        custom_rid = "test-correlation-id-aaaa-bbbb-cccc"
        r = await ac.get("/api/me", headers={
            "Authorization": "Bearer x",
            "X-Request-ID": custom_rid,
        })
        assert r.status_code == 401
        assert r.headers.get("x-request-id") == custom_rid, f"got {r.headers.get('x-request-id')!r}"
        print(f"  PASS - X-Request-ID={custom_rid!r}")

        # ---- T12: 缺失 sub 声明 → 401 ----
        print("\n[T12] Token 缺 sub 声明 → 401")
        token_no_sub = jwt.encode(
            {"type": "access", "exp": int(time.time()) + 3600},
            SECRET, algorithm=ALG,
        )
        r = await ac.get("/api/me", headers={"Authorization": f"Bearer {token_no_sub}"})
        assert r.status_code == 401
        print(f"  PASS - 401")

    print("\n" + "=" * 60)
    print("ALL_TESTS_OK")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
