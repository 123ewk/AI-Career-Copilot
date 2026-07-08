"""add analysis_result to jobs

Revision ID: 5dfc58a234f1
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03 20:02:20.545661

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "5dfc58a234f1"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 为 jobs 表添加 analysis_result 列，存储 Job Analysis Agent 的完整分析结果
    op.add_column(
        "jobs",
        sa.Column(
            "analysis_result",
            JSONB,
            nullable=True,
            comment="Job Analysis Agent 完整分析结果（JSONB）",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "analysis_result")
