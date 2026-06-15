"""init: create all tables with enums and indexes

Revision ID: c528168c702d
Revises:
Create Date: 2026-06-14 23:39:23.443167

初始化迁移：创建所有表、ENUM 类型、索引和外键约束。

执行顺序：
1. 先创建 ENUM 类型（被表的列引用，必须先存在）
2. 创建无外键依赖的表（users, jobs）
3. 创建依赖 users/jobs 的表（sessions, resumes, applications）
4. 创建依赖 sessions 的表（tasks）
5. 创建依赖 users + sessions 的表（agent_memories）
6. 创建所有索引（包括 GIN、HNSW、部分唯一索引）
7. 启用 pgvector 扩展（向量类型依赖此扩展）
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ENUM, JSONB

# revision identifiers, used by Alembic.
revision: str = "c528168c702d"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# PG ENUM 类型定义
# create_type=False：由下方 op.execute 手动创建，避免 SQLAlchemy 在 create_table 时自动创建导致冲突
session_status_enum = ENUM("ACTIVE", "IDLE", "CLOSED", name="session_status", create_type=False)
task_status_enum = ENUM(
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED",
    name="task_status", create_type=False,
)
application_status_enum = ENUM(
    "DISCOVERED", "ANALYZED", "MATCHED", "RECOMMENDED",
    "COMMUNICATION_READY", "APPLIED", "SKIPPED", "VIEWED",
    "REJECTED", "INTERVIEW", "SCHEDULED", "PASSED", "FAILED", "OFFERED",
    name="application_status", create_type=False,
)
memory_type_enum = ENUM(
    "short_term", "long_term", "reflection",
    name="memory_type", create_type=False,
)


def upgrade() -> None:
    """升级：创建所有数据库对象"""

    # 1. 启用 pgvector 扩展（Vector 类型依赖此扩展）
    # IF NOT EXISTS：幂等操作，扩展已存在时不报错
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. 创建 ENUM 类型
    # 必须在 CREATE TABLE 之前：表的列引用这些类型
    # 使用 op.execute 手动创建，配合 create_type=False 避免 SQLAlchemy 自动创建冲突
    op.execute("CREATE TYPE session_status AS ENUM ('ACTIVE', 'IDLE', 'CLOSED')")
    op.execute("CREATE TYPE task_status AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')")
    op.execute(
        "CREATE TYPE application_status AS ENUM "
        "('DISCOVERED', 'ANALYZED', 'MATCHED', 'RECOMMENDED', "
        "'COMMUNICATION_READY', 'APPLIED', 'SKIPPED', 'VIEWED', "
        "'REJECTED', 'INTERVIEW', 'SCHEDULED', 'PASSED', 'FAILED', 'OFFERED')"
    )
    op.execute("CREATE TYPE memory_type AS ENUM ('short_term', 'long_term', 'reflection')")

    # 3. 创建 users 表（无外键依赖，最先创建）
    op.create_table(
        "users",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="用户ID，UUID v4"),
        sa.Column("email", sa.String(320), nullable=False, comment="邮箱，唯一，用于登录"),
        sa.Column("password_hash", sa.String(255), nullable=False, comment="密码哈希（bcrypt）"),
        sa.Column("name", sa.String(100), nullable=True, comment="姓名"),
        sa.Column("target_position", sa.String(200), nullable=True, comment="目标岗位，如 AI应用开发工程师"),
        sa.Column("target_industry", sa.String(200), nullable=True, comment="目标行业，如 互联网"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="更新时间"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_target_position", "users", ["target_position"])
    op.create_index("ix_users_target_industry", "users", ["target_industry"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # 4. 创建 jobs 表（无外键依赖）
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="岗位ID，UUID v4"),
        sa.Column("title", sa.String(300), nullable=False, comment="岗位名称"),
        sa.Column("company", sa.String(300), nullable=False, comment="公司名称"),
        sa.Column("salary_min", sa.Integer, nullable=True, comment="最低薪资（K）"),
        sa.Column("salary_max", sa.Integer, nullable=True, comment="最高薪资（K）"),
        sa.Column("jd_text", sa.Text, nullable=False, comment="JD 原文"),
        sa.Column("source", sa.String(50), nullable=False, comment="来源平台（boss/liepin/zhilian/shixisheng）"),
        sa.Column("source_url", sa.String(1000), nullable=True, comment="原始链接，唯一"),
        sa.Column("location", sa.String(200), nullable=True, comment="工作地点"),
        sa.Column("skills", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), comment="提取的技能列表，JSONB 数组"),
        sa.Column("keywords", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), comment="提取的关键词，JSONB 数组"),
        sa.Column("seniority", sa.String(50), nullable=True, comment="资历要求（junior/mid/senior/lead）"),
        sa.Column("difficulty", sa.String(50), nullable=True, comment="难度评级（easy/medium/hard）"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间"),
    )
    op.create_index("ix_jobs_source_url", "jobs", ["source_url"], unique=True)
    op.create_index("ix_jobs_title", "jobs", ["title"])
    op.create_index("ix_jobs_company", "jobs", ["company"])
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_salary_range", "jobs", ["salary_min", "salary_max"])
    op.create_index("gin_jobs_skills", "jobs", ["skills"], postgresql_using="gin")
    op.create_index("gin_jobs_keywords", "jobs", ["keywords"], postgresql_using="gin")

    # 5. 创建 sessions 表（依赖 users）
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="会话ID，UUID v4，WebSocket 路径参数"),
        sa.Column("user_id", sa.UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="所属用户ID"),
        sa.Column("status", session_status_enum, nullable=False, server_default="ACTIVE", comment="会话状态（ACTIVE/IDLE/CLOSED）"),
        sa.Column("title", sa.String(500), nullable=True, comment="会话标题"),
        sa.Column("metadata", JSONB, nullable=True, comment="会话元数据，如来源页面、触发方式"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="更新时间"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_status", "sessions", ["status"])
    op.create_index("ix_sessions_created_at", "sessions", ["created_at"])
    op.create_index("ix_sessions_updated_at", "sessions", ["updated_at"])

    # 6. 创建 resumes 表（依赖 users）
    op.create_table(
        "resumes",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="简历ID，UUID v4"),
        sa.Column("user_id", sa.UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="所属用户ID"),
        sa.Column("raw_text", sa.Text, nullable=False, comment="简历原文"),
        sa.Column("structured_data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), comment="结构化数据，JSONB 对象"),
        sa.Column("skills", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"), comment="技能列表，JSONB 数组"),
        sa.Column("experience_years", sa.Integer, nullable=True, comment="工作年限"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("false"), comment="是否为当前活跃简历"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间"),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])
    op.create_index("ix_resumes_created_at", "resumes", ["created_at"])
    # 部分唯一索引：每用户最多一条活跃简历，PG 原子保证并发安全
    op.create_index(
        "uq_resumes_user_active", "resumes", ["user_id"],
        unique=True, postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index("gin_resumes_skills", "resumes", ["skills"], postgresql_using="gin")
    op.create_index("gin_resumes_structured_data", "resumes", ["structured_data"], postgresql_using="gin")

    # 7. 创建 applications 表（依赖 users + jobs）
    op.create_table(
        "applications",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="投递记录ID，UUID v4"),
        sa.Column("user_id", sa.UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="所属用户ID"),
        sa.Column("job_id", sa.UUID, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, comment="目标岗位ID"),
        sa.Column("status", application_status_enum, nullable=False, server_default="DISCOVERED", comment="投递状态，见 ApplicationStatus 枚举"),
        sa.Column("match_score", sa.Float, nullable=True, comment="匹配分数（0-100）"),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True, comment="投递时间"),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"), comment="状态最后变更时间"),
        sa.Column("notes", sa.Text, nullable=True, comment="备注"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    # 联合唯一索引：同一用户对同一岗位只投递一次
    op.create_index("uq_applications_user_job", "applications", ["user_id", "job_id"], unique=True)
    op.create_index("ix_applications_status", "applications", ["status"])
    op.create_index("ix_applications_status_updated_at", "applications", ["status_updated_at"])
    op.create_index("ix_applications_applied_at", "applications", ["applied_at"])

    # 8. 创建 tasks 表（依赖 sessions）
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="任务ID，UUID v4"),
        sa.Column("session_id", sa.UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, comment="所属会话ID"),
        sa.Column("task_type", sa.String(100), nullable=False, comment="任务类型，如 analyze_jd / match_resume"),
        sa.Column("status", task_status_enum, nullable=False, server_default="PENDING", comment="任务状态（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED）"),
        sa.Column("input_data", JSONB, nullable=True, server_default=sa.text("'{}'::jsonb"), comment="任务输入参数，JSONB 对象"),
        sa.Column("result", JSONB, nullable=True, comment="任务执行结果，JSONB 对象"),
        sa.Column("error_message", sa.Text, nullable=True, comment="错误信息，仅 FAILED 状态"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="更新时间"),
    )
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])
    op.create_index("ix_tasks_updated_at", "tasks", ["updated_at"])

    # 9. 创建 agent_memories 表（依赖 users + sessions）
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.UUID, server_default=sa.text("gen_random_uuid()"), primary_key=True, comment="记忆ID，UUID v4"),
        sa.Column("user_id", sa.UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="所属用户ID"),
        sa.Column("session_id", sa.UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, comment="关联会话ID，long_term 记忆可为空"),
        sa.Column("memory_type", memory_type_enum, nullable=False, comment="记忆类型（short_term/long_term/reflection）"),
        sa.Column("content", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"), comment="记忆内容，JSONB 对象"),
        sa.Column("embedding", Vector(1024), nullable=True, comment="向量嵌入，1024 维，pgvector"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间"),
    )
    op.create_index("ix_agent_memories_user_id", "agent_memories", ["user_id"])
    op.create_index("ix_agent_memories_session_id", "agent_memories", ["session_id"])
    op.create_index("ix_agent_memories_memory_type", "agent_memories", ["memory_type"])
    op.create_index("ix_agent_memories_created_at", "agent_memories", ["created_at"])
    # HNSW 向量索引：余弦距离，支持高效近似最近邻搜索
    op.create_index(
        "hnsw_agent_memories_embedding", "agent_memories", ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """降级：按依赖反序删除所有数据库对象"""

    # 删表（反序：先删依赖最多的表）
    op.drop_table("agent_memories")
    op.drop_table("tasks")
    op.drop_table("applications")
    op.drop_table("resumes")
    op.drop_table("sessions")
    op.drop_table("jobs")
    op.drop_table("users")

    # 删 ENUM 类型（表已删，无引用）
    op.execute("DROP TYPE IF EXISTS memory_type")
    op.execute("DROP TYPE IF EXISTS application_status")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS session_status")

    # 删 pgvector 扩展（谨慎：可能影响其他数据库对象）
    # op.execute("DROP EXTENSION IF EXISTS vector")
