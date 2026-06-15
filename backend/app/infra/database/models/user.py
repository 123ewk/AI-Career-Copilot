"""User ORM Model

职责：
- 定义 users 表结构，映射 PRD §10.2 User 实体
- 声明索引策略，优化高频查询路径

设计动机：
- ORM Model 只关心数据库映射，不含业务逻辑，与 Domain Model 分离
- 密码字段使用 password_hash 命名，强调存储的是哈希值而非明文
- email 唯一索引：登录/注册场景下按 email 查询是最高频操作，B-tree 索引 O(log N)
- target_position / target_industry 普通索引：Agent 推荐策略需按目标岗位/行业筛选用户

索引设计理由：
- uq_users_email：唯一索引，保证邮箱不重复 + 加速登录查询
  失效场景：WHERE email LIKE '%@gmail.com' 前缀通配符无法走索引
- ix_users_target_position：普通索引，加速"按目标岗位筛选用户"查询
  失效场景：低基数列（大量相同值）时索引效果差，但岗位名称分散度高，适合索引
- ix_users_target_industry：普通索引，加速"按目标行业筛选用户"查询
  失效场景：同上，行业值分散度足够
- ix_users_created_at：时间索引，支持按注册时间排序/分页/统计
"""

import uuid
from datetime import datetime

from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class User(Base):
    """用户表 ORM Model

    字段与 PRD §10.2 一致：
    - id: UUID 主键，应用层生成（避免数据库序列争用）
    - email: 邮箱，唯一约束
    - password_hash: bcrypt 哈希后的密码
    - name: 用户姓名
    - target_position: 目标岗位，Agent 推荐策略的关键输入
    - target_industry: 目标行业，同上
    - created_at: 创建时间，数据库默认值
    - updated_at: 更新时间，数据库自动维护
    """

    __tablename__ = "users"

    # 主键：应用层生成 UUID，避免数据库序列在高并发下成为争用点
    # uuid4 基于 randomness，不保证时间递增，但分布式环境下无需中心化协调
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="用户ID，UUID v4",
    )

    # 邮箱：唯一约束 + 索引，登录/注册的核心查询字段
    # 长度 320 符合 RFC 5321 最长邮箱地址限制（local@domain 各 64/255 字节）
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        comment="邮箱，唯一，用于登录",
    )

    # 密码哈希：bcrypt 输出固定 60 字符，预留 255 以备算法升级
    # 永远不存储明文密码，Service 层负责 bcrypt.hash/bcrypt.verify
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="密码哈希（bcrypt）",
    )

    # 姓名：允许为空，用户可能先注册后完善信息
    name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="姓名",
    )

    # 目标岗位：Agent 推荐策略的关键输入，允许为空
    target_position: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="目标岗位，如 AI应用开发工程师",
    )

    # 目标行业：Agent 推荐策略的关键输入，允许为空
    target_industry: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="目标行业，如 互联网",
    )

    # 创建时间：数据库默认值，避免应用层时钟不一致
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    # 更新时间：数据库自动维护，每次 UPDATE 时刷新
    # 需 Alembic 迁移中创建触发器或应用层显式更新
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.now,
        comment="更新时间",
    )

    # 显式声明索引，集中管理而非散落在各字段定义中
    # 好处：索引策略一目了然，便于 DBA 审查
    __table_args__ = (
        # 登录/注册查询：WHERE email = ?，唯一索引保证邮箱不重复
        Index("ix_users_email", "email", unique=True),
        # Agent 推荐筛选：WHERE target_position = ?
        Index("ix_users_target_position", "target_position"),
        # Agent 推荐筛选：WHERE target_industry = ?
        Index("ix_users_target_industry", "target_industry"),
        # 注册时间排序/分页：ORDER BY created_at DESC
        Index("ix_users_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        """调试友好输出，避免泄露 password_hash"""
        return f"<User id={self.id} email={self.email!r}>"
