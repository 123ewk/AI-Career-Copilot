"""SQLAlchemy ORM 基类

职责：
- 定义 DeclarativeBase，所有 ORM Model 继承此类
- 提供 MetaData 实例，Alembic 通过 target_metadata 发现所有表

设计动机：
- 集中管理 Base 而非在各 model 文件中 declarative_base()，
  确保 Alembic 的 target_metadata 能发现所有表的定义
- 统一 naming convention，让索引/约束命名一致，便于 Alembic 生成可读的迁移脚本
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 统一命名约定：索引、唯一约束、外键、检查约束的命名规则
# 好处：Alembic 生成的迁移脚本中约束名可读、可预测，避免自动生成的随机名
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """ORM 基类

    所有 ORM Model 必须继承此类，Alembic 通过 Base.metadata 发现表结构。
    不在此类上定义通用列（如 id/created_at），避免隐式耦合，
    各 Model 按需显式定义，职责更清晰。
    """

    metadata = MetaData(naming_convention=naming_convention)
