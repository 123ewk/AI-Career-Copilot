"""消息发布者（带 Publisher Confirms 可靠性保证）

职责：
- 封装消息发布逻辑，提供统一的发送接口
- 基于 aio-pika Publisher Confirms 机制保证 Broker 已持久化后才返回
- 消息序列化、持久化控制、指数退避重试、message_id 注入
- 批量发布返回结构化结果（部分失败不抛异常，调用方决定补偿）

设计动机：
- 可靠性优先：项目核心链路（任务分发、Agent 流水线、沟通通知）丢消息
  会直接导致用户错过 offer，必须保证 Broker 已 Ack 才能视为发布成功
- Publisher Confirms 替代 fire-and-forget：aio-pika 9.5 RobustChannel 默认
  publisher_confirms=True，await exchange.publish() 实际已在等 Basic.Ack；
  Broker Nack 时抛出 aio_pika.exceptions.DeliveryError，可走相同重试路径
- message_id 注入：业务侧唯一 ID（task_id / notification_id）写入 AMQP
  message_id 字段 + headers["x-business-id"]，消费者据此去重，避免
  at-least-once 投递导致重复处理
- 批量部分失败不抛：publish_batch 收集每条的成功/失败状态返回给调用方，
  避免 FastAPI 500 掩盖业务可降级的部分场景（如 100 条通知发 97 条）

核心机制：
- aio_pika.Message 封装 AMQP 消息属性（delivery_mode、headers、expiration、
  message_id）
- Publisher Confirms：RobustChannel 默认开启，await publish() 等 Broker Ack，
  超时由 asyncio.wait_for 控制；Nack 抛 DeliveryError 进重试
- 指数退避重试：失败后等待 2^attempt 秒重试，最多 3 次，避免雪崩
- Confirm 超时：单次发布最大等待 confirm_timeout_s 秒（默认 10s），
  防止 Broker 假死阻塞调用方

为什么不使用 AMQP 事务（tx_select/tx_commit）：
- 事务性能差：每条消息都需要 Broker 同步确认，吞吐降低约 10 倍
- 批量场景语义不符：tx_commit 只影响当前 Channel 的后续操作，
  中间失败时已发布消息无法回滚
- 当前方案 Publisher Confirms + 消费者幂等 = at-least-once + exactly-effect，
  满足业务可靠性要求且性能可接受
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractExchange, AbstractRobustChannel
from aio_pika.exceptions import DeliveryError

from app.core.logger import logger

# 默认最大重试次数
DEFAULT_PUBLISH_RETRIES = 3

# 指数退避基础间隔（秒），实际等待 2^attempt 秒
BACKOFF_BASE_SECONDS = 1.0

# Publisher Confirms 超时（秒）：超过此时间未收到 Broker Ack/Nack 视为失败
# 为什么需要：Broker 假死（TCP 看似正常但已停止响应）时 await 会无限挂起，
# 阻塞 FastAPI 请求处理。10s 是经验值，对正常 1-5ms 的 Confirm 留足余量
DEFAULT_CONFIRM_TIMEOUT_S = 10.0

# 批量发布中从 payload 提取业务 ID 的候选 key 列表（按优先级）
# 业务方传 payload 时应保证其中之一存在，否则 publisher 兜底生成 UUID
# 为什么 business_id 排第一：
# - business_id 是业务方传入的稳定 ID，专门用于 MQ 幂等
# - id 在 Task 等表中是 UUID PK（每次新生成），不参与业务去重
# 提取顺序：business_id > task_id > notification_id > id > message_id
# 与 app.domain.common.idempotent._ID_KEYS 保持完全一致
_BATCH_ID_KEYS: tuple[str, ...] = (
    "business_id",
    "task_id",
    "notification_id",
    "id",
    "message_id",
)


@dataclass(frozen=True)
class BatchPublishResult:
    """批量发布结果

    为什么用 dataclass：结构清晰、不可变、字段名即文档，
    调用方只需读 success / failed 即可决策是否告警/补偿。

    字段：
    - success：发布成功的 message_id 列表（顺序与输入一致）
    - failed：发布失败列表，每项为 (message_id, exception) 元组
    """

    success: list[str] = field(default_factory=list)
    failed: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def has_failure(self) -> bool:
        """是否存在失败消息（调用方常用判断）"""
        return bool(self.failed)

    @property
    def total(self) -> int:
        """总消息数（成功 + 失败）"""
        return len(self.success) + len(self.failed)

    @property
    def failure_rate(self) -> float:
        """失败率（0.0 ~ 1.0），无消息时返回 0.0"""
        if self.total == 0:
            return 0.0
        return len(self.failed) / self.total


class MessagePublisher:
    """消息发布者（带 Publisher Confirms 可靠性保证）

    封装消息发布、序列化、持久化、重试、Confirm 等待、message_id 注入。
    通过 Channel 获取 Exchange 并发布消息。

    生命周期：
    1. 初始化时传入 channel（建议使用 RobustChannel，默认开启 Confirms）
    2. 调用 publish() / publish_batch() 发送消息
    3. 不需要显式关闭，channel 由调用方管理

    为什么不用单例：Publisher 是无状态的，每次发布独立，
    channel 由 connection factory 管理，Publisher 不持有连接资源
    """

    def __init__(
        self,
        channel: AbstractRobustChannel,
        *,
        confirm_timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S,
    ) -> None:
        self._channel = channel
        # 单次 Publish 等待 Broker Confirm 的超时（防止 Broker 假死阻塞）
        self._confirm_timeout_s = confirm_timeout_s
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
        message_id: str | None = None,
        persistent: bool = True,
        expiration_ms: int | None = None,
        headers: dict[str, Any] | None = None,
        retries: int = DEFAULT_PUBLISH_RETRIES,
    ) -> str:
        """统一发送接口（带 Publisher Confirms 等待）

        参数：
        - exchange_name：目标 Exchange 名称，使用 exchanges.py 中的常量
        - routing_key：路由键，决定消息投递到哪个 Queue
        - payload：消息体，自动 JSON 序列化
        - message_id：业务侧唯一 ID（如 task_id / notification_id），
          用于消费者去重和审计追踪。不传则自动生成 UUID（兜底）。
          强烈建议业务侧显式传入，便于消息链路追踪
        - persistent：是否持久化（写入磁盘），默认 True
          True 适用于关键业务消息（任务/通知），False 适用于可丢失的日志类消息
        - expiration_ms：单条消息的 TTL（毫秒），None 表示使用队列默认 TTL
        - headers：AMQP 消息头，x-business-id 会自动注入（来自 message_id）
        - retries：发布失败时的最大重试次数

        返回：
        - message_id：实际使用的 message_id（自动生成时为 UUID）

        异常：
        - DeliveryError：Broker Nack（消息未持久化），已重试 retries 次
        - asyncio.TimeoutError：Confirm 超时（Broker 假死），已重试 retries 次
        - 其他 AMQPException：网络错误 / Channel 关闭，已重试 retries 次

        用法：
            publisher = MessagePublisher(channel)
            msg_id = await publisher.publish(
                exchange_name=EXCHANGE_TASK,
                routing_key=ROUTING_TASK_CREATED,
                payload={"task_id": "xxx", "type": "resume_parse"},
                message_id="xxx",  # 业务侧 ID 透传
            )
        """
        # 业务侧未传时兜底生成 UUID，保证每条消息有唯一 ID
        actual_message_id = message_id or str(uuid.uuid4())

        delivery_mode = DeliveryMode.PERSISTENT if persistent else DeliveryMode.NOT_PERSISTENT

        # 合并 header：业务侧 header + x-business-id 注入
        # x-business-id 冗余于 AMQP message_id 字段，
        # 便于在 header 一处集中查看业务上下文（链路追踪、监控）
        merged_headers: dict[str, Any] = dict(headers or {})
        merged_headers["x-business-id"] = actual_message_id

        message = Message(
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            delivery_mode=delivery_mode,
            # content_type 帮助消费者知道如何反序列化
            content_type="application/json",
            # aio-pika expiration 单位是秒（与 RabbitMQ 文档一致）
            expiration=expiration_ms / 1000.0 if expiration_ms else None,
            # AMQP 协议级 message_id 字段：消费者可读取用于去重
            message_id=actual_message_id,
            headers=merged_headers,
        )

        exchange = await self._get_exchange(exchange_name)
        await self._publish_with_retry(
            exchange=exchange,
            routing_key=routing_key,
            message=message,
            max_retries=retries,
        )
        return actual_message_id

    async def _publish_with_retry(
        self,
        exchange: AbstractExchange,
        routing_key: str,
        message: Message,
        max_retries: int,
    ) -> None:
        """带指数退避重试的发布（等待 Publisher Confirms）

        重试策略：
        - 第 1 次失败后等 1 秒重试
        - 第 2 次失败后等 2 秒重试
        - 第 3 次失败后等 4 秒重试
        - 超过 max_retries 后抛出最后一次的异常

        为什么用指数退避而非固定间隔：
        - 瞬时故障（网络抖动）1 秒后大概率恢复
        - 持续故障（Broker 宕机）固定间隔会持续冲击，指数退避逐渐降低压力
        - 避免雪崩：所有发布者同时重试会压垮刚恢复的 Broker

        为什么 DeliveryError / TimeoutError 都走重试：
        - DeliveryError = Broker Nack = Broker 主动拒绝持久化，重试可能成功
        - TimeoutError = Confirm 超时 = Broker 假死/网络异常，重试可能恢复
        - 两者都表示"未确认持久化"，语义上等同，需重试
        """
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                # asyncio.wait_for 给单次 Publish 加 Confirm 超时上限
                # 防止 Broker 假死（TCP 正常但不响应）时无限等待
                await asyncio.wait_for(
                    exchange.publish(message, routing_key=routing_key),
                    timeout=self._confirm_timeout_s,
                )

                if attempt > 0:
                    logger.info(
                        "消息发布成功（重试第 {} 次）",
                        attempt,
                        extra={
                            "exchange": exchange.name,
                            "routing_key": routing_key,
                            "message_id": message.message_id,
                        },
                    )

                return

            except (DeliveryError, TimeoutError) as e:
                # 可靠性失败：Broker 明确拒绝（DeliveryError）或未在超时内确认
                last_error = e
                # DeliveryError 有 frame 属性，记录以便排查
                extra: dict[str, Any] = {
                    "exchange": exchange.name,
                    "routing_key": routing_key,
                    "message_id": message.message_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
                if isinstance(e, DeliveryError):
                    extra["nack_reason"] = str(getattr(e, "reason", ""))

            except Exception as e:
                # 其他异常（网络断开、Channel 关闭等）同样需要重试
                last_error = e
                extra = {
                    "exchange": exchange.name,
                    "routing_key": routing_key,
                    "message_id": message.message_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }

            if attempt < max_retries:
                # 指数退避：1s, 2s, 4s, ...
                wait_seconds = BACKOFF_BASE_SECONDS * (2 ** attempt)
                logger.warning(
                    "消息发布失败（{}），{} 秒后重试（第 {}/{} 次）",
                    type(last_error).__name__,
                    wait_seconds,
                    attempt + 1,
                    max_retries,
                    extra=extra,
                )
                await asyncio.sleep(wait_seconds)
            else:
                # 重试耗尽，记录错误并抛出
                logger.error(
                    "消息发布失败，重试耗尽",
                    extra={**extra, "attempts": max_retries + 1},
                )

        # mypy 无法推断此处 last_error 必不为 None，但逻辑上一定有值
        assert last_error is not None  # for type checker
        raise last_error

    async def publish_batch(
        self,
        exchange_name: str,
        messages: list[tuple[str, dict[str, Any]]],
        *,
        persistent: bool = True,
        retries: int = DEFAULT_PUBLISH_RETRIES,
    ) -> BatchPublishResult:
        """批量发布：返回结构化结果（部分失败不抛异常）

        参数：
        - exchange_name：目标 Exchange
        - messages：列表，每项为 (routing_key, payload) 元组
        - persistent：是否持久化
        - retries：每条消息的重试次数

        返回：
        - BatchPublishResult：包含 success（成功 message_id 列表）、
          failed（失败 (message_id, exception) 列表）

        message_id 自动提取逻辑：
        - 按顺序从 payload 查找 _BATCH_ID_KEYS 中的 key（id / task_id /
          notification_id / message_id），找到则使用其值
        - 都没有则生成 UUID 兜底
        - 业务方应保证 payload 中含业务唯一 ID，否则失败消息无法精确重投

        为什么失败不抛异常：
        - 批量发布中单条失败不应让整个批次返回 500
        - 业务方可按 has_failure / failure_rate 决定告警/重投策略
        - 例如：100 条通知发 97 条成功，3 条失败可由调用方单独补偿

        用法：
            result = await publisher.publish_batch(
                exchange_name=EXCHANGE_NOTIFICATION,
                messages=[
                    (ROUTING_NOTIFICATION_EMAIL, {"id": "n-1", "to": "a@b.com"}),
                    (ROUTING_NOTIFICATION_WECHAT, {"id": "n-2", "openid": "xxx"}),
                ],
            )
            if result.has_failure:
                logger.warning("批量发布部分失败: {}/{}", len(result.failed), result.total)
                for mid, err in result.failed:
                    await retry_later(mid, err)
        """
        success: list[str] = []
        failed: list[tuple[str, Exception]] = []

        for routing_key, payload in messages:
            # 从 payload 自动提取业务 ID，调用方无需关心
            batch_message_id = self._extract_business_id(payload)
            try:
                actual_id = await self.publish(
                    exchange_name=exchange_name,
                    routing_key=routing_key,
                    payload=payload,
                    message_id=batch_message_id,
                    persistent=persistent,
                    retries=retries,
                )
                success.append(actual_id)
            except Exception as e:
                # 单条失败不影响其他消息：收集错误继续
                # 调用方根据 result.has_failure 决定补偿
                failed.append((batch_message_id, e))
                logger.error(
                    "批量发布单条失败",
                    extra={
                        "exchange": exchange_name,
                        "routing_key": routing_key,
                        "message_id": batch_message_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )

        result = BatchPublishResult(success=success, failed=failed)
        if result.has_failure:
            logger.warning(
                "批量发布完成，存在失败",
                extra={
                    "exchange": exchange_name,
                    "total": result.total,
                    "success_count": len(result.success),
                    "failed_count": len(result.failed),
                    "failure_rate": round(result.failure_rate, 4),
                },
            )
        return result

    @staticmethod
    def _extract_business_id(payload: dict[str, Any]) -> str:
        """从 payload 中提取业务唯一 ID（兜底生成 UUID）

        优先按 _BATCH_ID_KEYS 顺序查找；都无值则生成 UUID。

        为什么用多个候选 key：项目不同模块的业务实体命名习惯不同
        （task_id / notification_id / id / message_id），兼容已有用法
        让业务方改动最小。
        """
        for key in _BATCH_ID_KEYS:
            value = payload.get(key)
            if value:  # 非 None 且非空字符串/0
                return str(value)
        return str(uuid.uuid4())
