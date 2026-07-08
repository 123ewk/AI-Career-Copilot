"""Job Domain Service

职责：
- 编排 Job 域的业务业务逻辑：创建、查询、分析
- 协调 Repository、AgentService、TaskService、Publisher、Cache 等依赖
- 事务控制：commit/rollback 由本层管理
- 分析任务异步化：LLM 调用必走 MQ（项目规则），RabbitMQ 不可用时降级同步执行

设计动机：
- 与 ResumeService / UserService 保持一致的分层模式
- analyze_job 异步化：创建 Task → 发送 MQ → 返回 202
- source_url 去重：同一 URL 不重复入库
- 缓存读路径：先读 cache → miss 读 DB analysis_result → 都不命中再走 LLM
- MQ 失败降级：Publisher 异常时同步执行 Agent，保证用户体验不中断
- Agent 细节收敛到 AgentService：Parser / Extractor / WebSearchTool 不再由 JobService 直接持有

潜在风险：
- LLM 提取耗时长（5-30s）：已改造为 MQ 异步分发
- MQ 不可用：sync_fallback 为 True 时降级同步执行，False 时抛 MessageQueueError
- Web 搜索部分失败：不影响主流程，降级为无外部数据
- 缓存击穿：cache 异常时 fail-open 走 DB，不阻塞流程
"""

from __future__ import annotations

import uuid

from app.core.exceptions import (
    ExternalServiceError,
    MessageQueueError,
    ResourceNotFoundError,
)
from app.core.logger import logger
from app.domain.agent.service import AgentService
from app.domain.cache.job import JobAnalysisCacheProtocol
from app.domain.job.models import (
    JobAnalysisResult,
    JobAnalyzeResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobSummary,
    JobUpdateRequest,
)
from app.domain.repositories.job import JobRepositoryProtocol
from app.domain.task.service import TaskService
from app.infra.message_queue import MessagePublisher
from app.infra.message_queue.exchanges import (
    EXCHANGE_AGENT,
    ROUTING_AGENT_JOB_ANALYSIS,
)
from app.runtime.state.agent_state import AgentState


class JobService:
    """Job 域服务

    用法：
        async with pg_session_factory() as session:
            service = JobService(session)
            job = await service.create_job(title=..., company=..., ...)
            response = await service.analyze_job(
                job_id=job.id,
                user_id=user_id,
                session_id=session_id,
            )
            # response.status == "pending" → 202
    """

    def __init__(
        self,
        session,
        repo: JobRepositoryProtocol | None = None,
        task_service: TaskService | None = None,
        publisher: MessagePublisher | None = None,
        cache: JobAnalysisCacheProtocol | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        """初始化

        Args:
            session: AsyncSession 实例
            repo: Job 仓储实现。None 时使用默认 JobRepository
            task_service: Task 域服务。None 时内部创建
            publisher: MQ 发布者。None 时 analyze_job 会走降级同步路径
            cache: Job 分析结果缓存。None 时跳过缓存读写
            agent_service: Agent 服务 facade。None 时创建默认实例
        """
        self._session = session
        if repo is None:
            # 延迟导入具体实现，避免 Domain 模块顶层依赖 Infra
            from app.infra.repositories.job_repo import JobRepository

            repo = JobRepository(session)
        self._repo: JobRepositoryProtocol = repo
        self._task_service = task_service or TaskService(session)
        self._publisher = publisher
        self._cache = cache
        self._agent_service = agent_service or AgentService()

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
        salary_unit: str | None = None,
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
            jd_text: JD 原文（海投模式可为空字符串）
            source: 来源平台
            source_url: 原始链接（唯一约束，用于去重）
            salary_min: 最低薪资（K）
            salary_max: 最高薪资（K）
            salary_unit: 薪资单位（K / 元/天 / 元/时，仅记录用）
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
            salary_unit=salary_unit,
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

    async def update_job(
        self,
        job_id: uuid.UUID,
        req: JobUpdateRequest,
    ) -> JobResponse:
        """部分更新岗位（PATCH /api/jobs/{job_id}）

        行为：
        - 仅更新 req 中显式传入的字段（用 model_dump(exclude_unset=True) 区分「未传入」与「显式 null」）
        - jd_text 从空 → 非空时不自动触发分析（由 Router / Extension 显式调用 analyze）
        - 不允许更新 source / source_url（已在 DTO 层面通过 extra="forbid" + 字段集合限制）

        设计：
        - 直接 ORM 属性赋值：SQLAlchemy 自动检测 dirty，flush 时生成 UPDATE SQL
        - 显式传入 null（如 seniority=None）会清空字段，符合 PATCH 语义
        - 不在此处做用户隔离校验：Job 表当前无 user_id（MVP 技术债，Phase 2 评估）
          · 若未来 Job 表增加 user_id 字段，应在此处校验 job.user_id == current_user.id

        Args:
            job_id: 岗位 UUID
            req: 部分更新请求（仅传入字段会被更新）

        Returns:
            JobResponse DTO（更新后的完整岗位信息）

        Raises:
            ResourceNotFoundError: 岗位不存在
        """
        logger.info(
            "更新岗位 | job_id={} | fields={}",
            job_id,
            list(req.model_dump(exclude_unset=True).keys()),
        )

        job = await self._repo.get_by_id(job_id)
        if job is None:
            raise ResourceNotFoundError(
                detail=f"岗位 {job_id} 不存在",
                extra={"job_id": str(job_id)},
            )

        # 仅更新显式传入的字段
        # exclude_unset=True：区分「未传入」（默认值）与「显式传 null」
        update_data = req.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(job, field, value)

        await self._session.commit()
        await self._session.refresh(job)

        logger.info(
            "岗位更新成功 | job_id={} | updated_fields={}",
            job_id,
            list(update_data.keys()),
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
            JobListResponse DTO（含分页信息，items 为 JobSummary）
        """
        jobs = await self._repo.list(limit=limit, offset=offset)
        total = await self._repo.count()

        return JobListResponse(
            items=[JobSummary.model_validate(j) for j in jobs],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def analyze_job(
        self,
        job_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        force: bool = False,
        sync_fallback: bool = True,
    ) -> JobAnalyzeResponse:
        """分析岗位（异步 MQ 版本）

        流程：
        1. 查询岗位
        2. 若 cache 命中且非 force，直接返回 completed（cached=True）
        3. 若 DB 中已有 analysis_result 且非 force，直接返回 completed
        4. 创建 Task(status=pending)
        5. Publisher 发送 agent.task.job_analysis 消息
        6. 发布成功 → 返回 JobAnalyzeResponse(job_id, task_id, status="pending")
        7. 发布失败 → 若 sync_fallback=True，同步执行 Agent 并返回 completed
                      否则抛 MessageQueueError

        Args:
            job_id: 岗位 UUID
            user_id: 所属用户 UUID（创建 Task 用）
            session_id: 所属会话 UUID（创建 Task 用）
            force: 是否强制重新分析（会失效缓存并重新走 LLM）
            sync_fallback: MQ 不可用时是否降级同步执行

        Returns:
            JobAnalyzeResponse

        Raises:
            ResourceNotFoundError: 岗位不存在
            MessageQueueError: MQ 发布失败且 sync_fallback=False
            ExternalServiceError: 同步降级时 LLM 调用失败
        """
        logger.info(
            "分析岗位开始 | job_id={} | user_id={} | force={} | sync_fallback={}",
            job_id,
            user_id,
            force,
            sync_fallback,
        )

        # 1. 查询岗位
        job = await self._repo.get_by_id(job_id)
        if job is None:
            raise ResourceNotFoundError(
                detail=f"岗位 {job_id} 不存在",
                extra={"job_id": str(job_id)},
            )

        # 2. 缓存命中直接返回（force 时先失效缓存）
        if force and self._cache is not None:
            await self._cache.invalidate(job_id)

        if not force and self._cache is not None:
            cached = await self._cache.get(job_id)
            if cached is not None:
                logger.info("岗位分析结果缓存命中 | job_id={}", job_id)
                return JobAnalyzeResponse(
                    job_id=job_id,
                    task_id=None,
                    status="completed",
                    analysis_result=cached,
                    cached=True,
                )

        # 3. DB 中已有分析结果直接返回
        if job.analysis_result is not None and not force:
            logger.info("岗位已有分析结果，跳过 | job_id={}", job_id)
            analysis = JobAnalysisResult.model_validate(job.analysis_result)
            return JobAnalyzeResponse(
                job_id=job_id,
                task_id=None,
                status="completed",
                analysis_result=analysis,
                cached=False,
            )

        # 4. 创建异步任务
        business_id = f"analyze_jd:{job_id}"
        task = await self._task_service.create_task(
            user_id=user_id,
            session_id=session_id,
            task_type="analyze_jd",
            business_id=business_id,
            input_data={
                "job_id": str(job_id),
                "jd_text": job.jd_text,
                "business_id": business_id,
            },
        )

        # 5. 尝试发送 MQ 消息
        if self._publisher is not None:
            try:
                await self._publisher.publish(
                    exchange_name=EXCHANGE_AGENT,
                    routing_key=ROUTING_AGENT_JOB_ANALYSIS,
                    payload={
                        "task_id": str(task.id),
                        "job_id": str(job_id),
                        "business_id": business_id,
                        "jd_text": job.jd_text,
                    },
                    # message_id 复用业务幂等键 business_id，便于 MQ 重投时追踪和去重
                    message_id=business_id,
                )
                logger.info(
                    "岗位分析任务已入队 | job_id={} | task_id={}",
                    job_id,
                    task.id,
                )
                return JobAnalyzeResponse(
                    job_id=job_id,
                    task_id=task.id,
                    status="pending",
                )
            except Exception as exc:
                logger.warning(
                    "岗位分析任务入队失败 | job_id={} | task_id={} | error={}",
                    job_id,
                    task.id,
                    exc,
                )
                if not sync_fallback:
                    # 标记任务失败，避免前端轮询时状态永远 PENDING
                    await self._task_service.mark_failed(
                        task.id,
                        error_message=f"MQ 发布失败: {exc}",
                    )
                    raise MessageQueueError(
                        detail="岗位分析任务入队失败，请稍后重试",
                        extra={"job_id": str(job_id), "task_id": str(task.id)},
                    ) from exc
                # 否则继续走降级同步路径
        else:
            logger.warning(
                "Publisher 未注入，岗位分析降级为同步执行 | job_id={}",
                job_id,
            )
            if not sync_fallback:
                await self._task_service.mark_failed(
                    task.id,
                    error_message="Publisher 未注入且 sync_fallback=False",
                )
                raise MessageQueueError(
                    detail="岗位分析任务入队失败：Publisher 未配置",
                    extra={"job_id": str(job_id), "task_id": str(task.id)},
                )

        # 6. 同步降级路径：直接执行 Agent，保存结果并标记任务完成
        # 先标记 RUNNING：同步降级仍然是一次完整的任务执行，必须遵守状态机
        # PENDING → RUNNING → COMPLETED，避免 mark_completed 时状态机校验失败
        logger.warning("岗位分析同步降级执行 | job_id={} | task_id={}", job_id, task.id)
        try:
            await self._task_service.mark_running(task.id)
            analysis = await self.run_analysis(
                job_id, job.jd_text, company=job.company
            )
        except Exception as exc:
            logger.error(
                "岗位分析同步降级失败 | job_id={} | task_id={} | error={}",
                job_id,
                task.id,
                exc,
            )
            await self._task_service.mark_failed(
                task.id,
                error_message=f"同步降级执行失败: {exc}",
            )
            raise ExternalServiceError(
                detail="岗位分析失败",
                extra={"job_id": str(job_id), "task_id": str(task.id)},
            ) from exc

        await self._task_service.mark_completed(
            task.id,
            result=analysis.model_dump(mode="json"),
        )
        return JobAnalyzeResponse(
            job_id=job_id,
            task_id=task.id,
            status="completed",
            analysis_result=analysis,
            cached=False,
        )

    async def run_analysis(
        self,
        job_id: uuid.UUID,
        jd_text: str,
        company: str | None = None,
    ) -> JobAnalysisResult:
        """同步执行岗位分析（供 MQ 失败降级和 Consumer 复用）

        流程：
        1. 调用 AgentService 执行完整分析流水线（解析 → 提取 → Web 搜索）
        2. 保存分析结果到数据库 + 缓存
        3. 提交事务

        Args:
            job_id: 岗位 UUID
            jd_text: JD 原文
            company: 公司名称（可选，用于 Web 搜索补充）

        Returns:
            JobAnalysisResult

        Raises:
            ResourceNotFoundError: 岗位不存在
            ExternalServiceError: Agent 分析失败
        """
        logger.info("同步执行岗位分析 | job_id={} | company={}", job_id, company)

        # 调用 Agent 执行完整流水线
        agent_result = await self._agent_service.analyze_job(
            jd_text=jd_text, company=company
        )
        if agent_result.agent_state != AgentState.COMPLETED:
            error_msg = agent_result.error or "Agent 分析失败"
            logger.error(
                "岗位分析 Agent 失败 | job_id={} | error={}",
                job_id,
                error_msg,
            )
            raise ExternalServiceError(
                detail=error_msg,
                extra={"job_id": str(job_id)},
            )
        analysis = agent_result.analysis
        if analysis is None:
            raise ExternalServiceError(
                detail="Agent 返回结果异常：analysis 为空",
                extra={"job_id": str(job_id)},
            )

        # 保存到数据库
        job = await self._repo.get_by_id(job_id)
        if job is None:
            raise ResourceNotFoundError(
                detail=f"岗位 {job_id} 不存在",
                extra={"job_id": str(job_id)},
            )
        await self._repo.update_analysis(
            job,
            analysis_result=analysis.model_dump(),
            skills=analysis.skills,
            keywords=analysis.keywords,
            seniority=analysis.seniority,
            difficulty=analysis.difficulty,
        )
        await self._session.commit()

        # 回填缓存（fail-open）
        if self._cache is not None:
            await self._cache.set(job_id, analysis)

        logger.info(
            "同步执行岗位分析完成 | job_id={} | skills_count={} | difficulty={}",
            job_id,
            len(analysis.skills),
            analysis.difficulty,
        )
        return analysis


__all__ = ["JobService"]
