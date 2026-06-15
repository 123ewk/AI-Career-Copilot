"""Request ID 中间件

职责：
- 为每个 HTTP 请求生成或提取唯一 request_id
- 写入 contextvars，让同一协程内所有日志自动携带
- 写入 scope["state"]，路由层通过 request.state.request_id 访问
- 写入响应头 X-Request-ID，前后端可联动排查
- 校验客户端传入的 request_id，防止日志注入

设计动机：
- request_id 是横切关注点，从 logging 中间件剥离
  · 单独的中间件便于其他中间件（auth / rate_limit）复用
  · 单一职责原则：logging 只管"何时记什么"，request_id 只管"这个请求是谁"
- 纯 ASGI 而非 BaseHTTPMiddleware：
  · BaseHTTPMiddleware 用 anyio TaskGroup 包装会引入"raise 路径响应不可控"问题
  · 纯 ASGI 直接控制 send 回调，可在 http.response.start 阶段强制注入 header
- 客户端可传 X-Request-ID：
  · 实现跨服务调用链串联（前端 → 网关 → 后端 → 下游服务）
  · 但必须严格校验，否则被恶意伪造做日志注入

关键技术点：
- 使用 starlette.types.ASGIApp 协议编写纯 ASGI 中间件
- 校验 client 提供的 ID：仅允许字母数字与 _-. 字符集、长度 1-128
- contextvars 写入：set_request_id()，协程内 logger 自动透传
- 包装 send：在 http.response.start 消息上覆盖 X-Request-ID header
- 仅处理 http scope：lifespan / websocket 不需要 request_id

潜在风险：
- 客户端伪造超长 ID 撑爆日志索引 → 长度上限 128 字符
- 客户端注入换行符 / ANSI 转义污染日志格式 → 严格白名单校验，拒绝则回退 UUID
- 多次 add_middleware 调用顺序错乱 → 必须在 main.py 中最先注册（让其他中间件可读 rid）
- 5xx 异常响应可能丢失 X-Request-ID header → 由 exception.py 的 _build_error_response
  补写（不在本任务范围）
"""

import re
import uuid
from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.logger import logger, set_request_id


# ==================== 常量 ====================

# 允许的请求 ID header 名（小写，HTTP header 大小写不敏感）
# 兼容 W3C Trace Context / 阿里云 / Spring 等不同生态的字段名
_ALLOWED_HEADERS: Final[frozenset[str]] = frozenset(
    {"x-request-id", "x-correlation-id", "x-trace-id"}
)

# 客户端提供的 request_id 允许的字符集与长度
# 拒绝：换行符 / 制表符 / 控制字符 / SQL 元字符 / 中文 / Emoji
# 防止日志注入（log forging）与索引膨胀
_VALID_REQUEST_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_\-.]{1,128}$")

# 响应头名（小写，内部统一规范）
_RESPONSE_HEADER: Final[str] = "x-request-id"


# ==================== 辅助函数 ====================

def _extract_request_id(headers: list[tuple[bytes, bytes]]) -> str:
    """从 ASGI 原始 headers 中提取或生成 request_id

    提取优先级：x-request-id > x-correlation-id > x-trace-id
    校验失败或缺失：回退到 uuid.uuid4()

    Args:
        headers: ASGI scope["headers"]，形如 [(b"x-request-id", b"abc-123"), ...]

    Returns:
        经过校验或新生成的 request_id 字符串
    """
    for name, value in headers:
        name_lower = name.decode("latin-1").lower()
        if name_lower in _ALLOWED_HEADERS:
            # errors="ignore" 防止非 UTF-8 字节序列导致解码异常
            raw = value.decode("latin-1", errors="ignore").strip()
            if raw and _VALID_REQUEST_ID.match(raw):
                return raw
    return str(uuid.uuid4())


# ==================== 中间件实现 ====================

class RequestIDMiddleware:
    """Request ID 中间件（纯 ASGI 实现）

    工作流程：
    1. 仅处理 http scope（lifespan / websocket 透传）
    2. 提取 / 校验 / 生成 request_id
    3. 写入 contextvars（协程内 logger 自动透传）
    4. 写入 scope["state"]（Starlette 会自动映射到 request.state）
    5. 包装 send，在 http.response.start 阶段注入 X-Request-ID

    为什么用纯 ASGI 而非 BaseHTTPMiddleware：
    - BaseHTTPMiddleware 的 dispatch 在 raise 路径下无法控制响应，
      无法保证 5xx 响应也有 X-Request-ID
    - 纯 ASGI 直接拦截 send 消息，可以在 http.response.start 阶段
      强制写入 header，对所有经过此中间件的响应生效
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        # lifespan / websocket 等非 HTTP 场景不需 request_id，直接透传
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 1. 提取或生成 request_id
        request_id = _extract_request_id(scope["headers"])

        # 2. 写入 contextvars：业务日志（logger.info / 异常处理 / service 层）
        #    在同一协程内自动带上 request_id
        set_request_id(request_id)

        # 3. 写入 scope state：路由层可通过 request.state.request_id 访问
        #    Starlette 内部会把 scope["state"] 绑定到 Request.state
        #    必须用 setdefault 防止覆盖 Starlette / FastAPI 已初始化的 state
        scope.setdefault("state", {})["request_id"] = request_id

        # 4. 包装 send，在响应头中注入 X-Request-ID
        #    使用 MutableHeaders 可以正确处理 header 的 setdefault / 覆盖语义
        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                # MutableHeaders 基于 message["headers"] 工作
                # 重复设置同名 header 会覆盖,符合"以服务端为准"的语义
                headers = MutableHeaders(scope=message)
                headers[_RESPONSE_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ==================== 注册入口 ====================

def add_request_id_middleware(app: ASGIApp) -> None:
    """注册 Request ID 中间件到 ASGI 应用

    Args:
        app: FastAPI / ASGI 应用实例

    注册顺序要求（参考 main.py 注释）：
    - 必须在 logging / exception 之前注册：
      让后续中间件能直接读取 contextvars 中的 request_id
    - 应在 cors 之后注册：预检请求由 cors 短路，不消耗 request_id
      （实际上本中间件对 OPTIONS 也会设置 rid，但日志中间件会跳过 /health，
       所以两者互不冲突）
    - 重复注册会抛错，测试 setup/teardown 中需注意
    """
    app.add_middleware(RequestIDMiddleware)
    logger.info(
        "注册 Request ID 中间件 | allowed_headers={}",
        sorted(_ALLOWED_HEADERS),
    )
