"""Task Domain Service

职责：
- 编排 Task 域的业务逻辑：创建、状态更新、查询
- 协调 TaskRepository 完成数据持久化
- 事务控制：commit/rollback 由本层管理

设计动机：
- 与 JobService / ResumeService 保持一致的分层模式
- Task 是异步任务的最小单元，由 API 层创建，由 Consumer 驱动状态流转
- 状态更新方法（mark_*）供 Consumer 调用，查询方法供 API 层调用

任务生命周期：
    PENDING → RUNNING → COMPLETED
                  └────→ FAILED

潜在风险：
- 并发更新：同一 Task 被多个 Consumer 同时处理
  → 防御：(user_id, business_id) 联合唯一索引 + MQ 单消费者模式
"""

from __future__ import annotations

import uuid

from app.core.exceptions import ResourceNotFoundError
from app.core.logger import logger
from app.domain.repositories.task import TaskRepositoryProtocol
from app.infra.database.models.task import Task, TaskStatus


class TaskService:
    """Task 域服务

    用法：
        async with pg_session_factory() as session:
            service = TaskService(session)
            task = await service.create_task(
                user_id=user_id,
                session_id=session_id,
                task_type="analyze_jd",
                business_id=f"analyze_jd:{job_id}",
                input_data={"job_id": str(job_id)},
            )
            await session.commit()

            # Consumer 调用：
            await service.mark_running(task.id)
            # ... 执行 Agent ...
            await service.mark_completed(task.id, result=agent_result)
    """

    def __init__(
        self,
        session,
        repo: TaskRepositoryProtocol | None = None,
    ) -> None:
        """初始化

        Args:
            session: AsyncSession 实例
            repo: Task 仓储实现。None 时使用默认 TaskRepository
        """
        self._session = session
        if repo is None:
            # 延迟导入具体实现，避免 Domain 模块顶层依赖 Infra
            from app.infra.repositories.task_repo import TaskRepository

            repo = TaskRepository(session)
        self._repo: TaskRepositoryProtocol = repo

    async def create_task(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        task_type: str,
        business_id: str,
        input_data: dict | list | None = None,
    ) -> Task:
        """创建异步任务

        行为：
- 创建 Task 记录（status=PENDING）
- 提交事务

        Args:
            user_id: 所属用户 UUID
            session_id: 所属会话 UUID
            task_type: 任务类型（如 analyze_jd / match_resume）
            business_id: 业务 ID（MQ 幂等键，如 "analyze_jd:job-uuid"）
            input_data: 任务输入参数（JSONB）

        Returns:
            新创建的 Task 实例
        """
        logger.info(
            "创建任务 | task_type={} | business_id={}",
            task_type,
            business_id,
        )

        task = await self._repo.create(
            user_id=user_id,
            session_id=session_id,
            task_type=task_type,
            business_id=business_id,
            input_data=input_data,
        )
        await self._session.commit()

        logger.info("任务创建成功 | task_id={}", task.id)
        return task

    async def mark_running(self, task_id: uuid.UUID) -> Task:
        """标记任务为运行中

        Args:
            task_id: 任务 UUID

        Returns:
            更新后的 Task 实例

        Raises:
            ResourceNotFoundError: 任务不存在
        """
        task = await self._get_task_or_raise(task_id)
        task = await self._repo.mark_running(task)
        await self._session.commit()

        logger.info("任务标记为运行中 | task_id={}", task_id)
        return task

    async def mark_completed(
        self,
        task_id: uuid.UUID,
        *,
        result: dict | list | None = None,
    ) -> Task:
        """标记任务为完成

        Args:
            task_id: 任务 UUID
            result: 任务执行结果（JSONB）

        Returns:
            更新后的 Task 实例

        Raises:
            ResourceNotFoundError: 任务不存在
        """
        task = await self._get_task_or_raise(task_id)
        task = await self._repo.mark_completed(task, result=result)
        await self._session.commit()

        logger.info("任务标记为完成 | task_id={}", task_id)
        return task

    async def mark_failed(
        self,
        task_id: uuid.UUID,
        *,
        error_message: str,
    ) -> Task:
        """标记任务为失败

        Args:
            task_id: 任务 UUID
            error_message: 错误信息

        Returns:
            更新后的 Task 实例

        Raises:
            ResourceNotFoundError: 任务不存在
        """
        task = await self._get_task_or_raise(task_id)
        task = await self._repo.mark_failed(task, error_message=error_message)
        await self._session.commit()

        logger.info("任务标记为失败 | task_id={} | error={}", task_id, error_message)
        return task

    async def get_task(self, task_id: uuid.UUID) -> Task:
        """查询任务详情

        Args:
            task_id: 任务 UUID

        Returns:
            Task 实例

        Raises:
            ResourceNotFoundError: 任务不存在
        """
        return await self._get_task_or_raise(task_id)

    async def list_tasks(
        self,
        *,
        user_id: uuid.UUID,
        status: TaskStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """分页查询任务列表

        Args:
            user_id: 用户 UUID
            status: 可选状态过滤
            limit: 每页大小
            offset: 偏移量

        Returns:
            dict: items + total + limit + offset
        """
        tasks = await self._repo.list_by_user(
            user_id, status=status, limit=limit, offset=offset,
        )
        total = await self._repo.count_by_user(user_id, status=status)

        return {
            "items": tasks,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def _get_task_or_raise(self, task_id: uuid.UUID) -> Task:
        """查询任务，不存在则抛异常"""
        task = await self._repo.get_by_id(task_id)
        if task is None:
            raise ResourceNotFoundError(
                detail=f"任务 {task_id} 不存在",
                extra={"task_id": str(task_id)},
            )
        return task
