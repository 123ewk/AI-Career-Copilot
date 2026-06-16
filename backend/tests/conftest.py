"""pytest 公共配置与 fixture

职责：
- 兜底设置 sys.path（pyproject.toml 已配 pythonpath = ["backend"]，这里是保险）
- 设置测试环境变量，避免依赖真实 .env（含生产密钥）
- 提供跨测试复用的 fixture：JWT 工厂、日志捕获、测试用 settings

设计动机：
- 测试 settings 通过 os.environ 覆盖而非 monkeypatch 真实模块，
  原因：pydantic-settings 在首次 import 时就解析环境变量，monkeypatch 太晚
- 公共 fixture 放 conftest.py，子目录 conftest.py 可继承与覆盖
- 不提供 db/redis/mq fixture：集成测试需要 testcontainers，
  属于「下一步」范围，本文件只搭骨架
"""

from __future__ import annotations

import io
import os
import sys
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import jwt
import pytest

if TYPE_CHECKING:
    from app.core.settings import Settings

# ==================== 路径兜底 ====================
# pyproject.toml 已通过 pythonpath = ["backend"] 告诉 pytest 把 backend/ 加入 sys.path
# 这里再保险一次：直接 `python -m pytest` 或 IDE 运行单个测试时也能解析 `from app.xxx`
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ==================== 警告过滤器 ====================
# 全局抑制 starlette 1.x 已弃用常量的 DeprecationWarning / StarletteDeprecationWarning
# pyproject.toml 的 filterwarnings = ["error"] 会把所有 warning 提升为 error，
# 而 exception.py 等 production code 仍在使用 starlette.status 旧 API
# 为什么放 conftest.py 而非 pyproject.toml：
# · StarletteDeprecationWarning 来自 starlette.exceptions，pyproject.toml 解析阶段
#   starlette 还没 import，pytest 无法解析该类（AttributeError）
# · 放 conftest.py 时 starlette 已被 import，warnings.filterwarnings 可正常匹配
# 该修复属于 production code 范畴，不在当前任务范围
# 参考：https://github.com/encode/starlette/releases（v1.0 重命名 HTTP_422_*）
try:
    from starlette.exceptions import StarletteDeprecationWarning  # type: ignore[attr-defined]

    warnings.filterwarnings(
        "ignore",
        category=StarletteDeprecationWarning,
    )
except ImportError:
    # 旧版本 starlette 用标准 DeprecationWarning，已在 pyproject.toml 中屏蔽
    pass
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"starlette(\..*)?",
)


# ==================== 环境变量 ====================
@pytest.fixture(scope="session", autouse=True)
def _setup_test_env() -> None:
    """会话级：测试启动前注入测试环境变量

    必须在 app.core.settings 首次被 import 前设置，
    否则 pydantic-settings 会用 .env 里的真实值（包括默认的 changeme 密钥）

    集成测试若需要真实 PostgreSQL/Redis/RabbitMQ，
    应在 conftest.py 本地覆盖这些值（testcontainers 模式）
    """
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("DEBUG", "false")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    # 鉴权：测试用固定密钥，不要用 .env 里的真实值
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-do-not-use-in-prod")
    os.environ.setdefault("JWT_ALGORITHM", "HS256")
    # LLM：测试不需要真实调用，留空即可
    os.environ.setdefault("OPENAI_API_KEY", "")
    os.environ.setdefault("DEEPSEEK_API_KEY", "")


# ==================== Settings 工厂 ====================
@pytest.fixture
def test_settings() -> Settings:
    """返回 Settings 单例（带测试环境变量）

    pydantic-settings 用 lru_cache 缓存，加载一次即可
    """
    # 必须在 import 前确保 _setup_test_env 已运行（autouse=True 保证）
    from app.core.settings import get_settings

    return get_settings()


# ==================== JWT 工厂 ====================
@pytest.fixture
def make_jwt(test_settings: Settings) -> Callable[..., str]:
    """构造测试 JWT 的工厂函数

    用法：
        def test_x(make_jwt):
            token = make_jwt(sub="user-1", token_type="access")
            headers = {"Authorization": f"Bearer {token}"}

    Args:
        sub: 主题（用户 ID）
        token_type: access / refresh
        exp_delta: 距当前时间的过期偏移（秒），负数=已过期
        secret: 签名密钥，默认用 settings.jwt_secret_key
    """
    settings = test_settings

    def _make(
        *,
        sub: str = "user-123",
        token_type: str = "access",
        exp_delta: int = 3600,
        secret: str | None = None,
    ) -> str:
        """构造一个测试 JWT（内部实现）

        参数说明同 make_jwt fixture
        """
        payload = {
            "sub": sub,
            "type": token_type,
            "iat": int(time.time()),
            "exp": int(time.time()) + exp_delta,
        }
        return jwt.encode(
            payload,
            secret or settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

    return _make


# ==================== 日志捕获 ====================
@pytest.fixture
def log_capture() -> io.StringIO:
    """捕获 logger 输出的 StringIO 缓冲区

    用法：
        def test_x(log_capture, monkeypatch):
            from app.core.logger import logger
            logger.add(log_capture, format="{message}", level="DEBUG")
            # ... 触发业务日志 ...
            assert "expected text" in log_capture.getvalue()
    """
    return io.StringIO()


# ==================== 时间冻结 ====================
@pytest.fixture
def frozen_time():
    """冻结当前时间为固定值（返回 time.time 替身）

    用法：
        def test_token_ttl(frozen_time):
            frozen_time.set(1700000000)
            token = make_jwt(exp_delta=3600)
            # 验证 token.exp == 1700000000 + 3600
    """
    from unittest.mock import patch

    current = {"value": int(time.time())}

    class FrozenTime:
        def set(self, ts: int) -> None:
            current["value"] = ts

        def __call__(self) -> float:
            return float(current["value"])

    with patch("time.time", side_effect=FrozenTime()):
        yield FrozenTime()
