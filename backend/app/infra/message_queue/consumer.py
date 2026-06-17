"""消息消费者

职责：
- 封装消息消费逻辑，提供统一的订阅接口
- 支持消息确认（ACK）、拒绝（NACK）、延迟重试
- 消费者并发控制、优雅关闭
- 同时支持 ABC 继承式（旧）和函数装饰器式（新）两种 handler 形式

设计动机：
- 统一消费模式：所有消费者继承 MessageConsumer 或用 FunctionConsumer，
  ACK/NACK/重试/日志由基类统一处理，避免每个消费者重复编写
- 重试策略升级：基于「延迟队列 + DLX」方案，每次失败将消息重新发布到
  专属重试队列（带 TTL），TTL 过期后由 RabbitMQ 自动 DLX 回主队列；
  x-retry-count 在 header 中精确递增，避免 nack(requeue=True) 计数丢失
- 并发控制：通过 prefetch_count 限制未确认消息数，
  防止消费者被大量消息压垮导致内存溢出
- 优雅关闭：通过 asyncio.Event 通知消费者停止，等待当前消息处理完毕

核心机制：
- prefetch_count：AMQP QoS 参数，Broker 一次最多推送 N 条未确认消息
  设为 10 表示消费者最多同时持有 10 条未确认消息，
  只有 ACK 一条后 Broker 才会推送新消息，实现背压（backpressure）
- 延迟重试：失败时 publish 到 retry exchange，header 中带 x-retry-count
  消息 expiration = base * 2^(count-1) 毫秒
  RabbitMQ 在 TTL 过期后通过 DLX 自动路由回原 exchange（原 routing key）
- 优雅关闭：设置 shutdown_event → 消费回调检测到后不再接收新消息
  → 等待 in_flight 计数归零 → 取消消费者标签

重试方案对比：
┌──────────────────┬──────────────────────┬────────────────────────┐
│ 方案              │ 优点                 │ 缺点                    │
├──────────────────┼──────────────────────┼────────────────────────┤
│ nack(requeue=True)│ 简单                 │ 计数不准确、立即重试    │
│ 延迟队列+DLX（采用）│ 计数精确、可控退避    │ 需多一组队列拓扑        │
└──────────────────┴──────────────────────┴────────────────────────┘
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, cast

from aio_pika import DeliveryMode, Message
from aio_pika.abc import (
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractRobustChannel,
)

from app.core.logger import logger
from app.infra.message_queue.exchanges import EXCHANGE_RETRY

# 默认最大重试次数，超过后消息进入死信队列
DEFAULT_MAX_RETRIES = 3

# 重试最大延迟（毫秒），防止无限指数增长
MAX_RETRY_DELAY_MS = 5 * 60 * 1000


class MessageConsumer(ABC):
    """消息消费者基类

    子类只需实现 handle_message()，ACK/NACK/重试/日志由基类统一处理。

    生命周期：
    1. start() → 创建 Channel、设置 QoS、注册消费者
    2. 消息到达 → _on_message() → handle_message() → ACK/NACK/重试
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
        retry_base_delay_ms: int = 5_000,
        retry_routing_key: str | None = None,
    ) -> None:
        """初始化消费者

        参数：
        - queue_name：消费的队列名称，必须与 exchanges.py 中定义一致
        - prefetch_count：未确认消息上限，控制并发度
          设为 1 是严格顺序消费，设为 N 是并发消费（N 越大吞吐越高）
        - max_retries：单条消息最大重试次数，超过后进入死信队列
        - retry_base_delay_ms：重试基础延迟（毫秒），
          实际 TTL = base * 2^(retry_count-1)，最大 5 分钟
        - retry_routing_key：重试时使用的 routing key，默认与 queue_name 一致
        """
        self._queue_name = queue_name
        self._prefetch_count = prefetch_count
        self._max_retries = max_retries
        self._retry_base_delay_ms = retry_base_delay_ms
        self._retry_routing_key = retry_routing_key or queue_name
        self._shutdown_event = asyncio.Event()
        # 当前正在处理的消息数，用于优雅关闭时等待
        self._in_flight = 0
        self._consumer_tag: str | None = None
        self._channel: AbstractRobustChannel | None = None
        # aio-pika 队列对象（start() 时赋值，stop() 时调用 cancel 消费者）
        self._queue_obj: Any = None
        # Exchange 缓存：避免每次重试都 get_exchange
        self._retry_exchange_obj: AbstractExchange | None = None

    @abstractmethod
    async def handle_message(self, body: dict[str, Any]) -> None:
        """处理消息的业务逻辑（子类实现）

        参数是已反序列化的 dict，基类负责 JSON 解码。
        抛出异常视为处理失败，基类会根据重试次数决定延迟重试或进入死信。
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
        # 保存 queue 引用用于后续取消消费者
        self._queue_obj = queue

        # 预取重试 exchange（缓存避免每次重试都查一次）
        self._retry_exchange_obj = await channel.get_exchange(EXCHANGE_RETRY)

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
        # aio-pika 推荐使用 queue.cancel(consumer_tag) 而非 channel.basic_cancel，
        # 后者不在 AbstractRobustChannel 的类型签名中（aio-pika 类型遗漏）
        if self._consumer_tag and self._queue_obj is not None:
            await self._queue_obj.cancel(self._consumer_tag)
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
        5. 失败 → 计算新 retry_count：
           - 未超限：publish 到 retry exchange（带 expiration）
                    → 成功后 ack 原消息
           - 超限：NACK(requeue=False) 进入死信
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

            try:
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
                # 仅在 handle_message 内部出现 JSON 错误（理论上不会，
                # 因为 body 已在上面 json.loads 过，这里是防御性代码）
                await message.nack(requeue=False)
                logger.error(
                    "消息处理异常（JSON 错误），进入死信",
                    extra={
                        "queue": self._queue_name,
                        "error": str(e),
                    },
                )

            except Exception as e:
                # 业务异常：进入重试流程
                await self._handle_retry(message, retry_count, e)

        except json.JSONDecodeError as e:
            # 消息体本身不是合法 JSON：无法重试（重试也是同样的 body），直接进死信
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
            # 兜底：连 JSON 解码之外的所有异常都捕获
            # 确保 _in_flight 一定递减，避免优雅关闭时永远等不到归零
            logger.exception(
                "_on_message 未预期异常",
                extra={
                    "queue": self._queue_name,
                    "error": str(e),
                },
            )
            # 谨慎选择：nack(requeue=True) 让消息被其他消费者重试
            try:
                await message.nack(requeue=True)
            except Exception:
                logger.exception("兜底 nack 也失败")

        finally:
            self._in_flight -= 1

    async def _handle_retry(
        self,
        message: AbstractIncomingMessage,
        retry_count: int,
        error: Exception,
    ) -> None:
        """重试处理：发布到重试队列（延迟队列 + DLX 回收）

        为什么不直接 nack(requeue=True)：
        1. requeue 后消息 header 不会更新，x-retry-count 始终为 0
        2. requeue 立即重新消费，没有延迟退避，可能雪崩
        3. 多次失败时无法区分「第 N 次失败」

        流程：
        1. 递增 x-retry_count
        2. 计算本次重试的 TTL（指数退避）
        3. publish 到 retry exchange（带 expiration header）
        4. publish 成功 → ack 原消息（避免重复消费）
        5. publish 失败 → nack(requeue=True) 走原始路径重试

        为什么 publish 成功才 ack：
        - 如果先 ack 再 publish，publish 失败时消息已丢失
        - 如果先 publish 再 ack，publish 失败时原消息还在，可走 nack 兜底
        - 极端情况：publish 成功但 ack 失败 → 消息会重复一次（消费侧需幂等）
          这是 at-least-once 的标准取舍

        为什么用 expiration 而非队列固定 TTL：
        - 队列固定 TTL 无法实现「每次重试不同延迟」的指数退避
        - expiration 是消息级 TTL，可以每次重试动态设置
        - 兜底：重试队列本身设一个较大的 x-message-ttl（如 5 分钟），
          防止 expiration 异常时消息永久堆积
        """
        new_retry_count = retry_count + 1

        if new_retry_count > self._max_retries:
            # 超过最大重试：NACK 不重入队，消息进入死信
            await message.nack(requeue=False)
            logger.error(
                "消息重试耗尽，进入死信",
                extra={
                    "queue": self._queue_name,
                    "retry_count": retry_count,
                    "max_retries": self._max_retries,
                    "error": str(error),
                },
            )
            return

        # 计算指数退避 TTL（毫秒）
        retry_ttl_ms = self._compute_retry_ttl_ms(new_retry_count)
        # aio-pika expiration 单位是秒
        retry_ttl_seconds = retry_ttl_ms / 1000.0

        # 合并 header：保留原 header，覆写 x-retry-count
        new_headers: dict[str, Any] = dict(message.headers or {})
        new_headers["x-retry-count"] = new_retry_count
        # 记录原始队列名（便于排查）
        new_headers["x-original-queue"] = self._queue_name
        # 记录最后一次错误（便于排查，无需查日志）
        new_headers["x-last-error"] = str(error)[:500]

        retry_message = Message(
            body=message.body,
            content_type=message.content_type or "application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            headers=new_headers,
            expiration=retry_ttl_seconds,
        )

        try:
            # publish 到重试 exchange，routing_key 决定进入哪个重试队列
            assert self._retry_exchange_obj is not None  # start() 时已初始化
            await self._retry_exchange_obj.publish(
                retry_message,
                routing_key=self._retry_routing_key,
            )
            # publish 成功：ack 原消息，避免重复消费
            await message.ack()

            logger.warning(
                "消息处理失败，已发到重试队列",
                extra={
                    "queue": self._queue_name,
                    "retry_count": new_retry_count,
                    "max_retries": self._max_retries,
                    "retry_ttl_ms": retry_ttl_ms,
                    "error": str(error),
                },
            )

        except Exception as publish_error:
            # publish 失败：放弃重试方案，走 nack(requeue=True) 兜底
            # 风险：nack requeue 会立即重试，没有延迟
            # 但总比消息丢失好——至少有重试机会
            logger.error(
                "重试队列发布失败，降级为 nack requeue",
                extra={
                    "queue": self._queue_name,
                    "retry_count": retry_count,
                    "publish_error": str(publish_error),
                    "original_error": str(error),
                },
            )
            await message.nack(requeue=True)

    def _compute_retry_ttl_ms(self, retry_count: int) -> int:
        """计算本次重试的 TTL（指数退避）

        公式：base * 2^(retry_count-1)，最大 MAX_RETRY_DELAY_MS
        - 第 1 次重试：base * 1
        - 第 2 次重试：base * 2
        - 第 3 次重试：base * 4
        - ...

        为什么指数退避：
        - 瞬时错误（DB 抖动）大概率在 1-2 次重试内恢复
        - 持续错误（下游服务宕机）需要更长等待，给系统恢复时间
        - 比固定间隔更能避免「雷暴群」效应

        为什么设上限：
        - 无上限可能产生 30 分钟延迟，对时效性业务不可接受
        - 5 分钟是常见 SRE 经验值，可配置
        """
        if retry_count < 1:
            return self._retry_base_delay_ms
        delay: int = self._retry_base_delay_ms * (2 ** (retry_count - 1))
        return min(delay, MAX_RETRY_DELAY_MS)

    @staticmethod
    def _get_retry_count(message: AbstractIncomingMessage) -> int:
        """从消息 header 中读取重试次数

        AMQP 消息的 headers 是 dict，x-retry-count 由发布者在重试时设置。
        首次消费时无此字段，返回 0。

        类型处理：AMQP header value 可能是 bytes / str / int 等，
        统一用 int() 转换；类型不匹配时安全返回 0。
        """
        if not message.headers:
            return 0
        raw = message.headers.get("x-retry-count", 0)
        if isinstance(raw, bool):  # bool 是 int 子类，但语义上不算"次数"
            return int(raw)
        if isinstance(raw, int):
            return raw
        try:
            return int(cast(Any, raw))  # bytes/str → int
        except (TypeError, ValueError):
            return 0


class FunctionConsumer(MessageConsumer):
    """函数式消费者（用于装饰器注册）

    与 MessageConsumer ABC 的区别：
    - ABC 子类通过 override handle_message 提供业务逻辑
    - FunctionConsumer 构造时直接传入 async 函数作为 handler
    - 适用场景：纯函数式业务逻辑，无需维护实例状态

    典型用法（通过 registry.register 装饰器）：
        @register("copilot.task.created")
        async def handle_task_created(body: dict) -> None:
            ...

    为什么需要这个子类：
    - 装饰器收集的 handler 是函数，不是类
    - 直接用 MessageConsumer ABC 会让装饰器用户被迫继承类
    - 函数式更轻量，符合 Pythonic 风格
    """

    def __init__(
        self,
        queue_name: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
        prefetch_count: int = 10,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_delay_ms: int = 5_000,
        retry_routing_key: str | None = None,
    ) -> None:
        super().__init__(
            queue_name=queue_name,
            prefetch_count=prefetch_count,
            max_retries=max_retries,
            retry_base_delay_ms=retry_base_delay_ms,
            retry_routing_key=retry_routing_key,
        )
        self._handler = handler

    async def handle_message(self, body: dict[str, Any]) -> None:
        """委托给构造时传入的 handler"""
        await self._handler(body)
