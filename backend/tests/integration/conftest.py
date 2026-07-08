"""集成测试专用 conftest

职责：
- 覆盖顶层 conftest 的测试环境变量，恢复真实 LLM API Key
- 统一使用 docker-compose 默认的数据库凭据
- 提供端到端测试所需的公共 fixture

设计动机：
- MVP 集成测试需要调用真实 LLM、真实 PostgreSQL/Redis/RabbitMQ
- 顶层 conftest 为避免泄露生产密钥，默认清空 LLM Key
- 本文件在子目录生效，负责把集成测试环境还原为"真实 .env + docker-compose 默认 DB"
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession


# ==================== 常量 ====================

# 本地开发/测试环境的 PostgreSQL 凭据
# 当前本地已运行 pg-zh 容器：user=postgres, password=postgres, db=copilot_dev
_DOCKER_POSTGRES_USER: str = "postgres"
_DOCKER_POSTGRES_PASSWORD: str = "postgres"
_DOCKER_POSTGRES_DB: str = "copilot_dev"

# 需要从真实 .env 恢复的敏感配置项
_SENSITIVE_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_BASE",
    "DEEPSEEK_MODEL",
    "MIMO_API_KEY",
    "MIMO_API_BASE",
    "MIMO_MODEL",
    "LLM_PROVIDER",
    "TAVILY_API_KEY",
    "TAVILY_API_BASE",
)


# ==================== 辅助函数 ====================

def _load_env_file(path: Path) -> dict[str, str]:
    """简易 .env 解析器

    为什么不直接用 python-dotenv：
    - 避免在 conftest 顶层引入额外依赖
    - 只需读取几个已知 key 的值，手动解析足够稳定

    Args:
        path: .env 文件路径

    Returns:
        key-value 字典
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


# ==================== 集成测试环境变量初始化 ====================
# 为什么放在模块顶层而非 fixture：
# - test_mvp_end_to_end.py 在模块顶层执行 `from main import app`
# - main.py 会调用 get_settings()，并用 lru_cache 缓存结果
# - 若放在 fixture 中，设置环境变量时 settings 已被缓存，导致覆盖失效
# - 放在 conftest 模块顶层可保证：test 模块 import 前，环境变量已就绪

# 1. 固定测试 JWT：避免使用 .env 里的真实密钥
os.environ["JWT_SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
os.environ["JWT_ALGORITHM"] = "HS256"

# 2. 应用环境：dev 会开放 /docs，且日志更详细
os.environ["APP_ENV"] = "dev"
os.environ["DEBUG"] = "true"
os.environ["LOG_LEVEL"] = "INFO"

# 3. 数据库指向本地 docker 容器（pg-zh）
#    当前本地已运行 pg-zh 容器：user=postgres, password=postgres, db=copilot_dev
os.environ["POSTGRES_USER"] = _DOCKER_POSTGRES_USER
os.environ["POSTGRES_PASSWORD"] = _DOCKER_POSTGRES_PASSWORD
os.environ["POSTGRES_DB"] = _DOCKER_POSTGRES_DB
# host/port 保持 .env 中的 localhost:5432 即可

# 4. 恢复真实 .env 中的 LLM 等敏感配置，确保真实调用
_ENV_PATH = Path(__file__).resolve().parents[3] / "app" / "configs" / ".env"
_REAL_ENV = _load_env_file(_ENV_PATH)
for _key in _SENSITIVE_ENV_KEYS:
    _value = _REAL_ENV.get(_key)
    if _value:
        os.environ[_key] = _value


# ==================== 保险：清除 settings 缓存 ====================
# 如果某个顶层 import 已经触发过 get_settings()，此处清除缓存，
# 确保 test 模块 import main.py 时重新读取上方设置的环境变量。
with contextlib.suppress(ImportError):
    from app.core.settings import get_settings

    get_settings.cache_clear()


# ==================== Fixture：独立 DB Session ====================
@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """提供独立 AsyncSession，供集成测试做数据清理用

    为什么不用 client 依赖注入的 session：
    - client 生命周期由 FastAPI 管理，测试代码不方便在 finally 中提交/回滚
    - 独立 session 可在测试结束后单独执行清理 DELETE，不影响请求级 session

    注意：
    - 本 fixture 会触发 pg_session_factory 懒初始化（读取当前环境变量）
    - conftest 顶层已设置好 POSTGRES_* 环境变量，因此指向正确的测试数据库
    """
    from app.infra.database.postgres import pg_session_factory

    session = pg_session_factory.create_session()
    try:
        yield session
    finally:
        await session.close()
