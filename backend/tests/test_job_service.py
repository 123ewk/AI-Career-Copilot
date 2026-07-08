"""Job Service 单元测试

职责：
- 测试 JobService 的业务编排逻辑
- Mock Repository / TaskService / Publisher / Cache / AgentService
- 覆盖创建/查询/列表/分析（异步化版本）正常 + 异常流程

测试策略：
- Mock 依赖：JobRepository、TaskService、MessagePublisher、JobAnalysisCacheProtocol、AgentService
- 正常流程：创建岗位、查询岗位、列表、异步分析（pending/completed）
- 边界条件：source_url 去重、已有分析结果、缓存命中
- 异常流程：岗位不存在、LLM 失败、MQ 失败降级

与异步 API 的对齐（Step 1.6.7 契约）：
- analyze_job 不再同步执行 Agent，而是创建 Task + 发 MQ → 返回 JobAnalyzeResponse
- analyze_job 必传 user_id 和 session_id 关键字参数
- MQ 失败时降级为同步执行（sync_fallback=True）或抛 MessageQueueError（sync_fallback=False）
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ExternalServiceError,
    MessageQueueError,
    ResourceNotFoundError,
)
from app.domain.agent.job_analysis_agent import AgentRunResult
from app.domain.job.models import (
    JobAnalysisResult,
    JobAnalyzeResponse,
    JobListResponse,
    JobResponse,
    JobUpdateRequest,
)
from app.domain.job.service import JobService
from app.domain.task.dto import TaskDTO
from app.infra.database.models.job import Job
from app.infra.database.models.task import TaskStatus
from app.runtime.state.agent_state import AgentState


# ==================== Fixtures ====================


@pytest.fixture
def mock_session() -> AsyncMock:
    """模拟 AsyncSession"""
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def mock_repo() -> AsyncMock:
    """模拟 JobRepository"""
    return AsyncMock()


@pytest.fixture
def mock_task_service() -> AsyncMock:
    """模拟 TaskService"""
    return AsyncMock()


@pytest.fixture
def mock_publisher() -> AsyncMock:
    """模拟 MessagePublisher"""
    return AsyncMock()


@pytest.fixture
def mock_cache() -> AsyncMock:
    """模拟 JobAnalysisCacheProtocol"""
    cache = AsyncMock()
    cache.get.return_value = None  # 默认缓存未命中
    return cache


@pytest.fixture
def mock_agent_service() -> AsyncMock:
    """模拟 AgentService

    引用模块级 MOCK_ANALYSIS_RESULT（定义在文件下方）：
    fixture 在测试运行时才被调用，此时模块已完全加载，模块级常量均可解析。
    """
    service = AsyncMock()
    service.analyze_job.return_value = AgentRunResult(
        analysis=MOCK_ANALYSIS_RESULT,
        agent_state=AgentState.COMPLETED,
        company_info=None,
        error=None,
    )
    return service


@pytest.fixture
def service(
    mock_session: AsyncMock,
    mock_repo: AsyncMock,
    mock_task_service: AsyncMock,
    mock_publisher: AsyncMock,
    mock_cache: AsyncMock,
    mock_agent_service: AsyncMock,
) -> JobService:
    """JobService 实例（所有依赖已 mock）

    注意：JobService 异步化后不再持有 _parser/_extractor/_web_search，
    这些依赖收敛到 AgentService Facade 内部。
    """
    return JobService(
        session=mock_session,
        repo=mock_repo,
        task_service=mock_task_service,
        publisher=mock_publisher,
        cache=mock_cache,
        agent_service=mock_agent_service,
    )


# ==================== 测试数据 ====================

SAMPLE_JOB_ID = uuid.uuid4()
SAMPLE_USER_ID = uuid.uuid4()
SAMPLE_SESSION_ID = uuid.uuid4()
SAMPLE_TASK_ID = uuid.uuid4()
SAMPLE_TITLE = "Python 高级工程师"
SAMPLE_COMPANY = "字节跳动"
SAMPLE_JD_TEXT = "负责后端系统开发，要求熟悉 Python、FastAPI、PostgreSQL..."
SAMPLE_SOURCE = "boss"
SAMPLE_BUSINESS_ID = f"analyze_jd:{SAMPLE_JOB_ID}"

MOCK_ANALYSIS_RESULT = JobAnalysisResult(
    skills=["Python", "FastAPI", "PostgreSQL"],
    keywords=["AI应用开发", "后端开发"],
    difficulty="hard",
    seniority="senior",
)


def _make_job_orm(**overrides) -> MagicMock:
    """构造模拟 Job ORM 实例"""
    defaults = {
        "id": SAMPLE_JOB_ID,
        "title": SAMPLE_TITLE,
        "company": SAMPLE_COMPANY,
        "salary_min": 30,
        "salary_max": 50,
        "salary_unit": "K",
        "jd_text": SAMPLE_JD_TEXT,
        "source": SAMPLE_SOURCE,
        "source_url": None,
        "location": "北京",
        "skills": ["Python", "FastAPI"],
        "keywords": ["AI应用开发"],
        "seniority": "senior",
        "difficulty": "hard",
        "analysis_result": None,  # 默认未分析
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    job = MagicMock(spec=Job)
    for key, value in defaults.items():
        setattr(job, key, value)
    return job


def _make_task_dto(status: TaskStatus = TaskStatus.PENDING) -> TaskDTO:
    """构造模拟 TaskDTO"""
    return TaskDTO(
        id=SAMPLE_TASK_ID,
        user_id=SAMPLE_USER_ID,
        session_id=SAMPLE_SESSION_ID,
        business_id=SAMPLE_BUSINESS_ID,
        task_type="analyze_jd",
        status=status,
        input_data={"job_id": str(SAMPLE_JOB_ID)},
        result=None,
        error_message=None,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        updated_at=datetime(2026, 1, 1, 12, 0, 1),
    )


# ==================== Create Job ====================


class TestJobServiceCreate:
    """创建岗位测试"""

    async def test_create_job_basic(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """创建基本岗位"""
        mock_repo.get_by_source_url.return_value = None
        mock_repo.create.return_value = _make_job_orm()

        result = await service.create_job(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
        )

        assert isinstance(result, JobResponse)
        assert result.title == SAMPLE_TITLE
        mock_repo.create.assert_called_once()

    async def test_create_job_with_source_url_dedup(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """source_url 已存在时返回已有岗位（去重）"""
        existing_job = _make_job_orm()
        mock_repo.get_by_source_url.return_value = existing_job

        result = await service.create_job(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
            source_url="https://example.com/job/1",
        )

        assert isinstance(result, JobResponse)
        assert result.id == existing_job.id
        # 不应调用 create（已存在）
        mock_repo.create.assert_not_called()

    async def test_create_job_without_source_url(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """无 source_url 时直接创建（不去重）"""
        mock_repo.create.return_value = _make_job_orm(source_url=None)

        result = await service.create_job(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
        )

        assert isinstance(result, JobResponse)
        mock_repo.create.assert_called_once()
        # 不应调用 get_by_source_url
        mock_repo.get_by_source_url.assert_not_called()

    async def test_create_job_commits(
        self,
        service: JobService,
        mock_session: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """创建岗位后提交事务"""
        mock_repo.get_by_source_url.return_value = None
        mock_repo.create.return_value = _make_job_orm()

        await service.create_job(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
        )

        mock_session.commit.assert_called_once()


# ==================== Get Job ====================


class TestJobServiceGet:
    """查询岗位测试"""

    async def test_get_job_found(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """查询岗位 - 找到"""
        mock_repo.get_by_id.return_value = _make_job_orm()

        result = await service.get_job(SAMPLE_JOB_ID)

        assert isinstance(result, JobResponse)
        assert result.id == SAMPLE_JOB_ID

    async def test_get_job_not_found(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """查询岗位 - 未找到"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.get_job(SAMPLE_JOB_ID)


# ==================== Update Job（海投模式 PATCH 补充详情）====================


class TestJobServiceUpdate:
    """部分更新岗位测试

    覆盖海投模式核心场景：
    - 列表页创建空 jd_text 岗位后，用户点击卡片补充详情
    - PATCH 语义：仅更新传入字段，未传入字段保持原值
    - 显式传 null 清空字段
    - 岗位不存在抛 ResourceNotFoundError
    """

    async def test_update_job_supplement_jd_text(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """海投模式：补充 jd_text（从空 → 完整 JD）"""
        # 列表页创建时 jd_text 为空
        empty_job = _make_job_orm(jd_text="")
        mock_repo.get_by_id.return_value = empty_job

        req = JobUpdateRequest(
            jd_text="完整 JD：负责 Python 后端开发，要求熟悉 FastAPI...",
            skills=["Python", "FastAPI", "PostgreSQL"],
            location="深圳·南山区",
        )

        result = await service.update_job(SAMPLE_JOB_ID, req)

        assert isinstance(result, JobResponse)
        # 验证 setattr 被调用，字段已更新
        assert empty_job.jd_text == req.jd_text
        assert empty_job.skills == req.skills
        assert empty_job.location == req.location
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once_with(empty_job)

    async def test_update_job_partial_fields_only(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """仅更新传入字段，未传入字段保持原值"""
        original_jd = "原 JD 内容"
        original_skills = ["Python"]
        job = _make_job_orm(jd_text=original_jd, skills=original_skills)
        mock_repo.get_by_id.return_value = job

        # 只更新 location，不传 jd_text / skills
        req = JobUpdateRequest(location="上海·浦东新区")

        await service.update_job(SAMPLE_JOB_ID, req)

        # 验证：location 被更新，jd_text 和 skills 保持原值
        assert job.location == "上海·浦东新区"
        assert job.jd_text == original_jd
        assert job.skills == original_skills

    async def test_update_job_not_found(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """岗位不存在抛 ResourceNotFoundError"""
        mock_repo.get_by_id.return_value = None

        req = JobUpdateRequest(jd_text="新 JD")

        with pytest.raises(ResourceNotFoundError):
            await service.update_job(SAMPLE_JOB_ID, req)

    async def test_update_job_explicit_null_clears_field(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """显式传 null 清空字段（PATCH 语义）

        场景：用户手动修正错误的 seniority，传 seniority=null 清空
        """
        job = _make_job_orm(seniority="senior")
        mock_repo.get_by_id.return_value = job

        # 显式传 null（不是省略）
        req = JobUpdateRequest(seniority=None)

        await service.update_job(SAMPLE_JOB_ID, req)

        # 验证：seniority 被清空为 None
        assert job.seniority is None

    async def test_update_job_salary_unit(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """更新 salary_unit（Boss 薪资单位：K / 元/天 / 元/时）"""
        job = _make_job_orm(salary_unit=None)
        mock_repo.get_by_id.return_value = job

        req = JobUpdateRequest(salary_unit="元/天")

        await service.update_job(SAMPLE_JOB_ID, req)

        assert job.salary_unit == "元/天"

    async def test_update_job_empty_request_no_changes(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_session: AsyncMock,
    ) -> None:
        """空请求体：不更新任何字段（合法场景）

        场景：Extension 发送 PATCH 但 body 为空（例如详情面板尚未加载完）
        """
        job = _make_job_orm()
        mock_repo.get_by_id.return_value = job
        original_title = job.title

        req = JobUpdateRequest()  # 全部使用默认值

        await service.update_job(SAMPLE_JOB_ID, req)

        # 验证：所有字段保持原值
        assert job.title == original_title
        # commit 仍应被调用（保持事务一致性）
        mock_session.commit.assert_called_once()


# ==================== List Jobs ====================


class TestJobServiceList:
    """列表查询测试"""

    async def test_list_jobs(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """分页查询岗位列表"""
        jobs = [_make_job_orm(), _make_job_orm(id=uuid.uuid4())]
        mock_repo.list.return_value = jobs
        mock_repo.count.return_value = 2

        result = await service.list_jobs(limit=20, offset=0)

        assert len(result.items) == 2
        assert result.total == 2
        assert result.limit == 20
        assert result.offset == 0

    async def test_list_jobs_empty(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """空列表"""
        mock_repo.list.return_value = []
        mock_repo.count.return_value = 0

        result = await service.list_jobs()

        assert len(result.items) == 0
        assert result.total == 0


# ==================== Analyze Job（异步化版本）=================


class TestJobServiceAnalyze:
    """分析岗位测试（Step 1.6.7 异步化契约）

    异步化后 analyze_job 行为：
    - 创建 Task(status=PENDING)
    - Publisher 发送 MQ 消息
    - 成功 → 返回 JobAnalyzeResponse(status="pending", task_id=...)
    - 失败 + sync_fallback=True → 同步执行 Agent → 返回 completed
    - 失败 + sync_fallback=False → 抛 MessageQueueError + mark_failed
    """

    async def test_analyze_job_cache_hit_returns_completed(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
    ) -> None:
        """缓存命中时直接返回 completed（cached=True）

        验收点：
        - 不创建 Task
        - 不发送 MQ
        - 不调用 Agent
        """
        mock_repo.get_by_id.return_value = _make_job_orm()
        mock_cache.get.return_value = MOCK_ANALYSIS_RESULT  # 缓存命中

        result = await service.analyze_job(
            SAMPLE_JOB_ID,
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
        )

        assert isinstance(result, JobAnalyzeResponse)
        assert result.status == "completed"
        assert result.cached is True
        assert result.analysis_result is not None
        assert result.analysis_result.skills == MOCK_ANALYSIS_RESULT.skills

        # 不应创建 Task
        mock_task_service.create_task.assert_not_awaited()
        # 不应发送 MQ
        mock_publisher.publish.assert_not_awaited()

    async def test_analyze_job_db_existing_returns_completed(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
    ) -> None:
        """DB 已有分析结果时返回 completed（cached=False）"""
        # DB 中已有 analysis_result
        mock_repo.get_by_id.return_value = _make_job_orm(
            analysis_result=MOCK_ANALYSIS_RESULT.model_dump()
        )
        mock_cache.get.return_value = None  # cache miss

        result = await service.analyze_job(
            SAMPLE_JOB_ID,
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
        )

        assert result.status == "completed"
        assert result.cached is False
        assert result.analysis_result is not None

        # 不应创建 Task
        mock_task_service.create_task.assert_not_awaited()

    async def test_analyze_job_pending_returns_task_id(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
    ) -> None:
        """正常异步流程：创建 Task + 发 MQ → 返回 pending + task_id"""
        mock_repo.get_by_id.return_value = _make_job_orm(analysis_result=None)
        mock_cache.get.return_value = None
        mock_task_service.create_task.return_value = _make_task_dto(TaskStatus.PENDING)

        result = await service.analyze_job(
            SAMPLE_JOB_ID,
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
        )

        assert isinstance(result, JobAnalyzeResponse)
        assert result.status == "pending"
        assert result.task_id == SAMPLE_TASK_ID

        # 验证 Task 被创建（business_id 符合契约）
        mock_task_service.create_task.assert_awaited_once()
        create_call = mock_task_service.create_task.call_args
        assert create_call.kwargs["task_type"] == "analyze_jd"
        assert create_call.kwargs["business_id"] == SAMPLE_BUSINESS_ID
        assert create_call.kwargs["user_id"] == SAMPLE_USER_ID
        assert create_call.kwargs["session_id"] == SAMPLE_SESSION_ID

        # 验证 MQ 消息被发送
        mock_publisher.publish.assert_awaited_once()
        publish_call = mock_publisher.publish.call_args
        assert publish_call.kwargs["message_id"] == SAMPLE_BUSINESS_ID

    async def test_analyze_job_not_found(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """分析岗位 - 岗位不存在"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.analyze_job(
                SAMPLE_JOB_ID,
                user_id=SAMPLE_USER_ID,
                session_id=SAMPLE_SESSION_ID,
            )

    async def test_analyze_job_force_invalidates_cache(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
    ) -> None:
        """force=True 时先失效缓存，再走异步流程"""
        # DB 和 cache 都有结果
        mock_repo.get_by_id.return_value = _make_job_orm(
            analysis_result=MOCK_ANALYSIS_RESULT.model_dump()
        )
        mock_cache.get.return_value = MOCK_ANALYSIS_RESULT
        mock_task_service.create_task.return_value = _make_task_dto(TaskStatus.PENDING)

        result = await service.analyze_job(
            SAMPLE_JOB_ID,
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            force=True,
        )

        # 验证缓存被失效
        mock_cache.invalidate.assert_awaited_once_with(SAMPLE_JOB_ID)

        # 验证创建了新 Task（即使 DB/cache 都有结果）
        mock_task_service.create_task.assert_awaited_once()

        # 验证发送了 MQ
        mock_publisher.publish.assert_awaited_once()

        assert result.status == "pending"

    async def test_analyze_job_mq_failure_with_sync_fallback(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
        mock_agent_service: AsyncMock,
    ) -> None:
        """MQ 失败 + sync_fallback=True → 同步执行 Agent → completed"""
        mock_repo.get_by_id.return_value = _make_job_orm(analysis_result=None)
        mock_repo.update_analysis.return_value = _make_job_orm(
            analysis_result=MOCK_ANALYSIS_RESULT.model_dump()
        )
        mock_cache.get.return_value = None
        mock_task_service.create_task.return_value = _make_task_dto(TaskStatus.PENDING)
        mock_publisher.publish.side_effect = ConnectionError("RabbitMQ 断连")

        result = await service.analyze_job(
            SAMPLE_JOB_ID,
            user_id=SAMPLE_USER_ID,
            session_id=SAMPLE_SESSION_ID,
            sync_fallback=True,
        )

        # 验收：降级同步执行
        assert result.status == "completed"
        assert result.task_id == SAMPLE_TASK_ID
        assert result.analysis_result is not None

        # Agent 被调用
        mock_agent_service.analyze_job.assert_awaited_once()

        # Task 被标记为完成
        mock_task_service.mark_completed.assert_awaited_once()

        # 缓存被回填
        mock_cache.set.assert_awaited_once()

    async def test_analyze_job_mq_failure_without_sync_fallback(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
        mock_agent_service: AsyncMock,
    ) -> None:
        """MQ 失败 + sync_fallback=False → 抛 MessageQueueError + mark_failed"""
        mock_repo.get_by_id.return_value = _make_job_orm(analysis_result=None)
        mock_cache.get.return_value = None
        mock_task_service.create_task.return_value = _make_task_dto(TaskStatus.PENDING)
        mock_publisher.publish.side_effect = ConnectionError("RabbitMQ 断连")

        with pytest.raises(MessageQueueError):
            await service.analyze_job(
                SAMPLE_JOB_ID,
                user_id=SAMPLE_USER_ID,
                session_id=SAMPLE_SESSION_ID,
                sync_fallback=False,
            )

        # 验收：Task 被标记失败
        mock_task_service.mark_failed.assert_awaited_once()

        # Agent 不应被调用
        mock_agent_service.analyze_job.assert_not_awaited()

        # mark_completed 不应被调用
        mock_task_service.mark_completed.assert_not_awaited()

    async def test_analyze_job_sync_fallback_agent_failure(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_task_service: AsyncMock,
        mock_publisher: AsyncMock,
        mock_agent_service: AsyncMock,
    ) -> None:
        """同步降级时 Agent 失败 → mark_failed + 抛 ExternalServiceError"""
        mock_repo.get_by_id.return_value = _make_job_orm(analysis_result=None)
        mock_cache.get.return_value = None
        mock_task_service.create_task.return_value = _make_task_dto(TaskStatus.PENDING)
        mock_publisher.publish.side_effect = ConnectionError("RabbitMQ 断连")
        # Agent 失败
        mock_agent_service.analyze_job.return_value = AgentRunResult(
            analysis=None,
            agent_state=AgentState.FAILED,
            error="LLM 调用超时",
        )

        with pytest.raises(ExternalServiceError):
            await service.analyze_job(
                SAMPLE_JOB_ID,
                user_id=SAMPLE_USER_ID,
                session_id=SAMPLE_SESSION_ID,
                sync_fallback=True,
            )

        # 验收：Task 被标记失败
        mock_task_service.mark_failed.assert_awaited_once()
        failed_call = mock_task_service.mark_failed.call_args
        assert "同步降级" in failed_call.kwargs.get("error_message", "")


# ==================== Run Analysis（同步执行路径）=================


class TestJobServiceRunAnalysis:
    """run_analysis 测试：被 Consumer 和同步降级路径复用"""

    async def test_run_analysis_success(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_cache: AsyncMock,
        mock_agent_service: AsyncMock,
    ) -> None:
        """同步执行分析成功：调用 Agent + 落库 + 写缓存"""
        mock_repo.get_by_id.return_value = _make_job_orm()
        mock_repo.update_analysis.return_value = _make_job_orm(
            analysis_result=MOCK_ANALYSIS_RESULT.model_dump()
        )

        result = await service.run_analysis(SAMPLE_JOB_ID, SAMPLE_JD_TEXT, company=SAMPLE_COMPANY)

        assert isinstance(result, JobAnalysisResult)
        assert result.skills == MOCK_ANALYSIS_RESULT.skills

        # 验证 Agent 被调用
        mock_agent_service.analyze_job.assert_awaited_once_with(
            jd_text=SAMPLE_JD_TEXT, company=SAMPLE_COMPANY
        )

        # 验证 DB 被更新
        mock_repo.update_analysis.assert_awaited_once()

        # 验证缓存被回填
        mock_cache.set.assert_awaited_once()

    async def test_run_analysis_job_not_found(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """run_analysis 时岗位不存在 → 抛 ResourceNotFoundError"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.run_analysis(SAMPLE_JOB_ID, SAMPLE_JD_TEXT)

    async def test_run_analysis_agent_failure(
        self,
        service: JobService,
        mock_agent_service: AsyncMock,
    ) -> None:
        """Agent 失败 → 抛 ExternalServiceError"""
        mock_agent_service.analyze_job.return_value = AgentRunResult(
            analysis=None,
            agent_state=AgentState.FAILED,
            error="LLM 调用失败",
        )

        # get_by_id 在 run_analysis 末尾才被调用，所以这里也 mock
        service._repo.get_by_id.return_value = _make_job_orm()

        with pytest.raises(ExternalServiceError):
            await service.run_analysis(SAMPLE_JOB_ID, SAMPLE_JD_TEXT)
