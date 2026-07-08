"""add salary_unit to jobs

为 jobs 表新增 salary_unit 列，存储 Boss 直聘薪资原始单位
（K / 元/天 / 元/时），便于海投模式保留原始信息。

Revision ID: e7f8a9b0c1d2
Revises: 73ba8bba4461
Create Date: 2026-07-05 23:30:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "73ba8bba4461"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: 新增 salary_unit 列"""
    # 为 jobs 表添加 salary_unit 列，存储薪资原始单位（K / 元/天 / 元/时）
    op.add_column(
        "jobs",
        sa.Column(
            "salary_unit",
            sa.String(50),
            nullable=True,
            comment="薪资单位（K / 元/天 / 元/时，仅记录用）",
        ),
    )


def downgrade() -> None:
    """Downgrade schema: 删除 salary_unit 列"""
    op.drop_column("jobs", "salary_unit")
