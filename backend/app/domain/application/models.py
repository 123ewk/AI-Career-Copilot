"""Application DTO / Schema（Pydantic v2）

职责：
- 定义投递记录模块的请求/响应 Pydantic Model
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.infra.database.models.application import ApplicationStatus


class ApplicationCreateRequest(BaseModel):
    """创建投递记录请求"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    match_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="匹配分数（0-100）",
    )
    notes: str | None = Field(
        default=None,
        description="备注",
    )


class ApplicationUpdateRequest(BaseModel):
    """更新投递记录请求"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    status: ApplicationStatus = Field(
        ...,
        description="投递状态",
    )
    notes: str | None = Field(
        default=None,
        description="备注",
    )


class ApplicationResponse(BaseModel):
    """投递记录响应"""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )

    id: uuid.UUID = Field(
        ...,
        description="投递记录 ID",
    )
    user_id: uuid.UUID = Field(
        ...,
        description="用户 ID",
    )
    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    status: ApplicationStatus = Field(
        ...,
        description="投递状态",
    )
    match_score: float | None = Field(
        default=None,
        description="匹配分数",
    )
    applied_at: datetime | None = Field(
        default=None,
        description="投递时间",
    )
    status_updated_at: datetime = Field(
        ...,
        description="状态最后更新时间",
    )
    notes: str | None = Field(
        default=None,
        description="备注",
    )
    created_at: datetime = Field(
        ...,
        description="创建时间",
    )


class ApplicationListResponse(BaseModel):
    """投递记录列表响应"""

    model_config = ConfigDict(
        extra="forbid",
    )

    items: list[ApplicationResponse] = Field(
        default_factory=list,
        description="投递记录列表",
    )
    total: int = Field(
        ...,
        description="总数",
    )
    limit: int = Field(
        ...,
        description="每页大小",
    )
    offset: int = Field(
        ...,
        description="偏移量",
    )


__all__ = [
    "ApplicationCreateRequest",
    "ApplicationUpdateRequest",
    "ApplicationResponse",
    "ApplicationListResponse",
]
