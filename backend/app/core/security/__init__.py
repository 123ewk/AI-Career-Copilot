"""核心层安全工具包

职责：
- 集中管理密码哈希、JWT 签发/校验等安全原语
- 为 Service 层提供统一的安全能力，避免业务代码直接 import 第三方库
- 与 settings.py 解耦：settings 只管配置，安全工具管算法细节

设计动机：
- 单一职责：service.py 不应 import bcrypt / jwt，应只调用本包接口
- 可替换：未来切换 argon2 / pwdlib 只需改本包，业务层零修改
- 易测试：安全工具作为纯函数（除时间侧信道外），便于单测覆盖

安全设计：
- 所有哈希接口强制接收 bytes/str 统一处理，避免「明文 vs 字节」混用
- 密码哈希走 bcrypt 成本因子 ≥ 12（OWASP 2023 推荐）
- JWT 签发显式指定 algorithm，校验时显式传入 algorithms 列表防 alg confusion
"""

from app.core.security.jwt import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.security.password import hash_password, verify_password


__all__ = [
    # JWT
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "TokenType",
    "TokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    # Password
    "hash_password",
    "verify_password",
]
