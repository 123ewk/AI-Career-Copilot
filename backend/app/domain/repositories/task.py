"""Task 仓储抽象接口（Domain 层）

职责：
- 定义 Task 仓储的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/repositories/task_repo.py 中的 TaskRepository 实现
- Domain Service / UseCase 仅依赖本 Protocol，便于替换 ORM 或测试时 mock

设计动机：
- 依赖倒置：业务层（domain）不依赖基础设施层（infra）的具体实现
- 易于测试：单元测试可以传一个 FakeTaskRepository 实现 Protocol
- Protocol vs ABC：选 Protocol（结构化子类型），duck typing，不强制继承
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.infra.database.models.task import Task, TaskStatus


@runtime_checkable
class TaskRepositoryProtocol(Protocol):
    """Task 仓储接口

    所有方法均为 async：调用方必须 await
    不调用 commit/rollback：让 Service / Router 控制事务边界
    异常透传：IntegrityError / OperationalError 等由调用方 / 中间件统一处理
    """

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

        Args:
            user_id: 所属用户 UUID
            session_id: 所属会话 UUID
            business_id: 业务 ID（MQ 幂等键）
            task_type: 任务类型（如 analyze_jd / match_resume）
            input_data: 任务输入参数（JSONB）

        Returns:
            新创建的 Task ORM 实例（已 flush）

        Raises:
            IntegrityError: (user_id, business_id) 重复时触发唯一索引冲突
        """
        ...

    async def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        """按主键查询任务，未找到返回 None"""
        ...

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: TaskStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Task]:
        """分页查询指定用户的任务列表

        Args:
            user_id: 用户 UUID
            status: 可选状态过滤
            limit: 每页大小
            offset: 偏移量

        Returns:
            Task 序列，按 created_at 倒序
        """
        ...

    async def count_by_user(
        self,
        user_id: uuid.UUID,
        *,
        status: TaskStatus | None = None,
    ) -> int:
        """统计指定用户的任务总数

        Args:
            user_id: 用户 UUID
            status: 可选状态过滤

        Returns:
            任务总数
        """
        ...

    async def mark_running(self, task: Task) -> Task:
        """标记任务为 RUNNING

        Args:
            task: 已加载的 Task 实例

        Returns:
            更新后的 Task 实例
        """
        ...

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
        ...

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
        ...


__all__ = ["TaskRepositoryProtocol"]
