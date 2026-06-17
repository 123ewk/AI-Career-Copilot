"""RabbitMQ 消息队列基础设施

公开 API（外部只需 from app.infra.message_queue import ...）：
- MessageConsumer, FunctionConsumer: 消费者基类
- register, CONSUMER_REGISTRY, consumer_manager: 消费者注册中心
- declare_all: 拓扑声明
- rabbitmq_connection_factory: 连接工厂
- get_rabbitmq_channel: FastAPI 依赖注入
- MessagePublisher: 消息发布者
- exchanges 模块下的常量: 队列/交换机/路由键
"""

from app.infra.message_queue.connection import (
    RabbitMQConnectionFactory,
    get_rabbitmq_channel,
    rabbitmq_connection_factory,
)
from app.infra.message_queue.consumer import (
    DEFAULT_MAX_RETRIES,
    MAX_RETRY_DELAY_MS,
    FunctionConsumer,
    MessageConsumer,
)
from app.infra.message_queue.publisher import MessagePublisher
from app.infra.message_queue.registry import (
    CONSUMER_REGISTRY,
    ConsumerManager,
    ConsumerSpec,
    clear_registry,
    consumer_manager,
    register,
)

__all__ = [
    # 连接
    "RabbitMQConnectionFactory",
    "rabbitmq_connection_factory",
    "get_rabbitmq_channel",
    # 消费者
    "MessageConsumer",
    "FunctionConsumer",
    "DEFAULT_MAX_RETRIES",
    "MAX_RETRY_DELAY_MS",
    # 注册中心
    "CONSUMER_REGISTRY",
    "ConsumerSpec",
    "ConsumerManager",
    "consumer_manager",
    "register",
    "clear_registry",
    # 发布者
    "MessagePublisher",
]
