"""Communication DTO / Schema（Pydantic v2）

职责：
- 定义沟通话术模块的请求/响应 Pydantic Model
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class CommunicationGenerateRequest(BaseModel):
    """生成沟通话术请求"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    session_id: uuid.UUID = Field(
        ...,
        description="会话 ID（用于任务幂等和业务追踪）",
    )
    resume_id: uuid.UUID | None = Field(
        default=None,
        description="简历 ID，未传时使用用户当前活跃简历",
    )
    tone: str = Field(
        default="natural",
        description="话术风格，MVP 固定为 natural（自然实习聊天风）",
    )


class CommunicationScriptResponse(BaseModel):
    """沟通话术内容"""

    model_config = ConfigDict(
        extra="forbid",
    )

    job_id: uuid.UUID = Field(
        ...,
        description="岗位 ID",
    )
    resume_id: uuid.UUID | None = Field(
        default=None,
        description="简历 ID",
    )
    greeting: str = Field(
        ...,
        description="初次打招呼话术",
    )
    follow_up: str = Field(
        ...,
        description="跟进/回复话术",
    )
    full_script: str = Field(
        ...,
        description="完整对话参考",
    )


class CommunicationGenerateResponse(BaseModel):
    """生成沟通话术异步任务响应"""

    model_config = ConfigDict(
        extra="forbid",
    )

    task_id: uuid.UUID = Field(
        ...,
        description="任务 ID",
    )
    status: str = Field(
        ...,
        description="任务状态，如 pending",
    )


__all__ = [
    "CommunicationGenerateRequest",
    "CommunicationScriptResponse",
    "CommunicationGenerateResponse",
]
