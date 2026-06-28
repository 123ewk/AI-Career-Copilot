"""Domain 层缓存抽象包

职责：
- 定义各业务域缓存的抽象契约（Protocol）
- 由 infra/cache/ 中的具体 Redis 实现
- Domain Service / UseCase 仅依赖本包中的 Protocol，便于替换缓存后端或测试时 mock

层级关系（自上而下）：
    api/routers  →  domain/services  →  domain/cache (Protocol)
                                          ↑ 实现
                                      infra/cache (具体 Redis 类)

各文件对应：
- resume.py  → ResumeCacheProtocol（Step 1.5.10,已完成）
- match.py   → MatchCacheProtocol（Step 1.7.12,待实现）
- communication.py → TemplateCacheProtocol（Step 1.8.11,待实现）
- session.py → SessionCacheProtocol（Step 1.10.7,待实现）

设计原则（沿用 Resume 缓存的约定）：
- Protocol 而非 ABC：结构化子类型，duck typing
- 所有方法 async：调用方必须 await
- 失败时静默降级（fail-open）：get 失败返回 None、set/invalidate 失败不抛异常
- Service 层不感知底层是 Redis 还是其他实现

注意:本 __init__.py 对未完成的模块做容错 import,
避免单个空文件阻塞整个 package 的加载。
"""

# 容错 import:对应模块若为空(开发阶段正常状态)则跳过,不阻塞其他 Protocol 的导出
# 使用 __getattr__ 实现 PEP 562 的延迟属性访问,避免模块未实现时立即抛 ImportError
def __getattr__(name: str):  # type: ignore[no-redef]
    """延迟解析:模块内 import 失败时不抛错,允许属性级重试"""
    _imports = {
        "MatchCacheProtocol": ("app.domain.cache.match", "MatchCacheProtocol"),
        "TemplateCacheProtocol": ("app.domain.cache.communication", "TemplateCacheProtocol"),
        "SessionCacheProtocol": ("app.domain.cache.session", "SessionCacheProtocol"),
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
from app.domain.cache.resume import ResumeCacheProtocol

__all__ = [
    "MatchCacheProtocol",
    "ResumeCacheProtocol",
    "SessionCacheProtocol",
    "TemplateCacheProtocol",
]
