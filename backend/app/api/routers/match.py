"""简历与岗位匹配路由

职责：
- 暴露 /api/match 下的匹配相关 HTTP 端点
- 接收 HTTP 请求，做最薄的协议层适配
- 调用 MatchService 处理业务逻辑

端点：
- POST /compute: 计算单个简历-岗位的匹配分（同步）
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.domain.match.models import MatchComputeRequest, MatchResultResponse
from app.domain.match.service import MatchService
from app.infra.database.postgres import get_db_session

# ==================== Router 实例 ====================

router = APIRouter()


# ==================== 端点：计算匹配度 ====================


@router.post(
    "/compute",
    response_model=MatchResultResponse,
    status_code=status.HTTP_200_OK,
    summary="计算简历-岗位匹配度",
    description=(
        "计算当前用户简历与指定岗位的匹配分数。\n\n"
        "- 若未传 resume_id，使用用户当前活跃简历\n"
        "- 返回综合匹配分、BM25 分、命中/缺失技能、LLM 生成的建议\n"
        "- 无活跃简历返回 404，岗位不存在返回 404\n"
        "- LLM 生成建议失败不影响匹配分返回"
    ),
)
async def compute_match(
    request: Request,
    body: MatchComputeRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MatchResultResponse:
    """计算匹配度端点

    Args:
        request: FastAPI 请求对象（从中读取 user_id）
        body: 匹配计算请求
        db: 请求级 AsyncSession

    Returns:
        MatchResultResponse: 匹配结果
    """
    user_id = uuid.UUID(request.state.user_id)
    logger.info(
        "匹配计算端点 | user_id={} | job_id={} | resume_id={}",
        user_id,
        body.job_id,
        body.resume_id,
    )

    service = MatchService(db)
    try:
        return await service.compute_match_for_request(
            user_id=user_id,
            request=body,
        )
    finally:
        await service.close()


__all__ = ["router"]
