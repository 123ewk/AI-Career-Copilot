"""Job Analysis Consumer

职责：
- 订阅 copilot.agent.job_analysis 队列
- 5 步流水线：
  1. mark_running: 标记任务为运行中
  2. run_analysis: 调用 JobService 执行分析（落库 + 缓存）
  3. mark_completed: 标记任务为完成
  4. publish event: 发布 agent.event.completed 事件
  5. 异常处理：失败时 mark_failed，由 consumer 基类重试

设计动机：
- 消费者是 Agent 的执行入口：API 层创建 Task + 发消息，Consumer 执行 Agent
- 每条消息独立 session：避免长事务和 session 泄漏
- 复用 JobService.run_analysis：保证 DB 更新、缓存回填逻辑与同步降级路径一致
- Agent 失败时 mark_failed + 重试（由 consumer 基类的重试机制处理）
- Web 搜索降级：agent 内部已处理，不影响主流程

消息体格式：
{
    "task_id": "uuid-string",
    "job_id": "uuid-string",
    "business_id": "analyze_jd:uuid-string"
}
"""

from __future__ import annotations

import uuid

from app.core.exceptions import ResourceNotFoundError, TaskStateError
from app.core.logger import logger
from app.domain.job.service import JobService
from app.domain.task.service import TaskService
from app.infra.cache.job_analysis import RedisJobAnalysisCache
from app.infra.database.models.task import TaskStatus
from app.infra.database.postgres import pg_session_factory
from app.infra.message_queue import MessagePublisher
from app.infra.message_queue.connection import rabbitmq_connection_factory
from app.infra.message_queue.exchanges import (
    EXCHANGE_AGENT,
    QUEUE_AGENT_JOB_ANALYSIS,
    ROUTING_AGENT_EVENT_COMPLETED,
)
from app.infra.message_queue.registry import register


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
    2. run_analysis（复用 JobService：落库 + 缓存）
    3. mark_completed
    4. publish agent.event.completed 事件
    5. 异常处理

    Args:
        body: 消息体，包含 task_id, job_id, business_id 及 __mq_meta__ 元数据
    """
    task_id = uuid.UUID(body["task_id"])
    job_id = uuid.UUID(body["job_id"])
    business_id = body.get("business_id", f"analyze_jd:{job_id}")

    # MQ 元数据：consumer 基类注入，用于判断当前是第几次重试
    mq_meta = body.get("__mq_meta__", {})
    retry_count = mq_meta.get("retry_count", 0)
    max_retries = mq_meta.get("max_retries", 3)
    is_last_retry = retry_count >= max_retries - 1

    logger.info(
        "Job Analysis Consumer 开始处理 | task_id={} | job_id={} | business_id={} | retry={}/{}",
        task_id,
        job_id,
        business_id,
        retry_count,
        max_retries,
    )

    async with pg_session_factory() as session:
        task_service = TaskService(session)
        cache = RedisJobAnalysisCache()
        # JobService 用于复用分析逻辑；不注入 publisher/cache 是为了避免 Consumer 内部再发 MQ
        job_service = JobService(session, cache=cache)

        # ---- Step 1: 幂等启动（PENDING 才 mark_running，RUNNING 跳过） ----
        try:
            task = await task_service.get_task(task_id)
        except ResourceNotFoundError:
            logger.error(
                "Job Analysis Consumer: 任务不存在 | task_id={}",
                task_id,
            )
            return  # 不重试

        if task.status == TaskStatus.COMPLETED:
            logger.info(
                "Job Analysis Consumer: 任务已完成，跳过 | task_id={}",
                task_id,
            )
            return
        if task.status == TaskStatus.FAILED and retry_count > 0:
            logger.info(
                "Job Analysis Consumer: 任务已失败且非首次，跳过 | task_id={}",
                task_id,
            )
            return

        if task.status == TaskStatus.PENDING:
            try:
                await task_service.mark_running(task_id)
            except TaskStateError as exc:
                # 并发下可能已被其他 consumer 标记为 RUNNING，跳过即可
                logger.warning(
                    "Job Analysis Consumer: mark_running 并发冲突，继续执行 | task_id={} | exc={}",
                    task_id,
                    exc,
                )
            except Exception as exc:
                logger.error(
                    "Job Analysis Consumer: mark_running 失败 | task_id={} | exc={}",
                    task_id,
                    exc,
                )
                raise

        # ---- Step 2: run_analysis ----
        try:
            job = await job_service.get_job(job_id)
            analysis = await job_service.run_analysis(
                job_id, job.jd_text, company=job.company
            )
        except ResourceNotFoundError:
            error_msg = f"岗位 {job_id} 不存在"
            logger.error("Job Analysis Consumer: {}", error_msg)
            await task_service.mark_failed(task_id, error_message=error_msg)
            return  # 不重试，业务错误
        except Exception as exc:
            logger.error(
                "Job Analysis Consumer: 分析执行失败 | task_id={} | job_id={} | retry={} | exc={}",
                task_id,
                job_id,
                retry_count,
                exc,
            )
            if is_last_retry:
                await task_service.mark_failed(
                    task_id, error_message=f"分析执行失败: {exc}"
                )
            raise  # 非末次重试时让 consumer 基类继续重试

        # ---- Step 3: mark_completed ----
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
            if is_last_retry:
                await task_service.mark_failed(
                    task_id, error_message=f"标记完成失败: {exc}"
                )
            raise

        # ---- Step 4: publish event ----
        try:
            channel = await rabbitmq_connection_factory.get_channel()
            publisher = MessagePublisher(channel)
            await publisher.publish(
                exchange_name=EXCHANGE_AGENT,
                routing_key=ROUTING_AGENT_EVENT_COMPLETED,
                payload={
                    "task_id": str(task_id),
                    "job_id": str(job_id),
                    "business_id": business_id,
                    "result": analysis.model_dump(),
                },
                # message_id 复用业务幂等键 business_id，与任务创建侧保持一致
                message_id=business_id,
            )
            await channel.close()
        except Exception as exc:
            # 事件发布失败不影响主流程：任务已完成，后续可人工补偿
            logger.warning(
                "Job Analysis Consumer: 发布完成事件失败（不影响主流程） | task_id={} | exc={}",
                task_id,
                exc,
            )

        logger.info(
            "Job Analysis Consumer 处理完成 | task_id={} | job_id={} | skills_count={} | difficulty={}",
            task_id,
            job_id,
            len(analysis.skills),
            analysis.difficulty,
        )
