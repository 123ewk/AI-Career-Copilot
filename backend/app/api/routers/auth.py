"""Auth Router（注册 / 登录 / 刷新 Token）

职责：
- 暴露 /api/auth/register、/api/auth/login、/api/auth/refresh 三个公开端点
- 接收 HTTP 请求，做最薄的协议层适配（DTO 解析、Cookie 写入、状态码选择）
- 调用 UserService 处理业务逻辑，业务异常（ConflictError / AuthenticationError）
  由全局异常中间件统一翻译为 4xx JSON 响应

设计动机：
- Router 不做业务逻辑：Service 已封装，Router 只负责 HTTP 协议层
- refresh token 走 HttpOnly Cookie 而非响应体：
  · 规避 XSS 窃取（前端 JS 无法读取 httpOnly Cookie）
  · 浏览器自动回传到 path=/api/auth/refresh，免去前端手动管理
  · 配合 SameSite=Lax 防御 CSRF（跨站 POST 不会携带 Cookie）
- access token 走响应体：前端存内存（不存 localStorage），过期前不持久化
  · 15min 短过期，泄露影响有限
- Cookie 路径限制为 /api/auth/refresh：
  · Cookie 只在刷新端点回传，业务接口不会无意泄露 refresh token
  · 减少 Cookie 体积，缩小泄露面
- Cookie Secure 由 app_env 决定：dev 允许 HTTP（方便本地调试），prod 强制 HTTPS
- 状态码：register 返回 201 Created（资源创建），login/refresh 返回 200 OK

业务流程：
1. POST /register
   - 接收 UserRegisterRequest（DTO 层已校验邮箱格式/密码强度/二次确认）
   - 调 UserService.register → 创建用户 + 签发 access/refresh
   - 写 refresh_token 到 Set-Cookie
   - 返回 201 + TokenResponse

2. POST /login
   - 接收 UserLoginRequest（DTO 层仅做格式校验，不做强度校验）
   - 调 UserService.login → 验证密码 + 签发 access/refresh
   - 写 refresh_token 到 Set-Cookie
   - 返回 200 + TokenResponse

3. POST /refresh
   - 从 Cookie 读取 refresh_token（httponly，前端无法伪造）
   - 调 UserService.refresh_token → 解码 refresh + 查用户 + 旋转签发
   - 写新 refresh_token 到 Set-Cookie（旋转，旧 token 应失效）
   - 返回 200 + TokenResponse

潜在风险：
- refresh token 落 Cookie：CSRF 攻击可触发刷新
  → 缓解：SameSite=Lax（跨站 POST 不带 Cookie）；path 限制到 /api/auth/refresh
  → 强化：未来可加 CSRF token 二次校验（不在本任务范围）
- refresh token 泄露：JWT 无状态，泄露后无主动吊销
  → 缓解：短过期（7d）+ 旋转策略（每次刷新发新，旧应失效）
  → 当前 Service 层不维护黑名单，旧 refresh 在过期前仍可使用
- access token 落入日志：JWT 在 loguru 日志中可能泄露
  → 防御：不在路由层 logger 输出 access_token；Service 层日志只打印 user_id
- 注册接口无防爆破：攻击者可暴力注册测试账号
  → 缓解：配合 rate_limit 中间件（按 IP 限流）
  → 强化：未来可加图形验证码或邮箱验证码
- dev 环境 Secure=False：本地 HTTP 也能收发 Cookie
  → 防御：settings.app_env 强校验，prod 强制 HTTPS
"""

from typing import Final

from fastapi import APIRouter, Cookie, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.logger import logger
from app.core.settings import get_settings
from app.domain.user.models import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
)
from app.domain.user.service import UserService
from app.infra.database.postgres import get_db_session


# ==================== Router 实例 ====================

router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
    # 当前所有 auth 端点均为匿名访问；未来若新增需要鉴权的 auth 端点（如 /logout），
    # 单独打装饰器或新建 router，不要在中间件白名单里直接加 path
)


# ==================== 常量 ====================

# refresh token 在 Cookie 中的字段名
# 与前端约定的字段名，保持一致
_REFRESH_COOKIE_NAME: Final[str] = "refresh_token"

# Cookie 路径：限制 Cookie 只在刷新端点回传
# 业务接口（/api/users/* 等）不会无意带上 refresh token
# 浏览器规范：path 越精确，Cookie 体积越小
_REFRESH_COOKIE_PATH: Final[str] = "/api/auth/refresh"

# SameSite 策略：Lax 防御 CSRF
# - Strict：跨站 GET 也不带 Cookie，影响部分第三方登录跳转
# - Lax：跨站 GET 带，跨站 POST 不带（POST 刷新仍能正常工作）
# - None：完全不带 CSRF 防护，必须配合 Secure
_COOKIE_SAMESITE: Final[str] = "lax"


# ==================== 辅助函数 ====================

def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """将 refresh token 写入 HttpOnly Cookie

    设计要点：
    - httponly=True：前端 JS 无法读取（document.cookie 看不到），防 XSS 窃取
    - samesite="lax"：跨站 POST 不带 Cookie，防御 CSRF
    - path=/api/auth/refresh：Cookie 只在刷新端点回传，业务接口不会带
    - max_age=refresh 过期秒数：浏览器到时自动删除，无需后端清理
    - secure=app_env=="prod"：生产强制 HTTPS，本地 dev 允许 HTTP 调试

    Args:
        response: FastAPI Response 对象（通过 Depends 注入）
        refresh_token: 已签发的 refresh token JWT 字符串

    安全设计：
    - 不在 response body 中再返回 refresh_token：避免双通道泄露
    - 不在 logger 中输出 refresh_token 内容：避免日志泄露
    """
    settings = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=(settings.app_env == "prod"),
        samesite=_COOKIE_SAMESITE,
    )


def _build_token_response(
    user_response: object,
    access_token: str,
) -> TokenResponse:
    """构造 TokenResponse 响应

    TokenResponse 字段：
    - user: UserResponse（已剥离 password_hash）
    - access_token: JWT 字符串
    - token_type: 固定 "bearer"（OAuth 2.0 标准）
    - expires_in: access token 剩余秒数（由 models 默认值 900 填充）

    Args:
        user_response: UserResponse 实例
        access_token: 已签发的 access token

    Returns:
        TokenResponse 响应体
    """
    return TokenResponse(
        user=user_response,  # type: ignore[arg-type]
        access_token=access_token,
    )


# ==================== 端点：注册 ====================

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="用户注册",
    description=(
        "注册新用户。\n\n"
        "- 邮箱已归一化为小写\n"
        "- 密码已通过强度校验（DTO 层）\n"
        "- 成功返回 access token + refresh token（refresh 通过 Set-Cookie 设置）\n"
        "- 失败返回 409（邮箱已注册）"
    ),
)
async def register(
    request: UserRegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """用户注册端点

    流程：
    1. FastAPI 自动校验请求体为 UserRegisterRequest
       （邮箱格式 / 密码强度 / 二次确认 / 邮箱 local part 检查均在 DTO 层完成）
    2. 创建 UserService 并调用 register
    3. 将 refresh token 写入 HttpOnly Cookie
    4. 返回 201 + TokenResponse

    Args:
        request: 已通过 DTO 校验的注册请求
        response: FastAPI Response，用于设置 Cookie
        db: 请求级 AsyncSession（由 get_db_session 注入）

    Returns:
        TokenResponse: 包含 user 信息 + access_token

    Raises:
        ConflictError: 邮箱已被注册（409）
        ValidationError: DTO 校验失败（422，由 Pydantic 自动抛）
    """
    service = UserService(db)
    user_response, access_token, refresh_token = await service.register(request)

    # refresh token 写入 HttpOnly Cookie
    _set_refresh_cookie(response, refresh_token)

    logger.info(
        "注册端点完成 | user_id={} | email={}",
        user_response.id,
        user_response.email,
    )

    return _build_token_response(user_response, access_token)


# ==================== 端点：登录 ====================

@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="用户登录",
    description=(
        "邮箱 + 密码登录。\n\n"
        "- 成功返回 access token + refresh token（refresh 通过 Set-Cookie 设置）\n"
        "- 失败统一返回 401（不区分用户不存在 / 密码错误，防枚举）"
    ),
)
async def login(
    request: UserLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """用户登录端点

    流程：
    1. FastAPI 自动校验请求体为 UserLoginRequest（仅格式校验，不做强度校验）
    2. 创建 UserService 并调用 login
    3. 将 refresh token 写入 HttpOnly Cookie
    4. 返回 200 + TokenResponse

    Args:
        request: 登录请求（email + password）
        response: FastAPI Response，用于设置 Cookie
        db: 请求级 AsyncSession

    Returns:
        TokenResponse: 包含 user 信息 + access_token

    Raises:
        AuthenticationError: 邮箱不存在或密码错误（401）
    """
    service = UserService(db)
    user_response, access_token, refresh_token = await service.login(request)

    # refresh token 写入 HttpOnly Cookie
    _set_refresh_cookie(response, refresh_token)

    logger.info(
        "登录端点完成 | user_id={} | email={}",
        user_response.id,
        user_response.email,
    )

    return _build_token_response(user_response, access_token)


# ==================== 端点：刷新 Token ====================

@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="刷新 access token",
    description=(
        "使用 refresh token 换发新的 access token。\n\n"
        "- refresh token 从 HttpOnly Cookie 自动读取（前端无需手动管理）\n"
        "- 同时旋转 refresh token（Set-Cookie 覆盖）\n"
        "- 失败返回 401（refresh token 无效 / 过期 / 类型错）"
    ),
)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    refresh_token: str | None = Cookie(
        default=None,
        alias=_REFRESH_COOKIE_NAME,
        description="refresh token，从 HttpOnly Cookie 自动读取",
    ),
) -> TokenResponse:
    """刷新 access token 端点

    流程：
    1. 从 Cookie 读取 refresh_token（httponly，前端无法伪造）
    2. 调 UserService.refresh_token 验证 + 旋转签发
    3. 将新 refresh token 写入 HttpOnly Cookie（覆盖旧的）
    4. 返回 200 + TokenResponse

    Args:
        response: FastAPI Response，用于设置 Cookie
        db: 请求级 AsyncSession
        refresh_token: 从 Cookie 注入的 refresh token 字符串

    Returns:
        TokenResponse: 包含 user 信息 + 新的 access_token

    Raises:
        AuthenticationError: refresh token 缺失 / 无效 / 过期 / 类型错（401）
        ResourceNotFoundError: token 合法但用户已被删除（404）

    安全设计：
    - refresh token 仅从 Cookie 读取，不接受请求体传入：
      防止 CSRF 攻击者通过 JSON body 伪造 refresh token
    - 强制 type=refresh：拒绝用 access 换 access（Service 层保证）
    - 旋转策略：每次刷新都发新 refresh，旧的应在 7d 过期后失效
      （当前实现未维护黑名单，旧的仍可使用直至过期）
    """
    if not refresh_token:
        # Cookie 缺失：401 而非 400，因为从语义上是「未携带有效凭证」
        logger.warning("刷新失败：缺少 refresh token Cookie")
        raise AuthenticationError(detail="缺少 refresh token")

    service = UserService(db)
    user_response, new_access, new_refresh = await service.refresh_token(refresh_token)

    # 写入新 refresh token（旋转），覆盖旧 Cookie
    _set_refresh_cookie(response, new_refresh)

    logger.info("刷新端点完成 | user_id={}", user_response.id)

    return _build_token_response(user_response, new_access)


__all__ = ["router"]
