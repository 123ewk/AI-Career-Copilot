"""JWT 签发与校验工具

职责：
- 封装 PyJWT 库，对 Service 层提供 create_access_token / create_refresh_token / decode_token 接口
- 集中管理 access / refresh 的差异：算法、过期时间、payload 字段、类型声明
- 与 settings.py 解耦：settings 读 .env，本模块用 settings 拼装 JWT

设计动机：
- 业务层不直接 import jwt：
  · 算法、过期时间、payload 字段变更只改本文件
  · 单测可以 monkeypatch 本模块的 create_* 函数
- access / refresh 必须区分：
  · 类型声明 (type=access / type=refresh)：auth 中间件已强制 access 拒绝 refresh
  · 过期时长不同：access 15min（短，减少泄露影响），refresh 7d（长，减少登录频次）
  · 用途不同：access 走 Authorization 头，refresh 走 httpOnly Cookie

安全设计：
- 强制指定 algorithm：签发时与 settings.jwt_algorithm 强绑定
- 校验时显式传 algorithms=[settings.jwt_algorithm]：
  · 防止 alg=none 攻击：攻击者伪造 alg=none 头绕过签名校验
  · 防止算法 confusion 攻击：HS256 公钥误用 RS256 验签
- payload 不含敏感信息：user_id 通过 sub 声明传递，email 不写入
  · JWT 一旦签发无法主动吊销（除非引入 jti 黑名单），写敏感信息会扩大泄露面
- 使用 settings.jwt_secret_key：密钥从环境变量注入，不硬编码

潜在风险：
- 密钥泄露：攻击者可签发任意 user_id 的 token
  → 防御：settings 启动时强校验密钥长度，运维侧定期轮换
- 密钥轮换期间旧 token 全部失效：用户体验差
  → 未来可加 kid 头支持多密钥并存（不在本任务范围）
- 撤销机制缺失：JWT 无状态，泄露后无主动吊销手段
  → 缓解：短过期 + refresh 机制
  → 未来可加 Redis 黑名单 jti → revoked（不在本任务范围）
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Final, Literal

import jwt

from app.core.settings import get_settings


# ==================== Token 类型常量 ====================

# Token 类型声明值：auth 中间件校验时会验证 type 字段
# access token 才能用于普通 API，refresh 只能用于 /api/auth/refresh
TOKEN_TYPE_ACCESS: Final[str] = "access"
TOKEN_TYPE_REFRESH: Final[str] = "refresh"
TokenType = Literal["access", "refresh"]


# ==================== Payload 声明键 ====================

# 标准 JWT 声明 + 自定义声明
_CLAIM_SUB: Final[str] = "sub"          # subject：用户 ID（UUID 字符串）
_CLAIM_EXP: Final[str] = "exp"          # 过期时间（Unix 时间戳，PyJWT 自动写入）
_CLAIM_IAT: Final[str] = "iat"          # 签发时间（Unix 时间戳，PyJWT 自动写入）
_CLAIM_TYPE: Final[str] = "type"        # token 类型：access / refresh
_CLAIM_JTI: Final[str] = "jti"          # JWT ID：唯一标识，便于未来吊销


# ==================== 签发 ====================

def _build_expires_delta(
    *,
    access: bool,
) -> timedelta:
    """根据 token 类型获取过期时长

    Args:
        access: True 取 access 过期时间，False 取 refresh 过期时间

    Returns:
        timedelta 对象
    """
    settings = get_settings()
    if access:
        return timedelta(minutes=settings.jwt_access_token_expire_minutes)
    return timedelta(days=settings.jwt_refresh_token_expire_days)


def _create_token(
    *,
    user_id: uuid.UUID,
    token_type: TokenType,
) -> str:
    """签发 token 内部实现

    行为：
    - 自动写入 iat / exp 声明（PyJWT 内部用 datetime.now + exp 算 exp 时间戳）
    - 强制 algorithm=settings.jwt_algorithm
    - payload 最小化：sub + type + jti，无 email / name 等业务字段

    Args:
        user_id: 用户 UUID
        token_type: 'access' 或 'refresh'

    Returns:
        JWT 字符串
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    expires_delta = _build_expires_delta(access=(token_type == TOKEN_TYPE_ACCESS))

    payload: dict[str, str | int] = {
        _CLAIM_SUB: str(user_id),
        _CLAIM_IAT: int(now.timestamp()),
        _CLAIM_TYPE: token_type,
        # jti 用于未来 token 黑名单（Redis 存 jti → revoked 映射）
        _CLAIM_JTI: uuid.uuid4().hex,
    }
    # PyJWT 会自动写入 exp，但需要传 expires_delta 才会计算
    # 也可以直接传 exp=int((now + delta).timestamp())，效果一致
    payload[_CLAIM_EXP] = int((now + expires_delta).timestamp())

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_access_token(user_id: uuid.UUID) -> str:
    """签发 access token

    Args:
        user_id: 用户 UUID

    Returns:
        JWT 字符串（15min 过期，type=access）

    使用场景：
    - 登录成功后返回给前端
    - refresh 成功后换发新 access
    """
    return _create_token(user_id=user_id, token_type=TOKEN_TYPE_ACCESS)


def create_refresh_token(user_id: uuid.UUID) -> str:
    """签发 refresh token

    Args:
        user_id: 用户 UUID

    Returns:
        JWT 字符串（7d 过期，type=refresh）

    使用场景：
    - 登录成功后通过 Set-Cookie 设置（httpOnly，浏览器自动回传）
    - 不会出现在响应体中（详见 models.TokenResponse docstring）
    """
    return _create_token(user_id=user_id, token_type=TOKEN_TYPE_REFRESH)


# ==================== 校验 ====================

class TokenError(Exception):
    """token 校验失败（签名错、过期、类型错）

    设计：与 jwt.exceptions 平级但不直接继承，避免在 Service 层
    依赖 jwt 库的具体异常类型，便于未来替换算法。
    """


def decode_token(
    token: str,
    *,
    expected_type: TokenType,
) -> dict:
    """解码并校验 token

    校验内容：
    1. 签名正确性（HS256 等）
    2. 过期时间（exp 声明）
    3. 类型匹配（type 必须等于 expected_type）
    4. sub 必须存在（用户身份标识）

    Args:
        token: JWT 字符串
        expected_type: 'access' 或 'refresh'，决定接受哪种 token

    Returns:
        payload dict，至少包含 sub / type / exp / iat / jti

    Raises:
        TokenError: 校验失败（签名错、过期、类型错、sub 缺失）
    """
    settings = get_settings()
    try:
        # 必须显式传 algorithms：传 None 时 PyJWT 接受任意算法
        # 攻击者可伪造 alg=none 绕过签名校验
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token 已过期") from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenError("token 签名无效") from exc
    except jwt.InvalidTokenError as exc:
        # DecodeError / InvalidAlgorithmError / MissingRequiredClaimError 等
        raise TokenError("token 无效") from exc

    # 校验类型声明
    token_type = payload.get(_CLAIM_TYPE)
    if token_type != expected_type:
        raise TokenError(f"token 类型错误，期望 {expected_type!r}")

    # 校验 sub 声明：必须存在
    if not payload.get(_CLAIM_SUB):
        raise TokenError("token 缺少 sub 声明")

    return payload


__all__ = [
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "TokenType",
    "TokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]
