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
- POST /analyze: 分析岗位（200 OK，同步版本；后续改造为 202 Accepted）

设计动机：
- Router 不做业务逻辑：参数校验由 FastAPI 自动完成
- 鉴权由全局 auth 中间件完成
- 状态码语义：create=201, get/list=200, analyze=200（后续改 202）
"""

import uuid

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.domain.job.models import (
    JobAnalysisResult,
    JobAnalyzeRequest,
    JobAnalyzeResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)
from app.domain.job.service import JobService
from app.infra.database.postgres import get_db_session

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
        "- 每页 1-100 条，默认 20 条"
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


# ==================== 端点：分析岗位 ====================


@router.post(
    "/analyze",
    response_model=JobAnalysisResult,
    summary="分析岗位（提取技能/关键词/难度）",
    description=(
        "调用 LLM 分析 JD 文本，提取结构化信息。\n\n"
        "- 同步版本：直接返回分析结果\n"
        "- 后续改造为异步：返回 202 + task_id，结果通过 WebSocket 推送\n"
        "- 已有分析结果时直接返回（force=true 强制重新分析）\n"
        "- 不存在返回 404\n"
        "- LLM 调用失败返回 502"
    ),
)
async def analyze_job(
    body: JobAnalyzeRequest,
    db: AsyncSession = Depends(get_db_session),
) -> JobAnalysisResult:
    """分析岗位端点

    Args:
        body: 分析请求（含 job_id）
        db: 请求级 AsyncSession

    Returns:
        JobAnalysisResult: 提取的结构化信息

    Raises:
        ResourceNotFoundError: 岗位不存在（中间件转 404）
        ExternalServiceError: LLM 调用失败（中间件转 502）
    """
    logger.info("分析岗位端点 | job_id={}", body.job_id)

    service = JobService(db)
    return await service.analyze_job(body.job_id)


__all__ = ["router"]
