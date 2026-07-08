"""Application Service

职责：
- 投递记录业务逻辑：创建、列表、详情、状态更新
- 用户点击「记录投递」时创建 Application，状态 APPLIED，并设置 applied_at
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, ResourceNotFoundError
from app.core.logger import logger
from app.domain.application.models import (
    ApplicationCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdateRequest,
)
from app.infra.database.models.application import ApplicationStatus
from app.infra.repositories.application_repo import ApplicationRepository


class ApplicationService:
    """投递记录 Service"""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._repo = ApplicationRepository(session)

    async def create_application(
        self,
        user_id: uuid.UUID,
        request: ApplicationCreateRequest,
    ) -> ApplicationResponse:
        """创建投递记录

        用户点击「记录投递」时调用，状态默认为 APPLIED，并设置 applied_at。
        """
        logger.info(
            "创建投递记录 | user_id={} | job_id={}",
            user_id,
            request.job_id,
        )

        try:
            application = await self._repo.create(
                user_id=user_id,
                job_id=request.job_id,
                status=ApplicationStatus.APPLIED,
                match_score=request.match_score,
                applied_at=datetime.now(timezone.utc),
                notes=request.notes,
            )
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            logger.warning(
                "投递记录已存在 | user_id={} | job_id={} | exc={}",
                user_id,
                request.job_id,
                exc,
            )
            raise ConflictError(
                detail="您已投递过该岗位",
                error_code="APP_002",
                extra={"job_id": str(request.job_id)},
            ) from exc

        logger.info(
            "投递记录创建成功 | application_id={} | user_id={} | job_id={}",
            application.id,
            user_id,
            request.job_id,
        )
        return ApplicationResponse.model_validate(application)

    async def list_applications(
        self,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> ApplicationListResponse:
        """分页查询用户的投递记录"""
        applications = await self._repo.list_by_user(
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        total = await self._repo.count_by_user(user_id=user_id)

        return ApplicationListResponse(
            items=[ApplicationResponse.model_validate(app) for app in applications],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_application(
        self,
        application_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ApplicationResponse:
        """查询投递记录详情"""
        application = await self._repo.get_by_id(application_id)
        if application is None or application.user_id != user_id:
            raise ResourceNotFoundError(
                detail="投递记录不存在",
                error_code="APP_001",
            )
        return ApplicationResponse.model_validate(application)

    async def update_application(
        self,
        application_id: uuid.UUID,
        user_id: uuid.UUID,
        request: ApplicationUpdateRequest,
    ) -> ApplicationResponse:
        """更新投递记录状态和备注"""
        application = await self._repo.get_by_id(application_id)
        if application is None or application.user_id != user_id:
            raise ResourceNotFoundError(
                detail="投递记录不存在",
                error_code="APP_001",
            )

        application = await self._repo.update_status(
            application=application,
            status=request.status,
            notes=request.notes,
        )
        await self._session.commit()

        logger.info(
            "投递记录更新成功 | application_id={} | status={}",
            application.id,
            request.status.value,
        )
        return ApplicationResponse.model_validate(application)


__all__ = ["ApplicationService"]
