"""PostgreSQL 异步会话工厂

职责：
- 创建并管理 async SQLAlchemy Engine（连接池）
- 通过工厂模式提供 AsyncSession，解耦 session 创建与业务逻辑
- 提供 FastAPI 依赖注入的 get_db_session，确保请求级 session 生命周期

设计动机：
- 工厂模式：将 session 创建逻辑封装在工厂中，业务层无需关心 engine/session 构造细节
- 连接池：asyncpg 内置连接池，SQLAlchemy AsyncEngine 在其上再封装 pool 管理，
  避免每次请求都新建 TCP 连接（三次握手 + SSL + 认证开销约 50-100ms）
- 请求级 session：每个 HTTP 请求获取独立 session，请求结束自动关闭，
  防止 session 泄漏和跨请求数据污染

核心机制：
- AsyncEngine 持有连接池（pool_size=20, max_overflow=10），最多同时 30 个连接
- async_sessionmaker 是工厂类，每次调用()生成新 AsyncSession
- AsyncSession 绑定到 Engine，通过 Engine 从池中获取/归还连接
- FastAPI 的 Depends + yield 确保请求结束后 session.close() 必定执行

协程调度原理：
- asyncpg 使用 asyncio.Protocol 实现 TCP 通信，IO 操作不阻塞 Event Loop
- SQLAlchemy async session 的 execute() 内部通过 await 将控制权交还 Event Loop
- 多个协程可共享同一 Engine 的连接池，实现高并发下的连接复用
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings


class PgSessionFactory:
    """PostgreSQL 异步会话工厂

    封装 Engine 创建和 Session 工厂，对外只暴露获取 session 的接口。
    单例模式：模块级别只创建一个工厂实例，整个应用共享同一个 Engine。

    为什么用工厂模式而非直接暴露 engine：
    - 隐藏 engine/sessionmaker 的构造细节，调用方只需关心"给我一个 session"
    - 方便测试时替换为内存 SQLite 的工厂（依赖注入替换）
    - 统一管理连接池参数、日志、事件钩子
    """

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    def _build_engine(self) -> AsyncEngine:
        """创建异步引擎

        连接池参数说明：
        - pool_size=20：常驻连接数，空闲时保持 20 个连接待命
        - max_overflow=10：突发流量时额外创建 10 个连接（总共最多 30 个）
        - pool_timeout=30：池满时等待可用连接的超时（秒），超时抛异常
        - pool_recycle=1800：连接存活最长时间（秒），防止 PG 主动断开闲置连接
          PG 默认 tcp_keepalives_idle=2h，但中间件/防火墙可能更短，30 分钟较安全
        - pool_pre_ping=True：每次从池中取连接时先发 SELECT 1 探测，
          避免拿到已被 PG/防火墙断开的死连接导致业务报错
        - echo=False：不打印 SQL，生产环境由 structlog 统一记录慢查询
        """
        settings = get_settings()
        return create_async_engine(
            settings.postgres_url,
            pool_size=20,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
            echo=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        """获取异步引擎（懒初始化）

        懒初始化原因：应用启动时可能还没加载 .env，延迟到首次使用时创建
        """
        if self._engine is None:
            self._engine = self._build_engine()
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """获取 session 工厂（懒初始化）

        expire_on_commit=False：commit 后对象属性不过期，避免 commit 后
        再访问属性触发额外 SELECT（lazy load），在高并发下减少不必要的查询
        """
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        return self._session_factory

    def create_session(self) -> AsyncSession:
        """创建新的异步会话

        每次调用返回独立 session，session 本身非线程安全，
        但在 async 单线程 Event Loop 中，同一时刻只有一个协程使用该 session
        """
        return self.session_factory()

    async def close(self) -> None:
        """关闭引擎，释放所有连接池资源

        应用关闭时调用，确保所有连接归还 PG、TCP 连接关闭。
        不调用会导致连接泄漏，PG 侧连接数持续增长直到 max_connections。
        """
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None


# 模块级单例：整个应用共享同一个工厂和 Engine
pg_session_factory = PgSessionFactory()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：提供请求级数据库会话

    使用 yield 确保请求结束后 session 必定关闭：
    - 正常流程：路由处理完毕 → yield 恢复 → session.close()
    - 异常流程：路由抛异常 → yield 仍恢复（类似 try/finally）→ session.close()

    session.close() 不是关闭 TCP 连接，而是将连接归还到 Engine 的连接池。

    用法：
        @router.get("/users")
        async def list_users(db: AsyncSession = Depends(get_db_session)):
            result = await db.execute(select(User))
            return result.scalars().all()
    """
    session = pg_session_factory.create_session()
    try:
        yield session
        # 业务层负责 commit/rollback，此处不自动 commit
        # 原因：让业务层显式控制事务边界，避免部分成功时误 commit
    finally:
        await session.close()
