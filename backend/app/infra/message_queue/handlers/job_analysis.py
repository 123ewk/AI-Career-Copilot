"""Job Analysis Consumer

职责：
- 订阅 copilot.agent.job_analysis 队列
- 5 步流水线：
  1. mark_running: 标记任务为运行中
  2. agent.run: 调用 Job Analysis Agent 分析 JD
  3. 落库 + 缓存: 保存分析结果到 DB + 写入缓存
  4. mark_completed: 标记任务为完成
  5. publish event: 发布完成事件（可选，后续接入）

设计动机：
- 消费者是 Agent 的执行入口：API 层创建 Task + 发消息，Consumer 执行 Agent
- 每条消息独立 session：避免长事务和 session 泄漏
- Agent 失败时 mark_failed + 重试（由 consumer 基类的重试机制处理）
- Web 搜索降级：agent 内部已处理，不影响主流程

消息体格式：
{
    "task_id": "uuid-string",
    "job_id": "uuid-string",
    "user_id": "uuid-string"
}
"""

from __future__ import annotations

import uuid

from app.core.logger import logger
from app.domain.agent.job_analysis_agent import JobAnalysisAgent
from app.domain.job.models import JobAnalysisResult
from app.domain.task.service import TaskService
from app.infra.cache.job_analysis import RedisJobAnalysisCache
from app.infra.database.postgres import pg_session_factory
from app.infra.message_queue.exchanges import QUEUE_AGENT_JOB_ANALYSIS
from app.infra.message_queue.registry import register
from app.infra.repositories.job_repo import JobRepository
from app.runtime.state.agent_state import AgentState


@register(
    QUEUE_AGENT_JOB_ANALYSIS,
    prefetch_count=1,  # Agent 任务耗时长，并发度低
    max_retries=3,
    retry_base_delay_ms=10_000,  # 10s 基础延迟
)
async def handle_job_analysis(body: dict) -> None:
    """Job Analysis Consumer handler

    5 步流水线：
    1. mark_running
    2. agent.run
    3. 落库 + 缓存
    4. mark_completed
    5. publish event（TODO: 接入 MQ publisher）

    Args:
        body: 消息体，包含 task_id, job_id, user_id
    """
    task_id = uuid.UUID(body["task_id"])
    job_id = uuid.UUID(body["job_id"])
    user_id = uuid.UUID(body.get("user_id", "00000000-0000-0000-0000-000000000000"))

    logger.info(
        "Job Analysis Consumer 开始处理 | task_id={} | job_id={}",
        task_id,
        job_id,
    )

    async with pg_session_factory() as session:
        task_service = TaskService(session)
        job_repo = JobRepository(session)
        agent = JobAnalysisAgent()
        cache = RedisJobAnalysisCache()

        # ---- Step 1: mark_running ----
        try:
            await task_service.mark_running(task_id)
        except Exception as exc:
            logger.error(
                "Job Analysis Consumer: mark_running 失败 | task_id={} | exc={}",
                task_id,
                exc,
            )
            raise  # 让 consumer 基类处理重试

        # ---- Step 2: agent.run ----
        # 先查 job 获取 jd_text 和 company
        job = await job_repo.get_by_id(job_id)
        if job is None:
            error_msg = f"岗位 {job_id} 不存在"
            logger.error("Job Analysis Consumer: {}", error_msg)
            await task_service.mark_failed(task_id, error_message=error_msg)
            return  # 不重试，业务错误

        result = await agent.run(jd_text=job.jd_text, company=job.company)

        # ---- 检查 Agent 是否成功 ----
        if result.agent_state == AgentState.FAILED or result.analysis is None:
            error_msg = result.error or "Agent 执行失败"
            logger.error(
                "Job Analysis Consumer: Agent 失败 | task_id={} | error={}",
                task_id,
                error_msg,
            )
            await task_service.mark_failed(task_id, error_message=error_msg)
            return  # 不重试，Agent 内部错误

        analysis = result.analysis

        # ---- Step 3: 落库 + 缓存 ----
        try:
            await job_repo.update_analysis(
                job,
                analysis_result=analysis.model_dump(),
                skills=analysis.skills,
                keywords=analysis.keywords,
                seniority=analysis.seniority,
                difficulty=analysis.difficulty,
            )
            await session.commit()
        except Exception as exc:
            logger.error(
                "Job Analysis Consumer: 落库失败 | task_id={} | job_id={} | exc={}",
                task_id,
                job_id,
                exc,
            )
            await task_service.mark_failed(task_id, error_message=f"落库失败: {exc}")
            raise  # 让 consumer 基类处理重试

        # 写缓存（fail-open，不影响主流程）
        try:
            await cache.set(job_id, analysis)
        except Exception as exc:
            logger.warning(
                "Job Analysis Consumer: 缓存写入失败（降级） | job_id={} | exc={}",
                job_id,
                exc,
            )

        # ---- Step 4: mark_completed ----
        try:
            await task_service.mark_completed(
                task_id,
                result=analysis.model_dump(),
            )
        except Exception as exc:
            logger.error(
                "Job Analysis Consumer: mark_completed 失败 | task_id={} | exc={}",
                task_id,
                exc,
            )
            raise  # 让 consumer 基类处理重试

        # ---- Step 5: publish event（TODO: 接入 MQ publisher）----
        # 后续接入 MessagePublisher 发布 agent.event.completed 事件
        # await publisher.publish(
        #     exchange_name=EXCHANGE_AGENT,
        #     routing_key="agent.event.completed",
        #     payload={"task_id": str(task_id), "job_id": str(job_id), "result": analysis.model_dump()},
        # )

        logger.info(
            "Job Analysis Consumer 处理完成 | task_id={} | job_id={} | skills_count={} | difficulty={}",
            task_id,
            job_id,
            len(analysis.skills),
            analysis.difficulty,
        )
