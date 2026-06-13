"""结构化日志模块

职责：
- 基于 Loguru 提供全局结构化日志
- 通过 contextvars 注入 request_id，协程安全
- 文件轮转：按大小 + 按时间，防止磁盘撑爆
- 拦截标准 logging，统一走 Loguru 输出

设计动机：
- contextvars 是 asyncio 下唯一正确的上下文传递方式，
  每个协程有独立副本，不存在 Race Condition
- 生产环境用 JSON 格式便于 ELK 采集，开发环境用可读格式
- 拦截 logging 是因为 uvicorn/httpx 等第三方库用标准 logging，
  不拦截会导致日志格式不统一、request_id 丢失
"""

import logging
import sys
from contextvars import ContextVar
from pathlib import Path

from loguru import logger as _logger

from app.core.settings import get_settings

# ==================== request_id 上下文 ====================

# 每个协程独立持有，asyncio 调度时自动复制，协程安全
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> None:
    """设置当前协程的 request_id

    在中间件层调用，整个请求生命周期内有效
    """
    _request_id_ctx.set(request_id)


def get_request_id() -> str:
    """获取当前协程的 request_id"""
    return _request_id_ctx.get()


# ==================== Patcher ====================

def _request_id_patcher(record: dict) -> None:
    """Loguru patcher：每条日志写入时自动注入当前协程的 request_id

    原理：Loguru 的 patch 机制在格式化前回调，
    此时从 contextvars 读取的值属于当前协程，不会串
    """
    record["extra"]["request_id"] = get_request_id()


# 模块级 patch：确保所有通过 logger 写入的日志都携带 request_id
# 必须在模块级做，否则函数内重新赋值只影响局部变量
logger = _logger.patch(_request_id_patcher)

# ==================== 日志格式 ====================

# 开发环境：彩色可读格式
_DEV_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<yellow>rid={extra[request_id]}</yellow> | "
    "<level>{message}</level>"
)

# 生产环境：JSON 格式，便于 ELK/Grafana Loki 采集
_PROD_FORMAT = (
    '{{"time":"{time:YYYY-MM-DD HH:mm:ss.SSS}",'
    '"level":"{level}",'
    '"request_id":"{extra[request_id]}",'
    '"logger":"{name}",'
    '"function":"{function}",'
    '"line":"{line}",'
    '"message":"{message}"}}'
)


# ==================== 日志初始化 ====================

def _build_log_dir() -> Path:
    """构建日志目录，不存在则创建"""
    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)
    return log_dir


def setup_logging() -> None:
    """初始化日志系统，应用启动时调用一次

    做了三件事：
    1. 移除 Loguru 默认 sink（避免重复输出）
    2. 添加 console + file sink，注入 request_id
    3. 拦截标准 logging，让第三方库日志也走 Loguru
    """
    settings = get_settings()
    is_dev = settings.app_env == "dev"
    log_level = settings.log_level.upper()

    # 移除默认 sink
    logger.remove()

    # ---- Console sink ----
    logger.add(
        sink=sys.stderr,
        level=log_level,
        format=_DEV_FORMAT if is_dev else _PROD_FORMAT,
        colorize=is_dev,
        enqueue=True,  # 异步写入，不阻塞事件循环
    )

    # ---- File sink：按大小轮转 ----
    log_dir = _build_log_dir()
    logger.add(
        sink=str(log_dir / "app.log"),
        level=log_level,
        format=_PROD_FORMAT,  # 文件始终用 JSON，便于日志平台解析
        rotation="10 MB",     # 单文件超过 10MB 轮转
        retention="30 days",  # 保留 30 天
        compression="zip",    # 轮转后压缩节省空间
        enqueue=True,
        encoding="utf-8",
    )

    # ---- File sink：按时间轮转（每天凌晨切割，方便按天检索）----
    logger.add(
        sink=str(log_dir / "app_{time:YYYY-MM-DD}.log"),
        level=log_level,
        format=_PROD_FORMAT,
        rotation="00:00",     # 每天零点轮转
        retention="90 days",  # 保留 90 天
        compression="zip",
        enqueue=True,
        encoding="utf-8",
    )

    # ---- 拦截标准 logging ----
    # uvicorn / httpx / sqlalchemy 等用标准 logging，
    # 不拦截则 request_id 丢失、格式不统一
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in logging.root.manager.loggerDict:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    logger.info("日志系统初始化完成 | env={} | level={}", settings.app_env, log_level)


class _InterceptHandler(logging.Handler):
    """将标准 logging 的记录转发到 Loguru

    原理：logging.Handler.emit 是每条日志的入口，
    在这里把 LogRecord 转换为 Loguru 的格式并写入
    """

    def emit(self, record: logging.LogRecord) -> None:
        # 映射 logging level → loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 找到真正发起日志调用的栈帧，跳过 logging 自身的帧
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, "{}", record.getMessage()
        )
