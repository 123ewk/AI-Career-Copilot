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
- job.py     → JobRepositoryProtocol（Step 1.6 阶段尚未实现,暂为空）
- resume.py  → ResumeRepositoryProtocol
- session.py → SessionRepositoryProtocol（Step 1.10 阶段尚未实现,暂为空）
- task.py    → TaskRepositoryProtocol（Step 1.10 阶段尚未实现,暂为空）

注意:本 __init__.py 对未完成的模块做容错 import,
避免单个空文件阻塞整个 package 的加载。
Step 1.5 阶段,Job / Session / Task 模块尚未实现,后续随开发进度会逐步补充。

Resume 域的缓存抽象（ResumeCacheProtocol）已迁出至 `app.domain.cache.resume`，
本包只承担「数据库仓储」职责,不再混入缓存抽象。
"""

# 容错 import:对应模块若为空(开发阶段正常状态)则跳过,不阻塞其他 Protocol 的导出
# 使用 __getattr__ 实现 PEP 562 的延迟属性访问,避免模块未实现时立即抛 ImportError
def __getattr__(name: str):  # type: ignore[no-redef]
    """延迟解析:模块内 import 失败时不抛错,允许属性级重试"""
    _imports = {
        "JobRepositoryProtocol": ("app.domain.repositories.job", "JobRepositoryProtocol"),
        "SessionRepositoryProtocol": ("app.domain.repositories.session", "SessionRepositoryProtocol"),
        "TaskRepositoryProtocol": ("app.domain.repositories.task", "TaskRepositoryProtocol"),
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
from app.domain.repositories.resume import ResumeRepositoryProtocol
from app.domain.repositories.user import UserRepositoryProtocol
