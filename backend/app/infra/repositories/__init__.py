"""Infra 层仓储实现包

职责：
- 存放 ORM（SQLAlchemy）实现的具体仓储类
- 全部实现 domain/repositories/ 中对应的 Protocol
  → UserRepository       实现 UserRepositoryProtocol
  → JobRepository        实现 JobRepositoryProtocol（Step 1.6 尚未实现,暂为空）
  → ResumeRepository     实现 ResumeRepositoryProtocol
  → SessionRepository    实现 SessionRepositoryProtocol（Step 1.10 尚未实现,暂为空）
  → TaskRepository       实现 TaskRepositoryProtocol（Step 1.10 尚未实现,暂为空）

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

注意:本 __init__.py 对未完成的模块做容错 import,
避免单个空文件阻塞整个 package 的加载(与 domain/repositories/__init__.py 一致)。
"""

# 容错 import:对应模块若为空(开发阶段正常状态)则跳过,不阻塞其他类的导出
def __getattr__(name: str):  # type: ignore[no-redef]
    """延迟解析:模块内 import 失败时不抛错,允许属性级重试"""
    _imports = {
        "JobRepository": ("app.infra.repositories.job_repo", "JobRepository"),
        "SessionRepository": ("app.infra.repositories.session_repo", "SessionRepository"),
        "TaskRepository": ("app.infra.repositories.task_repo", "TaskRepository"),
    }
    if name in _imports:
        module_path, attr_name = _imports[name]
        import importlib
        try:
            mod = importlib.import_module(module_path)
            value = getattr(mod, attr_name)
            globals()[name] = value  # 缓存,避免重复 import
            return value
        except (ImportError, AttributeError):
            raise AttributeError(
                f"{name} 尚未实现（{module_path} 为空或缺失），"
                f"请在对应 Phase 的 Step 中补全"
            ) from None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 始终能 import 的部分（已完成模块）
from app.infra.repositories.resume_repo import ResumeRepository
from app.infra.repositories.user_repo import UserRepository

__all__ = [
    "JobRepository",
    "ResumeRepository",
    "SessionRepository",
    "TaskRepository",
    "UserRepository",
]
