"""Session ORM Model

职责：
- 定义 sessions 表结构，映射 PRD §4.2.5 sessions 实体
- 定义 SessionStatus 枚举，区分会话生命周期状态
- 声明索引策略，优化会话查询路径

设计动机：
- 会话是 Agent 与用户交互的上下文容器，一个会话内可执行多个任务
- status 使用 PG ENUM：会话状态有限且固定，ENUM 约束合法值
- WebSocket 连接通过 session_id 标识（PRD §12.1: /ws/agent/{session_id}）
- user_id 外键级联删除：用户注销时会话一并清理
- AgentMemory.session_id 引用此表，短期记忆随会话销毁而清理

会话生命周期：
- ACTIVE：会话活跃，用户可交互，Agent 可执行任务
- IDLE：会话空闲，无活跃任务，等待用户输入
- CLOSED：会话关闭，不可再操作，记忆已归档

索引设计理由：
- ix_sessions_user_id：按用户查会话列表，最高频查询
- ix_sessions_status：按状态筛选活跃/空闲会话
- ix_sessions_created_at：按创建时间排序
- ix_sessions_updated_at：按更新时间排序，支持清理过期会话
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class SessionStatus(StrEnum):
    """会话状态枚举

    状态流转：
    ACTIVE → IDLE → ACTIVE（用户再次交互）
         └──────→ CLOSED（用户关闭 / 超时）

    - ACTIVE：会话活跃，有 Agent 任务在执行或用户正在交互
    - IDLE：会话空闲，无活跃任务，等待用户输入
    - CLOSED：会话已关闭，不可再操作

    继承 StrEnum：序列化直接输出字符串，日志/API 可读
    """

    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    CLOSED = "CLOSED"


# PG ENUM 类型：会话状态枚举
# create_type=False：由 Alembic 迁移负责 CREATE TYPE
session_status_enum = ENUM(
    *[s.value for s in SessionStatus],
    name="session_status",
    create_type=False,
)


class Session(Base):
    """会话表 ORM Model

    字段基于 PRD §4.2.5 sessions 实体设计：
    - id: UUID 主键，WebSocket 路径参数（/ws/agent/{session_id}）
    - user_id: 所属用户，外键关联 users.id
    - status: 会话状态，PG ENUM
    - title: 会话标题，用户可自定义或由 Agent 自动生成
    - metadata_: 会话元数据，如来源页面、触发方式等
    - created_at: 创建时间
    - updated_at: 更新时间，用于检测过期会话
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="会话ID，UUID v4，WebSocket 路径参数",
    )

    # 所属用户：级联删除，用户注销时会话一并清理
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户ID",
    )

    # 会话状态：PG ENUM，约束合法值
    status: Mapped[SessionStatus] = mapped_column(
        session_status_enum,
        nullable=False,
        default=SessionStatus.ACTIVE,
        server_default="ACTIVE",
        comment="会话状态（ACTIVE/IDLE/CLOSED）",
    )

    # 会话标题：用户可自定义，或由 Agent 根据首次交互自动生成
    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="会话标题",
    )

    # 会话元数据：存储来源页面 URL、触发方式、客户端信息等
    # 使用 metadata_ 而非 metadata：SQLAlchemy Base.metadata 是保留属性，
    # 列名冲突会导致运行时错误，通过 column_metadata 显式映射 PG 列名为 metadata
    # 必须显式指定 JSONB 类型：dict | list | None 的 Union 类型无法被 SQLAlchemy 自动推断
    metadata_: Mapped[dict | list | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="会话元数据，如来源页面、触发方式",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    # 更新时间：每次会话状态变更时更新，用于检测过期会话
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.now,
        comment="更新时间",
    )

    __table_args__ = (
        # 按用户查会话列表：最高频查询路径
        Index("ix_sessions_user_id", "user_id"),
        # 按状态筛选：如查所有活跃会话
        Index("ix_sessions_status", "status"),
        # 创建时间索引：按创建时间排序
        Index("ix_sessions_created_at", "created_at"),
        # 更新时间索引：支持清理过期会话（WHERE updated_at < now() - interval '1 day'）
        Index("ix_sessions_updated_at", "updated_at"),
    )

    def __repr__(self) -> str:
        return f"<Session id={self.id} user_id={self.user_id} status={self.status.value}>"
