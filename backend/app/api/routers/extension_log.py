"""Extension 前端日志路由

职责：
- 接收 Chrome 扩展前端（Content Script / Service Worker / interceptor）发送的运行时日志
- 统一输出到后端终端，便于开发调试时前后端日志一体化查看
- 仅用于开发/测试环境，不存储到数据库

设计动机：
- MV3 扩展的日志分散在多个执行上下文（Content Script isolated world、
  Service Worker、页面主世界 interceptor.js），浏览器 Console 需要切换上下文查看
- 通过此端点把关键路径日志汇总到后端终端，排查数据流问题时更高效
- 使用 Pydantic 模型做严格校验，避免前端传入非法字段
"""

from typing import Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.core.logger import get_request_id, logger

# ==================== Router 实例 ====================

router = APIRouter(tags=["扩展日志"])


# ==================== 请求模型 ====================

class ExtensionLogEntry(BaseModel):
    """单条前端日志条目"""

    level: Literal["debug", "info", "warn", "error"] = Field(
        ...,
        description="日志级别",
    )
    source: Literal["content", "service_worker", "interceptor", "sidepanel"] = Field(
        ...,
        description="日志来源上下文",
    )
    message: str = Field(..., min_length=1, description="日志内容")
    timestamp: int | None = Field(
        None,
        description="前端产生日志的时间戳（毫秒）",
    )
    context: dict | None = Field(
        None,
        description="可选上下文数据（URL、岗位数等）",
    )


class ExtensionLogBatch(BaseModel):
    """前端日志批量请求体"""

    logs: list[ExtensionLogEntry] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="日志条目列表，单次最多 100 条",
    )


# ==================== 端点：批量接收日志 ====================


@router.post(
    "/logs",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="批量接收扩展前端日志",
    description="接收 Content Script / Service Worker / interceptor 发送的运行时日志，输出到后端终端。",
)
async def receive_extension_logs(
    request: Request,
    body: ExtensionLogBatch,
) -> None:
    """批量接收并打印扩展前端日志

    Args:
        request: FastAPI 请求对象，用于读取 user_id
        body: 批量日志请求体
    """
    user_id = getattr(request.state, "user_id", None)
    rid = get_request_id()

    for entry in body.logs:
        # 路径判定日志：用 warning 级别 + 醒目标记，单独输出到终端
        if entry.message.startswith("PATH_DECISION:"):
            decision = entry.message.replace("PATH_DECISION:", "").strip()
            logger.warning(
                "=== PATH DECISION === 用户={} | {} | ctx={} ====================",
                user_id or "未知用户",
                decision,
                entry.context or {},
            )
            continue

        log_fn = {
            "debug": logger.debug,
            "info": logger.info,
            "warn": logger.warning,
            "error": logger.error,
        }[entry.level]

        log_fn(
            "[ext-log][{}] {} | user={} rid={} | ctx={}",
            entry.source,
            entry.message,
            user_id or "-",
            rid or "-",
            entry.context or {},
        )
