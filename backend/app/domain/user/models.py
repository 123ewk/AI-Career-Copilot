"""User DTO / Schema（Pydantic v2）

职责：
- 定义用户域的 Pydantic Model，作为 API 层 ↔ Service 层之间的数据契约
- 入参（Request）做严格校验：注册时强校验密码复杂度，登录时仅做基本格式
- 出参（Response）只暴露公开字段，绝不泄露 password_hash 等敏感信息
- 具体校验规则（邮箱格式、密码强度、黑名单）由 validator.py 提供，本模块仅做组合调用

设计动机：
- DTO 与 ORM Model 分离：DTO 是 API 契约，ORM 是数据库映射
  · 防止 ORM 字段变动直接暴露给前端（强边界）
  · DTO 可按场景裁剪字段（注册响应 vs 列表摘要 vs 详情）
- DTO 与 Validator 分离：DTO 描述「数据结构」，Validator 描述「校验规则」
  · Service 层忘记密码 / 改密场景复用同一套 validator
  · 弱密码黑名单等策略升级只需改 validator.py
- 密码哈希由 Service 层负责（bcrypt），DTO 只接收明文
  · DTO 层不接触 bcrypt，避免算法细节泄露到 API 文档
- 响应中时间字段用 ISO 8601 字符串：前端无需处理 datetime 序列化
  · Pydantic v2 自动 datetime → ISO 8601 字符串

字段约束对齐（与 ORM Model 保持一致，参考 app/infra/database/models/user.py）：
- email: 1-320 字符，RFC 5321 最长邮箱地址限制
- name: 0-100 字符
- target_position / target_industry: 0-200 字符

安全设计：
- 响应模型（UserResponse）绝不暴露 password_hash
- 密码强度校验由 validator.py 提供：早失败，不浪费 Service/DB 资源
- 注册时密码不能包含邮箱的 local part：防止「密码 == 邮箱」类弱密码
- 登录 DTO 不做强度校验：老用户密码可能在历史规则下注册，不应在登录时拒绝

潜在风险：
- 响应忘记排除 password_hash：可能泄露密码哈希
  → 防御：UserResponse 不声明该字段，docstring 强调；ORM → DTO 转换走白名单
- 注册 DTO 包含明文密码：日志/异常链中可能泄露
  → 防御：Service 层 bcrypt 后立即丢弃明文；DTO 不参与日志输出（不要 log model_dump）
- email 验证未做「邮箱可达性」校验（验证链接）：DTO 层只做格式校验
  → 业务侧后续通过发送验证邮件二次确认
"""

import uuid
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.user.validator import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    password_contains_email_local,
    validate_email,
    validate_password_strength,
)


# ==================== 常量 ====================

# OAuth 2.0 Bearer 协议 token_type 固定值
_BEARER_TOKEN_TYPE: Final[str] = "bearer"

# Access token 过期时间（秒）：与 settings.jwt_access_token_expire_minutes 对齐
# 15min = 900s
_ACCESS_TOKEN_EXPIRE_SECONDS: Final[int] = 15 * 60


# ==================== 入参 DTO ====================

class UserRegisterRequest(BaseModel):
    """用户注册请求

    字段：
    - email: 登录邮箱，注册后不可改
    - password: 登录密码（明文，DTO 层强校验强度）
    - password_confirm: 二次输入密码，防止前端误输入
    - name: 姓名（可选）
    - target_position: 目标岗位（可选，Agent 推荐输入）
    - target_industry: 目标行业（可选，Agent 推荐输入）

    校验：
    - 邮箱格式 + 长度（归一化为小写）
    - 密码强度（DTO 层强校验，不合法直接 400）
    - password == password_confirm
    - 密码不能包含邮箱的 local part（防止「密码 = 邮箱」类弱密码）
    """

    model_config = ConfigDict(
        # 禁止额外字段，防止前端误传（如 password2、username 等）
        extra="forbid",
        # 字符串统一 strip 首尾空白（Pydantic v2 配置名）
        str_strip_whitespace=True,
    )

    email: str = Field(
        ...,
        min_length=3,
        max_length=320,
        description="登录邮箱，注册后不可改",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=(
            f"登录密码，长度 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 位，"
            "必须同时包含字母和数字"
        ),
    )
    password_confirm: str = Field(
        ...,
        description="二次确认密码，必须与 password 完全一致",
    )
    name: str | None = Field(
        default=None,
        max_length=100,
        description="姓名（可选）",
    )
    target_position: str | None = Field(
        default=None,
        max_length=200,
        description="目标岗位（可选，Agent 推荐策略输入）",
        examples=["AI应用开发工程师"],
    )
    target_industry: str | None = Field(
        default=None,
        max_length=200,
        description="目标行业（可选，Agent 推荐策略输入）",
        examples=["互联网"],
    )

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        """邮箱格式校验 + 归一化（委托 validator.py）"""
        return validate_email(value)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        """密码强度校验（委托 validator.py）"""
        return validate_password_strength(value)

    @model_validator(mode="after")
    def _check_password_consistency(self) -> "UserRegisterRequest":
        """password 与 password_confirm 一致性 + 防「密码 == 邮箱 local part」

        必须在 password 字段校验通过后执行（_check_password 先于 model_validator），
        避免「密码不一致」的错误覆盖「密码强度不足」的错误信息。
        """
        if self.password != self.password_confirm:
            raise ValueError("两次输入的密码不一致")

        if password_contains_email_local(self.email, self.password):
            raise ValueError("密码不能包含邮箱的用户名部分")

        return self


class UserLoginRequest(BaseModel):
    """用户登录请求

    设计：仅邮箱 + 密码登录
    - 项目 ORM 唯一索引为 email，登录查询走唯一索引 O(log N)
    - 简单清晰：避免「邮箱/手机号/用户名」多入口带来的复杂度
    - 后续若需手机号登录，新增独立 endpoint + 验证码流程

    校验策略（与注册不同）：
    - 密码不做强度校验：老用户密码可能在历史规则下注册，登录时不应拒绝
    - 密码仍校验 min_length=1：防止空字符串触发 ORM 异常
    - 密码仍校验 max_length：防止超长字符串触发 bcrypt 截断异常 / DOS
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    email: str = Field(
        ...,
        min_length=3,
        max_length=320,
        description="登录邮箱",
        examples=["user@example.com"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="登录密码",
    )

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        """登录时也校验格式：避免格式错误的请求浪费 DB 查询"""
        return validate_email(value)


# ==================== 出参 DTO ====================

class UserResponse(BaseModel):
    """用户公开响应

    设计要点：
    - 绝不暴露 password_hash
    - 绝不暴露内部审计字段（暂时无；新增 ORM 字段时需手动评估是否同步）
    - 时间字段 ISO 8601 字符串：Pydantic v2 自动 datetime → ISO 8601

    用途：
    - 登录/注册响应的 user 字段
    - GET /api/users/me 当前用户信息
    - GET /api/users/{id} 用户详情
    - 任何对外返回用户信息的场景
    """

    model_config = ConfigDict(
        # 支持从 ORM Model 创建：UserResponse.model_validate(orm_user)
        from_attributes=True,
        # 禁止额外字段：ORM 多了字段也不会被透传（白名单机制）
        extra="ignore",
    )

    id: uuid.UUID = Field(
        ...,
        description="用户 ID（UUID v4）",
    )
    email: str = Field(
        ...,
        description="登录邮箱",
    )
    name: str | None = Field(
        default=None,
        description="姓名",
    )
    target_position: str | None = Field(
        default=None,
        description="目标岗位",
    )
    target_industry: str | None = Field(
        default=None,
        description="目标行业",
    )
    created_at: datetime = Field(
        ...,
        description="注册时间（ISO 8601）",
    )
    updated_at: datetime = Field(
        ...,
        description="最后更新时间（ISO 8601）",
    )


class TokenResponse(BaseModel):
    """登录/注册成功响应

    字段约定：
    - user: 用户公开信息
    - access_token: 短期 access token（15min 过期），前端存内存（不存 localStorage）
    - token_type: 固定 "bearer"，符合 OAuth 2.0 标准
    - expires_in: access token 剩余秒数，前端据此实现自动刷新

    refresh_token 不在响应体中（安全设计）：
    - 由 router 层通过 Set-Cookie 设置为 httpOnly + Secure + SameSite=Lax
    - 前端 JS 无法读取，规避 XSS 窃取
    - Cookie 由浏览器自动回传 /api/auth/refresh
    - 避免落入「前端 localStorage + XSS = token 全泄露」反模式
    """

    model_config = ConfigDict(
        extra="forbid",
    )

    user: UserResponse = Field(
        ...,
        description="用户公开信息",
    )
    access_token: str = Field(
        ...,
        description="Access Token（JWT，15min 过期）",
    )
    token_type: str = Field(
        default=_BEARER_TOKEN_TYPE,
        description="Token 类型，固定 'bearer'，符合 OAuth 2.0",
    )
    expires_in: int = Field(
        default=_ACCESS_TOKEN_EXPIRE_SECONDS,
        ge=1,
        description="Access Token 剩余有效期（秒）",
    )


__all__ = [
    "UserRegisterRequest",
    "UserLoginRequest",
    "UserResponse",
    "TokenResponse",
]
