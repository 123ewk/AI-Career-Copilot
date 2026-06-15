"""CORS 跨域中间件

职责：
- 处理浏览器跨域请求，特别是 Chrome / Firefox 浏览器扩展来源
- 兼容配置化的白名单（Web 前端、第三方回调等固定域名）
- 预检请求（OPTIONS）必须最先处理，否则后续业务中间件会拦截

设计动机：
- 浏览器扩展的 Origin 形如 chrome-extension://<extension_id> 或
  moz-extension://<uuid>，无法用固定列表枚举：
  · 开发期扩展 ID 由 Chrome 随机生成（unpacked 模式）
  · 发布期 ID 由 Web Store 分配且不可变
- Starlette 的 CORSMiddleware 支持 allow_origin_regex，
  配合正则可一次匹配所有 chrome-extension:// / moz-extension:// 来源
- allow_origins 负责固定域名（Web 前端 / 第三方回调），两者组合使用
- 显式 allow_headers 而非通配符：部分浏览器（含扩展）拒绝 * 通配符

关键技术点：
- CORSMiddleware 是基于 ASGI 的纯 HTTP 头处理中间件，
  不会修改业务请求体，因此无需 async 包装
- 预检请求由中间件直接短路返回 200，不会进入路由层，
  节省一次数据库 / 业务逻辑开销
- 必须在 app.add_middleware 中尽早注册，
  保证 OPTIONS 预检不被认证 / 日志中间件误拦截

潜在风险：
- allow_origin_regex=.* 放行所有扩展源存在被钓鱼扩展盗用 Token 的风险
  → 生产环境建议在 settings.cors_allow_extensions=False，
    并在业务层用 X-Extension-ID 头 + 已知扩展 ID 列表做二次校验
- allow_credentials=True 时 allow_origins 不能是 ['*']，
  框架会在启动时报错；本实现已通过显式列表规避
"""

from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logger import logger
from app.core.settings import get_settings

# ==================== 常量 ====================

# 浏览器扩展 Origin 正则
# 匹配示例：chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef
#           moz-extension://9b9c5d04-4d8a-4d8e-bbf6-7a0e9c1f2a3b
# 说明：扩展 ID 在 Chrome 加载 unpacked 扩展时随机生成、发布后固定，
# 用正则可同时覆盖开发与生产场景
_EXTENSION_ORIGIN_REGEX: Final[str] = r"^(chrome|moz|edge)-extension://.+$"

# 业务常用 HTTP 方法
_ALLOW_METHODS: Final[list[str]] = [
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
]

# 显式声明允许的请求头
# 避免使用 "*"：部分浏览器（尤其扩展 Content Script）会拒绝通配头
_ALLOW_HEADERS: Final[list[str]] = [
    "Authorization",   # JWT 鉴权
    "Content-Type",    # 请求体类型
    "X-Request-ID",    # 全链路追踪
    "X-Extension-ID",  # 扩展二次校验
    "X-Extension-Version",
]

# 暴露给浏览器侧的响应头（前端 JS 可通过 getResponseHeader 读取）
_EXPOSE_HEADERS: Final[list[str]] = [
    "X-Request-ID",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
]

# 开发环境兜底的本地前端端口
# 解决"启动前端忘了配 CORS 报错"的痛点；生产环境不会引入这些项
_DEV_LOCAL_ORIGINS: Final[tuple[str, ...]] = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8080",
)


def _build_allow_origins() -> list[str]:
    """组装最终允许的精确 Origin 列表

    来源：
    1. settings.cors_allow_origins（运营/前端约定的固定域名）
    2. 开发环境追加本地端口（避免开发体验割裂）

    Returns:
        去重后的精确 Origin 列表，作为 CORSMiddleware.allow_origins 的入参
    """
    settings = get_settings()
    origins: list[str] = list(settings.cors_allow_origins)

    if settings.app_env == "dev":
        origins.extend(_DEV_LOCAL_ORIGINS)

    # 保留首次出现的元素顺序去重（dict.fromkeys 比 set 更稳）
    return list(dict.fromkeys(origins))


def add_cors_middleware(app: FastAPI) -> None:
    """注册 CORS 中间件到 FastAPI 应用

    必须在 create_app 中最先注册（参考 main.py 注释），
    保证 OPTIONS 预检请求被短路返回，不会被认证/限流中间件误拦截。

    Args:
        app: FastAPI 应用实例

    设计权衡：
    - allow_credentials=True 允许携带 Cookie/Authorization，
      但要求 allow_origins 必须是显式列表（不能用 ['*']），
      本实现通过正则 + 显式列表组合规避此限制
    - max_age=600：浏览器在 10 分钟内对相同 URL 的预检请求会走缓存，
      减少冗余 OPTIONS 请求；CDN 友好但调试时需手动清缓存
    """
    settings = get_settings()

    allow_origins = _build_allow_origins()

    # 仅当配置显式开启时才放行扩展来源
    # 关闭后只剩 allow_origins 精确白名单 + 开发端口
    allow_origin_regex: str | None = (
        _EXTENSION_ORIGIN_REGEX if settings.cors_allow_extensions else None
    )

    logger.info(
        "注册 CORS 中间件 | env={} | allow_origins={} | allow_extensions={} | credentials={}",
        settings.app_env,
        allow_origins,
        bool(allow_origin_regex),
        settings.cors_allow_credentials,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=_ALLOW_METHODS,
        allow_headers=_ALLOW_HEADERS,
        expose_headers=_EXPOSE_HEADERS,
        max_age=settings.cors_max_age_seconds,
    )
