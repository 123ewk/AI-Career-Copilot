"""Conversation ORM Model

职责：
- 定义 conversations 表结构，映射多轮对话历史
- 支持 BOSS 直聘等多渠道的聊天消息持久化
- JSONB messages 字段存储消息列表，支持灵活查询

设计动机：
- messages 使用 JSONB 而非独立 messages 表：
  消息列表是全量快照（DOM 提取），非增量追加；
  单条对话消息量有限（通常 < 100 条），JSONB 足够
- user_id + job_id + recruiter_name 联合定位：
  同一用户可能对同一岗位有多个 HR 联系，
  但同一 HR 对同一岗位只有一条对话记录
- channel 字段预留多平台扩展（boss/zhilian/liepin）

索引设计理由：
- ix_conversations_user_id：按用户查询对话列表
- ix_conversations_user_job：复合索引，按用户+岗位查询关联对话
- ix_conversations_last_message_at：按最后消息时间排序
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class Conversation(Base):
    """对话历史表 ORM Model

    存储用户与招聘方的多轮对话记录。
    messages 字段为 JSONB 数组，每条消息包含 role/text/timestamp。
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="对话ID，UUID v4",
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="用户ID",
    )

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联岗位ID（可选）",
    )

    recruiter_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="招聘方姓名",
    )

    recruiter_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="BOSS 平台招聘方用户ID（可选）",
    )

    channel: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'boss'"),
        comment="渠道标识（boss/zhilian/liepin）",
    )

    messages: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="消息列表 JSONB: [{role, text, timestamp}]",
    )

    last_message_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="最后一条消息的时间",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="更新时间",
    )

    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
        Index("ix_conversations_user_job", "user_id", "job_id"),
        Index("ix_conversations_last_message_at", "last_message_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} recruiter={self.recruiter_name!r} "
            f"channel={self.channel!r}>"
        )
