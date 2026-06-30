"""Task 领域层 DTO

职责：
- 定义 Task 对外暴露的数据结构
- 避免将 ORM Task 直接返回给 API / Consumer，防止泄露持久化细节
- 统一 API 层、Service 层、测试层的 Task 数据契约

设计动机：
- 与 Resume / Job 模块保持一致：Domain DTO + Infra ORM 分离
- 便于后续扩展：前端只需要 id / status / result / error_message 等稳定字段
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.infra.database.models.task import TaskStatus


class TaskDTO(BaseModel):
    """Task 数据传输对象

    字段与 tasks 表一一对应，但剥离 SQLAlchemy 状态，可被 Pydantic 序列化。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID
    business_id: str
    task_type: str
    status: TaskStatus
    input_data: dict | list | None = None
    result: dict | list | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    """任务列表分页响应"""

    items: list[TaskDTO]
    total: int
    limit: int
    offset: int


__all__ = ["TaskDTO", "TaskListResponse"]
