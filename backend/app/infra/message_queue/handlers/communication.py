"""Communication Consumer

职责：
- 订阅 copilot.agent.communication 队列
- 处理沟通话术生成任务：
  1. mark_running
  2. 调用 CommunicationService.generate_script
  3. mark_completed（结果写入 task.result）
  4. 异常时 mark_failed

消息体格式：
{
    "task_id": "uuid-string",
    "job_id": "uuid-string",
    "user_id": "uuid-string",
    "resume_id": "uuid-string | null",
    "business_id": "communication:job-uuid:resume-uuid"
}
"""

from __future__ import annotations

import uuid

from app.core.exceptions import ResourceNotFoundError, TaskStateError
from app.core.logger import logger
from app.domain.communication.service import CommunicationService
from app.domain.task.service import TaskService
from app.infra.database.models.task import TaskStatus
from app.infra.database.postgres import pg_session_factory
from app.infra.message_queue.exchanges import QUEUE_AGENT_COMMUNICATION
from app.infra.message_queue.registry import register


@register(
    QUEUE_AGENT_COMMUNICATION,
    prefetch_count=1,
    max_retries=3,
    retry_base_delay_ms=10_000,
)
async def handle_communication(body: dict) -> None:
    """Communication Consumer handler

    Args:
        body: 消息体，包含 task_id, job_id, user_id, resume_id, business_id
    """
    task_id = uuid.UUID(body["task_id"])
    job_id = uuid.UUID(body["job_id"])
    user_id = uuid.UUID(body["user_id"])
    resume_id = uuid.UUID(body["resume_id"]) if body.get("resume_id") else None
    business_id = body.get("business_id", f"communication:job-{job_id}")

    mq_meta = body.get("__mq_meta__", {})
    retry_count = mq_meta.get("retry_count", 0)
    max_retries = mq_meta.get("max_retries", 3)
    is_last_retry = retry_count >= max_retries - 1

    logger.info(
        "Communication Consumer 开始处理 | task_id={} | job_id={} | user_id={} | retry={}/{}",
        task_id,
        job_id,
        user_id,
        retry_count,
        max_retries,
    )

    async with pg_session_factory() as session:
        task_service = TaskService(session)
        communication_service = CommunicationService(session)

        # ---- Step 1: 幂等启动 ----
        try:
            task = await task_service.get_task(task_id)
        except ResourceNotFoundError:
            logger.error(
                "Communication Consumer: 任务不存在 | task_id={}",
                task_id,
            )
            return

        if task.status == TaskStatus.COMPLETED:
            logger.info(
                "Communication Consumer: 任务已完成，跳过 | task_id={}",
                task_id,
            )
            return
        if task.status == TaskStatus.FAILED and retry_count > 0:
            logger.info(
                "Communication Consumer: 任务已失败且非首次，跳过 | task_id={}",
                task_id,
            )
            return

        if task.status == TaskStatus.PENDING:
            try:
                await task_service.mark_running(task_id)
            except TaskStateError as exc:
                logger.warning(
                    "Communication Consumer: mark_running 并发冲突，继续执行 | task_id={} | exc={}",
                    task_id,
                    exc,
                )
            except Exception as exc:
                logger.error(
                    "Communication Consumer: mark_running 失败 | task_id={} | exc={}",
                    task_id,
                    exc,
                )
                raise

        # ---- Step 2: 生成话术 ----
        try:
            script = await communication_service.generate_script(
                user_id=user_id,
                job_id=job_id,
                resume_id=resume_id,
            )
        except ResourceNotFoundError:
            error_msg = f"岗位 {job_id} 或简历不存在"
            logger.error("Communication Consumer: {}", error_msg)
            await task_service.mark_failed(task_id, error_message=error_msg)
            return
        except Exception as exc:
            logger.error(
                "Communication Consumer: 生成话术失败 | task_id={} | job_id={} | retry={} | exc={}",
                task_id,
                job_id,
                retry_count,
                exc,
            )
            if is_last_retry:
                await task_service.mark_failed(
                    task_id, error_message=f"生成话术失败: {exc}"
                )
            raise

        # ---- Step 3: mark_completed ----
        # mode="json"：将 UUID/datetime 等不可 JSON 序列化的类型转为字符串，
        # 避免 PostgreSQL JSONB 写入时抛 "Object of type UUID is not JSON serializable"
        try:
            await task_service.mark_completed(
                task_id,
                result=script.model_dump(mode="json"),
            )
        except Exception as exc:
            logger.error(
                "Communication Consumer: mark_completed 失败 | task_id={} | exc={}",
                task_id,
                exc,
            )
            if is_last_retry:
                await task_service.mark_failed(
                    task_id, error_message=f"标记完成失败: {exc}"
                )
            raise

        logger.info(
            "Communication Consumer 处理完成 | task_id={} | job_id={} | user_id={}",
            task_id,
            job_id,
            user_id,
        )
