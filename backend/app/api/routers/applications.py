"""投递记录相关路由

职责：
- 暴露 /api/applications 下的投递记录 HTTP 端点
- 接收 HTTP 请求，做最薄的协议层适配
- 调用 ApplicationService 处理业务逻辑

端点：
- POST /: 创建投递记录
- GET /: 投递记录列表
- GET /{application_id}: 投递记录详情
- PATCH /{application_id}: 更新投递状态/备注
"""

import uuid

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.domain.application.models import (
    ApplicationCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdateRequest,
)
from app.domain.application.service import ApplicationService
from app.infra.database.postgres import get_db_session

# ==================== Router 实例 ====================

router = APIRouter()


# ==================== 端点：创建投递记录 ====================


@router.post(
    "/",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建投递记录",
    description=(
        "用户确认投递后创建投递记录。\n\n"
        "- 状态默认为 APPLIED，并设置 applied_at\n"
        "- 同一用户对同一岗位重复投递返回 409\n"
        "- 可传入 match_score 和 notes"
    ),
)
async def create_application(
    request: Request,
    body: ApplicationCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ApplicationResponse:
    """创建投递记录端点"""
    user_id = uuid.UUID(request.state.user_id)
    logger.info(
        "创建投递记录端点 | user_id={} | job_id={}",
        user_id,
        body.job_id,
    )

    service = ApplicationService(db)
    return await service.create_application(
        user_id=user_id,
        request=body,
    )


# ==================== 端点：投递记录列表 ====================


@router.get(
    "/",
    response_model=ApplicationListResponse,
    summary="获取投递记录列表",
    description=(
        "分页返回当前用户的投递记录。\n\n"
        "- 默认按创建时间倒序（最新在前）\n"
        "- 每页 1-100 条，默认 20 条"
    ),
)
async def list_applications(
    request: Request,
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="每页大小（1-100）",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="偏移量（>=0）",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ApplicationListResponse:
    """投递记录列表端点"""
    user_id = uuid.UUID(request.state.user_id)

    service = ApplicationService(db)
    return await service.list_applications(
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


# ==================== 端点：投递记录详情 ====================


@router.get(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="获取投递记录详情",
    description="按 ID 查询投递记录详情。不存在或无权访问返回 404。",
)
async def get_application(
    request: Request,
    application_id: uuid.UUID = Path(
        ...,
        description="投递记录 UUID v4",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ApplicationResponse:
    """投递记录详情端点"""
    user_id = uuid.UUID(request.state.user_id)

    service = ApplicationService(db)
    return await service.get_application(
        application_id=application_id,
        user_id=user_id,
    )


# ==================== 端点：更新投递记录 ====================


@router.patch(
    "/{application_id}",
    response_model=ApplicationResponse,
    summary="更新投递记录",
    description="更新投递状态和备注。不存在或无权访问返回 404。",
)
async def update_application(
    request: Request,
    body: ApplicationUpdateRequest,
    application_id: uuid.UUID = Path(
        ...,
        description="投递记录 UUID v4",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ApplicationResponse:
    """更新投递记录端点"""
    user_id = uuid.UUID(request.state.user_id)

    service = ApplicationService(db)
    return await service.update_application(
        application_id=application_id,
        user_id=user_id,
        request=body,
    )


__all__ = ["router"]
