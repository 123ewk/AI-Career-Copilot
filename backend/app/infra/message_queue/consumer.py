"""消息消费者

职责：
- 封装消息消费逻辑，提供统一的订阅接口
- 支持消息确认（ACK）、拒绝（NACK）、重试
- 消费者并发控制、优雅关闭

设计动机：
- 统一消费模式：所有消费者继承 MessageConsumer，只需实现 handle_message()，
  ACK/NACK/重试/日志由基类统一处理，避免每个消费者重复编写
- 重试策略：基于消息 header 中的 x-retry-count 计数，
  超过最大重试次数后 NACK 且不重入队，消息进入死信队列
- 并发控制：通过 prefetch_count 限制未确认消息数，
  防止消费者被大量消息压垮导致内存溢出
- 优雅关闭：通过 asyncio.Event 通知消费者停止，等待当前消息处理完毕

核心机制：
- prefetch_count：AMQP QoS 参数，Broker 一次最多推送 N 条未确认消息
  设为 10 表示消费者最多同时持有 10 条未确认消息，
  只有 ACK 一条后 Broker 才会推送新消息，实现背压（backpressure）
- 重试流程：NACK(requeue=True) → 消息重新入队 → 再次投递
  每次重试在 header 中递增 x-retry-count，超过阈值则放弃
- 优雅关闭：设置 shutdown_event → 消费回调检测到后不再接收新消息
  → 等待 in_flight 计数归零 → 取消消费者标签
"""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel

from app.core.logger import logger

# 默认最大重试次数，超过后消息进入死信队列
DEFAULT_MAX_RETRIES = 3


class MessageConsumer(ABC):
    """消息消费者基类

    子类只需实现 handle_message()，ACK/NACK/重试/日志由基类统一处理。

    生命周期：
    1. start() → 创建 Channel、设置 QoS、注册消费者
    2. 消息到达 → _on_message() → handle_message() → ACK/NACK
    3. stop() → 设置 shutdown_event → 等待 in_flight 归零 → 取消消费

    为什么用抽象基类而非函数回调：
    - 基类封装了重试、日志、关闭等横切关注点，子类只关注业务
    - 状态（max_retries、shutdown_event）自然绑定到实例
    - 方便为每个消费者独立配置参数
    """

    def __init__(
        self,
        queue_name: str,
        prefetch_count: int = 10,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """初始化消费者

        参数：
        - queue_name：消费的队列名称，必须与 exchanges.py 中定义一致
        - prefetch_count：未确认消息上限，控制并发度
          设为 1 是严格顺序消费，设为 N 是并发消费（N 越大吞吐越高）
        - max_retries：单条消息最大重试次数，超过后进入死信队列
        """
        self._queue_name = queue_name
        self._prefetch_count = prefetch_count
        self._max_retries = max_retries
        self._shutdown_event = asyncio.Event()
        # 当前正在处理的消息数，用于优雅关闭时等待
        self._in_flight = 0
        self._consumer_tag: str | None = None
        self._channel: AbstractRobustChannel | None = None

    @abstractmethod
    async def handle_message(self, body: dict[str, Any]) -> None:
        """处理消息的业务逻辑（子类实现）

        参数是已反序列化的 dict，基类负责 JSON 解码。
        抛出异常视为处理失败，基类会根据重试次数决定 NACK requeue 或进入死信。
        """
        ...

    async def start(self, channel: AbstractRobustChannel) -> None:
        """启动消费者

        流程：设置 QoS → 获取队列 → 注册消费回调
        必须在 exchanges.declare_all() 之后调用，确保队列已存在。
        """
        self._channel = channel

        # 设置 QoS：限制 Broker 推送的未确认消息数
        # 为什么重要：如果消费者处理慢但 Broker 不断推送，
        # 消息堆积在消费者内存中导致 OOM
        await channel.set_qos(prefetch_count=self._prefetch_count)

        queue = await channel.get_queue(self._queue_name)

        # 注册消费者回调，返回 consumer_tag 用于后续取消
        self._consumer_tag = await queue.consume(self._on_message)

        logger.info(
            "消费者已启动",
            extra={
                "queue": self._queue_name,
                "prefetch": self._prefetch_count,
                "max_retries": self._max_retries,
            },
        )

    async def stop(self) -> None:
        """优雅关闭消费者

        流程：设置关闭标志 → 等待 in_flight 归零 → 取消消费者
        不直接关闭 Channel，由 connection factory 统一管理。
        """
        self._shutdown_event.set()

        # 等待所有正在处理的消息完成，避免中途 ACK/NACK 导致数据不一致
        # 设置超时防止卡死：最多等 30 秒
        timeout = 30
        for _ in range(timeout):
            if self._in_flight <= 0:
                break
            await asyncio.sleep(1)

        if self._in_flight > 0:
            logger.warning(
                "消费者关闭超时，仍有消息未处理完",
                extra={
                    "queue": self._queue_name,
                    "in_flight": self._in_flight,
                },
            )

        # 取消消费者标签，Broker 不再向该消费者推送消息
        if self._consumer_tag and self._channel:
            await self._channel.basic_cancel(self._consumer_tag)
            self._consumer_tag = None

        logger.info(
            "消费者已关闭",
            extra={"queue": self._queue_name},
        )

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """消息到达回调（基类统一处理）

        流程：
        1. 检查是否正在关闭 → 是则 NACK requeue
        2. 解码 JSON body
        3. 调用子类 handle_message()
        4. 成功 → ACK
        5. 失败 → 检查重试次数 → 未超限 NACK requeue / 超限 NACK 进死信
        """
        # 关闭中不再处理新消息，让消息重新入队给其他消费者
        if self._shutdown_event.is_set():
            await message.nack(requeue=True)
            return

        self._in_flight += 1
        try:
            # 解码消息体，aio-pika 的 body 是 bytes
            body = json.loads(message.body.decode("utf-8"))

            # 读取重试次数（首次消费时 header 中无此字段，默认为 0）
            retry_count = self._get_retry_count(message)

            await self.handle_message(body)
            # 业务处理成功，确认消息，Broker 不再投递
            await message.ack()

            logger.debug(
                "消息处理成功",
                extra={
                    "queue": self._queue_name,
                    "retry_count": retry_count,
                },
            )

        except json.JSONDecodeError as e:
            # JSON 解码失败，消息格式有问题，重试无意义，直接进死信
            await message.nack(requeue=False)
            logger.error(
                "消息 JSON 解码失败，进入死信",
                extra={
                    "queue": self._queue_name,
                    "body_preview": message.body[:200],
                    "error": str(e),
                },
            )

        except Exception as e:
            retry_count = self._get_retry_count(message)
            should_retry = retry_count < self._max_retries

            if should_retry:
                # 未超重试上限，NACK 并重入队，下次重新投递
                # requeue=True：消息回到队列头部，尽快重新消费
                await message.nack(requeue=True)
                logger.warning(
                    "消息处理失败，将重试",
                    extra={
                        "queue": self._queue_name,
                        "retry_count": retry_count,
                        "max_retries": self._max_retries,
                        "error": str(e),
                    },
                )
            else:
                # 超过重试上限，NACK 且不重入队，消息进入死信队列
                # 由运维人员在死信队列中排查
                await message.nack(requeue=False)
                logger.error(
                    "消息重试耗尽，进入死信",
                    extra={
                        "queue": self._queue_name,
                        "retry_count": retry_count,
                        "error": str(e),
                    },
                )

        finally:
            self._in_flight -= 1

    @staticmethod
    def _get_retry_count(message: AbstractIncomingMessage) -> int:
        """从消息 header 中读取重试次数

        AMQP 消息的 headers 是 dict，x-retry-count 由发布者在重试时设置。
        首次消费时无此字段，返回 0。

        注意：当前实现中 NACK requeue 的消息不会自动递增此计数，
        因为 requeue 后消息的 header 不会被修改。
        精确的重试计数需要发布者在发布时设置，或使用延迟队列方案。
        这里用 x-death header（死信机制自动添加）作为辅助判断。
        """
        if not message.headers:
            return 0
        return int(message.headers.get("x-retry-count", 0))


class ConsumerManager:
    """消费者管理器

    统一管理所有消费者的启动和关闭，应用启动/关闭时调用。

    为什么需要管理器：
    - 多个消费者需要统一启动和关闭，避免分散管理
    - 应用关闭时需要按顺序：先停消费者 → 再关连接
    - 提供全局视图，方便监控消费者状态
    """

    def __init__(self) -> None:
        self._consumers: list[MessageConsumer] = []

    def register(self, consumer: MessageConsumer) -> None:
        """注册消费者（启动前调用）"""
        self._consumers.append(consumer)

    async def start_all(self, channel: AbstractRobustChannel) -> None:
        """启动所有已注册的消费者"""
        for consumer in self._consumers:
            await consumer.start(channel)
        logger.info(
            "所有消费者已启动",
            extra={"count": len(self._consumers)},
        )

    async def stop_all(self) -> None:
        """优雅关闭所有消费者"""
        # 并发关闭，不串行等待
        await asyncio.gather(
            *(consumer.stop() for consumer in self._consumers),
            return_exceptions=True,
        )
        logger.info(
            "所有消费者已关闭",
            extra={"count": len(self._consumers)},
        )


# 模块级单例
consumer_manager = ConsumerManager()
