"""Task Repository（异步 PostgreSQL 仓储）

职责：
- 封装 tasks 表的 CRUD 操作，对 Domain Service 层提供统一的数据访问入口
- 仅做数据访问，不做业务校验、不抛业务异常
- 不自动 commit：事务边界由 Service 层显式控制

实现契约：
- 实现 domain/repositories/task.py 中的 TaskRepositoryProtocol
- 与 JobRepository / ResumeRepository 保持一致的设计模式

设计动机：
- Repository 模式隔离 ORM 细节
- 状态更新方法（mark_running/completed/failed）直接赋值 ORM 属性
- 分页查询走 ix_tasks_created_at 索引
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.task import TaskRepositoryProtocol
from app.infra.database.models.task import Task, TaskStatus


class TaskRepository:
    """Task 仓储

    使用方式：
        session = pg_session_factory.create_session()
        repo = TaskRepository(session)
        task = await repo.create(user_id=..., session_id=..., ...)
        await session.commit()

    设计原则：
- 构造时注入 AsyncSession，单次请求共用同一个 session
- 所有方法均为 async，调用方必须 await
- 不调用 commit/rollback：让 Service 层控制事务边界
- 异常透传：IntegrityError / OperationalError 等由中间件统一处理
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ==================== Create ====================

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        business_id: str,
        task_type: str,
        input_data: dict | list | None = None,
    ) -> Task:
        """创建任务记录

        行为：
        - 主键 id 由 ORM default=uuid.uuid4 + 数据库 server_default=gen_random_uuid() 兜底
        - status 默认 PENDING（ORM default + server_default 双保险）
        - created_at 由数据库 server_default=now() 自动填充
        - 调用 session.flush() 而非 commit：让 Service 层控制事务边界

        Args:
            user_id: 所属用户 UUID
            session_id: 所属会话 UUID
            business_id: 业务 ID（MQ 幂等键）
            task_type: 任务类型
            input_data: 任务输入参数（JSONB）

        Returns:
            新创建的 Task 实例（已 flush）

        Raises:
            IntegrityError: (user_id, business_id) 重复时触发唯一索引冲突
        """
        task = Task(
            id=uuid.uuid4(),
            user_id=user_id,
            session_id=session_id,
            business_id=business_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            input_data=input_data,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    # ==================== Read ====================

    async def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        """按主键查询任务

        使用 Session.get() 优先从 identity map 取，避免重复查询。
        """
        return await self._session.get(Task, task_id)

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: TaskStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Task]:
        """分页查询指定用户的任务列表

        默认按 created_at 倒序（最新创建的在前），走 ix_tasks_created_at 索引。
        可选按 status 过滤，走 ix_tasks_status 索引。
        """
        if limit <= 0:
            return []
        stmt = (
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
        if status is not None:
            stmt = stmt.where(Task.status == status)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: TaskStatus | None = None,
    ) -> int:
        """统计指定用户的任务总数"""
        stmt = select(func.count(Task.id)).where(Task.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    # ==================== Update ====================

    async def mark_running(self, task: Task) -> Task:
        """标记任务为 RUNNING

        Args:
            task: 已加载的 Task 实例

        Returns:
            更新后的 Task 实例
        """
        task.status = TaskStatus.RUNNING
        await self._session.flush()
        return task

    async def mark_completed(
        self,
        task: Task,
        *,
        result: dict | list | None = None,
    ) -> Task:
        """标记任务为 COMPLETED

        Args:
            task: 已加载的 Task 实例
            result: 任务执行结果（JSONB）

        Returns:
            更新后的 Task 实例
        """
        task.status = TaskStatus.COMPLETED
        task.result = result
        await self._session.flush()
        return task

    async def mark_failed(
        self,
        task: Task,
        *,
        error_message: str,
    ) -> Task:
        """标记任务为 FAILED

        Args:
            task: 已加载的 Task 实例
            error_message: 错误信息

        Returns:
            更新后的 Task 实例
        """
        task.status = TaskStatus.FAILED
        task.error_message = error_message
        await self._session.flush()
        return task


__all__ = ["TaskRepository"]
