"""消费者注册中心

职责：
- 提供全局消费者注册表 CONSUMER_REGISTRY，避免 main.py 硬编码
- 提供 @register 装饰器，声明式收集「队列 → handler」映射
- 提供 ConsumerManager 统一管理 start_all / stop_all
- 测试时可清空注册表

设计动机：
- 声明式注册：handler 函数旁边加 @register(...) 即可被自动发现，
  无需在 main.py 中维护一份消费者列表，新增消费者 = 新增一个被装饰的函数
- 关注点分离：业务模块只关心「我处理哪个队列、什么逻辑」，
  启动/关闭/拓扑声明等横切关注点由 ConsumerManager 统一处理
- 单向依赖：业务模块 → registry，registry → consumer，不反向依赖

核心机制：
- Python 模块加载顺序：import main 时会触发所有 router / domain 模块的 import，
  这些模块的 import 又会触发 @register 装饰器执行，从而填充 CONSUMER_REGISTRY
- ConsumerManager 在 lifespan startup 时遍历注册表创建消费者
- 应用关闭时按注册顺序逆序停止，保证先停生产者再停消费者
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aio_pika.abc import AbstractRobustChannel

from app.core.logger import logger
from app.infra.message_queue.consumer import (
    DEFAULT_MAX_RETRIES,
    FunctionConsumer,
    MessageConsumer,
)

# 全局消费者注册表：装饰器填充，ConsumerManager 消费
# 使用 list 而非 dict：保证注册顺序，调试时按文件加载顺序输出
CONSUMER_REGISTRY: list[ConsumerSpec] = []


@dataclass(frozen=True)
class ConsumerSpec:
    """消费者规格

    不可变：装饰器在 import 时执行，spec 不允许后续被修改（防误改）。
    如果需要改配置，请改源码装饰器参数。
    """

    queue_name: str
    # handler 两种形式：
    # 1. async 函数（装饰器用法）
    # 2. MessageConsumer 子类实例（直接复用 ABC 实例，向后兼容）
    handler: Callable[..., Awaitable[Any]] | MessageConsumer
    prefetch_count: int = 10
    max_retries: int = DEFAULT_MAX_RETRIES
    # 重试基础延迟（毫秒），实际 TTL = base * 2^(retry_count-1)，最大 5 分钟
    retry_base_delay_ms: int = 5_000
    # 重试时使用的 routing key，默认与 queue_name 一致
    # 仅在队列绑定到 exchange 的 routing key 与 queue_name 不同时需要显式指定
    retry_routing_key: str | None = None


def register(
    queue_name: str,
    *,
    prefetch_count: int = 10,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_base_delay_ms: int = 5_000,
    retry_routing_key: str | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """消费者注册装饰器

    用法：
        from app.infra.message_queue.registry import register

        @register("copilot.task.created")
        async def handle_task_created(body: dict) -> None:
            ...

    参数：
    - queue_name：消费的队列名（必须与 exchanges.py 中的常量一致）
    - prefetch_count：未确认消息上限，控制并发
    - max_retries：单条消息最大重试次数，超过后进死信
    - retry_base_delay_ms：重试基础延迟，TTL = base * 2^(retry_count-1)
    - retry_routing_key：重试队列的 routing key，None 时用 queue_name

    工作原理：
    1. 装饰器在模块 import 时执行，填充 CONSUMER_REGISTRY
    2. ConsumerManager.start_all() 遍历注册表创建 FunctionConsumer
    3. 装饰器返回原函数，业务代码可正常调用

    为什么用装饰器而非配置文件：
    - 装饰器是 Python 原生机制，IDE 跳转、重构都支持
    - 配置与代码同处一处，修改时不易遗漏
    - 类型注解完整保留（不像 YAML/JSON 字符串无法校验）

    为什么是函数装饰器而非类装饰器：
    - 业务 handler 通常是纯函数，无状态、无需依赖注入
    - 函数式比继承式更轻量，符合「组合优于继承」原则
    - 若需要复杂依赖（DB session、Redis client），用 closure 注入：
        def make_handler(db):
            @register("queue.x")
            async def handler(body):
                await db.execute(...)
            return handler
    """
    # 装饰器入参校验：越早失败越好，import 时就报错比启动时再报错友好
    if not queue_name or not isinstance(queue_name, str):
        raise ValueError(f"queue_name 必须是非空字符串，得到: {queue_name!r}")
    if prefetch_count < 1:
        raise ValueError(f"prefetch_count 必须 >= 1，得到: {prefetch_count}")
    if max_retries < 0:
        raise ValueError(f"max_retries 必须 >= 0，得到: {max_retries}")
    if retry_base_delay_ms < 100:
        raise ValueError(
            f"retry_base_delay_ms 必须 >= 100ms（避免过于频繁重试），得到: {retry_base_delay_ms}"
        )

    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        # 运行时再校验一次：handler 必须是 async 函数
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"@register 装饰的函数必须是 async 函数: {func.__name__!r}"
            )

        spec = ConsumerSpec(
            queue_name=queue_name,
            handler=func,
            prefetch_count=prefetch_count,
            max_retries=max_retries,
            retry_base_delay_ms=retry_base_delay_ms,
            retry_routing_key=retry_routing_key,
        )
        CONSUMER_REGISTRY.append(spec)

        logger.debug(
            "消费者已注册: {} -> {}",
            queue_name,
            func.__name__,
        )
        return func

    return decorator


def clear_registry() -> None:
    """清空注册表（仅用于测试）

    在测试 setup 中调用，避免不同测试间装饰器累积污染。
    """
    CONSUMER_REGISTRY.clear()


class ConsumerManager:
    """消费者管理器

    遍历 CONSUMER_REGISTRY 创建消费者实例，统一 start / stop。
    应用启动时调用 start_all()，关闭时调用 stop_all()。

    与旧的 ConsumerManager 区别：
    - 旧版持有 MessageConsumer 实例列表（外部 register）
    - 新版从 CONSUMER_REGISTRY 懒构建，无需外部手动注册

    启动并发策略：
    - 每个消费者在独立 task 中执行 start()
    - 用 asyncio.gather 等待所有启动完成，单个失败不影响其他
    - 这里 create_task 是为了在启动阶段不阻塞后续业务启动
    """

    def __init__(self) -> None:
        self._consumers: list[MessageConsumer] = []
        self._start_tasks: list[asyncio.Task[None]] = []
        self._started = False

    @property
    def consumers(self) -> list[MessageConsumer]:
        """获取已构建的消费者列表（仅用于测试和监控）"""
        return list(self._consumers)

    def _build_consumers(self) -> list[MessageConsumer]:
        """从注册表构建消费者实例

        支持两种 handler 形式：
        1. async 函数：包成 FunctionConsumer
        2. MessageConsumer 子类实例：直接使用（向后兼容旧的 ABC 风格）

        为什么支持 ABC 风格：
        - 旧代码可能已经继承 MessageConsumer 实现了复杂 handler
        - 渐进式迁移：先让新代码用装饰器，旧代码保持不动
        """
        consumers: list[MessageConsumer] = []
        for spec in CONSUMER_REGISTRY:
            if isinstance(spec.handler, MessageConsumer):
                # ABC 子类实例：直接复用（修改运行时配置不影响 spec）
                consumer = spec.handler
            else:
                # async 函数：包成 FunctionConsumer
                consumer = FunctionConsumer(
                    queue_name=spec.queue_name,
                    handler=spec.handler,
                    prefetch_count=spec.prefetch_count,
                    max_retries=spec.max_retries,
                    retry_base_delay_ms=spec.retry_base_delay_ms,
                    retry_routing_key=spec.retry_routing_key,
                )
            consumers.append(consumer)
        return consumers

    async def start_all(self, channel: AbstractRobustChannel) -> None:
        """启动所有消费者

        流程：
        1. 从注册表构建消费者实例
        2. 为每个消费者创建 asyncio.Task 并发执行 start()
        3. await 所有 task，任意失败时记录并继续（不阻塞其他消费者启动）

        为什么用 create_task：
        - 满足项目要求"start_all() 拉起所有 Consumer（asyncio.create_task）"
        - 避免单个消费者的 start() 阻塞后续消费者
        - 实际消费由 aio-pika 内部事件循环处理，task 完成不代表消费结束
        """
        if self._started:
            logger.warning("ConsumerManager 已启动，跳过重复 start_all")
            return

        self._consumers = self._build_consumers()
        if not self._consumers:
            logger.info("注册表为空，无消费者需要启动")
            self._started = True
            return

        # 并发启动：用 create_task 把每个 start() 包装成独立协程
        self._start_tasks = [
            asyncio.create_task(
                consumer.start(channel),
                name=f"consumer-start-{consumer._queue_name}",
            )
            for consumer in self._consumers
        ]

        # 等待所有启动完成；任一失败不影响其他（return_exceptions=True）
        results = await asyncio.gather(*self._start_tasks, return_exceptions=True)

        # 统计启动结果
        success = sum(1 for r in results if r is None)
        failed = len(results) - success
        if failed > 0:
            failed_queues = [
                self._consumers[i]._queue_name
                for i, r in enumerate(results)
                if r is not None
            ]
            logger.error(
                "部分消费者启动失败",
                extra={"failed_queues": failed_queues, "failed_count": failed},
            )
        else:
            logger.info(
                "所有消费者已启动",
                extra={"count": success},
            )
        self._started = True

    async def stop_all(self) -> None:
        """优雅关闭所有消费者

        流程：
        1. 并发调用每个消费者的 stop()（内部等待 in_flight 归零）
        2. 等待 start_task 结束（实际 start 早已完成，这里保险）

        与启动的区别：stop 不需要 create_task（每个 stop 内部已经是异步等待）
        """
        if not self._started:
            return

        if self._consumers:
            await asyncio.gather(
                *(consumer.stop() for consumer in self._consumers),
                return_exceptions=True,
            )
            logger.info("所有消费者已关闭")

        # 清理 start_tasks（避免悬挂引用）
        self._start_tasks.clear()
        self._started = False


# 模块级单例
consumer_manager = ConsumerManager()
