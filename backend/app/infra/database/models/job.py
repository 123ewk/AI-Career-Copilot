"""Job ORM Model

职责：
- 定义 jobs 表结构，映射 PRD §10.2 Job 实体
- 声明索引策略，优化岗位查询与 Agent 分析路径

设计动机：
- skills/keywords 使用 JSONB 而非 JSON：JSONB 支持索引（GIN）、
  包含查询（@>）、路径查询，而 JSON 只存文本，无法建索引
- source_url 唯一索引：防止同一来源岗位重复入库（同一 URL 只对应一条记录）
- salary_min/salary_max 分开存储：支持范围查询（WHERE salary_max >= ? AND salary_min <= ?）

索引设计理由：
- uq_jobs_source_url：唯一索引，去重 + 加速按来源链接查询
  失效场景：source_url 为 NULL 时不参与唯一约束（PostgreSQL 唯一索引忽略 NULL）
- ix_jobs_title：岗位名称模糊搜索，B-tree 支持前缀匹配（LIKE 'Python%'）
  失效场景：LIKE '%工程师' 后缀通配无法走索引，需 pg_trgm 全文索引
- ix_jobs_company：按公司筛选岗位
- ix_jobs_source：按来源平台筛选
- ix_jobs_created_at：按发现时间排序/分页
- ix_jobs_salary_range：复合索引，支持薪资区间筛选
- gin_jobs_skills：GIN 索引，支持 JSONB 包含查询（WHERE skills @> '["Python"]'）
- gin_jobs_keywords：同上，支持关键词包含查询
"""

import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class Job(Base):
    """岗位表 ORM Model

    字段与 PRD §10.2 一致：
    - id: UUID 主键，应用层生成
    - title: 岗位名称
    - company: 公司名称
    - salary_min/salary_max: 薪资范围（单位：K），分开存储支持范围查询
    - jd_text: JD 原文，Text 类型无长度限制
    - source: 来源平台（boss/liepin/zhilian/shixisheng）
    - source_url: 原始链接，唯一约束防重复
    - location: 工作地点
    - skills: 提取的技能列表，JSONB 数组（如 ["Python", "LangChain"]）
    - keywords: 提取的关键词，JSONB 数组（如 ["AI应用开发", "RAG"]）
    - seniority: 资历要求（junior/mid/senior/lead）
    - difficulty: 难度评级（easy/medium/hard）
    - created_at: 创建时间
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="岗位ID，UUID v4",
    )

    # 岗位名称：高频查询字段，B-tree 索引支持前缀匹配
    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        comment="岗位名称",
    )

    # 公司名称：按公司筛选岗位的常用条件
    company: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        comment="公司名称",
    )

    # 薪资范围：单位 K（千），分开存储支持区间查询
    # 允许 NULL：部分岗位未标注薪资
    salary_min: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="最低薪资（K）",
    )

    salary_max: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="最高薪资（K）",
    )

    # JD 原文：Text 类型无长度限制，存储完整岗位描述
    jd_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="JD 原文",
    )

    # 来源平台：枚举值，按平台筛选岗位
    source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="来源平台（boss/liepin/zhilian/shixisheng）",
    )

    # 原始链接：唯一约束，防止同一来源岗位重复入库
    # 允许 NULL：部分岗位可能无直接链接
    source_url: Mapped[str | None] = mapped_column(
        String(1000),
        unique=True,
        nullable=True,
        comment="原始链接，唯一",
    )

    # 工作地点
    location: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="工作地点",
    )

    # 技能列表：JSONB 数组，如 ["Python", "LangChain", "FastAPI"]
    # JSONB 而非 JSON：支持 GIN 索引和包含查询（@>），Agent 匹配时高效检索
    # server_default='[]'：PG 侧默认空数组，避免 NULL 判断
    skills: Mapped[dict | list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="提取的技能列表，JSONB 数组",
    )

    # 关键词列表：JSONB 数组，如 ["AI应用开发", "RAG", "Agent"]
    keywords: Mapped[dict | list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="提取的关键词，JSONB 数组",
    )

    # 资历要求：Agent 分析输出，允许 NULL（未分析时）
    seniority: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="资历要求（junior/mid/senior/lead）",
    )

    # 难度评级：Agent 分析输出，允许 NULL
    difficulty: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="难度评级（easy/medium/hard）",
    )

    # 完整分析结果：Job Analysis Agent 的 LLM 提取输出（JSONB）
    # 包含 skills/keywords/seniority/difficulty/salary_range/company_info/hidden_requirements
    # 顶层 skills/keywords/seniority/difficulty 为冗余字段（便于索引查询），
    # analysis_result 存储完整结构化数据（含 salary_range/company_info/hidden_requirements）
    analysis_result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Job Analysis Agent 完整分析结果（JSONB）",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    __table_args__ = (
        # 唯一索引：按来源链接去重，同一 URL 不重复入库
        Index("ix_jobs_source_url", "source_url", unique=True),
        # 岗位名称索引：支持前缀搜索 LIKE 'Python%'
        Index("ix_jobs_title", "title"),
        # 公司索引：按公司筛选岗位
        Index("ix_jobs_company", "company"),
        # 来源平台索引：按平台筛选
        Index("ix_jobs_source", "source"),
        # 创建时间索引：按发现时间排序/分页
        Index("ix_jobs_created_at", "created_at"),
        # 薪资范围复合索引：支持 WHERE salary_min <= ? AND salary_max >= ?
        Index("ix_jobs_salary_range", "salary_min", "salary_max"),
        # GIN 索引：支持 JSONB 包含查询 WHERE skills @> '["Python"]'
        # GIN（Generalized Inverted Index）将 JSONB 数组每个元素建倒排索引，
        # 包含查询复杂度从 O(N) 全表扫描降为 O(log N)
        Index("gin_jobs_skills", "skills", postgresql_using="gin"),
        # GIN 索引：关键词包含查询
        Index("gin_jobs_keywords", "keywords", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title!r} company={self.company!r}>"
