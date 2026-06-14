"""FastAPI 应用工厂

职责：
- 创建 FastAPI 应用实例
- 注册中间件（CORS / 请求日志 / 异常处理 / 限流 / 认证）
- 挂载路由（auth / user / resume / jobs / match / session / task / agent / workflow）
- 管理 lifespan（PostgreSQL / Redis / RabbitMQ 连接与断开）

设计动机：
- 应用工厂模式：将应用创建逻辑封装在函数中，方便测试时替换配置、
  也方便 uvicorn 以 import 字符串方式引用（uvicorn app.main:app）
- lifespan 替代 on_event：FastAPI 官方推荐用 async context manager
  管理启动/关闭生命周期，保证资源获取和释放成对出现
- 中间件注册顺序：FastAPI 按注册的逆序执行中间件，
  所以最先注册的中间件最外层（最先进入、最后退出）

核心机制：
- lifespan 是 async context manager，yield 前是启动阶段，yield 后是关闭阶段
- 即使启动阶段抛异常，关闭阶段的资源释放仍需逐个 try/except，
  避免一个资源关闭失败导致其他资源泄漏
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.core.logger import logger, setup_logging
from app.core.settings import get_settings
from app.infra.database.postgres import pg_session_factory
from app.infra.database.redis import redis_client_factory
from app.infra.message_queue.connection import rabbitmq_connection_factory
from app.api.middleware.cors import add_cors_middleware
from app.api.middleware.logging import add_logging_middleware
from app.api.middleware.exception import add_exception_middleware
from app.api.middleware.rate_limit import add_rate_limit_middleware
from app.api.middleware.auth import add_auth_middleware
from app.api.routers import auth, user, resume, jobs, match, session, task, agent, workflow


# ==================== Lifespan ====================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理

    启动阶段（yield 前）：
    1. 初始化日志系统（最先做，后续启动日志才能输出）
    2. 连接 PostgreSQL（Engine 创建 + 连接池初始化）
    3. 连接 Redis（连接池初始化 + 健康检查）
    4. 连接 RabbitMQ（RobustConnection 建立 + 自动重连就绪）

    关闭阶段（yield 后）：
    按启动的逆序释放资源，每个释放独立 try/except，
    确保一个失败不影响其他资源的释放。

    为什么用 asynccontextmanager 而非 on_event：
    - on_event(startup/shutdown) 是两个独立函数，无法共享局部变量
    - context manager 天然成对，yield 前后逻辑一目了然
    - FastAPI 官方已标记 on_event 为 deprecated
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

    await rabbitmq_connection_factory.connect()
    logger.info("RabbitMQ 连接就绪 | host={}:{}", settings.rabbitmq_host, settings.rabbitmq_port)

    logger.info("应用启动完成 | app={}", settings.app_name)

    yield  # 应用运行中，请求在此期间被处理

    # ---- 关闭阶段（逆序释放）----
    logger.info("应用关闭中，释放资源...")

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

    中间件注册顺序说明（FastAPI 按逆序执行）：
    最先注册 → 最外层 → 最先进入/最后退出
    1. CORS        — 跨域预检请求需最先处理，否则后续中间件可能拦截
    2. 日志        — 记录所有请求的 request_id / 耗时 / 状态码
    3. 异常处理    — 捕获所有未处理异常，返回统一格式响应
    4. 限流        — 在认证之前限流，避免恶意请求消耗认证资源
    5. 认证        — 最内层，只对需要认证的路由生效
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,  # 生产环境关闭 Swagger
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ---- 注册中间件 ----
    add_cors_middleware(app)
    add_logging_middleware(app)
    add_exception_middleware(app)
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
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)