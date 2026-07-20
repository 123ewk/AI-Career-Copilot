"""Communication DTO / Schema（Pydantic v2）

职责：
- 定义沟通话术模块的请求/响应 Pydantic Model
- 包含一次性话术生成（generate）和多轮对话回复（reply）两套 DTO
"""

import uuid
from datetime import datetime
from typing import Literal

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
    "ChatMessage",
    "ConversationContextRequest",
    "ConversationReplyResponse",
    "ConversationSyncRequest",
    "ConversationSyncResponse",
    "ConversationSummary",
    "ConversationDetail",
]


# ==================== 多轮对话回复 DTO ====================


class ChatMessage(BaseModel):
    """单条聊天消息"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "recruiter"] = Field(
        ...,
        description="消息角色：user（用户）或 recruiter（招聘方）",
    )
    text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="消息文本",
    )
    timestamp: str | None = Field(
        default=None,
        description="消息时间（ISO 格式字符串，可选）",
    )


class ConversationContextRequest(BaseModel):
    """多轮对话回复生成请求"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_id: uuid.UUID | None = Field(
        default=None,
        description="关联岗位 ID（可选，用于加载岗位上下文）",
    )
    recruiter_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="招聘方姓名",
    )
    messages: list[ChatMessage] = Field(
        ...,
        min_length=1,
        description="完整对话历史（至少一条消息）",
    )
    resume_id: uuid.UUID | None = Field(
        default=None,
        description="简历 ID（可选，未传时使用用户活跃简历）",
    )
    tone: Literal["natural", "formal", "enthusiastic"] = Field(
        default="natural",
        description="回复风格",
    )


class ConversationReplyResponse(BaseModel):
    """AI 生成的对话回复响应"""

    model_config = ConfigDict(extra="forbid")

    suggested_reply: str = Field(
        ...,
        description="AI 建议的回复文本",
    )
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="关联的对话 ID（如已持久化）",
    )


class ConversationSyncRequest(BaseModel):
    """DOM 消息同步请求"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    job_id: uuid.UUID | None = Field(
        default=None,
        description="关联岗位 ID（可选）",
    )
    recruiter_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="招聘方姓名",
    )
    messages: list[ChatMessage] = Field(
        ...,
        description="最新的消息列表（全量快照）",
    )


class ConversationSyncResponse(BaseModel):
    """消息同步响应"""

    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID = Field(
        ...,
        description="对话 ID",
    )
    message_count: int = Field(
        ...,
        description="同步后的消息数量",
    )


class ConversationSummary(BaseModel):
    """对话摘要（列表用）"""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: uuid.UUID
    recruiter_name: str
    job_id: uuid.UUID | None = None
    channel: str
    last_message: str | None = None
    last_message_at: datetime | None = None
    message_count: int = 0


class ConversationDetail(BaseModel):
    """对话详情（含完整消息历史）"""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: uuid.UUID
    user_id: uuid.UUID
    job_id: uuid.UUID | None = None
    recruiter_name: str
    channel: str
    messages: list[ChatMessage] = []
    created_at: datetime
    updated_at: datetime
