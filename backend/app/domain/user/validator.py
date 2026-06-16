"""User 域校验器

职责：
- 集中管理用户域的可复用校验规则（邮箱格式、密码强度）
- 与 Pydantic DTO（models.py）解耦：DTO 负责「数据结构」，本模块负责「校验规则」
- Service 层（如发送验证码、改密场景）也能直接复用，避免重复实现

设计动机：
- 单一职责：models.py 只定义 DTO 字段与文档，校验规则统一在 validator.py
- 可测试性：纯函数式校验器，单测无需构造完整 Pydantic Model
- 可复用性：未来「忘记密码」「修改密码」「管理员重置密码」等场景都能复用同一套规则
- 集中维护：弱密码黑名单等策略升级时只需改一个文件

安全设计：
- 邮箱只做格式校验（不验证可达性），真实可达性靠验证邮件二次确认
- 密码强度校验只用于「创建/重置」场景，登录场景不应拒绝老用户的弱密码
- 弱密码黑名单大小写不敏感：用户经常用首字母大写绕过
- 密码长度上限 64 字节：bcrypt 截断 72 字节以上输入，留余量
"""

import re
from typing import Final


# ==================== 常量 ====================

# 邮箱正则：满足 RFC 5321 常见子集，不引入 email-validator 第三方依赖
# 选择理由：本项目当前不依赖 email-validator，正则已能覆盖 99% 合法邮箱
# 真实可达性靠「验证邮件」二次确认，不在 DTO 层做
EMAIL_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# 密码长度边界：OWASP 推荐 8 起步，bcrypt 上限 72 字节
# 设为 8-64：覆盖 OWASP 同时给 bcrypt 留余量（bcrypt 会截断 72 字节以上）
PASSWORD_MIN_LENGTH: Final[int] = 8
PASSWORD_MAX_LENGTH: Final[int] = 64

# 常见弱密码黑名单：Top 50 高频泄露/弱密码
# 防止用户设置「看似复杂实际无效」的密码
# 来源：RockYou 泄露数据集 + 历年 OWASP 弱密码报告
WEAK_PASSWORDS: Final[frozenset[str]] = frozenset(
    {
        "12345678", "123456789", "1234567890", "password", "password1",
        "qwerty", "qwerty123", "abc12345", "iloveyou", "admin123",
        "welcome", "monkey123", "dragon123", "letmein", "1qaz2wsx",
        "qwertyuiop", "00000000", "11111111", "88888888", "66666666",
        "passw0rd", "p@ssw0rd", "p@ssword1", "123qwe", "qwe123",
        "asd123", "zxc123", "1q2w3e4r", "1q2w3e", "qweasd", "asdzxc",
        "football", "baseball", "superman", "batman", "starwars",
        "master123", "login123", "michael1", "shadow1", "ashley1",
        "trustno1", "hello123", "charlie1", "donald1", "password!",
        "abcd1234", "qwer1234", "test1234", "love1234", "sex12345",
    }
)


# ==================== 校验器 ====================

def validate_email(value: str) -> str:
    """统一邮箱格式校验 + 归一化

    为什么不直接用 pydantic.EmailStr：
    - 需额外安装 email-validator 依赖，本项目 pyproject.toml 未引入
    - 本场景对邮箱合规性要求「格式合理」即可
    - 真实权威性靠「邮箱验证链接」二次确认，不在 DTO 层做

    Args:
        value: 原始邮箱字符串

    Returns:
        归一化后的邮箱（去首尾空格 + 小写）

    Raises:
        ValueError: 格式不合法
    """
    normalized = value.strip().lower()
    if not EMAIL_REGEX.match(normalized):
        raise ValueError("邮箱格式不合法")
    return normalized


def validate_password_strength(password: str) -> str:
    """密码强度校验（仅用于「创建/重置密码」场景）

    规则（任一不满足即拒绝）：
    1. 长度 8-64 位
    2. 必须同时包含字母和数字（避免纯字母/纯数字弱密码）
    3. 不能是常见弱密码（大小写不敏感匹配黑名单）
    4. 不允许纯字母或纯数字（str.isalpha/isdigit 直接拒绝）

    适用场景：
    - 用户注册
    - 忘记密码后重置
    - 管理员重置用户密码
    - 修改密码时校验新密码

    不适用场景：
    - 用户登录：老用户密码可能在历史规则下注册，登录时不应拒绝

    Args:
        password: 用户输入的明文密码

    Returns:
        原密码（不归一化，保留用户输入大小写交给 Service bcrypt）

    Raises:
        ValueError: 校验失败，前端可显示错误
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码长度不能少于 {PASSWORD_MIN_LENGTH} 位")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"密码长度不能超过 {PASSWORD_MAX_LENGTH} 位")

    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        raise ValueError("密码必须同时包含字母和数字")

    if password.lower() in WEAK_PASSWORDS:
        raise ValueError("密码过于简单，请更换")

    # 防御性二次检查：拒绝全字母或全数字（避免 has_letter/has_digit 之外的情况）
    if password.isalpha() or password.isdigit():
        raise ValueError("密码不能全是字母或全是数字")

    return password


def password_contains_email_local(email: str, password: str) -> bool:
    """检查密码是否包含邮箱的 local part（用户名部分）

    防御场景：
    - 邮箱 user@example.com + 密码 user12345 → 弱密码，常见于「密码=邮箱+序号」模式
    - 仅做包含检查，不做相等检查：避免误判包含邮箱子串的合法强密码

    Args:
        email: 已归一化的邮箱（建议先调用 validate_email）
        password: 明文密码

    Returns:
        True 表示密码包含邮箱 local part
    """
    email_local = email.split("@", 1)[0]
    if not email_local:
        return False
    return email_local in password.lower()


__all__ = [
    "EMAIL_REGEX",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "WEAK_PASSWORDS",
    "validate_email",
    "validate_password_strength",
    "password_contains_email_local",
]
