"""FastAPI 应用工厂

职责：
- 创建 FastAPI 应用实例
- 注册中间件（CORS / request_id / 异常处理 / 日志 / 限流 / 认证）
- 挂载路由（auth / user / resume / jobs / match / session / task / agent / workflow）
- 管理 lifespan（PostgreSQL / Redis / RabbitMQ 连接与断开）

设计动机：
- 应用工厂模式：将应用创建逻辑封装在函数中，方便测试时替换配置、
  也方便 uvicorn 以 import 字符串方式引用（uvicorn main:app）
- lifespan 替代 on_event：FastAPI 官方推荐用 async context manager
  管理启动/关闭生命周期，保证资源获取和释放成对出现
- 中间件注册顺序：FastAPI 按注册顺序"先注册→最外层"执行（参考 Starlette 文档），
  所以最先注册的中间件最先进入、最先返回

核心机制：
- lifespan 是 async context manager，yield 前是启动阶段，yield 后是关闭阶段
- 即使启动阶段抛异常，关闭阶段的资源释放仍需逐个 try/except，
  避免一个资源关闭失败导致其他资源泄漏
- request_id 中间件必须早于 logging/auth/rate_limit 注册：
  它把 request_id 写入 contextvars，后续中间件在同一协程内自动可读，
  这正是它们日志/响应头/限流键能联动 request_id 的前提
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware.auth import add_auth_middleware
from app.api.middleware.cors import add_cors_middleware
from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.rate_limit import add_rate_limit_middleware
from app.api.middleware.request_id import add_request_id_middleware
from app.api.routers import agent, auth, jobs, match, resume, session, task, user, workflow
from app.core.logger import logger, setup_logging
from app.core.settings import get_settings
from app.infra.database.postgres import pg_session_factory
from app.infra.database.redis import redis_client_factory
from app.infra.message_queue.connection import rabbitmq_connection_factory
from app.infra.message_queue.exchanges import declare_all
from app.infra.message_queue.registry import consumer_manager

# ==================== Lifespan ====================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理

    启动阶段（yield 前）：
    1. 初始化日志系统（最先做，后续启动日志才能输出）
    2. 连接 PostgreSQL（Engine 创建 + 连接池初始化）
    3. 连接 Redis（连接池初始化 + 健康检查）
    4. 连接 RabbitMQ（RobustConnection 建立 + 自动重连就绪）
    5. 声明 MQ 拓扑（Exchange / Queue / 重试队列 / 死信队列）
    6. 启动消费者（从 CONSUMER_REGISTRY 拉起，asyncio.create_task 并发）

    关闭阶段（yield 后）：
    按启动的逆序释放资源，每个释放独立 try/except，
    确保一个失败不影响其他资源的释放。

    为什么用 asynccontextmanager 而非 on_event：
    - on_event(startup/shutdown) 是两个独立函数，无法共享局部变量
    - context manager 天然成对，yield 前后逻辑一目了然
    - FastAPI 官方已标记 on_event 为 deprecated

    启动异常策略：
    - 当前启动阶段任一步失败会直接抛出，让进程以非零码退出并被 supervisor 重启
    - 这是"显式失败"策略：宁可让进程崩溃也不要带着半残状态服务请求
      （例：RabbitMQ 没连上却启动 HTTP，会出现"任务不消费"这种隐蔽故障）

    关于消费者注册时机：
    - 装饰器在模块 import 时填充 CONSUMER_REGISTRY
    - 所有使用 @register 装饰的 consumer 模块必须在本文件顶部 import 进来
      （或被其他顶部 import 的模块间接 import）
    - 否则消费者不会在 lifespan 启动时被拉起
    """
    settings = get_settings()

    # ---- 启动阶段 ----
    setup_logging()
    logger.info("应用启动中 | env={}", settings.app_env)

    # 触发 PostgreSQL Engine 懒加载（首次访问 .engine 属性创建连接池）
    _ = pg_session_factory.engine
    logger.info("PostgreSQL 连接池就绪 | pool_size=20 | max_overflow=10")

    # 触发 Redis 连接池初始化（懒加载首次访问）
    _ = redis_client_factory.client
    logger.info("Redis 连接池就绪 | max_connections=20")

    # RabbitMQ：建连 + 声明拓扑 + 启动消费者
    # 这三步必须在同一个 channel 上完成（拓扑声明后才能注册消费者）
    mq_channel = await rabbitmq_connection_factory.get_channel()
    logger.info(
        "RabbitMQ 连接就绪 | host={}:{}",
        settings.rabbitmq_host,
        settings.rabbitmq_port,
    )

    # 声明所有 Exchange/Queue/Binding（幂等）
    await declare_all(mq_channel)
    logger.info("RabbitMQ 拓扑声明完成")

    # 启动所有已注册的消费者
    # ConsumerManager.start_all 内部用 asyncio.create_task 拉起每个消费者
    # 真正消费消息由 aio-pika 内部事件循环处理，不阻塞 startup
    await consumer_manager.start_all(mq_channel)
    if consumer_manager.consumers:
        logger.info(
            "消费者已拉起 | count={}",
            len(consumer_manager.consumers),
        )
    else:
        logger.info("注册表为空，跳过消费者启动")

    logger.info("应用启动完成 | app={}", settings.app_name)

    yield  # 应用运行中，请求在此期间被处理

    # ---- 关闭阶段（逆序释放）----
    logger.info("应用关闭中，释放资源...")

    # 1. 优雅停止所有消费者（等待 in_flight 归零）
    try:
        await consumer_manager.stop_all()
        logger.info("所有消费者已停止")
    except Exception:
        logger.exception("消费者停止异常")

    # 2. 关闭 RabbitMQ 连接（内部会取消所有 consumer 和 channel）
    try:
        await rabbitmq_connection_factory.close()
        logger.info("RabbitMQ 连接已关闭")
    except Exception:
        logger.exception("RabbitMQ 关闭异常，可能已断连")

    try:
        await redis_client_factory.close()
        logger.info("Redis 连接池已关闭")
    except Exception:
        logger.exception("Redis 关闭异常")

    try:
        await pg_session_factory.close()
        logger.info("PostgreSQL 连接池已关闭")
    except Exception:
        logger.exception("PostgreSQL 关闭异常")

    logger.info("应用已关闭")


# ==================== 应用工厂 ====================

def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例

    中间件注册顺序说明（Starlette 规则：先注册 → 最外层 → 最先进入/最后退出）：
    1. CORS        — 跨域预检请求需最先处理，否则后续中间件可能拦截
    2. Request ID  — 把 request_id 写入 contextvars，
                      后续 logging / rate_limit / auth 才能读到同一个 ID
    3. 异常处理    — 用 add_exception_handler 注册（不是真正的中间件），
                      位置在 logging 之前，方便异常路径的日志也带 request_id
    4. 日志        — 记录所有请求的 request_id / 耗时 / 状态码
    5. 限流        — 在认证之前限流，避免恶意请求消耗认证资源
    6. 认证        — 最内层，只对需要认证的路由生效

    为什么 request_id 必须在 logging 之前：
    - logging 通过 logger 自动从 contextvars 读 request_id；
      若 request_id 中间件晚于 logging 注册，请求全程 logging 都看不到 rid
    - auth 同理：401 响应要带 X-Request-ID 头，需要从 contextvars 读 rid
    - rate_limit 同理：429 响应头和日志都需要 rid

    为什么 exception 用 add_exception_handler 而非 BaseHTTPMiddleware：
    - exception_handler 是 FastAPI 官方推荐方式，不破坏 StreamingResponse
    - 它在路由层兜底，无法被任何 add_middleware 覆盖
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,  # 生产环境关闭 Swagger
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ---- 注册中间件（按"先注册→最外层"的顺序书写）----
    add_cors_middleware(app)
    add_request_id_middleware(app)
    add_exception_middleware(app)
    add_logging_middleware(app)
    add_rate_limit_middleware(app)
    add_auth_middleware(app)

    # ---- 挂载路由 ----
    app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
    app.include_router(user.router, prefix="/api/users", tags=["用户"])
    app.include_router(resume.router, prefix="/api/resumes", tags=["简历"])
    app.include_router(jobs.router, prefix="/api/jobs", tags=["岗位"])
    app.include_router(match.router, prefix="/api/match", tags=["匹配"])
    app.include_router(session.router, prefix="/api/sessions", tags=["会话"])
    app.include_router(task.router, prefix="/api/tasks", tags=["任务"])
    app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])
    app.include_router(workflow.router, prefix="/api/workflows", tags=["工作流"])

    return app


# 模块级应用实例，uvicorn 通过 import 字符串引用
# 例如：uvicorn main:app --reload
app = create_app()

if __name__ == "__main__":
    import uvicorn
    # 用 import 字符串而非 app 对象，确保 reload 模式下能找到模块路径
    # （直接传 app 对象时 reload 会报"无法导入 app"）
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
