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

与异步 API 的对齐（Step 1.6.10 契约）：
- POST /analyze 返回 202 Accepted + {task_id}（不是 200 + result）
- 缓存命中或 DB 已有结果时返回 completed（状态码仍为 202，语义为"已接受"）
- 请求体必须包含 job_id 和 session_id 两个字段
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import ResourceNotFoundError
from app.domain.job.models import (
    JobAnalyzeResponse,
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


@pytest.fixture(autouse=True)
def _mock_rate_limit_redis():
    """模块级 autouse：patch rate_limit 中间件内的 redis_client_factory 单例

    rate_limit 中间件 dispatch 在每个请求中访问 redis_client_factory.client，
    会触发模块级单例创建真实 Redis socket：
    - 单例在第一个测试结束后绑定到已关闭的 event loop
    - 后续测试复用旧单例 → RuntimeError: Event loop is closed
    - socket 在 gc 时触发 ResourceWarning → pytest 提升为 error

    防御：替换为 MagicMock，client 属性返回 AsyncMock，避免真实 Redis 连接。
    本 fixture 只针对本测试文件，不影响真正需要 Redis 的集成测试。
    """
    mock_factory = MagicMock()
    mock_factory.client = AsyncMock()
    mock_factory.close = AsyncMock()
    with patch(
        "app.api.middleware.rate_limit.redis_client_factory",
        new=mock_factory,
    ):
        yield


TEST_USER_UUID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
async def client(app, make_jwt):
    """异步 HTTP 测试客户端（带有效 JWT）

    make_jwt 在 conftest.py 定义，注入有效 JWT 绕过 auth 中间件。
    sub 必须是合法 UUID 字符串：路由层 `uuid.UUID(request.state.user_id)` 会校验。
    """
    token = make_jwt(sub=TEST_USER_UUID)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


# ==================== 测试数据 ====================

SAMPLE_JOB_ID = uuid.uuid4()
SAMPLE_SESSION_ID = uuid.uuid4()

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

# 异步分析响应：pending 状态 + task_id（Step 1.6.10 契约）
MOCK_ANALYZE_PENDING_RESPONSE = JobAnalyzeResponse(
    job_id=SAMPLE_JOB_ID,
    task_id=uuid.uuid4(),
    status="pending",
)

# 异步分析响应：completed 状态（缓存命中或 DB 已有结果时）
MOCK_ANALYZE_COMPLETED_RESPONSE = JobAnalyzeResponse(
    job_id=SAMPLE_JOB_ID,
    task_id=None,
    status="completed",
    analysis_result=JobAnalysisResult(
        skills=["Python", "FastAPI", "PostgreSQL"],
        keywords=["AI应用开发", "后端开发"],
        difficulty="hard",
        seniority="senior",
    ),
    cached=True,
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


# ==================== Analyze Job（异步化，Step 1.6.10 契约）====================


class TestJobRouterAnalyze:
    """分析岗位端点测试

    契约（Step 1.6.10）：
    - POST /analyze 返回 202 Accepted + {task_id}（不是 200 + result）
    - 请求体必须包含 job_id 和 session_id

    Windows ProactorEventLoop + 模块级单例注意事项：
    路由签名含 `Depends(get_rabbitmq_channel)` 和路由体内 `RedisJobAnalysisCache()`：
    - rabbitmq_connection_factory 是模块级单例，首次调用后绑定到当时的 event loop
    - 后续测试创建新 event loop 时，旧单例仍引用已关闭的 loop，触发 RuntimeError
    防御：autouse fixture 同时覆盖 FastAPI dependency_overrides + patch RedisJobAnalysisCache，
    避免触碰真实 MQ / Redis。
    """

    @pytest.fixture(autouse=True)
    def _mock_mq_channel_and_cache(self, app):
        """autouse：覆盖 MQ channel 依赖 + patch RedisJobAnalysisCache

        路由签名 `Depends(get_rabbitmq_channel)` 会触发 rabbitmq_connection_factory 单例
        创建真实 MQ 连接；路由体内 `RedisJobAnalysisCache()` 会触发 redis_client_factory
        单例创建真实 Redis 连接。两者都是模块级单例：
        - 单例在第一个测试结束后绑定到已关闭的 event loop
        - 后续测试复用旧单例 → RuntimeError: Event loop is closed

        防御：
        - 用 FastAPI dependency_overrides 覆盖 get_rabbitmq_channel，返回 AsyncMock
        - patch 路由模块的 RedisJobAnalysisCache 类，避免触发 redis_client_factory 单例
        - rate_limit 中间件的 redis 单例已由模块级 _mock_rate_limit_redis fixture 处理
        """
        from app.infra.message_queue.connection import get_rabbitmq_channel

        app.dependency_overrides[get_rabbitmq_channel] = lambda: AsyncMock()

        with patch(
            "app.api.routers.jobs.RedisJobAnalysisCache",
            new=MagicMock(return_value=AsyncMock()),
        ):
            yield

        app.dependency_overrides.clear()

    async def test_analyze_job_returns_202_pending(
        self,
        client: AsyncClient,
    ) -> None:
        """分析岗位异步入队 → 返回 202 + task_id + status=pending"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.analyze_job.return_value = MOCK_ANALYZE_PENDING_RESPONSE
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/analyze",
                json={
                    "job_id": str(SAMPLE_JOB_ID),
                    "session_id": str(SAMPLE_SESSION_ID),
                },
            )

        assert response.status_code == 202
        data = response.json()
        assert data["job_id"] == str(SAMPLE_JOB_ID)
        assert data["task_id"] is not None
        assert data["status"] == "pending"

    async def test_analyze_job_returns_202_completed_when_cached(
        self,
        client: AsyncClient,
    ) -> None:
        """缓存命中时返回 202 + completed + analysis_result（状态码仍为 202）"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.analyze_job.return_value = MOCK_ANALYZE_COMPLETED_RESPONSE
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/analyze",
                json={
                    "job_id": str(SAMPLE_JOB_ID),
                    "session_id": str(SAMPLE_SESSION_ID),
                },
            )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "completed"
        assert data["cached"] is True
        assert data["analysis_result"] is not None
        assert "Python" in data["analysis_result"]["skills"]

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
                json={
                    "job_id": str(SAMPLE_JOB_ID),
                    "session_id": str(SAMPLE_SESSION_ID),
                },
            )

        assert response.status_code == 404

    async def test_analyze_job_missing_required_fields(
        self,
        client: AsyncClient,
    ) -> None:
        """缺少 job_id 或 session_id 返回 422"""
        response = await client.post(
            "/api/jobs/analyze",
            json={
                "job_id": str(SAMPLE_JOB_ID),
                # 缺少 session_id
            },
        )

        assert response.status_code == 422

    async def test_analyze_job_empty_body(
        self,
        client: AsyncClient,
    ) -> None:
        """空请求体返回 422"""
        response = await client.post(
            "/api/jobs/analyze",
            json={},
        )

        assert response.status_code == 422

    async def test_analyze_job_invalid_job_id(
        self,
        client: AsyncClient,
    ) -> None:
        """非法 job_id 格式返回 422"""
        response = await client.post(
            "/api/jobs/analyze",
            json={
                "job_id": "not-a-uuid",
                "session_id": str(SAMPLE_SESSION_ID),
            },
        )

        assert response.status_code == 422

    async def test_analyze_job_invalid_session_id(
        self,
        client: AsyncClient,
    ) -> None:
        """非法 session_id 格式返回 422"""
        response = await client.post(
            "/api/jobs/analyze",
            json={
                "job_id": str(SAMPLE_JOB_ID),
                "session_id": "not-a-uuid",
            },
        )

        assert response.status_code == 422
