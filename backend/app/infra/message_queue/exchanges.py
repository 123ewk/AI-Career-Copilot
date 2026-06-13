"""Exchange 和 Queue 定义

职责：
- 集中定义项目中所有 RabbitMQ Exchange 和 Queue
- 确保队列和交换机的命名规范、参数一致
- 应用启动时自动声明，避免手动在 RabbitMQ 管理后台创建

设计动机：
- 集中声明拓扑：所有 Exchange/Queue/Binding 在一处定义，
  避免各模块分散声明导致命名冲突或参数不一致
- 启动时声明：应用启动时调用 declare_all()，确保 Broker 上拓扑存在，
  即使 Broker 被清空也能自动恢复
- 命名规范：exchange 用大驼峰，queue 用蛇形，routing_key 用点分命名

消息流设计（基于项目领域模块推导）：

1. 任务事件流：agent 执行任务 → 发布事件 → 消费者处理
   copilot.task.exchange (Topic)
     ├── queue: copilot.task.created    ← routing_key: task.created
     ├── queue: copilot.task.completed  ← routing_key: task.completed
     └── queue: copilot.task.failed     ← routing_key: task.failed

2. 工作流事件流：workflow 状态变更通知
   copilot.workflow.exchange (Topic)
     ├── queue: copilot.workflow.started    ← routing_key: workflow.started
     ├── queue: copilot.workflow.completed  ← routing_key: workflow.completed
     └── queue: copilot.workflow.failed     ← routing_key: workflow.failed

3. 通信通知流：邮件/微信/Webhook 等通知发送
   copilot.notification.exchange (Direct)
     ├── queue: copilot.notification.email   ← routing_key: email
     ├── queue: copilot.notification.wechat  ← routing_key: wechat
     └── queue: copilot.notification.webhook ← routing_key: webhook

4. 简历解析流：上传简历 → 异步解析 → 结果回调
   copilot.resume.exchange (Direct)
     └── queue: copilot.resume.parse ← routing_key: resume.parse

5. 职位匹配流：新职位 → 异步匹配 → 结果通知
   copilot.match.exchange (Direct)
     └── queue: copilot.match.compute ← routing_key: match.compute

6. 死信队列：消费失败的消息进入死信，人工排查
   copilot.dlx.exchange (Fanout)
     └── queue: copilot.dlx.dead_letter
"""

from aio_pika import ExchangeType
from aio_pika.abc import AbstractRobustChannel

from app.core.settings import get_settings


# ==================== 命名常量 ====================

# Exchange 名称
EXCHANGE_TASK = "copilot.task.exchange"
EXCHANGE_WORKFLOW = "copilot.workflow.exchange"
EXCHANGE_NOTIFICATION = "copilot.notification.exchange"
EXCHANGE_RESUME = "copilot.resume.exchange"
EXCHANGE_MATCH = "copilot.match.exchange"
EXCHANGE_DLX = "copilot.dlx.exchange"

# Queue 名称
QUEUE_TASK_CREATED = "copilot.task.created"
QUEUE_TASK_COMPLETED = "copilot.task.completed"
QUEUE_TASK_FAILED = "copilot.task.failed"

QUEUE_WORKFLOW_STARTED = "copilot.workflow.started"
QUEUE_WORKFLOW_COMPLETED = "copilot.workflow.completed"
QUEUE_WORKFLOW_FAILED = "copilot.workflow.failed"

QUEUE_NOTIFICATION_EMAIL = "copilot.notification.email"
QUEUE_NOTIFICATION_WECHAT = "copilot.notification.wechat"
QUEUE_NOTIFICATION_WEBHOOK = "copilot.notification.webhook"

QUEUE_RESUME_PARSE = "copilot.resume.parse"

QUEUE_MATCH_COMPUTE = "copilot.match.compute"

QUEUE_DLX_DEAD_LETTER = "copilot.dlx.dead_letter"

# Routing Key
ROUTING_TASK_CREATED = "task.created"
ROUTING_TASK_COMPLETED = "task.completed"
ROUTING_TASK_FAILED = "task.failed"

ROUTING_WORKFLOW_STARTED = "workflow.started"
ROUTING_WORKFLOW_COMPLETED = "workflow.completed"
ROUTING_WORKFLOW_FAILED = "workflow.failed"

ROUTING_NOTIFICATION_EMAIL = "email"
ROUTING_NOTIFICATION_WECHAT = "wechat"
ROUTING_NOTIFICATION_WEBHOOK = "webhook"

ROUTING_RESUME_PARSE = "resume.parse"

ROUTING_MATCH_COMPUTE = "match.compute"


# ==================== 拓扑声明 ====================

async def declare_all(channel: AbstractRobustChannel) -> None:
    """声明所有 Exchange、Queue 和绑定关系

    应用启动时调用，确保 Broker 上拓扑完整。
    声明是幂等的：如果 Exchange/Queue 已存在且参数一致，不会重复创建；
    如果参数不一致会抛异常（防止误改参数导致静默不一致）。

    为什么用 RobustChannel：重连后自动重新声明，无需手动处理
    """
    settings = get_settings()
    # 死信队列的 TTL，消费失败后消息保留 7 天供排查
    dlx_message_ttl_ms = 7 * 24 * 3600 * 1000

    # ---------- 死信 Exchange（Fanout：所有死信消息都路由到同一队列）----------
    dlx_exchange = await channel.declare_exchange(
        EXCHANGE_DLX,
        ExchangeType.FANOUT,
        durable=True, # 持久化，确保在 Broker 重启后存在
    )
    await channel.declare_queue(
        QUEUE_DLX_DEAD_LETTER,
        durable=True,
        arguments={
            # 死信消息保留 7 天
            "x-message-ttl": dlx_message_ttl_ms,
        },
    )
    await (await channel.get_queue(QUEUE_DLX_DEAD_LETTER)).bind(dlx_exchange)

    # ---------- 任务事件 Exchange（Topic：支持 task.* 通配订阅）----------
    task_exchange = await channel.declare_exchange(
        EXCHANGE_TASK,
        ExchangeType.TOPIC,
        durable=True,
    )

    # 业务队列的公共参数：消费失败进入死信 + 单条消息 TTL
    task_queue_args = {
        # 消费失败（nack 且不重入队）时转发到死信 Exchange
        "x-dead-letter-exchange": EXCHANGE_DLX,
        # 消息在队列中最多存活 1 小时，超时未消费则进入死信
        # 防止积压过久导致内存压力
        "x-message-ttl": 3600 * 1000,
    }

    for queue_name, routing_key in [
        (QUEUE_TASK_CREATED, ROUTING_TASK_CREATED),
        (QUEUE_TASK_COMPLETED, ROUTING_TASK_COMPLETED),
        (QUEUE_TASK_FAILED, ROUTING_TASK_FAILED),
    ]:
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments=task_queue_args,
        )
        await queue.bind(task_exchange, routing_key=routing_key)

    # ---------- 工作流事件 Exchange（Topic）----------
    workflow_exchange = await channel.declare_exchange(
        EXCHANGE_WORKFLOW,
        ExchangeType.TOPIC,
        durable=True,
    )

    workflow_queue_args = {
        "x-dead-letter-exchange": EXCHANGE_DLX,
        "x-message-ttl": 3600 * 1000,
    }

    for queue_name, routing_key in [
        (QUEUE_WORKFLOW_STARTED, ROUTING_WORKFLOW_STARTED),
        (QUEUE_WORKFLOW_COMPLETED, ROUTING_WORKFLOW_COMPLETED),
        (QUEUE_WORKFLOW_FAILED, ROUTING_WORKFLOW_FAILED),
    ]:
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments=workflow_queue_args,
        )
        await queue.bind(workflow_exchange, routing_key=routing_key)

    # ---------- 通信通知 Exchange（Direct：精确路由，一种通知类型一个队列）----------
    notification_exchange = await channel.declare_exchange(
        EXCHANGE_NOTIFICATION,
        ExchangeType.DIRECT,
        durable=True,
    )

    notification_queue_args = {
        "x-dead-letter-exchange": EXCHANGE_DLX,
        # 通知消息 TTL 30 分钟，过期则不再发送（通知有时效性）
        "x-message-ttl": 30 * 60 * 1000,
    }

    for queue_name, routing_key in [
        (QUEUE_NOTIFICATION_EMAIL, ROUTING_NOTIFICATION_EMAIL),
        (QUEUE_NOTIFICATION_WECHAT, ROUTING_NOTIFICATION_WECHAT),
        (QUEUE_NOTIFICATION_WEBHOOK, ROUTING_NOTIFICATION_WEBHOOK),
    ]:
        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments=notification_queue_args,
        )
        await queue.bind(notification_exchange, routing_key=routing_key)

    # ---------- 简历解析 Exchange（Direct）----------
    resume_exchange = await channel.declare_exchange(
        EXCHANGE_RESUME,
        ExchangeType.DIRECT,
        durable=True,
    )

    resume_queue_args = {
        "x-dead-letter-exchange": EXCHANGE_DLX,
        # 简历解析消息 TTL 2 小时，大文件解析可能耗时较长
        "x-message-ttl": 2 * 3600 * 1000,
    }

    resume_queue = await channel.declare_queue(
        QUEUE_RESUME_PARSE,
        durable=True,
        arguments=resume_queue_args,
    )
    await resume_queue.bind(resume_exchange, routing_key=ROUTING_RESUME_PARSE)

    # ---------- 职位匹配 Exchange（Direct）----------
    match_exchange = await channel.declare_exchange(
        EXCHANGE_MATCH,
        ExchangeType.DIRECT,
        durable=True,
    )

    match_queue_args = {
        "x-dead-letter-exchange": EXCHANGE_DLX,
        "x-message-ttl": 2 * 3600 * 1000,
    }

    match_queue = await channel.declare_queue(
        QUEUE_MATCH_COMPUTE,
        durable=True,
        arguments=match_queue_args,
    )
    await match_queue.bind(match_exchange, routing_key=ROUTING_MATCH_COMPUTE)
