"""Infra 层仓储实现包

职责：
- 存放 ORM（SQLAlchemy）实现的具体仓储类
- 全部实现 domain/repositories/ 中对应的 Protocol
  → UserRepository       实现 UserRepositoryProtocol
  → JobRepository        实现 JobRepositoryProtocol
  → ResumeRepository     实现 ResumeRepositoryProtocol
  → SessionRepository    实现 SessionRepositoryProtocol
  → TaskRepository       实现 TaskRepositoryProtocol

层级关系（自上而下）：
    api/routers  →  domain/services  →  domain/repositories (Protocol)
                                              ↑ 实现
                                          infra/repositories (具体类，本包)

命名约定：
- 类名 = 实体名 + "Repository"（如 UserRepository）
- 文件名 = 类名小写 + 下划线（user_repo.py）
- 与 ORM Model 同目录的 models/ 包一一对应

扩展指南：
- 新增实体时先在 domain/repositories/ 中定义 Protocol，
  再在本包中编写实现，保证 Domain → Infra 的依赖方向不反转
"""

from app.infra.repositories.job_repo import JobRepository
from app.infra.repositories.resume_repo import ResumeRepository
from app.infra.repositories.session_repo import SessionRepository
from app.infra.repositories.task_repo import TaskRepository
from app.infra.repositories.user_repo import UserRepository

__all__ = [
    "JobRepository",
    "ResumeRepository",
    "SessionRepository",
    "TaskRepository",
    "UserRepository",
]
