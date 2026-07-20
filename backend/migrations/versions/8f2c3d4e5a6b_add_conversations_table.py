"""add conversations table

为沟通模块新增 conversations 表，存储用户与招聘方的多轮对话历史。
支持 BOSS 直聘等多渠道的聊天消息持久化。

Revision ID: 8f2c3d4e5a6b
Revises: e7f8a9b0c1d2
Create Date: 2026-07-18 10:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "8f2c3d4e5a6b"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: 创建 conversations 表"""
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="对话ID，UUID v4",
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="用户ID",
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
            comment="关联岗位ID（可选）",
        ),
        sa.Column(
            "recruiter_name",
            sa.String(100),
            nullable=False,
            comment="招聘方姓名",
        ),
        sa.Column(
            "recruiter_id",
            sa.String(200),
            nullable=True,
            comment="BOSS 平台招聘方用户ID（可选）",
        ),
        sa.Column(
            "channel",
            sa.String(50),
            server_default=sa.text("'boss'"),
            nullable=False,
            comment="渠道标识（boss/zhilian/liepin）",
        ),
        sa.Column(
            "messages",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="消息列表 JSONB: [{role, text, timestamp}]",
        ),
        sa.Column(
            "last_message_at",
            TIMESTAMP(timezone=True),
            nullable=True,
            comment="最后一条消息的时间",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
            comment="更新时间",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 索引
    op.create_index(
        "ix_conversations_user_id",
        "conversations",
        ["user_id"],
    )
    op.create_index(
        "ix_conversations_user_job",
        "conversations",
        ["user_id", "job_id"],
    )
    op.create_index(
        "ix_conversations_last_message_at",
        "conversations",
        ["last_message_at"],
    )


def downgrade() -> None:
    """Downgrade schema: 删除 conversations 表"""
    op.drop_index("ix_conversations_last_message_at", table_name="conversations")
    op.drop_index("ix_conversations_user_job", table_name="conversations")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
