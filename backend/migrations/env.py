"""Alembic 迁移环境配置

职责：
- 配置数据库连接（从 Settings 读取，而非硬编码在 alembic.ini）
- 设置 target_metadata，让 autogenerate 自动发现所有 ORM Model
- 支持异步迁移（asyncpg 驱动）

设计动机：
- 不在 alembic.ini 中硬编码数据库 URL，而是从 .env + Settings 动态读取
  原因：不同环境（dev/test/prod）连接不同数据库，硬编码容易误操作
- 使用异步引擎执行迁移，与项目运行时一致（asyncpg）
- 导入所有 domain models 模块，确保 Base.metadata 注册了所有表定义
  Alembic autogenerate 通过比较 Base.metadata 和数据库实际结构来生成迁移脚本
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.settings import get_settings
from app.infra.database.base import Base

# 此处导入所有 domain models 模块，确保它们的表定义注册到 Base.metadata
# Alembic autogenerate 只能看到已导入的 Model，未导入的表不会出现在迁移脚本中
# 当新增 domain model 时，需要在此处添加对应的 import
# from app.domain.user.models import ...  # noqa: F401
# from app.domain.job.models import ...   # noqa: F401
# from app.domain.session.models import ...  # noqa: F401
# from app.domain.resume.models import ...  # noqa: F401
# from app.domain.workflow.models import ...  # noqa: F401

config = context.config

# 从 Settings 动态获取数据库 URL，覆盖 alembic.ini 中的占位值
# 这样不同环境（dev/staging/prod）自动使用对应的数据库
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.postgres_url)

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata 是 Alembic autogenerate 的核心：
# 它包含所有 ORM Model 的表定义，Alembic 对比此 metadata 与数据库实际结构，
# 生成 CREATE TABLE / ALTER TABLE / DROP TABLE 等迁移操作
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式运行迁移

    不连接数据库，只生成 SQL 脚本输出到文件。
    适用场景：在 CI 中生成迁移 SQL 供 DBA 审查，或无法直连数据库时。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """执行迁移的通用逻辑（同步）

    抽取公共逻辑，online 同步和 online 异步都复用此函数。
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """异步模式运行迁移

    使用 asyncpg 驱动连接数据库，与项目运行时保持一致。
    为什么用异步：项目使用 asyncpg 驱动，Alembic 也需要用异步引擎，
    否则同步驱动（psycopg2）和异步驱动（asyncpg）可能产生 DDL 不一致。
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # run_sync：在异步连接上同步执行迁移逻辑
        # Alembic 的迁移操作本身是同步 API，通过 run_sync 桥接到异步连接
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式运行迁移

    连接数据库并执行迁移。使用异步引擎（asyncpg）。
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
