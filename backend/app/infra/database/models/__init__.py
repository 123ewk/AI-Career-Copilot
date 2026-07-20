"""ORM Model 包

职责：
- 导出所有 ORM Model，使 Alembic 通过 Base.metadata 发现全部表定义
- 导入即注册：SQLAlchemy 的 DeclarativeBase 机制要求 Model 类被 Python 解释器加载，
  才会在 Base.metadata 中注册表结构，因此必须在此处显式 import

设计动机：
- 集中导出而非在各处零散 import，确保 Alembic 的 target_metadata 引用此包即可发现所有表
- ORM Model 与 Domain Model 分离：ORM Model 映射数据库表结构，Domain Model 表达业务语义，
  二者职责不同，避免泄露数据库实现细节到业务层
"""

from app.infra.database.models.agent_memory import AgentMemory, EMBEDDING_DIMENSIONS, MemoryType
from app.infra.database.models.application import Application, ApplicationStatus
from app.infra.database.models.conversation import Conversation
from app.infra.database.models.job import Job
from app.infra.database.models.resume import Resume
from app.infra.database.models.session import Session, SessionStatus
from app.infra.database.models.task import Task, TaskStatus
from app.infra.database.models.user import User

__all__ = [
    "AgentMemory",
    "Application",
    "ApplicationStatus",
    "Conversation",
    "EMBEDDING_DIMENSIONS",
    "Job",
    "MemoryType",
    "Resume",
    "Session",
    "SessionStatus",
    "Task",
    "TaskStatus",
    "User",
]
