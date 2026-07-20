"""对话历史相关路由

职责：
- 暴露 /api/conversations 下的对话 CRUD HTTP 端点
- 接收 HTTP 请求，做最薄的协议层适配
- 调用 CommunicationService 处理消息同步

端点：
- GET  /: 对话列表（分页）
- GET  /{id}: 对话详情（含完整消息历史）
- POST /sync: 同步 DOM 提取的消息到后端
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.core.logger import logger
from app.domain.communication.models import (
    ConversationDetail,
    ConversationSyncRequest,
    ConversationSyncResponse,
)
from app.domain.communication.service import CommunicationService
from app.infra.database.postgres import get_db_session
from app.infra.repositories.conversation_repo import ConversationRepository

# ==================== Router 实例 ====================

router = APIRouter()


# ==================== 端点：对话列表 ====================


@router.get(
    "/",
    response_model=list[ConversationDetail],
    status_code=status.HTTP_200_OK,
    summary="对话列表",
    description="获取当前用户的对话列表，按最后消息时间倒序。",
)
async def list_conversations(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="每页大小"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    db: AsyncSession = Depends(get_db_session),
) -> list[ConversationDetail]:
    """对话列表端点"""
    user_id = uuid.UUID(request.state.user_id)
    repo = ConversationRepository(db)
    conversations = await repo.list_by_user(user_id, limit=limit, offset=offset)
    return [
        ConversationDetail.model_validate(conv) for conv in conversations
    ]


# ==================== 端点：对话详情 ====================


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    status_code=status.HTTP_200_OK,
    summary="对话详情",
    description="获取单个对话的完整信息，含消息历史。",
)
async def get_conversation(
    request: Request,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> ConversationDetail:
    """对话详情端点"""
    user_id = uuid.UUID(request.state.user_id)
    repo = ConversationRepository(db)
    conversation = await repo.get_by_id(conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise ResourceNotFoundError(
            detail="对话不存在",
            error_code="CONV_001",
        )
    return ConversationDetail.model_validate(conversation)


# ==================== 端点：同步消息 ====================


@router.post(
    "/sync",
    response_model=ConversationSyncResponse,
    status_code=status.HTTP_200_OK,
    summary="同步对话消息",
    description=(
        "同步 Content Script 从 DOM 提取的消息到后端。\n\n"
        "- 按 (user_id, job_id, recruiter_name) 幂等查找或创建\n"
        "- messages 全量覆盖（DOM 是快照，非增量）"
    ),
)
async def sync_messages(
    request: Request,
    body: ConversationSyncRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConversationSyncResponse:
    """消息同步端点"""
    user_id = uuid.UUID(request.state.user_id)
    logger.info(
        "同步对话消息端点 | user_id={} | recruiter={} | count={}",
        user_id,
        body.recruiter_name,
        len(body.messages),
    )

    service = CommunicationService(db)
    try:
        result = await service.sync_messages(
            user_id=user_id,
            request=body,
        )
    finally:
        await service.close()

    return result


__all__ = ["router"]
