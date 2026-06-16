"""Domain 层仓储接口包

职责：
- 定义仓储的抽象契约（Protocol）
- Domain Service / UseCase 仅 import 本包中的 Protocol，
  不接触 infra 层的 SQLAlchemy / ORM 实现细节
- 单元测试可传一个 Fake 实现，避开真实数据库

层级关系（自上而下）：
    api/routers  →  domain/services  →  domain/repositories (Protocol)
                                              ↑ 实现
                                          infra/repositories (具体类)

各文件对应：
- user.py    → UserRepositoryProtocol
- job.py     → JobRepositoryProtocol
- resume.py  → ResumeRepositoryProtocol
- session.py → SessionRepositoryProtocol
- task.py    → TaskRepositoryProtocol
"""

from app.domain.repositories.job import JobRepositoryProtocol
from app.domain.repositories.resume import ResumeRepositoryProtocol
from app.domain.repositories.session import SessionRepositoryProtocol
from app.domain.repositories.task import TaskRepositoryProtocol
from app.domain.repositories.user import UserRepositoryProtocol

__all__ = [
    "JobRepositoryProtocol",
    "ResumeRepositoryProtocol",
    "SessionRepositoryProtocol",
    "TaskRepositoryProtocol",
    "UserRepositoryProtocol",
]
