"""add created_at to applications

Revision ID: 73ba8bba4461
Revises: 5dfc58a234f1
Create Date: 2026-07-03 21:26:32.815443

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "73ba8bba4461"
down_revision: str | Sequence[str] | None = "5dfc58a234f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 为 applications 表补充 created_at 字段
    # 已有记录使用 now() 作为默认值，与 status_updated_at 初始值保持一致
    op.add_column(
        "applications",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("applications", "created_at")
