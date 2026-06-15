"""Resume ORM Model

职责：
- 定义 resumes 表结构，映射 PRD §10.2 Resume 实体
- 声明索引策略，优化简历查询与 Agent 匹配路径

设计动机：
- structured_data 使用 JSONB：存储解析后的结构化简历数据（教育经历、工作经历等），
  JSONB 支持路径查询和 GIN 索引，Agent 可按字段精确检索
- skills 使用 JSONB：与 Job.skills 类型一致，支持包含查询做技能匹配
- is_active 标记活跃简历：每个用户只有一份活跃简历，通过部分索引（Partial Index）
  保证同一用户最多一条 is_active=True 的记录
- user_id 外键：关联 users 表，级联删除，用户注销时简历一并清理

索引设计理由：
- ix_resumes_user_id：按用户查简历，最高频查询（每个请求都带 user_id）
- ix_resumes_is_active：筛选活跃简历，但低基数（只有 True/False），
  配合 user_id 的部分索引才能真正高效
- uq_resumes_user_active：部分唯一索引，WHERE is_active = TRUE，
  保证每个用户最多一条活跃简历，这是数据库层面的业务约束
- ix_resumes_created_at：按上传时间排序
- gin_resumes_skills：GIN 索引，支持技能匹配查询
- gin_resumes_structured_data：GIN 索引，支持结构化数据路径查询
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class Resume(Base):
    """简历表 ORM Model

    字段与 PRD §10.2 一致：
    - id: UUID 主键
    - user_id: 所属用户，外键关联 users.id
    - raw_text: 简历原文（PDF/DOCX 解析后的纯文本）
    - structured_data: 结构化数据（教育经历、工作经历、项目经历等）
    - skills: 技能列表，JSONB 数组，与 Job.skills 格式一致便于匹配
    - experience_years: 工作年限，Agent 匹配时的关键维度
    - is_active: 是否为当前活跃简历，每用户仅一条
    - created_at: 创建时间
    """

    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="简历ID，UUID v4",
    )

    # 外键关联 users 表：ON DELETE CASCADE，用户注销时简历一并删除
    # 避免孤儿数据：用户不存在后残留的简历无业务意义，且可能含敏感信息需清理
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户ID",
    )

    # 简历原文：PDF/DOCX 解析后的纯文本，保留完整信息供 Agent 分析
    raw_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="简历原文",
    )

    # 结构化数据：JSONB 对象，存储解析后的结构化信息
    # 典型结构：{"education": [...], "experience": [...], "projects": [...], ...}
    # JSONB 支持 GIN 索引和路径查询（如 structured_data->'experience' @> '[...]'）
    structured_data: Mapped[dict | list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="结构化数据，JSONB 对象",
    )

    # 技能列表：JSONB 数组，如 ["Python", "FastAPI", "PostgreSQL"]
    # 与 Job.skills 格式一致，匹配时直接用 @> 操作符比较
    skills: Mapped[dict | list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="技能列表，JSONB 数组",
    )

    # 工作年限：Agent 匹配的关键维度，允许 NULL（应届生/未填写）
    experience_years: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="工作年限",
    )

    # 活跃简历标记：每用户仅一条 is_active=True
    # 默认 False，Service 层创建时显式设置，并取消旧简历的活跃状态
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="是否为当前活跃简历",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    __table_args__ = (
        # 按用户查简历：最高频查询路径
        Index("ix_resumes_user_id", "user_id"),
        # 创建时间索引：按上传时间排序/分页
        Index("ix_resumes_created_at", "created_at"),
        # 部分唯一索引：WHERE is_active = TRUE
        # 保证每个用户最多一条活跃简历，这是数据库层面的业务约束
        # 比应用层检查更可靠：并发场景下两个请求可能同时设置 is_active=True，
        # 应用层检查存在 TOCTOU 竞态，部分唯一索引由 PG 原子保证
        Index(
            "uq_resumes_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
        ),
        # GIN 索引：技能匹配查询 WHERE skills @> '["Python"]'
        Index("gin_resumes_skills", "skills", postgresql_using="gin"),
        # GIN 索引：结构化数据路径查询
        # 支持 WHERE structured_data @> '{"experience": [...]}' 等包含查询
        Index("gin_resumes_structured_data", "structured_data", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Resume id={self.id} user_id={self.user_id} is_active={self.is_active}>"
