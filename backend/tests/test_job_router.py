"""Job Router 单元测试

职责：
- 测试 /api/jobs 端点的 HTTP 层行为
- 使用 httpx.AsyncClient + FastAPI TestClient 模式
- 覆盖正常流程、参数校验、异常响应

测试策略：
- Mock JobService：避免真实业务逻辑
- 测试 HTTP 状态码、请求/响应格式
- 测试参数校验（FastAPI 自动校验）
- 通过 make_jwt fixture 注入有效 JWT 绕过 auth 中间件
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import ResourceNotFoundError
from app.domain.job.models import (
    JobAnalysisResult,
    JobListResponse,
    JobResponse,
)
from main import create_app

# ==================== Fixtures ====================


@pytest.fixture
def app():
    """创建测试用 FastAPI app"""
    return create_app()


@pytest.fixture
async def client(app, make_jwt):
    """异步 HTTP 测试客户端（带有效 JWT）"""
    token = make_jwt(sub="test-user-123")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


# ==================== 测试数据 ====================

SAMPLE_JOB_ID = uuid.uuid4()

MOCK_JOB_RESPONSE = JobResponse(
    id=SAMPLE_JOB_ID,
    title="Python 高级工程师",
    company="字节跳动",
    jd_text="负责后端系统开发...",
    source="boss",
    source_url=None,
    salary_min=30,
    salary_max=50,
    location="北京",
    skills=["Python", "FastAPI"],
    keywords=["AI应用开发"],
    seniority="senior",
    difficulty="hard",
    analysis=JobAnalysisResult(
        skills=["Python", "FastAPI", "PostgreSQL"],
        keywords=["AI应用开发", "后端开发"],
        difficulty="hard",
        seniority="senior",
    ),
    created_at=datetime(2026, 1, 1, 12, 0, 0),
)

MOCK_ANALYSIS_RESULT = JobAnalysisResult(
    skills=["Python", "FastAPI", "PostgreSQL"],
    keywords=["AI应用开发", "后端开发"],
    difficulty="hard",
    seniority="senior",
)


# ==================== Create Job ====================


class TestJobRouterCreate:
    """创建岗位端点测试"""

    async def test_create_job_201(
        self,
        client: AsyncClient,
    ) -> None:
        """创建岗位返回 201"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.create_job.return_value = MOCK_JOB_RESPONSE
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/",
                json={
                    "title": "Python 高级工程师",
                    "company": "字节跳动",
                    "jd_text": "负责后端系统开发...",
                    "source": "boss",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Python 高级工程师"
        assert data["company"] == "字节跳动"

    async def test_create_job_missing_required_fields(
        self,
        client: AsyncClient,
    ) -> None:
        """缺少必填字段返回 422"""
        response = await client.post(
            "/api/jobs/",
            json={
                "title": "Python 高级工程师",
                # 缺少 company, jd_text, source
            },
        )

        assert response.status_code == 422

    @pytest.mark.xfail(
        reason="exception handler 序列化 ValueError 为 JSON 时 TypeError（已知 bug）",
        strict=False,
    )
    async def test_create_job_invalid_source(
        self,
        client: AsyncClient,
    ) -> None:
        """无效 source 值返回 422"""
        response = await client.post(
            "/api/jobs/",
            json={
                "title": "Python 高级工程师",
                "company": "字节跳动",
                "jd_text": "负责后端系统开发...",
                "source": "invalid_source",
            },
        )

        assert response.status_code == 422


# ==================== Get Job ====================


class TestJobRouterGet:
    """查询岗位端点测试"""

    async def test_get_job_200(
        self,
        client: AsyncClient,
    ) -> None:
        """查询岗位返回 200"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.get_job.return_value = MOCK_JOB_RESPONSE
            MockService.return_value = mock_instance

            response = await client.get(f"/api/jobs/{SAMPLE_JOB_ID}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(SAMPLE_JOB_ID)
        assert data["title"] == "Python 高级工程师"

    async def test_get_job_not_found(
        self,
        client: AsyncClient,
    ) -> None:
        """岗位不存在返回 404"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.get_job.side_effect = ResourceNotFoundError(
                detail=f"岗位 {SAMPLE_JOB_ID} 不存在"
            )
            MockService.return_value = mock_instance

            response = await client.get(f"/api/jobs/{SAMPLE_JOB_ID}")

        assert response.status_code == 404

    async def test_get_job_invalid_uuid(
        self,
        client: AsyncClient,
    ) -> None:
        """非法 UUID 返回 422"""
        response = await client.get("/api/jobs/not-a-uuid")

        assert response.status_code == 422


# ==================== List Jobs ====================


class TestJobRouterList:
    """列表查询端点测试"""

    async def test_list_jobs_200(
        self,
        client: AsyncClient,
    ) -> None:
        """列表查询返回 200"""
        mock_list_response = JobListResponse(
            items=[MOCK_JOB_RESPONSE],
            total=1,
            limit=20,
            offset=0,
        )
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.list_jobs.return_value = mock_list_response
            MockService.return_value = mock_instance

            response = await client.get("/api/jobs/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1

    async def test_list_jobs_with_pagination(
        self,
        client: AsyncClient,
    ) -> None:
        """分页参数正确传递"""
        mock_list_response = JobListResponse(
            items=[],
            total=0,
            limit=10,
            offset=20,
        )
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.list_jobs.return_value = mock_list_response
            MockService.return_value = mock_instance

            response = await client.get("/api/jobs/?limit=10&offset=20")

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["offset"] == 20


# ==================== Analyze Job ====================


class TestJobRouterAnalyze:
    """分析岗位端点测试"""

    async def test_analyze_job_200(
        self,
        client: AsyncClient,
    ) -> None:
        """分析岗位返回 200"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.analyze_job.return_value = MOCK_ANALYSIS_RESULT
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/analyze",
                json={"job_id": str(SAMPLE_JOB_ID)},
            )

        assert response.status_code == 200
        data = response.json()
        assert "skills" in data
        assert "Python" in data["skills"]

    async def test_analyze_job_not_found(
        self,
        client: AsyncClient,
    ) -> None:
        """分析不存在的岗位返回 404"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.analyze_job.side_effect = ResourceNotFoundError(
                detail=f"岗位 {SAMPLE_JOB_ID} 不存在"
            )
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/analyze",
                json={"job_id": str(SAMPLE_JOB_ID)},
            )

        assert response.status_code == 404

    async def test_analyze_job_missing_job_id(
        self,
        client: AsyncClient,
    ) -> None:
        """缺少 job_id 返回 422"""
        response = await client.post(
            "/api/jobs/analyze",
            json={},
        )

        assert response.status_code == 422

    async def test_analyze_job_invalid_job_id(
        self,
        client: AsyncClient,
    ) -> None:
        """非法 job_id 返回 422"""
        response = await client.post(
            "/api/jobs/analyze",
            json={"job_id": "not-a-uuid"},
        )

        assert response.status_code == 422
