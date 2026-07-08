"""Application 仓储抽象接口（Domain 层）

职责：
- 定义 Application 仓储的契约（Protocol）
- 由 infra/repositories/application_repo.py 中的 ApplicationRepository 实现
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.infra.database.models.application import Application, ApplicationStatus


@runtime_checkable
class ApplicationRepositoryProtocol(Protocol):
    """Application 仓储接口"""

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        status: ApplicationStatus = ApplicationStatus.APPLIED,
        match_score: float | None = None,
        applied_at: datetime | None = None,
        notes: str | None = None,
    ) -> Application:
        """创建投递记录"""
        ...

    async def get_by_id(self, application_id: uuid.UUID) -> Application | None:
        """按主键查询投递记录"""
        ...

    async def get_by_user_and_job(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> Application | None:
        """查询指定用户对指定岗位的投递记录"""
        ...

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Application]:
        """分页查询指定用户的投递记录"""
        ...

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """统计指定用户的投递记录总数"""
        ...

    async def update_status(
        self,
        application: Application,
        *,
        status: ApplicationStatus,
        notes: str | None = None,
    ) -> Application:
        """更新投递状态和备注"""
        ...


__all__ = ["ApplicationRepositoryProtocol"]
