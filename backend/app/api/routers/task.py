"""任务路由

职责：
- 暴露 /api/tasks 下的异步任务 HTTP 端点
- 供前端轮询任务状态

端点：
- GET /{task_id}: 查询任务详情（200 OK）
"""

import uuid

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, ResourceNotFoundError
from app.core.logger import logger
from app.domain.task.dto import TaskDTO
from app.domain.task.service import TaskService
from app.infra.database.postgres import get_db_session

router = APIRouter()


@router.get(
    "/{task_id}",
    response_model=TaskDTO,
    status_code=status.HTTP_200_OK,
    summary="查询任务状态",
    description=(
        "按 ID 查询异步任务详情。\n\n"
        "- 前端通过轮询此接口获取 Job Analysis 任务执行状态\n"
        "- status=COMPLETED 时 result 字段包含分析结果\n"
        "- status=FAILED 时 error_message 字段包含错误信息\n"
        "- 只能查询当前用户的任务"
    ),
)
async def get_task(
    request: Request,
    task_id: uuid.UUID = Path(
        ...,
        description="任务 UUID v4",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> TaskDTO:
    """查询任务状态端点

    Args:
        request: FastAPI 请求对象（读取 user_id）
        task_id: 任务 UUID（Path 校验）
        db: 请求级 AsyncSession

    Returns:
        TaskDTO: 任务详情

    Raises:
        AuthenticationError: 未认证（中间件转 401）
        ResourceNotFoundError: 任务不存在（中间件转 404）
    """
    user_id_str = getattr(request.state, "user_id", None)
    if not user_id_str:
        raise AuthenticationError(detail="缺少用户认证信息")

    user_id = uuid.UUID(user_id_str)
    logger.info("查询任务状态端点 | task_id={} | user_id={}", task_id, user_id)

    service = TaskService(db)
    task = await service.get_task(task_id)
    # 校验任务归属：防止用户查询其他用户的任务
    # 统一返回 404，避免泄露任务存在性
    if task.user_id != user_id:
        raise ResourceNotFoundError(
            detail=f"任务 {task_id} 不存在",
            extra={"task_id": str(task_id)},
        )
    return task


__all__ = ["router"]
