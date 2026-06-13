"""Redis 异步客户端工厂

职责：
- 创建并管理 async Redis 连接池
- 通过工厂模式提供 Redis 客户端，解耦连接创建与业务逻辑
- 提供 FastAPI 依赖注入的 get_redis，确保请求级客户端生命周期

设计动机：
- 工厂模式：与 PgSessionFactory 保持一致的架构风格，隐藏连接池构造细节
- 连接池：Redis 单线程模型下，一个连接同一时刻只能处理一个命令，
  连接池允许并发请求各自使用不同连接，避免排队等待
- 请求级客户端：每个请求从池中获取连接，用完归还，防止连接泄漏

核心机制：
- redis.asyncio.Redis 内部维护连接池（ConnectionPool）
- 每次调用 redis 客户端命令时，从池中借一个连接，命令完成后归还
- 多个协程并发操作时，各自使用池中不同连接，互不阻塞

协程调度原理：
- redis-py 的 async 实现基于 asyncio，底层使用 asyncio.open_connection
- 命令发送后 await 读取响应，期间 Event Loop 可调度其他协程
- Redis 6.0+ 的 RESP3 协议支持多线程 IO，但单连接仍是串行处理命令
"""

from collections.abc import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis

from app.core.settings import get_settings


class RedisClientFactory:
    """Redis 异步客户端工厂

    封装连接池和客户端创建，对外只暴露获取客户端的接口。
    单例模式：模块级别只创建一个工厂实例，整个应用共享同一个连接池。

    为什么用工厂模式而非直接暴露 Redis 实例：
    - 隐藏连接池参数配置，调用方只需关心"给我一个 redis 客户端"
    - 方便测试时替换为 fakeredis 的工厂
    - 统一管理连接池参数、编解码、重试策略
    """

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._client: Redis | None = None

    def _build_pool(self) -> ConnectionPool:
        """创建异步连接池

        连接池参数说明：
        - max_connections=20：池中最大连接数，超出后新请求排队等待
          Redis 单线程处理命令，连接数不需要像 PG 那么大，20 足够应对
          大部分并发场景（每个连接串行执行命令，20 个连接 = 20 路并发）
        - decode_responses=True：自动将 Redis 返回的 bytes 解码为 str，
          避免业务层到处写 .decode()，同时 JSON 序列化更方便
        - socket_timeout=5：单次命令的 socket 超时（秒），
          防止 Redis 卡住时协程无限等待，阻塞 Event Loop
        - socket_connect_timeout=3：建立 TCP 连接的超时（秒），
          Redis 挂掉时快速失败而非让用户等很久
        - retry_on_timeout=True：socket 超时后自动重试一次，
          适用于网络瞬断场景（如 K8s 网络抖动），避免业务层误报错
        """
        settings = get_settings()
        return ConnectionPool.from_url(
            settings.redis_url,
            max_connections=20,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=3,
            retry_on_timeout=True,
        )

    @property
    def pool(self) -> ConnectionPool:
        """获取连接池（懒初始化）

        懒初始化原因：与 PgSessionFactory 一致，延迟到首次使用时创建
        """
        if self._pool is None:
            self._pool = self._build_pool()
        return self._pool

    @property
    def client(self) -> Redis:
        """获取 Redis 客户端（懒初始化）

        使用共享连接池：多个 Redis 实例可共享同一 ConnectionPool，
        这里只创建一个客户端绑定到池，整个应用复用
        """
        if self._client is None:
            self._client = Redis(connection_pool=self.pool)
        return self._client

    async def close(self) -> None:
        """关闭连接池，释放所有连接

        应用关闭时调用，确保所有连接归还 Redis、TCP 连接关闭。
        必须关闭 pool 而非 client，因为 pool 才是连接的真正持有者。
        """
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
            self._client = None


# 模块级单例：整个应用共享同一个工厂和连接池
redis_client_factory = RedisClientFactory()


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI 依赖注入：提供 Redis 客户端

    与 get_db_session 不同，Redis 客户端本身是线程/协程安全的
    （内部自动从池中借还连接），所以这里 yield 的是共享客户端实例，
    而非每次新建。但仍然用 yield 模式保持依赖注入风格一致性，
    且方便测试时替换。

    用法：
        @router.post("/verify-code")
        async def send_code(redis: Redis = Depends(get_redis)):
            await redis.setex(f"sms:{phone}", 300, code)
            return {"msg": "sent"}
    """
    yield redis_client_factory.client
