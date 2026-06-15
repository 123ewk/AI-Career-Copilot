"""Application ORM Model

职责：
- 定义 applications 表结构，映射 PRD §10.2 Application 实体
- 定义 ApplicationStatus 枚举，映射 PRD §6 岗位状态机
- 声明索引策略，优化投递记录查询与状态跟踪路径

设计动机：
- status 使用 PG ENUM 而非 String：枚举值有限且固定，ENUM 类型在 PG 内部用 4 字节存储，
  比字符串更紧凑、比较更快；且数据库层面约束合法值，防止写入非法状态
- status_updated_at 记录状态最后变更时间：与 created_at 分离，
  支持计算"某状态停留时长"（如 HR 多久未查看），驱动 Agent 超时提醒
- (user_id, job_id) 联合唯一索引：同一用户对同一岗位只投递一次，
  防止重复投递，数据库层面保证原子性

索引设计理由：
- ix_applications_user_id：按用户查投递列表，最高频查询
- ix_applications_job_id：按岗位查投递者列表
- uq_applications_user_job：联合唯一，防重复投递
- ix_applications_status：按状态筛选（如查所有 INTERVIEW 状态）
- ix_applications_status_updated_at：按状态更新时间排序，支持超时检测
- ix_applications_applied_at：按投递时间排序/分页
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.database.base import Base


class ApplicationStatus(StrEnum):
    """投递状态枚举，映射 PRD §6 岗位状态机

    状态流转：
    DISCOVERED → ANALYZED → MATCHED → RECOMMENDED → COMMUNICATION_READY
                                                              │
                     ┌────────────────────────────────────────┤
                     ↓                                        ↓
                  APPLIED                                 SKIPPED
                    │
        ┌───────────┼───────────┐
        ↓           ↓           ↓
     VIEWED     REJECTED    INTERVIEW
                                │
                   ┌────────────┼────────────┐
                   ↓            ↓            ↓
              SCHEDULED      PASSED       FAILED
                   │
                   ↓
              OFFERED

    继承 StrEnum：
    - 序列化时直接输出字符串（如 "APPLIED"），API 响应可读
    - 比 IntEnum 更安全：日志/数据库中直接可读，无需查映射表
    - PG ENUM 存储时也用字符串名，Alembic 迁移可追踪
    """

    DISCOVERED = "DISCOVERED"
    ANALYZED = "ANALYZED"
    MATCHED = "MATCHED"
    RECOMMENDED = "RECOMMENDED"
    COMMUNICATION_READY = "COMMUNICATION_READY"
    APPLIED = "APPLIED"
    SKIPPED = "SKIPPED"
    VIEWED = "VIEWED"
    REJECTED = "REJECTED"
    INTERVIEW = "INTERVIEW"
    SCHEDULED = "SCHEDULED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    OFFERED = "OFFERED"


# PG ENUM 类型：在数据库中创建名为 application_status 的枚举类型
# create_type=False：由 Alembic 迁移负责 CREATE TYPE，避免 SQLAlchemy 自动创建/删除
# 导致与 Alembic 迁移冲突（Alembic 检测到类型已存在会报错）
application_status_enum = ENUM(
    *[s.value for s in ApplicationStatus],
    name="application_status",
    create_type=False,
)


class Application(Base):
    """投递记录表 ORM Model

    字段与 PRD §10.2 一致：
    - id: UUID 主键
    - user_id: 所属用户，外键关联 users.id
    - job_id: 目标岗位，外键关联 jobs.id
    - status: 投递状态，PG ENUM 类型，值域见 ApplicationStatus
    - match_score: 匹配分数（0-100），Agent 匹配结果
    - applied_at: 投递时间，用户确认投递时记录
    - status_updated_at: 状态最后变更时间，用于超时检测和停留时长计算
    - notes: 备注，用户手动添加的备注信息
    """

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="投递记录ID，UUID v4",
    )

    # 所属用户：级联删除，用户注销时投递记录一并清理
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户ID",
    )

    # 目标岗位：级联删除，岗位删除时关联投递记录一并清理
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        comment="目标岗位ID",
    )

    # 投递状态：PG ENUM，数据库层面约束合法值
    # 默认 DISCOVERED：Extension 检测到新岗位时即创建记录，初始状态为已发现
    status: Mapped[ApplicationStatus] = mapped_column(
        application_status_enum,
        nullable=False,
        default=ApplicationStatus.DISCOVERED,
        server_default="DISCOVERED",
        comment="投递状态，见 ApplicationStatus 枚举",
    )

    # 匹配分数：0-100 浮点数，Agent 匹配计算结果
    # 允许 NULL：未执行匹配时无分数
    match_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="匹配分数（0-100）",
    )

    # 投递时间：用户确认投递时由 Service 层显式设置
    # 允许 NULL：记录可能在 APPLIED 之前的状态创建（如 DISCOVERED 时就入库）
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="投递时间",
    )

    # 状态更新时间：每次状态变更时由 Service 层更新
    # 与 created_at 分离：created_at 是记录创建时间，status_updated_at 是状态变更时间
    # 用途：计算某状态停留时长，驱动 Agent 超时提醒（如 HR 7 天未查看则提醒）
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        comment="状态最后变更时间",
    )

    # 备注：用户手动添加的补充信息
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="备注",
    )

    __table_args__ = (
        # 按用户查投递列表：最高频查询路径
        Index("ix_applications_user_id", "user_id"),
        # 按岗位查投递者列表
        Index("ix_applications_job_id", "job_id"),
        # 联合唯一索引：同一用户对同一岗位只投递一次
        # 并发场景下两个请求可能同时投递同一岗位，应用层检查存在 TOCTOU 竞态，
        # 唯一索引由 PG 原子保证，违反约束时抛 IntegrityError
        Index(
            "uq_applications_user_job",
            "user_id",
            "job_id",
            unique=True,
        ),
        # 按状态筛选：如查所有 INTERVIEW 状态的投递
        # 低基数列（14 个枚举值），但状态筛选是高频操作，索引仍有价值
        Index("ix_applications_status", "status"),
        # 状态更新时间索引：支持超时检测（WHERE status_updated_at < now() - interval '7 days'）
        Index("ix_applications_status_updated_at", "status_updated_at"),
        # 投递时间索引：按投递时间排序/分页
        Index("ix_applications_applied_at", "applied_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Application id={self.id} user_id={self.user_id} "
            f"job_id={self.job_id} status={self.status.value}>"
        )
