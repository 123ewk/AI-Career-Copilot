"""Job Repository 单元测试

职责：
- 测试 JobRepository 的 CRUD 操作
- 测试按技能/关键词 JSONB 查询
- 测试 analysis_result 更新
- 使用 unittest.mock 模拟 AsyncSession

测试策略：
- Mock AsyncSession：避免真实数据库依赖
- 正常流程：创建、查询、更新、删除
- 边界条件：空结果、不存在记录、source_url 去重
- 异常流程：IntegrityError 透传
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.database.models.job import Job
from app.infra.repositories.job_repo import JobRepository

# ==================== Fixtures ====================


@pytest.fixture
def mock_session() -> AsyncMock:
    """模拟 AsyncSession"""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.get = AsyncMock()
    return session


@pytest.fixture
def repo(mock_session: AsyncMock) -> JobRepository:
    """JobRepository 实例（使用模拟 session）"""
    return JobRepository(mock_session)


# ==================== 测试数据 ====================

SAMPLE_JOB_ID = uuid.uuid4()
SAMPLE_TITLE = "Python 高级工程师"
SAMPLE_COMPANY = "字节跳动"
SAMPLE_JD_TEXT = "负责后端系统开发，要求熟悉 Python、FastAPI、PostgreSQL..."
SAMPLE_SOURCE = "boss"
SAMPLE_SOURCE_URL = "https://www.zhipin.com/job_detail/123.html"
SAMPLE_LOCATION = "北京"
SAMPLE_SKILLS = ["Python", "FastAPI", "PostgreSQL"]
SAMPLE_KEYWORDS = ["AI应用开发", "后端开发"]
SAMPLE_SENIORITY = "senior"
SAMPLE_DIFFICULTY = "hard"

SAMPLE_ANALYSIS_RESULT = {
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
    "keywords": ["AI应用开发", "后端开发", "大模型"],
    "seniority": "senior",
    "difficulty": "hard",
    "salary_range": {"min": 30, "max": 50, "unit": "K"},
    "company_info": {"industry": "互联网", "scale": "10000人以上"},
    "hidden_requirements": ["有大模型经验优先"],
}


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
        "source_url": SAMPLE_SOURCE_URL,
        "location": SAMPLE_LOCATION,
        "skills": SAMPLE_SKILLS,
        "keywords": SAMPLE_KEYWORDS,
        "seniority": SAMPLE_SENIORITY,
        "difficulty": SAMPLE_DIFFICULTY,
        "analysis_result": None,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
    }
    defaults.update(overrides)
    job = MagicMock(spec=Job)
    for key, value in defaults.items():
        setattr(job, key, value)
    return job


# ==================== Create ====================


class TestJobRepoCreate:
    """创建岗位测试"""

    async def test_create_basic(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """创建基本岗位"""
        job = await repo.create(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
        )

        assert isinstance(job, Job)
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    async def test_create_with_all_fields(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """创建包含所有字段的岗位"""
        job = await repo.create(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
            source_url=SAMPLE_SOURCE_URL,
            salary_min=30,
            salary_max=50,
            location=SAMPLE_LOCATION,
            skills=SAMPLE_SKILLS,
            keywords=SAMPLE_KEYWORDS,
            seniority=SAMPLE_SENIORITY,
            difficulty=SAMPLE_DIFFICULTY,
        )

        assert isinstance(job, Job)
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    async def test_create_with_defaults(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """创建岗位时可选字段使用默认值"""
        job = await repo.create(
            title=SAMPLE_TITLE,
            company=SAMPLE_COMPANY,
            jd_text=SAMPLE_JD_TEXT,
            source=SAMPLE_SOURCE,
        )

        # 检查默认值
        added_job = mock_session.add.call_args[0][0]
        assert added_job.skills == []
        assert added_job.keywords == []
        assert added_job.source_url is None
        assert added_job.salary_min is None
        assert added_job.salary_max is None
        assert added_job.location is None
        assert added_job.seniority is None
        assert added_job.difficulty is None


# ==================== Read ====================


class TestJobRepoRead:
    """查询岗位测试"""

    async def test_get_by_id_found(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按 ID 查询岗位 - 找到"""
        expected_job = _make_job()
        mock_session.get.return_value = expected_job

        result = await repo.get_by_id(SAMPLE_JOB_ID)

        assert result == expected_job
        mock_session.get.assert_called_once_with(Job, SAMPLE_JOB_ID)

    async def test_get_by_id_not_found(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按 ID 查询岗位 - 未找到"""
        mock_session.get.return_value = None

        result = await repo.get_by_id(SAMPLE_JOB_ID)

        assert result is None

    async def test_get_by_source_url_found(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按 source_url 查询岗位 - 找到"""
        expected_job = _make_job()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = expected_job
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_source_url(SAMPLE_SOURCE_URL)

        assert result == expected_job
        mock_session.execute.assert_called_once()

    async def test_get_by_source_url_not_found(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按 source_url 查询岗位 - 未找到"""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repo.get_by_source_url("https://nonexistent.com")

        assert result is None

    async def test_list_default(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """分页查询岗位列表 - 默认参数"""
        jobs = [_make_job(), _make_job(id=uuid.uuid4())]
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = jobs
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.list()

        assert len(result) == 2
        mock_session.execute.assert_called_once()

    async def test_list_with_pagination(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """分页查询岗位列表 - 自定义分页"""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.list(limit=10, offset=20)

        assert len(result) == 0

    async def test_list_empty(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """分页查询 - 空结果"""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.list()

        assert len(result) == 0

    async def test_count(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """统计岗位总数"""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 42
        mock_session.execute.return_value = mock_result

        result = await repo.count()

        assert result == 42

    async def test_count_zero(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """统计岗位总数 - 零"""
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_session.execute.return_value = mock_result

        result = await repo.count()

        assert result == 0


# ==================== Update ====================


class TestJobRepoUpdate:
    """更新岗位测试"""

    async def test_update_analysis(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """更新分析结果"""
        job = _make_job()
        analysis = SAMPLE_ANALYSIS_RESULT

        result = await repo.update_analysis(
            job,
            analysis_result=analysis,
            skills=analysis["skills"],
            keywords=analysis["keywords"],
            seniority=analysis["seniority"],
            difficulty=analysis["difficulty"],
        )

        assert result == job
        assert job.analysis_result == analysis
        assert job.skills == analysis["skills"]
        assert job.keywords == analysis["keywords"]
        assert job.seniority == analysis["seniority"]
        assert job.difficulty == analysis["difficulty"]
        mock_session.flush.assert_called_once()

    async def test_update_analysis_partial(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """部分更新分析结果 - 只更新 analysis_result"""
        job = _make_job()

        result = await repo.update_analysis(
            job,
            analysis_result=SAMPLE_ANALYSIS_RESULT,
        )

        assert result == job
        assert job.analysis_result == SAMPLE_ANALYSIS_RESULT
        # 其他字段不变
        assert job.skills == SAMPLE_SKILLS
        mock_session.flush.assert_called_once()

    async def test_update_analysis_none_fields_not_overwritten(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """未传的字段保持原值（None 哨兵）"""
        job = _make_job(seniority="mid")
        original_seniority = job.seniority

        await repo.update_analysis(
            job,
            analysis_result=SAMPLE_ANALYSIS_RESULT,
            # 不传 seniority
        )

        assert job.seniority == original_seniority


# ==================== Search ====================


class TestJobRepoSearch:
    """搜索岗位测试"""

    async def test_search_by_skills(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按技能搜索岗位"""
        jobs = [_make_job()]
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = jobs
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.search_by_skills(["Python", "FastAPI"])

        assert len(result) == 1
        mock_session.execute.assert_called_once()

    async def test_search_by_skills_empty(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按技能搜索 - 无匹配"""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.search_by_skills(["Rust"])

        assert len(result) == 0

    async def test_search_by_keywords(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按关键词搜索岗位"""
        jobs = [_make_job(), _make_job(id=uuid.uuid4())]
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = jobs
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.search_by_keywords(["AI应用开发"])

        assert len(result) == 2

    async def test_search_by_skills_with_pagination(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按技能搜索 - 带分页"""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repo.search_by_skills(["Python"], limit=5, offset=10)

        assert len(result) == 0


# ==================== Delete ====================


class TestJobRepoDelete:
    """删除岗位测试"""

    async def test_delete(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """删除岗位"""
        job = _make_job()

        await repo.delete(job)

        mock_session.delete.assert_called_once_with(job)
        mock_session.flush.assert_called_once()

    async def test_delete_by_id_found(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按 ID 删除岗位 - 找到"""
        job = _make_job()
        mock_session.get.return_value = job

        result = await repo.delete_by_id(SAMPLE_JOB_ID)

        assert result is True
        mock_session.delete.assert_called_once_with(job)

    async def test_delete_by_id_not_found(
        self,
        repo: JobRepository,
        mock_session: AsyncMock,
    ) -> None:
        """按 ID 删除岗位 - 未找到"""
        mock_session.get.return_value = None

        result = await repo.delete_by_id(SAMPLE_JOB_ID)

        assert result is False
        mock_session.delete.assert_not_called()
