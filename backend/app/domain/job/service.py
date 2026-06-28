"""Job Domain Service

职责：
- 编排 Job 域的业务逻辑：创建、查询、分析
- 协调 Repository、Parser、Extractor、WebSearchTool 等依赖
- 事务控制：commit/rollback 由本层管理

设计动机：
- 与 ResumeService / UserService 保持一致的分层模式
- analyze_job 为同步版本，后续 Step 1.6.7 改造为异步 MQ 版本
- source_url 去重：同一 URL 不重复入库

潜在风险：
- LLM 提取耗时长（5-30s）：同步模式下阻塞请求
  → 后续改造为 MQ 异步分发
- Web 搜索部分失败：不影响主流程，降级为无外部数据
"""

from __future__ import annotations

import uuid

from app.core.exceptions import ResourceNotFoundError
from app.core.logger import logger
from app.domain.job.extractor import JobExtractor
from app.domain.job.models import (
    JobAnalysisResult,
    JobAnalyzeResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
)
from app.domain.job.parser import JDParser
from app.domain.repositories.job import JobRepositoryProtocol
from app.tools.retrieval.web_search import WebSearchTool


class JobService:
    """Job 域服务

    用法：
        async with pg_session_factory() as session:
            service = JobService(session)
            job = await service.create_job(title=..., company=..., ...)
            result = await service.analyze_job(job.id)
    """

    def __init__(
        self,
        session,
        repo: JobRepositoryProtocol | None = None,
    ) -> None:
        """初始化

        Args:
            session: AsyncSession 实例
            repo: Job 仓储实现。None 时使用默认 JobRepository
        """
        self._session = session
        if repo is None:
            # 延迟导入具体实现，避免 Domain 模块顶层依赖 Infra
            from app.infra.repositories.job_repo import JobRepository

            repo = JobRepository(session)
        self._repo: JobRepositoryProtocol = repo
        self._parser = JDParser()
        self._extractor = JobExtractor()
        self._web_search = WebSearchTool()

    async def create_job(
        self,
        *,
        title: str,
        company: str,
        jd_text: str,
        source: str,
        source_url: str | None = None,
        salary_min: int | None = None,
        salary_max: int | None = None,
        location: str | None = None,
        skills: list[str] | None = None,
        keywords: list[str] | None = None,
        seniority: str | None = None,
        difficulty: str | None = None,
    ) -> JobResponse:
        """创建岗位

        行为：
        - 若 source_url 已存在，返回已有岗位（去重）
        - 否则创建新岗位并提交事务

        Args:
            title: 岗位名称
            company: 公司名称
            jd_text: JD 原文
            source: 来源平台
            source_url: 原始链接（唯一约束，用于去重）
            salary_min: 最低薪资（K）
            salary_max: 最高薪资（K）
            location: 工作地点
            skills: 技能列表
            keywords: 关键词列表
            seniority: 资历要求
            difficulty: 难度评级

        Returns:
            JobResponse DTO
        """
        logger.info("创建岗位 | title={} | company={}", title, company)

        # source_url 去重
        if source_url:
            existing = await self._repo.get_by_source_url(source_url)
            if existing is not None:
                logger.info("岗位已存在（source_url 去重） | id={}", existing.id)
                return JobResponse.model_validate(existing)

        job = await self._repo.create(
            title=title,
            company=company,
            jd_text=jd_text,
            source=source,
            source_url=source_url,
            salary_min=salary_min,
            salary_max=salary_max,
            location=location,
            skills=skills,
            keywords=keywords,
            seniority=seniority,
            difficulty=difficulty,
        )
        await self._session.commit()

        logger.info("岗位创建成功 | id={}", job.id)
        return JobResponse.model_validate(job)

    async def get_job(self, job_id: uuid.UUID) -> JobResponse:
        """查询岗位详情

        Args:
            job_id: 岗位 UUID

        Returns:
            JobResponse DTO

        Raises:
            ResourceNotFoundError: 岗位不存在
        """
        job = await self._repo.get_by_id(job_id)
        if job is None:
            raise ResourceNotFoundError(
                detail=f"岗位 {job_id} 不存在",
                extra={"job_id": str(job_id)},
            )
        return JobResponse.model_validate(job)

    async def list_jobs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> JobListResponse:
        """分页查询岗位列表

        Args:
            limit: 每页大小，默认 20
            offset: 偏移量，默认 0

        Returns:
            JobListResponse DTO（含分页信息）
        """
        jobs = await self._repo.list(limit=limit, offset=offset)
        total = await self._repo.count()

        return JobListResponse(
            items=[JobResponse.model_validate(j) for j in jobs],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def analyze_job(
        self,
        job_id: uuid.UUID,
        *,
        force: bool = False,
    ) -> JobAnalysisResult:
        """分析岗位（同步版本）

        流程：
        1. 查询岗位
        2. 若已有分析结果且 force=False，直接返回
        3. 调用 JobExtractor 提取结构化信息
        4. 保存分析结果到数据库
        5. 提交事务

        后续 Step 1.6.7 改造为异步 MQ 版本：
        - 创建 Task(status=pending)
        - Publisher 发送 agent.task.job_analysis 消息
        - 返回 {job_id, task_id}

        Args:
            job_id: 岗位 UUID
            force: 是否强制重新分析（已有结果时）

        Returns:
            JobAnalysisResult

        Raises:
            ResourceNotFoundError: 岗位不存在
            ExternalServiceError: LLM 提取失败
        """
        logger.info("分析岗位开始 | job_id={} | force={}", job_id, force)

        # 1. 查询岗位
        job = await self._repo.get_by_id(job_id)
        if job is None:
            raise ResourceNotFoundError(
                detail=f"岗位 {job_id} 不存在",
                extra={"job_id": str(job_id)},
            )

        # 2. 已有分析结果时跳过
        if job.analysis_result is not None and not force:
            logger.info("岗位已有分析结果，跳过 | job_id={}", job_id)
            return JobAnalysisResult.model_validate(job.analysis_result)

        # 3. 调用 LLM 提取
        analysis = await self._extractor.extract(job.jd_text)

        # 4. 保存分析结果
        await self._repo.update_analysis(
            job,
            analysis_result=analysis.model_dump(),
            skills=analysis.skills,
            keywords=analysis.keywords,
            seniority=analysis.seniority,
            difficulty=analysis.difficulty,
        )
        await self._session.commit()

        logger.info(
            "分析岗位完成 | job_id={} | skills_count={} | difficulty={}",
            job_id,
            len(analysis.skills),
            analysis.difficulty,
        )

        return analysis
