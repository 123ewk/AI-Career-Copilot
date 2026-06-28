"""Job Service 单元测试

职责：
- 测试 JobService 的业务逻辑
- 使用 unittest.mock 模拟 Repository、Parser、Extractor、WebSearchTool
- 覆盖正常流程、边界条件、异常流程

测试策略：
- Mock 依赖：JobRepository、JDParser、JobExtractor、WebSearchTool
- 正常流程：创建岗位、查询岗位、分析岗位
- 边界条件：source_url 去重、空 JD、已有分析结果
- 异常流程：LLM 提取失败、Web 搜索失败
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.domain.job.extractor import JobExtractor
from app.domain.job.models import (
    JobAnalysisResult,
    JobAnalyzeResponse,
    JobCreateRequest,
    JobResponse,
)
from app.domain.job.parser import JDParser
from app.domain.job.service import JobService
from app.infra.database.models.job import Job
from app.infra.repositories.job_repo import JobRepository
from app.tools.retrieval.web_search import WebSearchTool

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
    repo = AsyncMock(spec=JobRepository)
    return repo


@pytest.fixture
def mock_parser() -> AsyncMock:
    """模拟 JDParser"""
    parser = AsyncMock(spec=JDParser)
    return parser


@pytest.fixture
def mock_extractor() -> AsyncMock:
    """模拟 JobExtractor"""
    extractor = AsyncMock(spec=JobExtractor)
    return extractor


@pytest.fixture
def mock_web_search() -> AsyncMock:
    """模拟 WebSearchTool"""
    tool = AsyncMock(spec=WebSearchTool)
    return tool


@pytest.fixture
def service(
    mock_session: AsyncMock,
    mock_repo: AsyncMock,
    mock_parser: AsyncMock,
    mock_extractor: AsyncMock,
    mock_web_search: AsyncMock,
) -> JobService:
    """JobService 实例（所有依赖已 mock）"""
    svc = JobService(session=mock_session, repo=mock_repo)
    # 注入其余 mock 依赖
    svc._parser = mock_parser
    svc._extractor = mock_extractor
    svc._web_search = mock_web_search
    return svc


# ==================== 测试数据 ====================

SAMPLE_JOB_ID = uuid.uuid4()
SAMPLE_TITLE = "Python 高级工程师"
SAMPLE_COMPANY = "字节跳动"
SAMPLE_JD_TEXT = "负责后端系统开发，要求熟悉 Python、FastAPI、PostgreSQL..."
SAMPLE_SOURCE = "boss"

MOCK_ANALYSIS_RESULT = JobAnalysisResult(
    skills=["Python", "FastAPI", "PostgreSQL"],
    keywords=["AI应用开发", "后端开发"],
    difficulty="hard",
    seniority="senior",
)


def _make_job(**overrides) -> MagicMock:
    """构造模拟 Job ORM 实例"""
    defaults = {
        "id": SAMPLE_JOB_ID,
        "title": SAMPLE_TITLE,
        "company": SAMPLE_COMPANY,
        "salary_min": 30,
        "salary_max": 50,
        "jd_text": SAMPLE_JD_TEXT,
        "source": SAMPLE_SOURCE,
        "source_url": None,
        "location": "北京",
        "skills": ["Python", "FastAPI"],
        "keywords": ["AI应用开发"],
        "seniority": "senior",
        "difficulty": "hard",
        "analysis_result": MOCK_ANALYSIS_RESULT.model_dump(),
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    job = MagicMock(spec=Job)
    for key, value in defaults.items():
        setattr(job, key, value)
    return job


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
        mock_repo.create.return_value = _make_job()

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
        existing_job = _make_job()
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
        mock_repo.create.return_value = _make_job(source_url=None)

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
        mock_repo.create.return_value = _make_job()

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
        mock_repo.get_by_id.return_value = _make_job()

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


# ==================== List Jobs ====================


class TestJobServiceList:
    """列表查询测试"""

    async def test_list_jobs(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """分页查询岗位列表"""
        jobs = [_make_job(), _make_job(id=uuid.uuid4())]
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


# ==================== Analyze Job ====================


class TestJobServiceAnalyze:
    """分析岗位测试"""

    async def test_analyze_job_basic(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_parser: AsyncMock,
        mock_extractor: AsyncMock,
    ) -> None:
        """分析岗位 - 正常流程"""
        job = _make_job(analysis_result=None)
        mock_repo.get_by_id.return_value = job
        mock_extractor.extract.return_value = MOCK_ANALYSIS_RESULT

        result = await service.analyze_job(SAMPLE_JOB_ID)

        assert isinstance(result, JobAnalysisResult)
        assert result.skills == ["Python", "FastAPI", "PostgreSQL"]
        mock_extractor.extract.assert_called_once()
        mock_repo.update_analysis.assert_called_once()

    async def test_analyze_job_not_found(
        self,
        service: JobService,
        mock_repo: AsyncMock,
    ) -> None:
        """分析岗位 - 岗位不存在"""
        mock_repo.get_by_id.return_value = None

        with pytest.raises(ResourceNotFoundError):
            await service.analyze_job(SAMPLE_JOB_ID)

    async def test_analyze_job_already_analyzed(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_extractor: AsyncMock,
    ) -> None:
        """分析岗位 - 已有分析结果时跳过 LLM 调用"""
        job = _make_job()  # analysis_result 已有值
        mock_repo.get_by_id.return_value = job

        result = await service.analyze_job(SAMPLE_JOB_ID)

        assert isinstance(result, JobAnalysisResult)
        # 不应调用 extractor
        mock_extractor.extract.assert_not_called()

    async def test_analyze_job_force_reanalyze(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_extractor: AsyncMock,
    ) -> None:
        """分析岗位 - 强制重新分析"""
        job = _make_job()  # 已有分析结果
        mock_repo.get_by_id.return_value = job
        mock_extractor.extract.return_value = MOCK_ANALYSIS_RESULT

        result = await service.analyze_job(SAMPLE_JOB_ID, force=True)

        assert isinstance(result, JobAnalysisResult)
        mock_extractor.extract.assert_called_once()

    async def test_analyze_job_saves_to_repo(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_extractor: AsyncMock,
    ) -> None:
        """分析结果保存到 Repository"""
        job = _make_job(analysis_result=None)
        mock_repo.get_by_id.return_value = job
        mock_extractor.extract.return_value = MOCK_ANALYSIS_RESULT

        await service.analyze_job(SAMPLE_JOB_ID)

        # 验证 update_analysis 被调用，且传入了正确的参数
        call_args = mock_repo.update_analysis.call_args
        assert call_args[0][0] == job  # 第一个位置参数是 job
        assert call_args[1]["analysis_result"] is not None
        assert call_args[1]["skills"] == ["Python", "FastAPI", "PostgreSQL"]
        assert call_args[1]["seniority"] == "senior"

    async def test_analyze_job_llm_failure(
        self,
        service: JobService,
        mock_repo: AsyncMock,
        mock_extractor: AsyncMock,
    ) -> None:
        """分析岗位 - LLM 提取失败"""
        job = _make_job(analysis_result=None)
        mock_repo.get_by_id.return_value = job
        mock_extractor.extract.side_effect = ExternalServiceError(
            detail="LLM 调用失败",
            error_code="EXT_003",
        )

        with pytest.raises(ExternalServiceError):
            await service.analyze_job(SAMPLE_JOB_ID)

    async def test_analyze_job_commits(
        self,
        service: JobService,
        mock_session: AsyncMock,
        mock_repo: AsyncMock,
        mock_extractor: AsyncMock,
    ) -> None:
        """分析完成后提交事务"""
        job = _make_job(analysis_result=None)
        mock_repo.get_by_id.return_value = job
        mock_extractor.extract.return_value = MOCK_ANALYSIS_RESULT

        await service.analyze_job(SAMPLE_JOB_ID)

        mock_session.commit.assert_called_once()
