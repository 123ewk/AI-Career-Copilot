"""RabbitMQ 连接管理模块

职责：
- 管理 RabbitMQ 异步连接的生命周期（创建、复用、关闭）
- 基于 aio-pika RobustConnection 实现自动重连，网络抖动时无需人工干预
- 单例模式：整个应用共享一个连接，避免重复创建 TCP 连接

设计动机：
- RobustConnection：aio-pika 内置的连接恢复机制，当 TCP 连接断开时
  自动重连并恢复 Channel、Exchange、Queue、Consumer 的声明和绑定，
  业务代码无需感知断连，这是选择 aio-pika 而非原始 aiormq 的核心原因
- 单例模式：RabbitMQ 连接是重量级资源（TCP + AMQP 握手 + 认证），
  多个连接浪费资源且增加 Broker 负担
- 工厂模式：与 PgSessionFactory / RedisClientFactory 保持架构一致

核心机制：
- RobustConnection 内部监听 Connection.Closed 事件，触发重连流程
- 重连成功后自动恢复：Channel 重新打开、Exchange/Queue 重新声明、
  Consumer 重新注册、未确认消息重新投递
- 应用层通过 connection_ready 事件感知连接状态变化

协程调度原理：
- aio-pika 基于 aiormq，底层使用 asyncio.Protocol 实现 AMQP 协议
- 所有 IO 操作（发布/消费/确认）都是 await，不阻塞 Event Loop
- 消费者回调在 Event Loop 中调度，同一时刻只有一个回调在执行
"""

from collections.abc import AsyncGenerator

import aio_pika
from aio_pika.abc import AbstractRobustConnection

from app.core.settings import get_settings


class RabbitMQConnectionFactory:
    """RabbitMQ 异步连接工厂

    封装 RobustConnection 的创建和生命周期管理。
    单例模式：模块级别只创建一个工厂实例，整个应用共享同一个连接。

    为什么用 RobustConnection 而非普通 Connection：
    - 普通 Connection 断开后，所有 Channel、Consumer 失效，需手动重建
    - RobustConnection 自动重连 + 自动恢复拓扑，业务代码无感知
    - 代价是轻微的性能开销（重连期间消息暂存），远优于手动恢复
    """

    def __init__(self) -> None:
        self._connection: AbstractRobustConnection | None = None

    async def _build_connection(self) -> AbstractRobustConnection:
        """创建鲁棒连接

        参数说明：
        - timeout=10：AMQP 握手超时（秒），防止 Broker 不可用时无限等待
        - reconnect_interval=5：断连后重试间隔（秒），
          RobustConnection 内部定时器，到期后自动发起重连
        - fail_fast=False：首次连接失败时不抛异常，而是进入重连循环，
          适用于 Broker 启动晚于应用的场景（如 Docker Compose 启动顺序）
        """
        settings = get_settings()
        return await aio_pika.connect_robust(
            settings.rabbitmq_url,
            timeout=10,
            reconnect_interval=5,
            fail_fast=False,
        )

    @property
    def connection(self) -> AbstractRobustConnection | None:
        """获取当前连接（可能为 None，首次需调用 connect()）"""
        return self._connection

    async def connect(self) -> AbstractRobustConnection:
        """建立连接（懒初始化）

        返回连接实例，如果已连接则直接复用。
        首次调用时创建 RobustConnection，后续调用返回同一实例。

        为什么不放在 @property 里：连接创建是 async 操作，
        Python property 不支持 async，必须显式 await
        """
        if self._connection is None:
            self._connection = await self._build_connection()
        return self._connection

    async def get_channel(self) -> aio_pika.RobustChannel:
        """获取一个新的 Channel

        AMQP Channel 是连接内的轻量级虚拟连接，用于隔离不同业务的消息流。
        每个 Channel 有独立的确认序列号和事务，互不影响。

        为什么每次新建 Channel 而非共享：
        - Channel 之间隔离确认和事务，共享 Channel 会导致确认混乱
        - Publisher 用一个 Channel，Consumer 用另一个，避免互相阻塞
        - Channel 创建开销极小（一次 AMQP 帧），无需池化
        """
        conn = await self.connect()
        return await conn.channel()

    async def close(self) -> None:
        """关闭连接，释放所有 Channel 和资源

        应用关闭时调用。RobustConnection 关闭时会：
        1. 取消所有 Consumer
        2. 关闭所有 Channel
        3. 关闭 TCP 连接
        关闭后不再自动重连（因为调用了显式关闭）
        """
        if self._connection is not None:
            await self._connection.close()
            self._connection = None


# 模块级单例：整个应用共享同一个工厂和连接
rabbitmq_connection_factory = RabbitMQConnectionFactory()


async def get_rabbitmq_channel() -> AsyncGenerator[aio_pika.RobustChannel, None]:
    """FastAPI 依赖注入：提供 RabbitMQ Channel

    每次请求获取独立 Channel，请求结束后关闭。
    Channel 关闭不影响底层 Connection，Connection 仍然存活。

    为什么 Channel 需要 finally 关闭而 Redis 客户端不需要：
    - Redis 客户端内部自动借还连接，无需手动管理
    - AMQP Channel 需要显式关闭，否则 Channel 数量持续增长
      直到 RabbitMQ 的 channel_max 限制

    用法：
        @router.post("/notify")
        async def notify(ch: RobustChannel = Depends(get_rabbitmq_channel)):
            exchange = await ch.get_exchange("notifications")
            await exchange.publish(Message(body=b"..."), routing_key="email")
            return {"msg": "sent"}
    """
    channel = await rabbitmq_connection_factory.get_channel()
    try:
        yield channel
    finally:
        await channel.close()
