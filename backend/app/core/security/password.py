"""密码哈希工具

职责：
- 封装 bcrypt.hashpw / bcrypt.checkpw，向上提供 hash_password / verify_password 两个接口
- 集中管理成本因子（rounds），避免散落在 Service 层各处

设计动机：
- 业务层不直接 import bcrypt：
  · 算法细节（成本因子、盐长度、编码）变更时只改本文件
  · 测试时便于 monkeypatch 替换为快速哈希
- 哈希结果统一使用字符串（db 字段 password_hash: String(255)）：
  · bcrypt 输出是 bytes，业务层 ORM 字段是 str
  · 本模块负责 bytes ↔ str 转换

安全设计：
- 成本因子默认 12：OWASP 2023 推荐值，PBKDF2-SHA256 / bcrypt 至少 600ms / 哈希
  · 12 轮 bcrypt 在普通服务器上约 250-400ms，足够抵抗离线爆破
  · 4 轮增量会让哈希时间翻倍
  · 高敏感场景可调到 13-14，需在 settings 中提供可调项
- 密码长度上限 64 字节：bcrypt 实际截断在 72 字节，留 8 字节余量防边界 bug
  · 由 validator.PASSWORD_MAX_LENGTH 强校验，本模块做防御性二次截断
- 永远不做密码回显、永远不记录哈希结果：
  · 哈希包含 salt，记录后无安全收益且增加泄露面

潜在风险：
- 成本因子硬编码：未来升级算法需修改本文件 + 数据库迁移
  → 防御：bcrypt 自带前缀 $2b$12$，未来可识别旧哈希并按需 rehash
- verify_password 未做时间归一：成功 / 失败耗时差异可能泄露「用户是否存在」
  → bcrypt.checkpw 实际耗时主要由 cost 决定，与是否成功无关，差异极小可忽略
- 同步 bcrypt 阻塞 Event Loop：单次哈希约 250ms，会卡住当前协程
  → 当前规模可接受；高并发场景应改用 passlib + thread pool
"""

import bcrypt

from app.domain.user.validator import PASSWORD_MAX_LENGTH


# bcrypt 成本因子：OWASP 2023 推荐 ≥ 12
# 修改此值需同步评估：哈希时间翻倍 → 登录/注册接口延迟变化
_BCRYPT_ROUNDS: int = 12


def hash_password(plain_password: str) -> str:
    """对明文密码做 bcrypt 哈希

    行为：
    - 内部使用 bcrypt 自动生成 salt（16 字节随机）
    - 编码：明文与盐均按 UTF-8 编码，bcrypt 限制 72 字节
    - 输出：bcrypt 完整字符串（含算法前缀、盐、哈希值），可直接存入 DB

    Args:
        plain_password: 用户明文密码（已通过 validator 强度校验）

    Returns:
        bcrypt 哈希字符串，格式如 "$2b$12$<22-char-salt><31-char-hash>"，共 60 字符

    注意：
    - 防御性截断到 64 字节：与 validator.PASSWORD_MAX_LENGTH 对齐
    - 实际 bcrypt 限制 72 字节，留 8 字节余量防止 UTF-8 边界 bug
    - 不传 bytes 强校验：上游 validator 已保证是 str
    """
    # bytes 编码：bcrypt 强制要求 bytes 输入
    # 截断 64 字节防御：即便 validator 被绕过也不会让 bcrypt 抛异常
    password_bytes = plain_password.encode("utf-8")[:PASSWORD_MAX_LENGTH]

    # 生成 salt + 哈希：bcrypt.hashpw 内部完成，salt 长度 16 字节
    # 每次调用 salt 不同，相同密码哈希结果不同（彩虹表失效）
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password_bytes, salt)

    # decode 回 str 便于 ORM 字段存储（password_hash: String(255)）
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配

    行为：
    - 内部按相同编码规则处理 plain_password
    - 恒定时间比较：bcrypt.checkpw 内部使用 hmac.compare_digest 防时序攻击
    - 即使密码错误也不抛异常：业务层只需 true / false

    Args:
        plain_password: 用户输入的明文密码
        password_hash: DB 中存储的 bcrypt 哈希字符串

    Returns:
        True 表示密码正确，False 表示密码错误

    异常：
    - 正常情况不抛异常
    - 若 password_hash 不是合法 bcrypt 字符串（DB 损坏），bcrypt.checkpw 抛 ValueError
      → 本模块不 catch：让 Service 层处理 DB 异常，翻译为 DatabaseError
    """
    # 防御性截断：与 hash_password 行为一致
    password_bytes = plain_password.encode("utf-8")[:PASSWORD_MAX_LENGTH]

    # bcrypt 接受 str 或 bytes，统一转 bytes
    hash_bytes = password_hash.encode("utf-8")

    return bcrypt.checkpw(password_bytes, hash_bytes)


__all__ = [
    "hash_password",
    "verify_password",
]
