"""全局异常处理中间件

职责：
- 捕获应用中所有未处理异常（业务异常 / 校验异常 / HTTP 异常 / 未知异常）
- 返回统一格式的 JSON 响应：{error_code, detail, request_id}
- 区分日志级别：4xx → WARN、5xx → ERROR（触发告警）
- 开发环境附加 traceback，便于本地排查

设计动机：
- FastAPI 自带的默认异常返回（{"detail": "..."}）与项目约定的响应格式不统一，
  前端需要大量 if/else 区分错误结构
- 业务异常（AppException）必须被显式区分对待：
  · 不在响应中泄露 stack trace（避免泄露 SQL/文件路径）
  · 不暴露 extra 字段（防止敏感调试信息泄露给前端）
- 统一响应格式让前端可以标准化处理：code + detail 即可

关键技术点：
- 使用 app.add_exception_handler 装饰器而非 BaseHTTPMiddleware：
  · BaseHTTPMiddleware 的流式处理会破坏 StreamingResponse
  · exception_handler 是 FastAPI 官方推荐的全局异常处理方式
- RequestValidationError 必须单独处理：默认返回 422 + 嵌套 list，
  需要拍平为与业务异常一致的结构
- 必须从 contextvars 读取 request_id（request_id 中间件已注入），
  保证错误响应和日志能通过同一个 ID 关联

潜在风险：
- 注册 Exception 兜底 handler 后，任何未显式处理的异常都会变成 500 JSON 响应，
  不会让 ASGI 服务器返回默认的 HTML 错误页（影响监控告警的判别）；
  → 已统一返回 error_code=SYS_000，告警系统按此码识别
- dev 环境 traceback 可能包含 SQL / 凭证等敏感信息，必须用 settings.app_env 强校验，
  防止误把 dev 设置发布到生产
"""

import traceback
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    EXCEPTION_LOG_LEVEL,
    AppException,
)
from app.core.logger import get_request_id, logger
from app.core.settings import get_settings


# ==================== 响应构建器 ====================

def _build_error_response(
    *,
    status_code: int,
    error_code: str,
    detail: str,
    debug_payload: dict[str, Any] | None = None,
) -> JSONResponse:
    """构造统一格式的错误响应

    Args:
        status_code: HTTP 状态码
        error_code: 业务错误码（如 AUTH_001 / VAL_001 / SYS_000）
        detail: 用户可读的错误描述
        debug_payload: 调试上下文（仅 dev 环境会写入响应 body）

    Returns:
        包含统一错误体的 JSONResponse
    """
    body: dict[str, Any] = {
        "error_code": error_code,
        "detail": detail,
        "request_id": get_request_id(),
    }

    if debug_payload is not None and get_settings().app_env == "dev":
        # 生产环境绝对不能带 traceback，避免泄露 SQL / 路径 / 凭证
        body["debug"] = debug_payload

    # 5xx/4xx 错误响应也必须带 X-Request-ID 响应头
    # 原因：Starlette ServerErrorMiddleware 在最外层，BaseHTTPMiddleware 的 raise
    # 路径会让 JSONResponse 不流经 RequestIDMiddleware.send_wrapper，
    # 这里由异常处理器显式补上响应头，确保前后端排障链路不断
    response = JSONResponse(status_code=status_code, content=body)
    response.headers["X-Request-ID"] = get_request_id()
    return response


# ==================== 业务异常处理 ====================

async def _app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """处理 AppException 及其子类

    日志级别根据 status_code 决定（4xx → WARN，5xx → ERROR）。
    响应不携带 extra 字段，防止敏感调试信息泄露给前端。
    """
    log_level = EXCEPTION_LOG_LEVEL.get(exc.status_code, "ERROR").lower()
    log_func = getattr(logger, log_level, logger.error)

    log_func(
        "业务异常 | error_code={} | status={} | path={} | detail={}",
        exc.error_code,
        exc.status_code,
        request.url.path,
        exc.detail,
    )
    if exc.extra:
        # extra 仅记录日志，不写入响应 body
        logger.debug("异常上下文 | error_code={} | extra={}", exc.error_code, exc.extra)

    debug_payload: dict[str, Any] | None = None
    if get_settings().app_env == "dev":
        # 业务异常的 traceback 通常价值不大（多为本意抛出的校验失败），
        # 只在 dev 模式下记录详细信息方便联调
        debug_payload = {"exc_type": type(exc).__name__, "extra": exc.extra}

    return _build_error_response(
        status_code=exc.status_code,
        error_code=exc.error_code,
        detail=exc.detail,
        debug_payload=debug_payload,
    )


# ==================== 参数校验异常 ====================

def _sanitize_validation_errors(
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把 Pydantic 错误详情中的不可序列化对象（如异常实例）转为字符串

    Pydantic v2 的 ctx 字段可能包含原始异常对象，JSONResponse 序列化时会失败。
    这里递归处理 dict / list，保留结构的同时把异常对象转为 str。
    """
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        sanitized.append(_make_json_safe(error))
    return sanitized


def _make_json_safe(obj: Any) -> Any:
    """递归把不可 JSON 序列化的对象转换为字符串"""
    if isinstance(obj, dict):
        return {str(k): _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_safe(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_make_json_safe(item) for item in obj)
    if isinstance(obj, BaseException):
        return f"{type(obj).__name__}: {obj}"
    # UUID / datetime 等也能被 JSONResponse 默认处理，但显式转字符串更安全
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理 Pydantic / FastAPI 参数校验失败

    默认 FastAPI 返回 422 + 嵌套 list[str]，与项目业务异常结构不一致。
    这里拍平为统一格式，detail 用首条错误 + 错误总数。
    """
    errors: list[dict[str, Any]] = list(exc.errors())
    first_msg = errors[0]["msg"] if errors else "参数校验失败"
    detail = f"{first_msg}（共 {len(errors)} 项错误）" if errors else "参数校验失败"

    logger.warning(
        "参数校验失败 | path={} | error_count={} | first_error={}",
        request.url.path,
        len(errors),
        first_msg,
    )

    debug_payload: dict[str, Any] | None = None
    if get_settings().app_env == "dev":
        # Pydantic 的 error ctx 里可能携带原始异常对象（如 ValueError），
        # JSONResponse 序列化时会抛 TypeError，因此先把异常对象转为字符串
        debug_payload = {"errors": _sanitize_validation_errors(errors)}

    return _build_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_code="VAL_001",
        detail=detail,
        debug_payload=debug_payload,
    )


# ==================== HTTP 异常 ====================

# HTTPException 状态码 → 业务错误码 映射
# 把 Starlette / FastAPI 内置的 HTTPException 转换为业务侧可识别的 error_code
_HTTP_ERROR_CODE_MAP: dict[int, str] = {
    400: "REQ_001",
    401: "AUTH_001",
    403: "AUTH_002",
    404: "RES_001",
    405: "REQ_002",
    409: "RES_002",
    415: "REQ_003",
    422: "VAL_001",
    429: "RATE_001",
}


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """处理 Starlette / FastAPI HTTPException

    典型场景：404 Not Found、405 Method Not Allowed、路由层 raise HTTPException。
    注意：AppException 不会走到这里（不是 HTTPException 子类）。
    """
    status_code = exc.status_code
    log_level = EXCEPTION_LOG_LEVEL.get(status_code, "ERROR").lower()
    log_func = getattr(logger, log_level, logger.error)

    log_func(
        "HTTP 异常 | status={} | path={} | detail={}",
        status_code,
        request.url.path,
        exc.detail,
    )

    error_code = _HTTP_ERROR_CODE_MAP.get(status_code, f"HTTP_{status_code}")
    detail = str(exc.detail) if exc.detail else "请求处理失败"

    return _build_error_response(
        status_code=status_code,
        error_code=error_code,
        detail=detail,
    )


# ==================== 兜底异常 ====================

async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理未预期的异常（最后一道防线）

    必须放在最后注册，作为兜底捕获所有未匹配的异常。
    记录完整 traceback 便于排查，前端只看到模糊的"服务内部错误"，
    避免泄露堆栈 / SQL / 路径等敏感信息。
    """
    # logger.opt(exception=True) 会自动附加当前异常的完整堆栈到日志
    logger.opt(exception=True).error(
        "未处理异常 | path={} | exc_type={}",
        request.url.path,
        type(exc).__name__,
    )

    debug_payload: dict[str, Any] | None = None
    if get_settings().app_env == "dev":
        # dev 模式下把 traceback 暴露给前端
        tb_str = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        debug_payload = {"exc_type": type(exc).__name__, "traceback": tb_str}

    return _build_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code="SYS_000",
        detail="服务内部错误",
        debug_payload=debug_payload,
    )


# ==================== 注册入口 ====================

def add_exception_middleware(app: FastAPI) -> None:
    """注册全局异常处理器到 FastAPI 应用

    注册顺序（按特异性从高到低）：
    1. AppException            - 自定义业务/基础设施异常
    2. RequestValidationError  - Pydantic 校验异常
    3. StarletteHTTPException  - HTTP 异常（404/405 等）
    4. Exception               - 兜底捕获所有未处理异常

    Args:
        app: FastAPI 应用实例

    注意事项：
    - FastAPI 按 MRO（isinstance）匹配 handler，最具体的优先；
      因此注册顺序不影响匹配结果，但建议按"特异 → 通用"书写便于阅读
    - 重复注册同一异常类型会抛 RuntimeError，应避免
    - 注册 Exception 兜底后，未处理的异常统一转为 500 JSON，
      不再让 ASGI 服务器返回默认 HTML 错误页
    """
    app.add_exception_handler(AppException, _app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    logger.info(
        "注册全局异常处理器 | handlers=[AppException, RequestValidationError, HTTPException, Exception]"
    )
