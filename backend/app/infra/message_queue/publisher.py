"""消息发布者

职责：
- 封装消息发布逻辑，提供统一的发送接口
- 支持同步发送和异步确认两种模式
- 消息序列化、持久化控制、重试机制

设计动机：
- 统一发送接口：业务层只需调用 publish(exchange, routing_key, payload)，
  序列化、持久化、确认模式由发布者统一控制
- 持久化控制：delivery_mode=2 表示消息写入磁盘，Broker 重启不丢失；
  delivery_mode=1 表示仅内存，性能更高但可能丢失
- 重试机制：发布失败时指数退避重试，避免瞬时网络抖动导致消息丢失
- 异步确认：Publisher Confirms 模式，Broker 确认消息已持久化后才返回，
  代价是吞吐降低，适用于关键业务消息

核心机制：
- aio_pika.Message 封装 AMQP 消息属性（delivery_mode、headers、expiration 等）
- Publisher Confirms：channel.set_qos() + await publish()，
  Broker 返回 Basic.Ack 确认消息已写入队列
- 指数退避重试：失败后等待 2^attempt 秒重试，最多 3 次，
  避免雪崩式重试压垮 Broker
"""

import json
from typing import Any

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractExchange, AbstractRobustChannel

from app.core.logger import logger

# 默认最大重试次数
DEFAULT_PUBLISH_RETRIES = 3

# 指数退避基础间隔（秒），实际等待 2^attempt 秒
BACKOFF_BASE_SECONDS = 1.0


class MessagePublisher:
    """消息发布者

    封装消息发布、序列化、持久化、重试逻辑。
    通过 Channel 获取 Exchange 并发布消息。

    生命周期：
    1. 初始化时传入 channel
    2. 调用 publish() 发送消息
    3. 不需要显式关闭，channel 由调用方管理

    为什么不用单例：Publisher 是无状态的，每次发布独立，
    channel 由 connection factory 管理，Publisher 不持有连接资源
    """

    def __init__(self, channel: AbstractRobustChannel) -> None:
        self._channel = channel
        # Exchange 缓存：避免每次发布都调用 get_exchange（一次 AMQP 帧）
        # 键为 exchange 名称，值为已获取的 Exchange 对象
        self._exchange_cache: dict[str, AbstractExchange] = {}

    async def _get_exchange(self, exchange_name: str) -> AbstractExchange:
        """获取 Exchange（带缓存）

        为什么缓存：get_exchange 本质是 Basic.Declare（幂等），
        但每次调用仍有一次 AMQP 帧往返，缓存后零网络开销。
        RobustChannel 重连后会自动恢复 Exchange，缓存仍然有效。
        """
        if exchange_name not in self._exchange_cache:
            self._exchange_cache[exchange_name] = await self._channel.get_exchange(
                exchange_name
            )
        return self._exchange_cache[exchange_name]

    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        payload: dict[str, Any],
        *,
        persistent: bool = True,
        expiration_ms: int | None = None,
        headers: dict[str, Any] | None = None,
        retries: int = DEFAULT_PUBLISH_RETRIES,
    ) -> None:
        """统一发送接口

        参数：
        - exchange_name：目标 Exchange 名称，使用 exchanges.py 中的常量
        - routing_key：路由键，决定消息投递到哪个 Queue
        - payload：消息体，自动 JSON 序列化
        - persistent：是否持久化（写入磁盘），默认 True
          True 适用于关键业务消息（任务/通知），False 适用于可丢失的日志类消息
        - expiration_ms：单条消息的 TTL（毫秒），None 表示使用队列默认 TTL
        - headers：AMQP 消息头，可用于传递 trace_id、retry_count 等元数据
        - retries：发布失败时的最大重试次数

        用法：
            publisher = MessagePublisher(channel)
            await publisher.publish(
                exchange_name=EXCHANGE_TASK,
                routing_key=ROUTING_TASK_CREATED,
                payload={"task_id": "xxx", "type": "resume_parse"},
            )
        """
        delivery_mode = DeliveryMode.PERSISTENT if persistent else DeliveryMode.NOT_PERSISTENT

        message = Message(
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            delivery_mode=delivery_mode,
            # content_type 帮助消费者知道如何反序列化
            content_type="application/json",
            expiration=expiration_ms,
            headers=headers,
        )

        exchange = await self._get_exchange(exchange_name)
        await self._publish_with_retry(
            exchange=exchange,
            routing_key=routing_key,
            message=message,
            max_retries=retries,
        )

    async def _publish_with_retry(
        self,
        exchange: AbstractExchange,
        routing_key: str,
        message: Message,
        max_retries: int,
    ) -> None:
        """带指数退避重试的发布

        重试策略：
        - 第 1 次失败后等 1 秒重试
        - 第 2 次失败后等 2 秒重试
        - 第 3 次失败后等 4 秒重试
        - 超过 max_retries 后抛出异常

        为什么用指数退避而非固定间隔：
        - 瞬时故障（网络抖动）1 秒后大概率恢复
        - 持续故障（Broker 宕机）固定间隔会持续冲击，指数退避逐渐降低压力
        - 避免雪崩：所有发布者同时重试会压垮刚恢复的 Broker
        """
        import asyncio

        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                await exchange.publish(message, routing_key=routing_key)

                if attempt > 0:
                    logger.info(
                        "消息发布成功（重试第 {} 次）",
                        attempt,
                        extra={
                            "exchange": exchange.name,
                            "routing_key": routing_key,
                        },
                    )

                return

            except Exception as e:
                last_error = e

                if attempt < max_retries:
                    # 指数退避：1s, 2s, 4s, ...
                    wait_seconds = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    logger.warning(
                        "消息发布失败，{} 秒后重试（第 {}/{} 次）",
                        wait_seconds,
                        attempt + 1,
                        max_retries,
                        extra={
                            "exchange": exchange.name,
                            "routing_key": routing_key,
                            "error": str(e),
                        },
                    )
                    await asyncio.sleep(wait_seconds)
                else:
                    # 重试耗尽，记录错误并抛出
                    logger.error(
                        "消息发布失败，重试耗尽",
                        extra={
                            "exchange": exchange.name,
                            "routing_key": routing_key,
                            "attempts": max_retries + 1,
                            "error": str(e),
                        },
                    )

        # mypy 无法推断此处 last_error 必不为 None，但逻辑上一定有值
        raise last_error  # type: ignore[misc]

    async def publish_batch(
        self,
        exchange_name: str,
        messages: list[tuple[str, dict[str, Any]]],
        *,
        persistent: bool = True,
        retries: int = DEFAULT_PUBLISH_RETRIES,
    ) -> None:
        """批量发布消息

        参数：
        - exchange_name：目标 Exchange
        - messages：列表，每项为 (routing_key, payload) 元组
        - persistent：是否持久化
        - retries：每条消息的重试次数

        为什么不使用 AMQP 事务（tx_select/tx_commit）：
        - 事务性能差：每条消息都需要 Broker 确认，吞吐降低 10 倍
        - 批量发布只是语法糖，不保证原子性，每条消息独立确认
        - 如需原子性，应使用 Publisher Confirms + 业务层幂等

        用法：
            await publisher.publish_batch(
                exchange_name=EXCHANGE_NOTIFICATION,
                messages=[
                    (ROUTING_NOTIFICATION_EMAIL, {"to": "a@b.com", "body": "..."}),
                    (ROUTING_NOTIFICATION_WECHAT, {"openid": "xxx", "body": "..."}),
                ],
            )
        """
        for routing_key, payload in messages:
            await self.publish(
                exchange_name=exchange_name,
                routing_key=routing_key,
                payload=payload,
                persistent=persistent,
                retries=retries,
            )
