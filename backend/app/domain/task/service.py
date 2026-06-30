"""Task Domain Service

职责：
- 编排 Task 域的业务逻辑：创建、状态更新、查询
- 协调 TaskRepository 完成数据持久化
- 事务控制：commit/rollback 由本层管理
- 状态机守卫：防止非法状态转换
- 幂等创建：使用 insert_idempotent 配合 (user_id, business_id) 唯一索引

设计动机：
- 与 JobService / ResumeService 保持一致的分层模式
- Task 是异步任务的最小单元，由 API 层创建，由 Consumer 驱动状态流转
- 状态更新方法（mark_*）供 Consumer 调用，查询方法供 API 层调用

任务生命周期：
    PENDING → RUNNING → COMPLETED
              │  └────→ FAILED
              └──────→ FAILED（未开始即失败：MQ 投递失败 / 参数校验失败）
    PENDING ──────→ CANCELLED

潜在风险：
- 并发更新：同一 Task 被多个 Consumer 同时处理
  → 防御：(user_id, business_id) 联合唯一索引 + MQ 单消费者模式
- 状态机非法转换：Consumer 异常重试时可能重复 mark_completed
  → 防御：mark_* 方法前置状态校验，非法转换抛 TaskStateError
- 重复创建：MQ 重投或网络重试导致同一业务 ID 多次 create_task
  → 防御：insert_idempotent 捕获 IntegrityError → DuplicateMessageError
"""

from __future__ import annotations

import uuid

from app.core.exceptions import (
    ConflictError,
    DuplicateMessageError,
    ResourceNotFoundError,
    TaskStateError,
)
from app.core.logger import logger
from app.domain.common.idempotent import insert_idempotent
from app.domain.repositories.task import TaskRepositoryProtocol
from app.domain.task.dto import TaskDTO, TaskListResponse
from app.infra.database.models.task import Task, TaskStatus

# 合法状态转换图：键为当前状态，值为允许的目标状态
# PENDING 允许直接转 FAILED：覆盖「MQ 投递失败 / Publisher 未注入 / 同步降级失败」等
# 任务尚未真正开始执行即失败的场景，避免 JobService 调用 mark_failed 时抛 TaskStateError
_VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


def _validate_status_transition(current: TaskStatus, target: TaskStatus) -> None:
    """校验任务状态转换是否合法

    Args:
        current: 当前状态
        target: 目标状态

    Raises:
        TaskStateError: 非法转换
    """
    if target not in _VALID_TRANSITIONS.get(current, set()):
        raise TaskStateError(
            detail=f"任务状态转换非法：{current.value} → {target.value}",
            extra={"current": current.value, "target": target.value},
        )


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
    ) -> TaskDTO:
        """创建异步任务（幂等）

        行为：
- 使用 insert_idempotent 写入 Task 记录，依赖 (user_id, business_id) 唯一索引防重
- 重复时抛 ConflictError（API 层应返回 409）
- 提交事务

        Args:
            user_id: 所属用户 UUID
            session_id: 所属会话 UUID
            task_type: 任务类型（如 analyze_jd / match_resume）
            business_id: 业务 ID（MQ 幂等键，如 "analyze_jd:job-uuid"）
            input_data: 任务输入参数（JSONB）

        Returns:
            TaskDTO

        Raises:
            ConflictError: (user_id, business_id) 已存在
        """
        logger.info(
            "创建任务 | task_type={} | business_id={}",
            task_type,
            business_id,
        )

        try:
            task: Task = await insert_idempotent(
                self._session,
                Task,
                id=uuid.uuid4(),
                user_id=user_id,
                session_id=session_id,
                business_id=business_id,
                task_type=task_type,
                status=TaskStatus.PENDING,
                input_data=input_data,
            )
        except DuplicateMessageError as e:
            # API 层重复提交属于业务冲突，转换为 HTTP 友好的 ConflictError
            raise ConflictError(
                detail=f"任务已存在：{business_id}",
                error_code="TASK_002",
                extra={"business_id": business_id},
            ) from e

        await self._session.commit()

        logger.info("任务创建成功 | task_id={} | business_id={}", task.id, business_id)
        return TaskDTO.model_validate(task)

    async def mark_running(self, task_id: uuid.UUID) -> TaskDTO:
        """标记任务为运行中

        Args:
            task_id: 任务 UUID

        Returns:
            TaskDTO

        Raises:
            ResourceNotFoundError: 任务不存在
            TaskStateError: 当前状态不是 PENDING
        """
        task = await self._get_task_or_raise(task_id)
        _validate_status_transition(task.status, TaskStatus.RUNNING)
        task = await self._repo.mark_running(task)
        await self._session.commit()

        logger.info("任务标记为运行中 | task_id={}", task_id)
        return TaskDTO.model_validate(task)

    async def mark_completed(
        self,
        task_id: uuid.UUID,
        *,
        result: dict | list | None = None,
    ) -> TaskDTO:
        """标记任务为完成

        Args:
            task_id: 任务 UUID
            result: 任务执行结果（JSONB）

        Returns:
            TaskDTO

        Raises:
            ResourceNotFoundError: 任务不存在
            TaskStateError: 当前状态不是 RUNNING
        """
        task = await self._get_task_or_raise(task_id)
        _validate_status_transition(task.status, TaskStatus.COMPLETED)
        task = await self._repo.mark_completed(task, result=result)
        await self._session.commit()

        logger.info("任务标记为完成 | task_id={}", task_id)
        return TaskDTO.model_validate(task)

    async def mark_failed(
        self,
        task_id: uuid.UUID,
        *,
        error_message: str,
    ) -> TaskDTO:
        """标记任务为失败

        允许的来源状态：PENDING / RUNNING
        - PENDING → FAILED：任务尚未开始即失败（MQ 投递失败 / Publisher 未注入 / 同步降级失败）
        - RUNNING → FAILED：任务执行过程中失败（Agent 异常 / 超时）

        Args:
            task_id: 任务 UUID
            error_message: 错误信息

        Returns:
            TaskDTO

        Raises:
            ResourceNotFoundError: 任务不存在
            TaskStateError: 当前状态为 COMPLETED / FAILED / CANCELLED
        """
        task = await self._get_task_or_raise(task_id)
        _validate_status_transition(task.status, TaskStatus.FAILED)
        task = await self._repo.mark_failed(task, error_message=error_message)
        await self._session.commit()

        logger.info("任务标记为失败 | task_id={} | error={}", task_id, error_message)
        return TaskDTO.model_validate(task)

    async def get_task(self, task_id: uuid.UUID) -> TaskDTO:
        """查询任务详情

        Args:
            task_id: 任务 UUID

        Returns:
            TaskDTO

        Raises:
            ResourceNotFoundError: 任务不存在
        """
        task = await self._get_task_or_raise(task_id)
        return TaskDTO.model_validate(task)

    async def list_tasks(
        self,
        *,
        user_id: uuid.UUID,
        status: TaskStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TaskListResponse:
        """分页查询任务列表

        Args:
            user_id: 用户 UUID
            status: 可选状态过滤
            limit: 每页大小
            offset: 偏移量

        Returns:
            TaskListResponse
        """
        tasks = await self._repo.list_by_user(
            user_id, status=status, limit=limit, offset=offset,
        )
        total = await self._repo.count_by_user(user_id, status=status)

        return TaskListResponse(
            items=[TaskDTO.model_validate(t) for t in tasks],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def _get_task_or_raise(self, task_id: uuid.UUID) -> Task:
        """查询任务，不存在则抛异常"""
        task = await self._repo.get_by_id(task_id)
        if task is None:
            raise ResourceNotFoundError(
                detail=f"任务 {task_id} 不存在",
                extra={"task_id": str(task_id)},
            )
        return task


__all__ = ["TaskService"]
