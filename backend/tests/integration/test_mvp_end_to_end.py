"""MVP 端到端集成测试

职责：
- 验证完整业务流程：注册/登录 → 上传简历 → 创建岗位 → JD 分析 → 匹配计算 → 沟通话术生成 → 创建投递记录
- 使用真实 PostgreSQL / Redis / RabbitMQ / LLM（由 conftest.py 恢复真实 .env 配置）
- 通过 ASGITransport + AsyncClient 启动完整 FastAPI 应用（包含 lifespan 和消费者）

设计动机：
- MVP 阶段需要一条自动化测试保证端到端可跑通，避免每次改代码后人工走完整流程
- 异步任务（JD 分析、沟通话术）通过轮询 /api/tasks/{task_id} 等待完成
- 测试数据隔离：使用唯一邮箱和 source_url，避免多次运行互相干扰
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import pytest
from asgi_lifespan import LifespanManager
from docx import Document
from httpx import ASGITransport, AsyncClient

from main import app


# ==================== 常量 ====================

# 测试账号信息：邮箱在测试函数内使用 uuid4 生成，确保每次运行都是独立用户
# 避免秒级时间戳在快速重跑时产生同一用户，导致 task (user_id, business_id) 唯一约束冲突
_TEST_EMAIL_DOMAIN = "test.com"
_TEST_PASSWORD = "MvpE2e#2024"

# 简历内容（会被写入 DOCX）
_RESUME_TEXT = """
张三
电话：138-0000-0000 | 邮箱：zhangsan@example.com

教育背景
北京大学 - 计算机科学与技术（本科） 2022.09 - 2026.06

技能
Python、FastAPI、LangChain、PostgreSQL、Redis、RabbitMQ、Docker、Git

项目经历
AI 求职助手
- 使用 FastAPI + PostgreSQL + Redis 构建后端 API
- 集成 LangChain 与 DeepSeek/Mimo 等大模型完成 JD 分析与话术生成
- 使用 RabbitMQ 异步处理耗时任务
"""

# Boss 直聘岗位 JD
_JOB_JD = """
【岗位】AI 应用开发工程师（实习）
【公司】未来科技有限公司
【地点】北京·海淀

岗位职责：
1. 参与 AI Agent 应用的后端开发，使用 Python + FastAPI；
2. 配合算法工程师完成 LLM 调用链路设计与优化；
3. 编写单元测试、接口文档，参与 Code Review。

任职要求：
1. 熟悉 Python 生态，有 FastAPI/Flask/Django 项目经验；
2. 了解 PostgreSQL、Redis 等常用中间件；
3. 对 LangChain、RAG、Agent 有一定了解者优先；
4. 实习 3 个月以上，每周至少 4 天。

薪资：200-250 元/天
"""


# ==================== Fixture ====================


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """创建连接到真实 ASGI 应用的 AsyncClient

    - httpx 0.28.1 的 ASGITransport 不自动触发 lifespan，
      必须使用 asgi-lifespan 的 LifespanManager 显式管理 startup/shutdown
    - lifespan 启动后会声明 RabbitMQ Exchange/Queue 并拉起消费者
    - 测试结束后 LifespanManager 自动发送 shutdown，释放资源
    """
    transport = ASGITransport(app=app)
    # follow_redirects=True：FastAPI 对末尾缺少 / 的路径会返回 307，
    # 浏览器/扩展会自动跟随，测试客户端也开启以简化请求书写
    async with LifespanManager(app):
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=True
        ) as c:
            yield c


# ==================== 辅助函数 ====================


def _build_docx_bytes(text: str) -> bytes:
    """构造一个合法 DOCX 文件字节流

    为什么用 DOCX：
    - 测试上传简历时需要通过 Magic Number 校验
    - python-docx 可快速生成有效 DOCX，无需引用外部文件
    """
    doc = Document()
    for line in text.strip().splitlines():
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


async def _poll_task(client: AsyncClient, token: str, task_id: str, timeout_seconds: float = 60.0) -> dict[str, Any]:
    """轮询任务状态直到完成或失败

    Args:
        client: HTTP 客户端
        token: access token
        task_id: 任务 UUID 字符串
        timeout_seconds: 最大等待时间

    Returns:
        最终任务详情

    Raises:
        TimeoutError: 超过最大等待时间仍未完成
    """
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"查询任务失败: {resp.text}"
        task = resp.json()
        if task["status"] in {"COMPLETED", "FAILED"}:
            return task
        await asyncio.sleep(1.0)

    raise TimeoutError(f"任务 {task_id} 在 {timeout_seconds}s 内未完成")


# ==================== 测试用例 ====================


@pytest.mark.asyncio
async def test_mvp_end_to_end_flow(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """MVP 端到端主流程

    步骤：
    1. 注册并获取 access token
    2. 上传简历（DOCX）
    3. 创建岗位（Boss 直聘来源）
    4. 触发 JD 分析并轮询到完成
    5. 计算简历-岗位匹配度
    6. 触发沟通话术生成并轮询到完成
    7. 创建投递记录
    8. 查询投递记录列表确认存在
    """
    user_id: str | None = None
    # 每次运行生成独立用户，避免 (user_id, business_id) 唯一约束冲突
    test_email = f"mvp_e2e_{uuid.uuid4().hex}@{_TEST_EMAIL_DOMAIN}"

    try:
        # ---- Step 1: 注册 ----
        register_resp = await client.post(
            "/api/auth/register",
            json={
                "email": test_email,
                "password": _TEST_PASSWORD,
                "password_confirm": _TEST_PASSWORD,
                "name": "MVP 测试用户",
                "target_position": "AI 应用开发工程师",
                "target_industry": "互联网",
            },
        )
        assert register_resp.status_code == 201, f"注册失败: {register_resp.text}"
        token_data = register_resp.json()
        access_token = token_data["access_token"]
        user_id = token_data["user"]["id"]
        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # ---- Step 2: 上传简历 ----
        resume_bytes = _build_docx_bytes(_RESUME_TEXT)
        resume_resp = await client.post(
            "/api/resumes/upload",
            headers=auth_headers,
            files={
                "file": ("test_resume.docx", io.BytesIO(resume_bytes), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            },
        )
        assert resume_resp.status_code == 201, f"简历上传失败: {resume_resp.text}"
        resume_id = resume_resp.json()["resume"]["id"]

        # ---- Step 3: 创建岗位 ----
        unique_source_url = f"https://www.zhipin.com/job/{uuid.uuid4().hex}.html"
        job_resp = await client.post(
            "/api/jobs",
            headers=auth_headers,
            json={
                "title": "AI 应用开发工程师（实习）",
                "company": "未来科技有限公司",
                "jd_text": _JOB_JD,
                "source": "boss",
                "source_url": unique_source_url,
                "salary_min": 200,
                "salary_max": 250,
                "location": "北京·海淀",
            },
        )
        assert job_resp.status_code == 201, f"岗位创建失败: {job_resp.text}"
        job_id = job_resp.json()["id"]

        # ---- Step 4: 触发 JD 分析 ----
        session_id = str(uuid.uuid4())
        analyze_resp = await client.post(
            "/api/jobs/analyze",
            headers=auth_headers,
            json={
                "job_id": job_id,
                "session_id": session_id,
            },
        )
        assert analyze_resp.status_code == 202, f"岗位分析触发失败: {analyze_resp.text}"
        analyze_data = analyze_resp.json()

        # 如果直接 completed（缓存或同步降级），则无需轮询
        if analyze_data["status"] == "pending":
            task = await _poll_task(client, access_token, str(analyze_data["task_id"]), timeout_seconds=90.0)
            assert task["status"] == "COMPLETED", f"岗位分析任务失败: {task.get('error_message')}"

        # 再次查询岗位，确认 analysis 已落库
        job_detail_resp = await client.get(
            f"/api/jobs/{job_id}",
            headers=auth_headers,
        )
        assert job_detail_resp.status_code == 200
        job_detail = job_detail_resp.json()
        assert job_detail["analysis"] is not None, "岗位分析结果未落库"
        assert len(job_detail["analysis"]["skills"]) > 0, "岗位分析未提取到 skills"

        # ---- Step 5: 匹配计算 ----
        match_resp = await client.post(
            "/api/match/compute",
            headers=auth_headers,
            json={
                "job_id": job_id,
                "resume_id": resume_id,
            },
        )
        assert match_resp.status_code == 200, f"匹配计算失败: {match_resp.text}"
        match_data = match_resp.json()
        assert match_data["score_detail"]["combined_score"] >= 0
        assert match_data["score_detail"]["combined_score"] <= 100
        match_score = match_data["score_detail"]["combined_score"]

        # ---- Step 6: 触发沟通话术生成 ----
        comm_session_id = str(uuid.uuid4())
        comm_resp = await client.post(
            "/api/communication/generate",
            headers=auth_headers,
            json={
                "job_id": job_id,
                "session_id": comm_session_id,
                "resume_id": resume_id,
                "tone": "natural",
            },
        )
        assert comm_resp.status_code == 202, f"沟通话术触发失败: {comm_resp.text}"
        comm_data = comm_resp.json()
        comm_task = await _poll_task(client, access_token, str(comm_data["task_id"]), timeout_seconds=90.0)
        assert comm_task["status"] == "COMPLETED", f"沟通话术任务失败: {comm_task.get('error_message')}"
        result = comm_task["result"]
        assert result["greeting"], "沟通话术 greeting 为空"
        assert result["follow_up"], "沟通话术 follow_up 为空"

        # ---- Step 7: 创建投递记录 ----
        app_resp = await client.post(
            "/api/applications",
            headers=auth_headers,
            json={
                "job_id": job_id,
                "match_score": match_score,
                "notes": "通过 MVP 端到端测试自动创建",
            },
        )
        assert app_resp.status_code == 201, f"投递记录创建失败: {app_resp.text}"
        application_id = app_resp.json()["id"]

        # ---- Step 8: 查询投递记录列表 ----
        list_resp = await client.get(
            "/api/applications",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200, f"投递记录列表查询失败: {list_resp.text}"
        list_data = list_resp.json()
        assert list_data["total"] >= 1
        assert any(item["id"] == application_id for item in list_data["items"])

    finally:
        # 清理测试用户：级联删除 resume / task / application / session
        # 岗位（jobs）本身无主外键，会残留，但 source_url 已用 uuid4 保证唯一，不影响后续运行
        if user_id is not None:
            try:
                from app.infra.repositories.user_repo import UserRepository

                repo = UserRepository(db_session)
                await repo.delete_by_id(uuid.UUID(user_id))
                await db_session.commit()
            except Exception as e:
                # 清理失败不应掩盖测试本身的断言失败
                import warnings

                warnings.warn(f"集成测试清理用户 {user_id} 失败: {e}")
