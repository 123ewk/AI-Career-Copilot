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

7. 重试队列（延迟队列 + DLX）：消费失败的消息按指数退避重新投递
   copilot.retry.exchange (Direct)
     ├── queue: copilot.task.created.retry      ← routing_key: copilot.task.created
     ├── queue: copilot.task.completed.retry    ← routing_key: copilot.task.completed
     ├── queue: copilot.task.failed.retry       ← routing_key: copilot.task.failed
     ├── queue: copilot.workflow.started.retry  ← routing_key: copilot.workflow.started
     ├── queue: copilot.workflow.completed.retry← routing_key: copilot.workflow.completed
     ├── queue: copilot.workflow.failed.retry   ← routing_key: copilot.workflow.failed
     ├── queue: copilot.notification.email.retry  ← routing_key: copilot.notification.email
     ├── queue: copilot.notification.wechat.retry ← routing_key: copilot.notification.wechat
     ├── queue: copilot.notification.webhook.retry← routing_key: copilot.notification.webhook
     ├── queue: copilot.resume.parse.retry      ← routing_key: copilot.resume.parse
     └── queue: copilot.match.compute.retry     ← routing_key: copilot.match.compute

   每个重试队列绑定一个主队列：
   - x-dead-letter-exchange = 主 exchange
   - x-dead-letter-routing-key = 主 routing key
   - x-message-ttl = 5 分钟（兜底上限）
   消息在重试队列中等待消息级 expiration 触发后，自动 DLX 回主 exchange。

   消息流：
   失败 → consumer publish 到 retry.exchange (routing_key=queue_name)
        → 进入对应 retry queue
        → 等待消息级 expiration（指数退避）
        → TTL 过期 → DLX 回主 exchange
        → 回到主 queue
        → 消费者再次拉取，header 中 x-retry-count 已递增
"""

from aio_pika import ExchangeType
from aio_pika.abc import AbstractRobustChannel

# ==================== 命名常量 ====================

# Exchange 名称
EXCHANGE_TASK = "copilot.task.exchange"
EXCHANGE_WORKFLOW = "copilot.workflow.exchange"
EXCHANGE_NOTIFICATION = "copilot.notification.exchange"
EXCHANGE_RESUME = "copilot.resume.exchange"
EXCHANGE_MATCH = "copilot.match.exchange"
EXCHANGE_DLX = "copilot.dlx.exchange"
EXCHANGE_RETRY = "copilot.retry.exchange"

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

# ==================== 重试队列映射 ====================
# 每个主队列对应一个重试队列，DLX 回主 exchange
# 命名规则：{原 queue_name}.retry
# 重试 routing key：默认与原 queue_name 一致
RETRY_QUEUE_SUFFIX = ".retry"
# 队列级最大 TTL：5 分钟兜底（消息级 expiration 通常更短）
# 防止 expiration 异常时消息永久积压
RETRY_QUEUE_MAX_TTL_MS = 5 * 60 * 1000


# ==================== 拓扑声明 ====================

async def _declare_main_queue_with_retry(
    channel: AbstractRobustChannel,
    main_exchange_name: str,
    main_exchange_type: ExchangeType,
    queue_name: str,
    routing_key: str,
    *,
    queue_message_ttl_ms: int,
) -> None:
    """声明一个主队列 + 对应的重试队列

    主队列：
    - 绑定到业务 exchange（main_exchange）
    - nack(requeue=False) 时消息进入 DLX
    - x-message-ttl 防止消息无限期积压

    重试队列：
    - 绑定到 retry exchange（EXCHANGE_RETRY），routing_key = 原 queue_name
    - 消息级 expiration 触发后 → DLX 回主 exchange（main_exchange），
      路由回主队列（routing_key = 原 routing_key）
    - x-message-ttl 作为兜底，防止 expiration 异常时消息永久积压

    为什么 routing_key 用 queue_name 而非 routing_key：
    - 业务 consumer publish 时只关心 queue_name，不需要知道 binding routing_key
    - 重试交换机的 binding 是「按队列一一对应」，用 queue_name 作 key 直观一致
    """
    # 声明主 exchange
    main_exchange = await channel.declare_exchange(
        main_exchange_name,
        main_exchange_type,
        durable=True,
    )

    # 主队列参数
    main_queue_args = {
        # 消费失败（nack 且不重入队）时进入死信
        "x-dead-letter-exchange": EXCHANGE_DLX,
        # 队列级 TTL：超过此时间未消费的消息自动进死信
        "x-message-ttl": queue_message_ttl_ms,
    }

    # 声明主队列
    main_queue = await channel.declare_queue(
        queue_name,
        durable=True,
        arguments=main_queue_args,
    )
    await main_queue.bind(main_exchange, routing_key=routing_key)

    # 声明重试队列
    # 重试队列参数：
    # - x-dead-letter-exchange = 主 exchange（TTL 过期后消息回主 exchange）
    # - x-dead-letter-routing-key = 主 routing key（确保回到原队列）
    # - x-message-ttl = 5 分钟兜底（消息级 expiration 通常更短）
    retry_queue_name = queue_name + RETRY_QUEUE_SUFFIX
    retry_queue_args = {
        "x-dead-letter-exchange": main_exchange_name,
        "x-dead-letter-routing-key": routing_key,
        "x-message-ttl": RETRY_QUEUE_MAX_TTL_MS,
    }

    retry_queue = await channel.declare_queue(
        retry_queue_name,
        durable=True,
        arguments=retry_queue_args,
    )
    # 重试 exchange 在 _ensure_retry_exchange 中预先声明为 durable
    retry_exchange = await channel.get_exchange(EXCHANGE_RETRY)
    # routing_key 用 queue_name：消费者重试时 publish 也用 queue_name
    await retry_queue.bind(retry_exchange, routing_key=queue_name)


async def _ensure_retry_exchange(channel: AbstractRobustChannel) -> None:
    """声明 retry exchange（Direct 模式，所有重试队列共享）

    必须在主队列声明之前调用，确保 retry exchange 已存在。
    """
    await channel.declare_exchange(
        EXCHANGE_RETRY,
        ExchangeType.DIRECT,
        durable=True,
    )


async def declare_all(channel: AbstractRobustChannel) -> None:
    """声明所有 Exchange、Queue 和绑定关系

    应用启动时调用，确保 Broker 上拓扑完整。
    声明是幂等的：如果 Exchange/Queue 已存在且参数一致，不会重复创建；
    如果参数不一致会抛异常（防止误改参数导致静默不一致）。

    为什么用 RobustChannel：重连后自动重新声明，无需手动处理

    拓扑结构：
    ┌──────────────────┐    ┌──────────────────┐
    │ main exchange    │───→│ main queue       │──→ consumer
    │ (Topic/Direct)   │    │ (x-message-ttl)  │     │
    └──────────────────┘    └──────────────────┘     │ 失败
                                                       ↓
    ┌──────────────────┐    ┌──────────────────┐     │ publish
    │ retry exchange   │───→│ retry queue      │←────┘
    │ (Direct)         │    │ (DLX=main,       │
    └──────────────────┘    │  x-message-ttl)  │
                            └──────────────────┘
                                    │ TTL 过期
                                    ↓
                            DLX 回 main exchange → main queue → consumer
    """
    # 先声明 retry exchange（主队列的 DLX 配置可能引用它，但实际不引用，
    # 这里只是为了在循环中通过 get_exchange 获取）
    await _ensure_retry_exchange(channel)

    # ---------- 任务事件 Exchange（Topic：支持 task.* 通配订阅）----------
    for queue_name, routing_key in [
        (QUEUE_TASK_CREATED, ROUTING_TASK_CREATED),
        (QUEUE_TASK_COMPLETED, ROUTING_TASK_COMPLETED),
        (QUEUE_TASK_FAILED, ROUTING_TASK_FAILED),
    ]:
        await _declare_main_queue_with_retry(
            channel,
            main_exchange_name=EXCHANGE_TASK,
            main_exchange_type=ExchangeType.TOPIC,
            queue_name=queue_name,
            routing_key=routing_key,
            # 任务事件 TTL 1 小时
            queue_message_ttl_ms=3600 * 1000,
        )

    # ---------- 工作流事件 Exchange（Topic）----------
    for queue_name, routing_key in [
        (QUEUE_WORKFLOW_STARTED, ROUTING_WORKFLOW_STARTED),
        (QUEUE_WORKFLOW_COMPLETED, ROUTING_WORKFLOW_COMPLETED),
        (QUEUE_WORKFLOW_FAILED, ROUTING_WORKFLOW_FAILED),
    ]:
        await _declare_main_queue_with_retry(
            channel,
            main_exchange_name=EXCHANGE_WORKFLOW,
            main_exchange_type=ExchangeType.TOPIC,
            queue_name=queue_name,
            routing_key=routing_key,
            queue_message_ttl_ms=3600 * 1000,
        )

    # ---------- 通信通知 Exchange（Direct：精确路由）----------
    for queue_name, routing_key in [
        (QUEUE_NOTIFICATION_EMAIL, ROUTING_NOTIFICATION_EMAIL),
        (QUEUE_NOTIFICATION_WECHAT, ROUTING_NOTIFICATION_WECHAT),
        (QUEUE_NOTIFICATION_WEBHOOK, ROUTING_NOTIFICATION_WEBHOOK),
    ]:
        await _declare_main_queue_with_retry(
            channel,
            main_exchange_name=EXCHANGE_NOTIFICATION,
            main_exchange_type=ExchangeType.DIRECT,
            queue_name=queue_name,
            routing_key=routing_key,
            # 通知消息 TTL 30 分钟（通知有时效性）
            queue_message_ttl_ms=30 * 60 * 1000,
        )

    # ---------- 简历解析 Exchange（Direct）----------
    await _declare_main_queue_with_retry(
        channel,
        main_exchange_name=EXCHANGE_RESUME,
        main_exchange_type=ExchangeType.DIRECT,
        queue_name=QUEUE_RESUME_PARSE,
        routing_key=ROUTING_RESUME_PARSE,
        # 简历解析消息 TTL 2 小时（大文件解析可能耗时较长）
        queue_message_ttl_ms=2 * 3600 * 1000,
    )

    # ---------- 职位匹配 Exchange（Direct）----------
    await _declare_main_queue_with_retry(
        channel,
        main_exchange_name=EXCHANGE_MATCH,
        main_exchange_type=ExchangeType.DIRECT,
        queue_name=QUEUE_MATCH_COMPUTE,
        routing_key=ROUTING_MATCH_COMPUTE,
        queue_message_ttl_ms=2 * 3600 * 1000,
    )

    # ---------- 死信 Exchange（Fanout：所有死信消息都路由到同一队列）----------
    # 必须最后声明：主队列的 x-dead-letter-exchange 引用了它，
    # 虽然 RabbitMQ 不要求引用方先存在（broker 端是 lazy binding），
    # 但先创建 DLX 可以让启动失败时更容易定位问题
    dlx_exchange = await channel.declare_exchange(
        EXCHANGE_DLX,
        ExchangeType.FANOUT,
        durable=True, # 持久化，确保在 Broker 重启后存在
    )
    await channel.declare_queue(
        QUEUE_DLX_DEAD_LETTER,
        durable=True,
        arguments={
            # 死信消息保留 7 天供排查
            "x-message-ttl": 7 * 24 * 3600 * 1000,
        },
    )
    await (await channel.get_queue(QUEUE_DLX_DEAD_LETTER)).bind(dlx_exchange)
