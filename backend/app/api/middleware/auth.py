"""鉴权中间件（JWT）

职责：
- 解析 Authorization: Bearer <token> 头
- 验证 JWT 签名 + 过期时间 + token 类型
- 注入用户信息到 request.state.user_id / request.state.user_payload
- 白名单路径（如 /api/auth/login）跳过鉴权
- 失败返回 401 + 统一错误格式

设计动机：
- 用 BaseHTTPMiddleware 而非纯 ASGI：
  · 与 rate_limit / logging 风格一致
  · BaseHTTPMiddleware 提供 request/response 对象，业务代码更直观
- 白名单用 prefix 匹配而非精确匹配：
  · /api/auth/* 整组端点（login/register/refresh/verify-code）需匿名访问
  · 写前缀比枚举每个端点更易维护（新增注册端点无需改中间件）
- 必须校验 token 类型（type=access）：
  · 防止 refresh token 被用于普通 API 调用
  · refresh 只能用于换发新 access，权限域不同
- 用户信息注入到 request.state：
  · 路由层直接 request.state.user_id 即可，无需 Depends
  · rate_limit 中间件通过 request.state.user_id 实现"按用户限流"（在 auth 之后注册时）

关键技术点：
- PyJWT 库做签名/验签/过期校验
- jwt.decode 显式传 algorithms=[settings.jwt_algorithm] 防止算法 confusion 攻击
  · 若传 None，客户端可伪造 alg=none 绕过签名校验
- 401 响应附带 WWW-Authenticate: Bearer 头（HTTP 标准）
- 异常细分：ExpiredSignatureError / InvalidSignatureError / InvalidTokenError
  · 全部归一为 401 + 模糊 detail（避免泄露"用户名不存在"vs"密码错误"类的枚举攻击）
- 失败用构造 JSONResponse 直接返回，而非 raise HTTPException：
  · 避免 BaseHTTPMiddleware + raise 路径下的响应头丢失问题（与 rate_limit 同模式）

潜在风险：
- 白名单过宽：攻击者访问 /api/auth/me 之类本应鉴权的端点
  → 白名单用 prefix 严格匹配；新增 /api/auth/* 路由前需评估是否应强制鉴权
- Token 泄露：JWT 无状态，泄露后无主动吊销手段
  → 当前靠短过期（15min）+ refresh token 机制缓解
  → 未来可加 Redis 黑名单：jti → revoked（不在本任务范围）
- jwt_secret_key 弱：被离线爆破 → 伪造任意用户 token
  → 由 settings 强校验 + 运维侧强制定期轮换（不在本任务范围）
- 失败 detail 区分"过期"vs"无效"：理论上泄露了 token 状态
  → 当前保留细分以便客户端做"自动 refresh"判断；安全敏感场景可统一为"认证失败"
"""

from typing import Final

import jwt
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidSignatureError,
    InvalidTokenError,
)
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.types import ASGIApp

from app.core.logger import get_request_id, logger
from app.core.settings import get_settings


# ==================== 常量 ====================

# 仅放行 access token，拒绝 refresh token 被用于普通 API
# refresh 应只用于 /api/auth/refresh 换发新 access，权限域不同
_EXPECTED_TOKEN_TYPE: Final[str] = "access"

# 401 响应业务错误码（与 exception.py 中 _HTTP_ERROR_CODE_MAP 401 → AUTH_001 一致）
_AUTH_ERROR_CODE: Final[str] = "AUTH_001"

# 鉴权失败响应头
_WWW_AUTHENTICATE: Final[str] = "WWW-Authenticate"

# Authorization 头名（小写，HTTP header 大小写不敏感）
_AUTH_HEADER: Final[str] = "authorization"

# Bearer 方案（标准："Bearer <token>"）
# 这里用小写形式用于大小写宽松比较；末尾空格保留
_BEARER_SCHEME: Final[str] = "bearer "

# 白名单路径前缀（这些路径下所有子路径跳过鉴权）
# 配合 _WHITELIST_EXACT 共同决定哪些端点可匿名访问
# 修改前请评估：新增 /api/auth/* 端点是否需要强制鉴权
_WHITELIST_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/refresh",
        "/api/auth/verify-code",
        "/api/auth/send-code",
    }
)

# 白名单精确路径（与 logging / rate_limit 中的跳过列表保持一致）
_WHITELIST_EXACT: Final[frozenset[str]] = frozenset(
    {
        "/health", "/healthz", "/ready", "/live", "/metrics",
        "/docs", "/redoc", "/openapi.json", "/favicon.ico",
    }
)


# ==================== 辅助函数 ====================

def _extract_bearer_token(request: Request) -> str | None:
    """从 Authorization 头提取 Bearer token

    支持大小写：HTTP header 名本身大小写不敏感，request.headers.get 已归一化
    支持前缀大小写：实际生产中 Bearer 也可能小写，这里做宽松匹配

    Args:
        request: FastAPI 请求对象

    Returns:
        token 字符串；缺失或格式错误返回 None
    """
    auth = request.headers.get(_AUTH_HEADER)
    if not auth:
        return None
    # 至少需要 "bearer " + 1 个字符
    if len(auth) <= len(_BEARER_SCHEME):
        return None
    # 大小写宽松：Bearer / bearer / BEARER 都接受
    if auth[:len(_BEARER_SCHEME)].lower() != _BEARER_SCHEME:
        return None
    # 去掉 Bearer 前缀（含其后的空格），再 strip 防止多空格
    token = auth[len(_BEARER_SCHEME):].strip()
    return token or None


def _is_whitelisted(path: str) -> bool:
    """判断路径是否在白名单中

    Args:
        path: 请求路径（不含 query string）

    Returns:
        True 表示跳过鉴权
    """
    if path in _WHITELIST_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _WHITELIST_PREFIXES)


def _decode_token(token: str) -> dict:
    """解码并验证 JWT

    校验内容：
    1. 签名正确性（HS256 / RS256 等，由 settings.jwt_algorithm 决定）
    2. 过期时间（exp 声明）
    3. token 类型（type 必须为 "access"）

    Args:
        token: 原始 JWT 字符串

    Returns:
        解码后的 payload dict

    Raises:
        ExpiredSignatureError: token 过期
        InvalidSignatureError: 签名错误
        DecodeError / InvalidTokenError: 其他无效
    """
    settings = get_settings()
    # 必须显式传 algorithms：传 None 时 PyJWT 接受任意算法
    # 攻击者可伪造 alg=none 绕过签名校验
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )

    # 校验 token 类型：仅放行 access
    token_type = payload.get("type")
    if token_type != _EXPECTED_TOKEN_TYPE:
        raise InvalidTokenError(f"token type 必须为 {_EXPECTED_TOKEN_TYPE!r}")

    return payload


def _build_unauthorized_response(detail: str, request_id: str) -> JSONResponse:
    """构造 401 鉴权失败响应

    响应体格式与项目统一错误响应保持一致：
    {error_code, detail, request_id}

    响应头：
    - WWW-Authenticate: Bearer（HTTP 标准，提示客户端使用 Bearer 认证方案）
    - X-Request-ID: 与 request_id 中间件透传一致

    Args:
        detail: 用户可读的错误描述
        request_id: 当前请求的 request_id

    Returns:
        401 JSONResponse
    """
    body: dict[str, str | int] = {
        "error_code": _AUTH_ERROR_CODE,
        "detail": detail,
        "request_id": request_id,
    }
    response = JSONResponse(status_code=401, content=body)
    response.headers[_WWW_AUTHENTICATE] = "Bearer"
    response.headers["X-Request-ID"] = request_id
    return response


# ==================== 中间件实现 ====================

class _AuthMiddleware(BaseHTTPMiddleware):
    """JWT 鉴权中间件

    工作流程（按失败短路组织）：
    1. 跳过白名单路径（OPTIONS 兜底 + login/register 等公开端点）
    2. 提取 Bearer token；缺失 → 401
    3. 解码 + 验签 + 过期 + 类型校验；失败 → 401
    4. 校验 sub 声明；缺失 → 401
    5. 注入 user_id / user_payload 到 request.state
    6. 放行下游
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # ---- 1. 跳过白名单 ----
        # OPTIONS 预检不应触发鉴权（CORS 中间件理论上已处理，这里兜底）
        if request.method == "OPTIONS":
            return await call_next(request)
        if _is_whitelisted(request.url.path):
            return await call_next(request)

        # 从 contextvars 读取 request_id，便于日志/响应头联动
        # 兜底 "-" 防止未注册 request_id 中间件时崩溃
        request_id = get_request_id() or "-"

        # ---- 2. 提取 Bearer token ----
        token = _extract_bearer_token(request)
        if not token:
            client = request.client.host if request.client else "-"
            logger.warning(
                "鉴权失败：缺少或格式错误的 Token | path={} | client={}",
                request.url.path, client,
            )
            return _build_unauthorized_response("缺少认证凭证", request_id)

        # ---- 3. 解码 + 校验 ----
        try:
            payload = _decode_token(token)
        except ExpiredSignatureError:
            logger.warning("鉴权失败：Token 已过期 | path={}", request.url.path)
            return _build_unauthorized_response("Token 已过期", request_id)
        except (InvalidSignatureError, DecodeError, InvalidTokenError) as exc:
            # 故意不打印 exc 详情：避免日志泄露 token 内容或解码堆栈
            logger.warning(
                "鉴权失败：Token 无效 | path={} | exc_type={}",
                request.url.path, type(exc).__name__,
            )
            return _build_unauthorized_response("认证失败", request_id)

        # ---- 4. 校验 sub 声明 ----
        user_id = payload.get("sub")
        if not user_id:
            logger.warning(
                "鉴权失败：Token 缺少 sub 声明 | path={}",
                request.url.path,
            )
            return _build_unauthorized_response("认证失败", request_id)

        # ---- 5. 注入用户信息 ----
        # 路由层 / 业务层可直接通过 request.state.user_id / .user_payload 访问
        # 同时供 rate_limit 中间件（按用户限流）使用
        request.state.user_id = str(user_id)
        request.state.user_payload = payload

        # ---- 6. 放行 ----
        return await call_next(request)


# ==================== 注册入口 ====================

def add_auth_middleware(app: FastAPI) -> None:
    """注册鉴权中间件到 FastAPI 应用

    Args:
        app: FastAPI 应用实例

    注册顺序说明（参考 main.py 注释）：
    - FastAPI add_middleware 是 LIFO：最后注册的中间件在最外层
    - 本函数应最后注册（在所有其他 add_*_middleware 之后调用），
      使 auth 作为最内层执行：先经过 CORS / logging / rate_limit
      再到 auth（最后一道关卡）
    - 当前 main.py 中的顺序：CORS → logging → exception → rate_limit → auth
      · 先按 IP 限流，避免恶意请求消耗认证资源
      · 再 JWT 鉴权，注入 user_id
    - 应在 add_request_id_middleware 之后注册：
      · 本中间件读 contextvars 中的 request_id（用于响应头 / 日志）
    - 重复注册同一中间件类会抛 RuntimeError，应避免
    """
    app.add_middleware(_AuthMiddleware)
    logger.info(
        "注册鉴权中间件 | whitelist_prefixes={} | whitelist_exact={} | expected_token_type={}",
        sorted(_WHITELIST_PREFIXES),
        sorted(_WHITELIST_EXACT),
        _EXPECTED_TOKEN_TYPE,
    )
