"""限流中间件

职责：
- 基于 Redis 计数器实现 60 req/min/user 的固定窗口限流
- 限流键优先级：request.state.user_id（认证后） > 客户端 IP（匿名）
- 超限时返回 429 + 统一错误体 + Retry-After / X-RateLimit-* 头
- 注入 X-RateLimit-Limit / Remaining / Reset 响应头，便于前端做节流

设计动机：
- Redis 分布式计数：多 worker / 多实例部署时，本地计数器失效，
  必须共享存储。Redis 的 INCR 是 O(1) 原子操作，天然适合做计数器
- 固定窗口（fixed window）而非滑动窗口：
  · 实现简单，单次 Lua 脚本 INCR + EXPIRE 即可
  · 边界突刺（59s 末 + 60s 初）业务可接受；严格场景后续可改 ZSET
- Fail-open：Redis 异常时放行请求并记录告警日志
  · 避免 Redis 抖动让所有用户 503，影响业务可用性
  · 监控侧需配套 rate_limit_fail_open_total 指标告警
- 限流键选择：当前中间件在 auth 之前注册（见 main.py 注释），
  读不到 user_id，统一以客户端 IP 为 key；如未来调整为 auth 之后注册，
  可自动按用户 ID 限流（_get_client_key 已支持）

关键技术点：
- 使用 Lua 脚本保证 INCR + EXPIRE 原子性，避免首次写入时未设过期
- 使用 starlette.middleware.base.BaseHTTPMiddleware：
  · 匹配 logging.py 的风格，提供 request/response 对象
  · 429 响应自行构造 JSONResponse，无需走下游
- 跳过 OPTIONS 预检：浏览器已对预检结果独立缓存，不应消耗服务配额
- 跳过健康检查 / Swagger：监控 / 文档调用频繁，不计入用户配额

潜在风险：
- Redis 单点故障 → fail-open 已用 try/except 兜底 + 告警日志
- 客户端伪造 X-Forwarded-For 绕过 IP 限流：
  → 当前用 request.client.host（直连 IP），不读代理头
  → 生产环境应在网关层（nginx）设置 trusted proxy 后再信任
- 边界突刺：固定窗口在分钟切换点可能有 2x 流量
  → 业务可接受；严格场景改 Redis ZSET 实现
- 时间窗口时钟漂移：使用服务器本地 time.time()，多实例需 NTP 同步
"""

import time
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.types import ASGIApp

from app.core.logger import get_request_id, logger
from app.infra.database.redis import redis_client_factory


# ==================== 常量 ====================

# 默认限流配额：每窗口允许的请求数
# 对齐用户需求：60 req/min/user
_DEFAULT_LIMIT: Final[int] = 60

# 默认窗口大小（秒）
_DEFAULT_WINDOW_SECONDS: Final[int] = 60

# Redis 限流键前缀
# 命名空间隔离：避免与业务键（session: / cache: / lock:）冲突
_KEY_PREFIX: Final[str] = "rl"

# 跳过限流的路径
# 监控 / 探针 / 文档 / favicon 高频调用，不应消耗用户配额
_SKIP_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/health", "/healthz", "/ready", "/live", "/metrics",
        "/docs", "/redoc", "/openapi.json", "/favicon.ico",
    }
)

# 429 响应业务错误码
# 与 exception.py 中 _HTTP_ERROR_CODE_MAP 的 429 → RATE_001 保持一致
_RATE_LIMIT_ERROR_CODE: Final[str] = "RATE_001"


# ==================== Lua 脚本（原子 INCR + EXPIRE）====================

# 原子地递增计数并在首次写入时设置过期时间
# 为什么需要 Lua：
# · 单纯 INCR 不会设过期时间，key 永远不过期 → 内存泄漏
# · 单纯 EXPIRE 不会递增 → 计数错乱
# · Lua 在 Redis 中单线程执行，保证两步原子完成
#
# KEYS[1]: 限流键
# ARGV[1]: 窗口 TTL（秒）
# 返回: 递增后的当前计数
_INCR_SCRIPT: Final[str] = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


# ==================== 辅助函数 ====================

def _get_client_key(request: Request) -> str:
    """获取限流键的用户维度标识

    优先级：
    1. request.state.user_id（认证后由 auth 中间件注入）
    2. 客户端 IP（匿名请求）

    Args:
        request: FastAPI 请求对象

    Returns:
        限流键的用户维度标识（不含窗口 / 前缀）
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"

    # request.client 在 ASGI lifespan 阶段可能为 None，需要兜底
    if request.client and request.client.host:
        return f"ip:{request.client.host}"

    return "ip:unknown"


def _current_window_start(window: int) -> int:
    """计算当前固定窗口的起始时间戳（秒）

    例如 window=60 时，59.9s 和 0.1s 落在同一窗口，
    60.0s 进入下一个窗口。

    Args:
        window: 窗口大小（秒）

    Returns:
        窗口起始的 Unix 时间戳
    """
    return int(time.time()) // window * window


def _build_rate_limit_response(
    *,
    limit: int,
    remaining: int,
    reset_at: int,
    retry_after: int,
    request_id: str,
) -> JSONResponse:
    """构造 429 限流响应

    响应体格式与项目统一错误响应保持一致：
    {error_code, detail, request_id}

    响应头说明：
    - X-RateLimit-Limit: 窗口内允许的最大请求数
    - X-RateLimit-Remaining: 剩余配额（超限时为 0）
    - X-RateLimit-Reset: 窗口重置的 Unix 时间戳（秒）
    - Retry-After: 距窗口重置的秒数（HTTP 标准，CDN / 客户端自动识别）
    - X-Request-ID: 与 request_id 中间件透传的 ID 保持一致，便于排障
    """
    body: dict[str, str | int] = {
        "error_code": _RATE_LIMIT_ERROR_CODE,
        "detail": "请求过于频繁，请稍后重试",
        "request_id": request_id,
    }
    response = JSONResponse(status_code=429, content=body)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_at)
    response.headers["Retry-After"] = str(retry_after)
    response.headers["X-Request-ID"] = request_id
    return response


# ==================== 中间件实现 ====================

class _RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 Redis 的限流中间件

    工作流程：
    1. 跳过白名单路径（健康检查 / Swagger / OPTIONS 预检）
    2. 提取限流键（user_id 优先，回退 IP）
    3. Lua 脚本原子计数
    4. Redis 异常时 fail-open（放行 + 告警日志）
    5. 未超限：注入 X-RateLimit-* 响应头后放行
    6. 超限：直接返回 429，不再调用下游
    """

    def __init__(self, app: ASGIApp, limit: int, window: int) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # ---- 1. 跳过白名单 ----
        # OPTIONS 预检不应消耗用户配额（浏览器已缓存）
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        # ---- 2. 计算限流键与窗口 ----
        client_key = _get_client_key(request)
        window_start = _current_window_start(self.window)
        reset_at = window_start + self.window
        redis_key = f"{_KEY_PREFIX}:{client_key}:{window_start}"

        # ---- 3. 原子计数（Lua 脚本）----
        current_count = await self._atomic_incr(redis_key, self.window)

        # Redis 不可用 → fail-open：放行请求 + 告警日志
        # 业务可用性优先于严格的限流
        if current_count is None:
            return await call_next(request)

        remaining = max(0, self.limit - current_count)

        # ---- 4. 超限 → 429 ----
        if current_count > self.limit:
            retry_after = max(1, reset_at - int(time.time()))
            # 从 contextvars 读取 request_id（由 request_id 中间件注入）
            # 兜底 "-" 防止未注册 request_id 中间件时崩溃
            # 选用 contextvars 而非 request.state 的原因：
            # · 跨协程安全（限流后续可能 await 别的中间件）
            # · 与 exception.py 风格统一
            request_id = get_request_id() or "-"
            logger.warning(
                "触发限流 | key={} | count={} | limit={} | window={}s | path={}",
                client_key, current_count, self.limit, self.window, request.url.path,
            )
            return _build_rate_limit_response(
                limit=self.limit,
                remaining=0,
                reset_at=reset_at,
                retry_after=retry_after,
                request_id=request_id,
            )

        # ---- 5. 未超限：注入响应头后放行 ----
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response

    async def _atomic_incr(self, key: str, ttl: int) -> int | None:
        """原子 INCR + EXPIRE；Redis 异常时返回 None（触发 fail-open）

        Args:
            key: Redis 限流键
            ttl: 窗口 TTL（秒）

        Returns:
            递增后的计数；Redis 不可用时返回 None
        """
        try:
            client = redis_client_factory.client
            # eval 保证 INCR + EXPIRE 原子性，避免竞态
            result = await client.eval(_INCR_SCRIPT, 1, key, str(ttl))
            return int(result)
        except Exception as exc:
            # 任何 Redis 异常（连接超时 / 命令错误 / 序列化失败）
            # 都走 fail-open，避免 Redis 抖动导致全站 5xx
            logger.warning(
                "限流 Redis 调用失败，fail-open | exc_type={} | exc={}",
                type(exc).__name__, exc,
            )
            return None


# ==================== 注册入口 ====================

def add_rate_limit_middleware(app: FastAPI) -> None:
    """注册限流中间件到 FastAPI 应用

    Args:
        app: FastAPI 应用实例

    注册顺序说明（参考 main.py 注释）：
    - 应在 add_cors_middleware 之后注册：OPTIONS 预检由 CORS 短路
    - 应在 add_logging_middleware 之后注册：限流日志应携带 request_id
    - 应在 add_exception_middleware 之后注册：异常处理器内的日志能
      从 contextvars 中读到 request_id
    - 当前在 add_auth_middleware 之前注册：按客户端 IP 限流，
      避免恶意请求消耗认证资源；如需按 user_id 限流，
      应将本中间件注册到 add_auth_middleware 之后
    """
    app.add_middleware(
        _RateLimitMiddleware,
        limit=_DEFAULT_LIMIT,
        window=_DEFAULT_WINDOW_SECONDS,
    )
    logger.info(
        "注册限流中间件 | limit={} req/{}s | key_strategy=ip_or_user | fail_open=true",
        _DEFAULT_LIMIT, _DEFAULT_WINDOW_SECONDS,
    )
