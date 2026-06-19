"""add business unique key to tasks for mq idempotency

Revision ID: a1b2c3d4e5f6
Revises: c528168c702d
Create Date: 2026-06-17 12:00:00.000000

任务表加业务幂等键：MQ 重投时通过 unique 约束保证恰好一次消费。

变更内容：
1. tasks.user_id：冗余自 sessions.user_id，用于 (user_id, business_id) 联合唯一索引
   · ON DELETE CASCADE：与 session_id 一致
   · 写入时由 Service 层从 session 同步（保证一致性）
2. tasks.business_id：业务方传入的稳定标识（NOT NULL）
   · 推荐命名：f"{task_type}:{business_key}"，如 "analyze_jd:job-uuid-123"
3. uq_tasks_user_business：(user_id, business_id) 联合唯一索引
   · MQ 重投（Publisher Confirms 竞态 / Consumer 崩溃在 commit 后 ACK 前）触发 unique 冲突
   · → IntegrityError → DuplicateMessageError → 消费者静默 ACK
4. ix_tasks_user_id：普通索引，按用户查任务列表（数据权限隔离场景）

幂等消费前置条件：
- 调用方必须在创建 Task 时传入 business_id
- 业务 ID 必须是稳定的：同一业务操作重试时 ID 必须一致
- Service 层必须在 INSERT 时从 session_id 同步 user_id（数据一致性）

回滚说明：
- down() 移除字段和索引
- 假设无现有数据：开发期添加，未做数据回填
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c528168c702d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级：加 business_id / user_id 字段 + 联合唯一索引"""
    # 1. 加 user_id 列（NOT NULL，FK users.id CASCADE）
    # 注意：开发期添加，假设 tasks 表为空或无历史脏数据
    # 如有历史数据，需先写 backfill SQL：UPDATE tasks SET user_id = ... FROM sessions WHERE ...
    op.add_column(
        "tasks",
        sa.Column(
            "user_id",
            sa.UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属用户ID（冗余自 sessions.user_id，用于业务幂等唯一约束）",
        ),
    )

    # 2. 加 business_id 列（NOT NULL）
    op.add_column(
        "tasks",
        sa.Column(
            "business_id",
            sa.String(100),
            nullable=False,
            comment="业务ID，业务方传入的稳定标识，MQ重投幂等键",
        ),
    )

    # 3. 加普通索引：按用户查任务列表
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    # 4. 加联合唯一索引：MQ 幂等消费的核心约束
    # 顺序 (user_id, business_id)：user_id 在前便于按用户范围查询/统计
    op.create_index(
        "uq_tasks_user_business",
        "tasks",
        ["user_id", "business_id"],
        unique=True,
    )


def downgrade() -> None:
    """降级：移除联合唯一索引 + 字段"""
    # 反向操作：先删索引（依赖列），再删列
    op.drop_index("uq_tasks_user_business", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_column("tasks", "business_id")
    op.drop_column("tasks", "user_id")
