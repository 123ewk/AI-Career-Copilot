"""请求日志中间件

职责：
- 记录请求方法 / 路径 / 状态码 / 耗时 / 客户端 IP
- 跳过健康检查等高频探针路径，避免日志刷屏
- request_id 透传：依赖 request_id 中间件已写入 contextvars，本中间件不重复生成

设计动机：
- request_id 的生成 / 校验 / 响应头回写已下放给 request_id 中间件
  · 单一职责：logging 只管"何时记什么"，request_id 只管"这个请求是谁"
  · 避免两个中间件各自生成 request_id 导致 contextvar 时序错乱
- 没有耗时监控，无法定位慢请求
- 业务日志（从 logger 自动带上 request_id）与 HTTP 日志必须统一维度
- 健康检查 / 探针端点通常由 K8s / LB 高频调用，不能污染访问日志

关键技术点：
- 使用 starlette.middleware.base.BaseHTTPMiddleware：
  · 这是 FastAPI 推荐的"HTTP 维度"中间件写法
  · 对于纯请求日志（不需要读 body）足够使用
- time.perf_counter() 而非 time.time()：
  · 单调时钟，不受系统时间跳变（NTP 校时）影响
  · 适合做"间隔"测量
- contextvars 自动注入：
  · 协程内 logger 自动从 contextvars 读取 request_id
  · 业务层（service / 异常处理）logger.info(...) 无需手动传 request_id

潜在风险：
- 高 QPS 下 INFO 级访问日志可能成为 IO 瓶颈
  → 后续优化方向：1% 采样正常请求，慢请求/错误请求 100% 记录
- 5xx 异常响应的 X-Request-ID 头依赖 request_id 中间件（纯 ASGI）注入
  → 若 main.py 注册顺序错误导致 request_id 未在 logging 之前注册，header 会丢失
- 健康检查路径漏配会导致日志被刷爆
  → 当前以白名单方式跳过，新接入服务需在此登记
"""

import time
from typing import Final

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)

from app.core.logger import logger


# ==================== 常量 ====================

# 跳过访问日志的路径（健康检查 / 探针）
# 用 frozenset 防止运行时被误改，且查询 O(1)
_SKIP_LOG_PATHS: Final[frozenset[str]] = frozenset(
    {"/health", "/healthz", "/ready", "/live", "/metrics"}
)


# ==================== 中间件实现 ====================

class _RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件

    工作流程（按 try/finally 组织，保证日志一定能输出）：
    1. 记录 perf_counter 起点
    2. await call_next(request)  把控制权交给下游
    3. finally 中计算耗时并按状态码选日志级别输出

    职责边界：
    - request_id 的生成 / 校验 / 响应头回写由 request_id 中间件负责
    - 本中间件只读 contextvars 中的 request_id（通过 logger patcher 注入日志）
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # ---- 1. 记录开始时间 ----
        # time.perf_counter() 是单调时钟，不受系统时间跳变影响
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        # request.client 在 ASGI lifespan 阶段可能为 None，需要兜底
        client_ip = request.client.host if request.client else "-"

        # ---- 2. 处理请求 ----
        # status_code 初值用于异常分支：异常时外层 exception 中间件会返回 500
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # 异常会被外层 exception 中间件捕获并格式化为 JSON 响应
            # 此处只补一行错误日志，方便 grep / request_id 联动
            logger.opt(exception=True).error(
                "请求处理异常 | method={} | path={} | client={}",
                method,
                path,
                client_ip,
            )
            raise
        finally:
            # ---- 3. 计算耗时并输出访问日志 ----
            # perf_counter 返回秒，乘 1000 得到毫秒；保留两位小数
            latency_ms = (time.perf_counter() - start) * 1000.0

            # 健康检查 / 探针：跳过访问日志
            # 监控埋点可在此追加（如 Prometheus Histogram）
            # 不可在 finally 中 return：会吞掉上游的 return / 异常
            if path not in _SKIP_LOG_PATHS:
                # 根据状态码选日志级别
                # 5xx → ERROR（服务端故障，必须告警）
                # 4xx → WARNING（客户端问题，不告警但需可观测）
                # 2xx/3xx → INFO（正常访问）
                if status_code >= 500:
                    log = logger.error
                elif status_code >= 400:
                    log = logger.warning
                else:
                    log = logger.info

                log(
                    "请求完成 | method={} | path={} | status={} | latency_ms={:.2f} | client={}",
                    method,
                    path,
                    status_code,
                    latency_ms,
                    client_ip,
                )


# ==================== 注册入口 ====================

def add_logging_middleware(app: FastAPI) -> None:
    """注册请求日志中间件

    Args:
        app: FastAPI 应用实例

    注册顺序说明（参考 main.py 注释）：
    - 应在 add_cors_middleware 之后注册：预检请求由 CORS 短路，
      不需要再走本中间件打业务日志
    - 应在 add_request_id_middleware 之后注册：
      本中间件依赖 request_id 中间件已写入 contextvars
    - 应在 add_exception_middleware 之后注册：让异常处理器内的日志
      能从 contextvars 中读到 request_id，自动关联
    """
    app.add_middleware(_RequestLoggingMiddleware)
    logger.info("注册请求日志中间件 | skip_paths={}", sorted(_SKIP_LOG_PATHS))
