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
def _mock_app_dependencies(app):
    """函数级 autouse：覆盖所有可能触发真实 IO 的依赖

    create_app() 会注册完整的 auth / rate_limit / logging 中间件，
    且 job router 依赖 get_db_session / get_rabbitmq_channel / RedisJobAnalysisCache。
    这些依赖对应的工厂都是模块级单例，首次使用后绑定到当时的 event loop；
    pytest-asyncio 为每个测试创建新 event loop 时，旧单例会触发
    RuntimeError: Event loop is closed。

    防御：通过 FastAPI dependency_overrides 替换 get_db_session / get_rabbitmq_channel，
    并 patch RedisJobAnalysisCache 类和 rate_limit 的 redis_client_factory，
    确保本文件所有测试都不触碰真实 PG / Redis / MQ。
    """
    from app.infra.database.postgres import get_db_session
    from app.infra.message_queue.connection import get_rabbitmq_channel

    mock_redis_factory = MagicMock()
    mock_redis_factory.client = AsyncMock()
    mock_redis_factory.close = AsyncMock()

    app.dependency_overrides[get_db_session] = lambda: AsyncMock()
    app.dependency_overrides[get_rabbitmq_channel] = lambda: AsyncMock()

    with patch(
        "app.api.middleware.rate_limit.redis_client_factory",
        new=mock_redis_factory,
    ):
        with patch(
            "app.infra.cache.job_analysis.RedisJobAnalysisCache",
            new=MagicMock(return_value=AsyncMock()),
        ):
            yield

    app.dependency_overrides.clear()


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

    async def test_create_job_with_empty_jd_text(
        self,
        client: AsyncClient,
    ) -> None:
        """海投模式：jd_text="" 允许创建（Step 0.1 关键变更）

        场景：列表页批量提取岗位时无完整 JD，先用基础信息创建
        """
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            # 模拟后端返回：jd_text 为空字符串的岗位
            mock_response = JobResponse(
                id=uuid.uuid4(),
                title="Python 实习生",
                company="科脉技术",
                jd_text="",
                source="boss",
                source_url="https://www.zhipin.com/job_detail/xxx.html",
                salary_min=300,
                salary_max=360,
                salary_unit="元/天",
                location="深圳·南山区·西丽",
                skills=[],
                keywords=["5天/周", "6个月", "本科"],
                seniority=None,
                difficulty=None,
                analysis=None,
                created_at=datetime(2026, 7, 5, 12, 0, 0),
            )
            mock_instance.create_job.return_value = mock_response
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/",
                json={
                    "title": "Python 实习生",
                    "company": "科脉技术",
                    "jd_text": "",
                    "source": "boss",
                    "source_url": "https://www.zhipin.com/job_detail/xxx.html",
                    "salary_min": 300,
                    "salary_max": 360,
                    "salary_unit": "元/天",
                    "location": "深圳·南山区·西丽",
                    "keywords": ["5天/周", "6个月", "本科"],
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["jd_text"] == ""
        assert data["salary_unit"] == "元/天"

    async def test_create_job_without_jd_text_field(
        self,
        client: AsyncClient,
    ) -> None:
        """海投模式：完全不传 jd_text 字段也允许创建（使用 default=""）"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.create_job.return_value = MOCK_JOB_RESPONSE
            MockService.return_value = mock_instance

            response = await client.post(
                "/api/jobs/",
                json={
                    "title": "Python 高级工程师",
                    "company": "字节跳动",
                    # 完全不传 jd_text
                    "source": "boss",
                },
            )

        assert response.status_code == 201


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


# ==================== Patch Job（海投模式补充详情）====================


class TestJobRouterPatch:
    """部分更新岗位端点测试

    覆盖海投模式核心场景：
    - 列表页创建空 jd_text 岗位后，用户点击卡片补充完整 JD
    - PATCH 语义：仅更新传入字段
    - 不存在返回 404
    - 非法字段被 extra=forbid 拒绝
    - 薪资范围校验
    """

    async def test_patch_job_supplement_jd_text(
        self,
        client: AsyncClient,
    ) -> None:
        """海投模式：PATCH 补充 jd_text + skills + location"""
        # 模拟更新后的响应：jd_text 已补充
        updated_response = JobResponse(
            id=SAMPLE_JOB_ID,
            title="Python 实习生",
            company="科脉技术",
            jd_text="完整 JD：负责 Python 后端开发...",
            source="boss",
            source_url="https://www.zhipin.com/job_detail/xxx.html",
            salary_min=300,
            salary_max=360,
            salary_unit="元/天",
            location="深圳·南山区·西丽",
            skills=["Python", "Pandas", "MySQL"],
            keywords=["5天/周", "6个月", "本科"],
            seniority=None,
            difficulty=None,
            analysis=None,
            created_at=datetime(2026, 7, 5, 12, 0, 0),
        )

        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.update_job.return_value = updated_response
            MockService.return_value = mock_instance

            response = await client.patch(
                f"/api/jobs/{SAMPLE_JOB_ID}",
                json={
                    "jd_text": "完整 JD：负责 Python 后端开发...",
                    "skills": ["Python", "Pandas", "MySQL"],
                    "location": "深圳·南山区·西丽",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["jd_text"] == "完整 JD：负责 Python 后端开发..."
        assert "Python" in data["skills"]
        # 验证 service 被正确调用
        mock_instance.update_job.assert_called_once()

    async def test_patch_job_not_found(
        self,
        client: AsyncClient,
    ) -> None:
        """更新不存在的岗位返回 404"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.update_job.side_effect = ResourceNotFoundError(
                detail=f"岗位 {SAMPLE_JOB_ID} 不存在"
            )
            MockService.return_value = mock_instance

            response = await client.patch(
                f"/api/jobs/{SAMPLE_JOB_ID}",
                json={"jd_text": "新 JD"},
            )

        assert response.status_code == 404

    async def test_patch_job_invalid_uuid(
        self,
        client: AsyncClient,
    ) -> None:
        """非法 UUID 返回 422"""
        response = await client.patch(
            "/api/jobs/not-a-uuid",
            json={"jd_text": "新 JD"},
        )

        assert response.status_code == 422

    async def test_patch_job_extra_field_rejected(
        self,
        client: AsyncClient,
    ) -> None:
        """extra=forbid：拒绝 DTO 未定义字段（防止越权更新 source / analysis_result）"""
        response = await client.patch(
            f"/api/jobs/{SAMPLE_JOB_ID}",
            json={
                "jd_text": "新 JD",
                "source": "liepin",  # 不允许更新 source
            },
        )

        assert response.status_code == 422

    async def test_patch_job_invalid_salary_range(
        self,
        client: AsyncClient,
    ) -> None:
        """salary_min > salary_max 返回 422"""
        response = await client.patch(
            f"/api/jobs/{SAMPLE_JOB_ID}",
            json={
                "salary_min": 100,
                "salary_max": 50,  # 下限 > 上限
            },
        )

        assert response.status_code == 422

    async def test_patch_job_invalid_seniority(
        self,
        client: AsyncClient,
    ) -> None:
        """非法 seniority 枚举值返回 422"""
        response = await client.patch(
            f"/api/jobs/{SAMPLE_JOB_ID}",
            json={"seniority": "invalid_seniority"},
        )

        assert response.status_code == 422

    async def test_patch_job_empty_body(
        self,
        client: AsyncClient,
    ) -> None:
        """空请求体：合法（不更新任何字段）

        场景：Extension 发送 PATCH 但 body 为空（详情面板尚未加载完）
        """
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.update_job.return_value = MOCK_JOB_RESPONSE
            MockService.return_value = mock_instance

            response = await client.patch(
                f"/api/jobs/{SAMPLE_JOB_ID}",
                json={},
            )

        assert response.status_code == 200
        mock_instance.update_job.assert_called_once()

    async def test_patch_job_partial_update(
        self,
        client: AsyncClient,
    ) -> None:
        """部分更新：只传 salary_unit，其他字段不传"""
        with patch("app.api.routers.jobs.JobService") as MockService:
            mock_instance = AsyncMock()
            mock_instance.update_job.return_value = MOCK_JOB_RESPONSE
            MockService.return_value = mock_instance

            response = await client.patch(
                f"/api/jobs/{SAMPLE_JOB_ID}",
                json={"salary_unit": "元/天"},
            )

        assert response.status_code == 200
        # 验证 service 收到的 req 只包含 salary_unit
        call_args = mock_instance.update_job.call_args
        # call_args = call(job_id, req)
        req_arg = call_args.args[1]
        assert req_arg.salary_unit == "元/天"
        # 其他字段应为 None（未传入）
        assert req_arg.jd_text is None
        assert req_arg.skills is None


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

    依赖覆盖已由模块级 _mock_app_dependencies fixture 统一处理，
    避免触碰真实 PG / Redis / MQ。
    """

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
