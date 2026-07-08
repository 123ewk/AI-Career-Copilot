"""Application Repository（异步 PostgreSQL 仓储）

职责：
- 封装 applications 表的 CRUD 操作
- 仅做数据访问，不做业务校验
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.database.models.application import Application, ApplicationStatus


class ApplicationRepository:
    """Application 仓储"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        application = Application(
            user_id=user_id,
            job_id=job_id,
            status=status,
            match_score=match_score,
            applied_at=applied_at,
            status_updated_at=datetime.now(timezone.utc),
            notes=notes,
        )
        self._session.add(application)
        await self._session.flush()
        return application

    async def get_by_id(self, application_id: uuid.UUID) -> Application | None:
        """按主键查询投递记录"""
        result = await self._session.execute(
            select(Application).where(Application.id == application_id)
        )
        return result.scalar_one_or_none()

    async def get_by_user_and_job(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> Application | None:
        """查询指定用户对指定岗位的投递记录"""
        result = await self._session.execute(
            select(Application).where(
                Application.user_id == user_id,
                Application.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Application]:
        """分页查询指定用户的投递记录"""
        result = await self._session.execute(
            select(Application)
            .where(Application.user_id == user_id)
            .order_by(Application.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """统计指定用户的投递记录总数"""
        result = await self._session.execute(
            select(func.count(Application.id)).where(Application.user_id == user_id)
        )
        return result.scalar_one()

    async def update_status(
        self,
        application: Application,
        *,
        status: ApplicationStatus,
        notes: str | None = None,
    ) -> Application:
        """更新投递状态和备注"""
        application.status = status
        application.status_updated_at = datetime.now(timezone.utc)
        if notes is not None:
            application.notes = notes
        self._session.add(application)
        await self._session.flush()
        return application


__all__ = ["ApplicationRepository"]
