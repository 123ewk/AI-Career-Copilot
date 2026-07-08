"""沟通话术相关路由

职责：
- 暴露 /api/communication 下的沟通话术 HTTP 端点
- 接收 HTTP 请求，做最薄的协议层适配
- 调用 CommunicationService 处理业务逻辑

端点：
- POST /generate: 异步生成沟通话术，返回 task_id
"""

import uuid

from aio_pika.abc import AbstractRobustChannel
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.domain.communication.models import (
    CommunicationGenerateRequest,
    CommunicationGenerateResponse,
)
from app.domain.communication.service import CommunicationService
from app.infra.database.postgres import get_db_session
from app.infra.message_queue import MessagePublisher
from app.infra.message_queue.connection import get_rabbitmq_channel
from app.infra.message_queue.exchanges import EXCHANGE_AGENT, ROUTING_AGENT_COMMUNICATION

# ==================== Router 实例 ====================

router = APIRouter()


# ==================== 端点：生成沟通话术 ====================


@router.post(
    "/generate",
    response_model=CommunicationGenerateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="生成沟通话术（异步）",
    description=(
        "触发 Communication Agent 异步生成沟通话术。\n\n"
        "- 202 Accepted：任务已入队，返回 task_id 供前端轮询\n"
        "- 轮询地址：GET /api/tasks/{task_id}\n"
        "- 若未传 resume_id，使用用户当前活跃简历\n"
        "- LLM 调用失败返回 502"
    ),
)
async def generate_communication(
    request: Request,
    body: CommunicationGenerateRequest,
    db: AsyncSession = Depends(get_db_session),
    channel: AbstractRobustChannel = Depends(get_rabbitmq_channel),
) -> CommunicationGenerateResponse:
    """生成沟通话术端点

    Args:
        request: FastAPI 请求对象（从中读取 user_id）
        body: 生成请求
        db: 请求级 AsyncSession
        channel: RabbitMQ Channel（用于构造 Publisher）

    Returns:
        CommunicationGenerateResponse: pending + task_id
    """
    user_id = uuid.UUID(request.state.user_id)
    logger.info(
        "生成沟通话术端点 | user_id={} | job_id={} | session_id={}",
        user_id,
        body.job_id,
        body.session_id,
    )

    service = CommunicationService(db)
    try:
        task_info = await service.generate_script_async(
            user_id=user_id,
            request=body,
        )
    finally:
        await service.close()

    # 发送 MQ 消息触发异步处理
    publisher = MessagePublisher(channel)
    await publisher.publish(
        exchange_name=EXCHANGE_AGENT,
        routing_key=ROUTING_AGENT_COMMUNICATION,
        payload={
            "task_id": str(task_info["task_id"]),
            "job_id": str(body.job_id),
            "user_id": str(user_id),
            "resume_id": str(body.resume_id) if body.resume_id else None,
            "business_id": f"communication:job-{body.job_id}:resume-{body.resume_id or 'active'}",
        },
    )

    return CommunicationGenerateResponse(
        task_id=task_info["task_id"],
        status=task_info["status"],
    )


__all__ = ["router"]
