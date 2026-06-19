"""Task ORM Model

职责：
- 定义 tasks 表结构，映射 PRD §4.2.5 tasks 实体
- 定义 TaskStatus 枚举，区分异步任务生命周期状态
- 声明索引策略，优化任务查询与状态跟踪路径

设计动机：
- Task 是 Agent 异步执行的最小单元，一个 Session 内可包含多个 Task
- status 使用 PG ENUM：任务状态有限且固定，ENUM 约束合法值
- result 使用 JSONB：任务结果结构多样（分析结果/匹配分数/生成内容），JSONB 灵活存储
- error_message 允许 NULL：仅 FAILED 状态时填充，便于排查问题
- session_id 外键级联删除：会话销毁时关联任务一并清理

任务生命周期：
- PENDING：任务已创建，等待调度执行
- RUNNING：任务正在执行，Agent 正在处理
- COMPLETED：任务执行成功，结果已写入 result
- FAILED：任务执行失败，错误信息写入 error_message
- CANCELLED：任务被用户取消

索引设计理由：
- ix_tasks_session_id：按会话查任务列表，最高频查询
- ix_tasks_status：按状态筛选，如查所有 RUNNING 任务
- ix_tasks_created_at：按创建时间排序
- ix_tasks_updated_at：按更新时间排序，支持超时检测
- uq_tasks_user_business：联合唯一索引（user_id, business_id），保证 MQ 重投幂等
  · 业务方传入稳定的 business_id（如 "analyze_jd:job-uuid-123"）
  · 同一用户对同一业务 ID 只能存在一条 Task 记录
  · 配合 app/domain/common/idempotent.py 的 insert_idempotent 使用，
    重复 INSERT 触发 IntegrityError → DuplicateMessageError → 消费者静默 ACK
  · 为什么需要 user_id 维度的隔离：避免多用户业务 ID 冲突（如都传 "default-task"）

幂等消费前置条件：
- 调用方必须在创建 Task 时传入 business_id（不可为 NULL）
- 推荐 business_id 命名：f"{task_type}:{business_key}"，如 "analyze_jd:job-uuid-123"
- 业务 ID 必须是稳定的：同一业务操作重试时 ID 必须一致
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class TaskStatus(StrEnum):
    """异步任务状态枚举

    状态流转：
    PENDING → RUNNING → COMPLETED
                  └────→ FAILED
    PENDING ──────→ CANCELLED

    - PENDING：任务已创建，等待调度器分配执行
    - RUNNING：任务正在执行，Agent 正在处理
    - COMPLETED：任务执行成功，结果已写入 result 字段
    - FAILED：任务执行失败，错误信息写入 error_message 字段
    - CANCELLED：任务被用户主动取消

    继承 StrEnum：序列化直接输出字符串，日志/API 可读
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# PG ENUM 类型：任务状态枚举
# create_type=False：由 Alembic 迁移负责 CREATE TYPE
task_status_enum = ENUM(
    *[s.value for s in TaskStatus],
    name="task_status",
    create_type=False,
)


class Task(Base):
    """异步任务表 ORM Model

    字段基于 PRD §4.2.5 tasks 实体设计：
    - id: UUID 主键，API 查询任务状态的标识（GET /api/agent/task/{id}）
    - session_id: 所属会话，外键关联 sessions.id
    - task_type: 任务类型，标识 Agent 执行的具体操作
    - status: 任务状态，PG ENUM
    - input_data: 任务输入参数，JSONB 灵活存储
    - result: 任务执行结果，JSONB 灵活存储
    - error_message: 错误信息，仅 FAILED 状态时填充
    - created_at: 创建时间
    - updated_at: 更新时间，用于超时检测
    """

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="任务ID，UUID v4",
    )

    # 所属用户：冗余字段（已有 session_id → user_id 关联链）
    # 为什么冗余：
    # - (user_id, business_id) 联合唯一索引必须 user_id 直接在 tasks 表上
    # - 否则联合索引要 join sessions 才能确认唯一性，DB 索引无法跨表
    # - ON DELETE CASCADE：用户注销时任务一并清理（与 session_id 级联链一致）
    # 写入一致性：Service 层必须在创建 Task 时从 session_id 同步 user_id
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户ID（冗余自 sessions.user_id，用于业务幂等唯一约束）",
    )

    # 所属会话：级联删除，会话销毁时任务一并清理
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话ID",
    )

    # 业务 ID：调用方传入的稳定标识，用于 MQ 重投幂等
    # 命名建议：f"{task_type}:{business_key}"，如 "analyze_jd:job-uuid-123"
    # 长度 100：覆盖命名建议 + UUID/雪花 ID + 业务前缀
    # NOT NULL：业务方必须显式传入，未传则违反契约
    business_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="业务ID，业务方传入的稳定标识，MQ重投幂等键",
    )

    # 任务类型：标识 Agent 执行的具体操作
    # 如 analyze_jd / match_resume / generate_greeting / generate_strategy
    # 使用 String 而非 ENUM：任务类型随业务扩展可能频繁新增，ENUM 需 ALTER TYPE
    task_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="任务类型，如 analyze_jd / match_resume",
    )

    # 任务状态：PG ENUM，约束合法值
    status: Mapped[TaskStatus] = mapped_column(
        task_status_enum,
        nullable=False,
        default=TaskStatus.PENDING,
        server_default="PENDING",
        comment="任务状态（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED）",
    )

    # 任务输入参数：JSONB 灵活存储不同任务的输入结构
    # analyze_jd: {"job_id": "...", "jd_text": "..."}
    # match_resume: {"job_id": "...", "resume_id": "..."}
    input_data: Mapped[dict | list | None] = mapped_column(
        JSONB,
        nullable=True,
        server_default=text("'{}'::jsonb"),
        comment="任务输入参数，JSONB 对象",
    )

    # 任务执行结果：JSONB 灵活存储不同任务的输出结构
    # analyze_jd: {"skills": [...], "keywords": [...], "difficulty": "medium"}
    # match_resume: {"match_score": 85, "missing_skills": [...]}
    result: Mapped[dict | list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="任务执行结果，JSONB 对象",
    )

    # 错误信息：仅 FAILED 状态时填充，便于排查问题
    # 使用 Text 而非 String：错误信息可能包含 traceback，长度不可预测
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="错误信息，仅 FAILED 状态",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    # 更新时间：每次状态变更时更新，用于超时检测
    # Agent 任务有超时机制（PRD §9.3: 失败自动重试最多 3 次），
    # 通过 updated_at 检测长时间 RUNNING 的任务
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.now,
        comment="更新时间",
    )

    __table_args__ = (
        # 按会话查任务列表：最高频查询路径
        Index("ix_tasks_session_id", "session_id"),
        # 按状态筛选：如查所有 RUNNING 任务用于监控
        Index("ix_tasks_status", "status"),
        # 创建时间索引：按创建时间排序
        Index("ix_tasks_created_at", "created_at"),
        # 更新时间索引：支持超时检测（WHERE status = 'RUNNING' AND updated_at < now() - interval '5 min'）
        Index("ix_tasks_updated_at", "updated_at"),
        # 联合唯一索引：MQ 幂等消费的前置条件
        # 同一用户对同一 business_id 只能存在一条 Task
        # MQ 重投（Publisher Confirms 竞态 / Consumer 崩溃在 commit 后 ACK 前）触发 unique 冲突
        # → IntegrityError → DuplicateMessageError → 消费者静默 ACK
        # 为什么需要 user_id 维度：多用户环境下避免 business_id 冲突
        # （如不同用户都传 "default-task" 这种简单 ID）
        Index(
            "uq_tasks_user_business",
            "user_id",
            "business_id",
            unique=True,
        ),
        # 普通索引：按用户查任务列表（数据权限隔离场景）
        Index("ix_tasks_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Task id={self.id} session_id={self.session_id} "
            f"type={self.task_type} status={self.status.value}>"
        )
