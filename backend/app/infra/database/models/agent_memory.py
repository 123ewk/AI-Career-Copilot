"""AgentMemory ORM Model

职责：
- 定义 agent_memories 表结构，映射 PRD §10.2 AgentMemory 实体
- 定义 MemoryType 枚举，区分记忆类型
- 使用 pgvector 的 Vector 类型存储嵌入向量，支持相似度检索
- 声明索引策略，优化记忆检索与 RAG 查询路径

设计动机：
- embedding 使用 pgvector 的 Vector 类型：pgvector 是 PG 扩展，
  在数据库内直接做向量相似度搜索（余弦/内积/L2），避免将全量向量加载到内存
- 向量维度 1536：对应 OpenAI text-embedding-3-small 的输出维度，
  若后续切换模型需修改维度并重建索引
- memory_type 使用 PG ENUM：三种类型有限且固定，ENUM 约束合法值
- content 使用 JSONB：记忆内容结构多样（对话摘要/反思结论/偏好信息），
  JSONB 灵活存储且支持索引
- session_id 允许 NULL：long_term 记忆跨会话，不绑定特定 session

pgvector 核心原理：
- 向量以 float4[] 存储，每个维度 4 字节，1536 维 = 6144 字节/行
- HNSW 索引：基于层次可导航小世界图，近似最近邻搜索，
  查询复杂度 O(log N)，比暴力扫描 O(N) 快几个数量级
- 余弦距离（cosine）：衡量向量方向相似性，不受向量长度影响，
  适合文本语义相似度（embedding 模型通常归一化输出）

索引设计理由：
- ix_agent_memories_user_id：按用户查记忆列表，最高频查询
- ix_agent_memories_session_id：按会话查短期记忆
- ix_agent_memories_memory_type：按类型筛选（如只查 long_term）
- ix_agent_memories_created_at：按时间排序，支持"最近记忆"查询
- hnsw_agent_memories_embedding：HNSW 向量索引，支持相似度搜索
  - m=16：HNSW 图每个节点的最大连接数，越大召回率越高但索引越大
  - ef_construction=64：构建索引时的搜索宽度，越大索引质量越高但构建越慢
  失效场景：向量维度与索引定义不一致时查询报错
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.infra.database.base import Base


class MemoryType(StrEnum):
    """记忆类型枚举

    - short_term：短期记忆，当前会话内的对话上下文，会话结束可清理
    - long_term：长期记忆，跨会话保留的用户偏好、求职历史摘要
    - reflection：反思记忆，Agent 执行后的经验总结和策略调整

    继承 StrEnum：序列化直接输出字符串，日志/API 可读
    """

    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    REFLECTION = "reflection"


# PG ENUM 类型：记忆类型枚举
# create_type=False：由 Alembic 迁移负责 CREATE TYPE
memory_type_enum = ENUM(
    *[t.value for t in MemoryType],
    name="memory_type",
    create_type=False,
)

# 向量维度：对应 OpenAI text-embedding-3-small 输出维度
# 若切换 embedding 模型需修改此值并重建向量索引
EMBEDDING_DIMENSIONS = 1024


class AgentMemory(Base):
    """Agent 记忆表 ORM Model

    字段与 PRD §10.2 一致：
    - id: UUID 主键
    - user_id: 所属用户，外键关联 users.id
    - session_id: 关联会话，允许 NULL（long_term 记忆跨会话）
    - memory_type: 记忆类型，PG ENUM
    - content: 记忆内容，JSONB 灵活存储
    - embedding: 向量嵌入，pgvector Vector 类型，支持相似度搜索
    - created_at: 创建时间
    """

    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="记忆ID，UUID v4",
    )

    # 所属用户：级联删除，用户注销时记忆一并清理
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户ID",
    )

    # 关联会话：short_term 记忆绑定会话，long_term/reflection 允许 NULL
    # 级联删除：会话销毁时关联短期记忆一并清理，长期记忆不受影响（NULL 不触发级联）
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联会话ID，long_term 记忆可为空",
    )

    # 记忆类型：PG ENUM，约束合法值
    memory_type: Mapped[MemoryType] = mapped_column(
        memory_type_enum,
        nullable=False,
        comment="记忆类型（short_term/long_term/reflection）",
    )

    # 记忆内容：JSONB 灵活存储不同结构的记忆数据
    # short_term: {"messages": [...], "summary": "..."}
    # long_term: {"preferences": {...}, "career_summary": "..."}
    # reflection: {"observation": "...", "lesson": "...", "strategy_adjustment": "..."}
    content: Mapped[dict | list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="记忆内容，JSONB 对象",
    )

    # 向量嵌入：pgvector Vector 类型，存储 embedding 模型输出
    # 用于 RAG 检索：给定查询向量，找余弦相似度最高的 K 条记忆
    # 允许 NULL：记忆入库时可能尚未生成 embedding（异步生成场景）
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS),
        nullable=True,
        comment=f"向量嵌入，{EMBEDDING_DIMENSIONS} 维，pgvector",
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        comment="创建时间",
    )

    __table_args__ = (
        # 按用户查记忆：最高频查询路径
        Index("ix_agent_memories_user_id", "user_id"),
        # 按会话查短期记忆：WHERE session_id = ? AND memory_type = 'short_term'
        Index("ix_agent_memories_session_id", "session_id"),
        # 按类型筛选：如只查 long_term 记忆
        Index("ix_agent_memories_memory_type", "memory_type"),
        # 按时间排序：支持"最近记忆"查询
        Index("ix_agent_memories_created_at", "created_at"),
        # HNSW 向量索引：支持高效近似最近邻搜索
        # 余弦距离：衡量向量方向相似性，适合文本语义搜索
        # m=16：HNSW 图节点最大连接数，平衡召回率与索引大小
        # ef_construction=64：构建时搜索宽度，平衡索引质量与构建速度
        # 查询时通过 SET hnsw.ef_search = 100; 控制搜索精度
        Index(
            "hnsw_agent_memories_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentMemory id={self.id} user_id={self.user_id} "
            f"type={self.memory_type.value}>"
        )
