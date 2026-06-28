"""Task 域模块

职责：
- 异步任务生命周期管理
- 与 MQ Consumer 配合，实现「API 落库 Task → Publisher 发消息 → Consumer 执行 → 落结果」流水线
"""

from app.domain.task.service import TaskService

__all__ = ["TaskService"]
