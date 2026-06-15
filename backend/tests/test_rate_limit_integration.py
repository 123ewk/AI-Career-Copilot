"""限流中间件端到端集成测试

依赖：本地 Redis 服务在 localhost:6379 运行
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware.rate_limit import add_rate_limit_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.exception import add_exception_middleware
from app.infra.database.redis import redis_client_factory
from app.core.logger import setup_logging

setup_logging()

app = FastAPI()
# 注册顺序按 main.py 的约定
# FastAPI add_middleware 是 LIFO：最后注册的中间件在最外层
# 这里 add_request_id_middleware 必须最后注册，才能作为最外层
# 在所有其他中间件之前运行、注入 request_id
add_rate_limit_middleware(app)   # 最内层（先注册）
add_exception_middleware(app)    # add_exception_handler（不影响中间件顺序）
add_logging_middleware(app)      # 中间层
add_request_id_middleware(app)   # 最外层（最后注册，请求进入时最先跑）


@app.get("/ping")
async def ping():
    return {"msg": "pong"}


@app.get("/health")
async def health():
    return {"status": "ok"}


async def reset_keys() -> int:
    """清空限流键，测试间互不干扰"""
    client = redis_client_factory.client
    keys = []
    async for k in client.scan_iter(match="rl:*"):
        keys.append(k)
    if keys:
        await client.delete(*keys)
    return len(keys)


async def count_keys() -> list[str]:
    client = redis_client_factory.client
    return [k async for k in client.scan_iter(match="rl:*")]


async def main() -> int:
    print("=" * 60)
    print("rate_limit 端到端集成测试")
    print("=" * 60)

    cleared = await reset_keys()
    print(f"\n[setup] 清空 {cleared} 个限流键")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:

        # ---------- T1: 60 个请求全部通过 ----------
        print("\n[T1] 60 个连续请求全部 200")
        for i in range(60):
            r = await ac.get("/ping")
            assert r.status_code == 200, f"第 {i+1} 个请求失败: {r.status_code}"
            assert r.headers.get("x-ratelimit-limit") == "60"
            remaining = int(r.headers["x-ratelimit-remaining"])
            assert remaining == 60 - (i + 1), f"第 {i+1} 个剩余配额错误: {remaining}"
        print("  PASS - 60 个请求全部通过，配额逐次递减")

        # ---------- T2: 第 61 个请求 429 ----------
        print("\n[T2] 第 61 个请求触发 429")
        r = await ac.get("/ping")
        assert r.status_code == 429, f"应为 429，实际 {r.status_code}"
        body = r.json()
        assert body["error_code"] == "RATE_001", body
        assert "request_id" in body, body
        assert int(r.headers["x-ratelimit-remaining"]) == 0
        assert int(r.headers["retry-after"]) >= 1
        assert "x-request-id" in {h.lower() for h in r.headers.keys()}
        print(f"  PASS - 429 | error_code={body['error_code']} | Retry-After={r.headers['retry-after']}s")

        # ---------- T3: 健康检查不消耗配额 ----------
        print("\n[T3] 健康检查路径不消耗配额")
        for i in range(5):
            r = await ac.get("/health")
            assert r.status_code == 200
            assert "x-ratelimit-limit" not in {h.lower() for h in r.headers.keys()}
        print("  PASS - 健康检查 200，无 rate limit 头")

        # ---------- T4: 配额仍用尽 ----------
        print("\n[T4] /ping 仍 429")
        r = await ac.get("/ping")
        assert r.status_code == 429
        print("  PASS - /ping 仍 429")

        # ---------- T5: Redis 键存在且 TTL 正确 ----------
        print("\n[T5] Redis 键存在且 TTL 正确")
        keys = await count_keys()
        assert len(keys) >= 1, f"未发现限流键: {keys}"
        sample = keys[0]
        ttl = await redis_client_factory.client.ttl(sample)
        assert 0 < ttl <= 60, f"TTL 异常: {ttl}"
        print(f"  PASS - 限流键={sample[:40]}... | TTL={ttl}s")

        # ---------- T6: 限流响应包含 X-Request-ID ----------
        print("\n[T6] 限流响应包含 X-Request-ID")
        r = await ac.get("/ping")
        assert r.status_code == 429
        rid = r.headers.get("x-request-id")
        assert rid and rid != "-", f"X-Request-ID 缺失: {rid}"
        print(f"  PASS - X-Request-ID={rid[:8]}...")

        # ---------- T7: 响应头字段完整 ----------
        print("\n[T7] 限流响应头字段完整")
        r = await ac.get("/ping")
        assert r.status_code == 429
        required = ["x-ratelimit-limit", "x-ratelimit-remaining",
                    "x-ratelimit-reset", "retry-after", "x-request-id"]
        for h in required:
            assert h in {k.lower() for k in r.headers.keys()}, f"缺少响应头: {h}"
        print(f"  PASS - 响应头齐全: {required}")

        # ---------- T8: 清空后重新计数 ----------
        print("\n[T8] 清空 Redis 后窗口重置")
        cleared = await reset_keys()
        r = await ac.get("/ping")
        assert r.status_code == 200, f"清空后仍 {r.status_code}: {r.text}"
        assert r.headers.get("x-ratelimit-remaining") == "59"
        print(f"  PASS - 配额恢复（remaining=59，已清空 {cleared} 个键）")

    print("\n" + "=" * 60)
    print("ALL_TESTS_OK")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
