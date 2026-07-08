"""岗位相关路由

职责：
- 暴露 /api/jobs 下的岗位相关 HTTP 端点
- 接收 HTTP 请求，做最薄的协议层适配
- 调用 JobService 处理业务逻辑
- 业务异常由全局异常中间件统一翻译为 4xx/5xx JSON 响应

端点：
- POST /: 创建岗位（201 Created）
- GET /: 岗位列表（200 OK，分页）
- GET /{job_id}: 岗位详情（200 OK）
- POST /analyze: 分析岗位（202 Accepted，异步返回 task_id）

设计动机：
- Router 不做业务逻辑：参数校验由 FastAPI 自动完成
- 鉴权由全局 auth 中间件完成，user_id 从 request.state.user_id 读取
- 状态码语义：create=201, get/list=200, analyze=202
"""

import uuid

from aio_pika.abc import AbstractRobustChannel
from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.domain.job.models import (
    JobAnalyzeRequest,
    JobAnalyzeResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobUpdateRequest,
)
from app.domain.job.service import JobService
from app.infra.cache.job_analysis import RedisJobAnalysisCache
from app.infra.database.postgres import get_db_session
from app.infra.message_queue import MessagePublisher
from app.infra.message_queue.connection import get_rabbitmq_channel

# ==================== Router 实例 ====================

router = APIRouter()


# ==================== 端点：创建岗位 ====================


@router.post(
    "/",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建岗位",
    description=(
        "创建一条岗位记录。\n\n"
        "- source_url 有唯一约束，重复提交返回已有记录（幂等）\n"
        "- skills/keywords 为可选列表，后续由 Agent 分析填充\n"
        "- 创建成功返回 201 + 完整岗位信息"
    ),
)
async def create_job(
    body: JobCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """创建岗位端点

    Args:
        body: 岗位创建请求（Pydantic 校验）
        db: 请求级 AsyncSession

    Returns:
        JobResponse: 完整岗位信息
    """
    logger.info("创建岗位端点 | title={} | company={}", body.title, body.company)

    service = JobService(db)
    return await service.create_job(
        title=body.title,
        company=body.company,
        jd_text=body.jd_text,
        source=body.source,
        source_url=body.source_url,
        salary_min=body.salary_min,
        salary_max=body.salary_max,
        salary_unit=body.salary_unit,
        location=body.location,
        skills=body.skills,
        keywords=body.keywords,
        seniority=body.seniority,
        difficulty=body.difficulty,
    )


# ==================== 端点：岗位列表 ====================


@router.get(
    "/",
    response_model=JobListResponse,
    summary="获取岗位列表（分页）",
    description=(
        "分页返回岗位列表。\n\n"
        "- 默认按创建时间倒序（最新在前）\n"
        "- 每页 1-100 条，默认 20 条\n"
        "- 列表项为 JobSummary，不包含 jd_text 全文"
    ),
)
async def list_jobs(
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
) -> JobListResponse:
    """岗位列表端点

    Args:
        limit: 每页大小（Query 校验 1-100）
        offset: 偏移量（Query 校验 >=0）
        db: 请求级 AsyncSession

    Returns:
        JobListResponse: items + total + 回显 limit/offset
    """
    service = JobService(db)
    return await service.list_jobs(limit=limit, offset=offset)


# ==================== 端点：岗位详情 ====================


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="获取岗位详情",
    description=(
        "按 ID 查询岗位完整信息。\n\n"
        "- 包含 analysis 分析结果（如有）\n"
        "- 不存在返回 404"
    ),
)
async def get_job(
    job_id: uuid.UUID = Path(
        ...,
        description="岗位 UUID v4",
    ),
    db: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """岗位详情端点

    Args:
        job_id: 岗位 UUID（Path 校验）
        db: 请求级 AsyncSession

    Returns:
        JobResponse: 完整岗位信息（含 analysis）

    Raises:
        ResourceNotFoundError: 岗位不存在（中间件转 404）
    """
    service = JobService(db)
    return await service.get_job(job_id)


# ==================== 端点：部分更新岗位 ====================


@router.patch(
    "/{job_id}",
    response_model=JobResponse,
    summary="部分更新岗位",
    description=(
        "部分更新岗位字段（PATCH 语义：仅更新传入字段）。\n\n"
        "- 海投模式：用户点击卡片加载详情后，调用此接口补充 jd_text / skills / location\n"
        "- 不允许更新 source / source_url（DTO 层 extra=forbid 拦截）\n"
        "- 显式传 null 可清空字段（如清空 seniority）\n"
        "- jd_text 从空 → 非空时不自动触发分析，需独立调用 POST /api/jobs/analyze\n"
        "- 不存在返回 404"
    ),
)
async def update_job(
    job_id: uuid.UUID = Path(
        ...,
        description="岗位 UUID v4",
    ),
    body: JobUpdateRequest = None,  # type: ignore[assignment]
    db: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """部分更新岗位端点

    Args:
        job_id: 岗位 UUID（Path 校验）
        body: 部分更新请求（字段全可选，仅传入字段会被更新）
        db: 请求级 AsyncSession

    Returns:
        JobResponse: 更新后的完整岗位信息

    Raises:
        ResourceNotFoundError: 岗位不存在（中间件转 404）
        ValidationError: 字段校验失败（中间件转 422）
    """
    # 允许 PATCH 空请求体：FastAPI 默认对 body=None 报 422，
    # 这里使用默认值 None + 手动构造空 DTO，兼容「PATCH 无 body」的合法场景
    if body is None:
        body = JobUpdateRequest()

    logger.info(
        "更新岗位端点 | job_id={} | fields={}",
        job_id,
        list(body.model_dump(exclude_unset=True).keys()),
    )

    service = JobService(db)
    return await service.update_job(job_id, body)


# ==================== 端点：分析岗位 ====================


@router.post(
    "/analyze",
    response_model=JobAnalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="分析岗位（异步）",
    description=(
        "触发 Job Analysis Agent 异步分析 JD 文本，提取结构化信息。\n\n"
        "- 202 Accepted：任务已入队，返回 task_id 供前端轮询\n"
        "- 缓存命中或 DB 已有结果时直接返回 completed（200 语义，但状态码仍为 202）\n"
        "- RabbitMQ 不可用时降级为同步执行，返回 completed\n"
        "- 轮询地址：GET /api/tasks/{task_id}\n"
        "- force=true 强制重新分析（会失效缓存）\n"
        "- 不存在返回 404，LLM 调用失败返回 502"
    ),
)
async def analyze_job(
    request: Request,
    body: JobAnalyzeRequest,
    db: AsyncSession = Depends(get_db_session),
    channel: AbstractRobustChannel = Depends(get_rabbitmq_channel),
) -> JobAnalyzeResponse:
    """分析岗位端点

    Args:
        request: FastAPI 请求对象（从中读取 user_id）
        body: 分析请求（含 job_id / session_id）
        db: 请求级 AsyncSession
        channel: RabbitMQ Channel（用于构造 Publisher）

    Returns:
        JobAnalyzeResponse: pending 时返回 task_id；completed 时返回 analysis_result

    Raises:
        ResourceNotFoundError: 岗位不存在（中间件转 404）
        MessageQueueError: MQ 不可用且未降级（中间件转 500/502）
        ExternalServiceError: LLM 调用失败（中间件转 502）
    """
    logger.info("分析岗位端点 | job_id={} | session_id={}", body.job_id, body.session_id)

    user_id = uuid.UUID(request.state.user_id)
    publisher = MessagePublisher(channel)
    cache = RedisJobAnalysisCache()
    service = JobService(db, publisher=publisher, cache=cache)

    return await service.analyze_job(
        job_id=body.job_id,
        user_id=user_id,
        session_id=body.session_id,
    )


__all__ = ["router"]
