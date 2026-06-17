"""Resume 缓存的 Redis 实现（Infra 层）

职责：
- 实现 domain/cache/resume.py 中的 ResumeCacheProtocol
- 封装 Redis GET / SETEX / DEL 操作，对 Service 层提供 fail-open 缓存接口
- 序列化为 Pydantic JSON：避免 ORM 对象跨 session / 跨进程访问的安全问题

设计动机：
- 与 SQLAlchemy 仓储同构：infra 层只做「数据访问」，不做业务
- 缓存 DTO 而非 ORM：ResumeResponse 是 Service 真实返回值，缓存它等价于缓存 API 响应
  → ORM 对象有 lazy-load 语义、绑定 session，跨 session 反序列化会触发异常
  → Pydantic model_dump_json / model_validate_json 是 v2 标准方法
- fail-open 模式：与 rate_limit.py / redis_client_factory 风格一致
  → Redis 不可用是「降级场景」而非「错误」，绝不应让缓存抖动变成 5xx

实现契约：
- 实现 ResumeCacheProtocol（结构化子类型，无需显式继承）
- 所有方法在异常时静默吞掉 + logger.warning
- key 格式：resume:active:{user_id}
- value 格式：ResumeResponse.model_dump_json()
- TTL：由 settings.resume_cache_ttl_seconds 决定，默认 1800s

文件命名约定（与 domain/cache/ 对齐）：
- domain/cache/resume.py   → ResumeCacheProtocol（抽象）
- infra/cache/resume.py    → RedisResumeCache（实现）
- 同名不同后缀是为了让「业务域」一眼可见，Protocol / Implementation
  仅以所在目录区分，目录即语义层

潜在风险：
- 反序列化失败：可能因 ResumeResponse schema 升级导致旧 key 解析失败
  → 防御：解析失败视为 miss，让下次写入覆盖
- 并发写竞争：写后失效与读穿透之间存在微秒级窗口
  → 防御：30 分钟 TTL 兜底，业务上简历变更不频繁
- Redis 内存压力：active resume 全文可能 50KB，10 万用户 ≈ 5GB
  → 防御：监控 Redis 内存；后续可改为只缓存 summary（id + skills + years + is_active）
- 大 key 风险：单 key 50KB 接近 Redis 字符串传输阈值
  → 防御：当前量级 OK；后续评估是否拆分为「summary 缓存」+ 「按需加载 raw_text」
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

from app.core.logger import logger
from app.core.settings import get_settings
from app.domain.cache.resume import ResumeCacheProtocol
from app.domain.resume.models import ResumeResponse
from app.infra.database.redis import redis_client_factory


# 缓存 key 前缀：便于 KEYS resume:* 排查 / MONITOR 观察 / SCAN 清理
_KEY_PREFIX: str = "resume:active:"


def _make_key(user_id: uuid.UUID) -> str:
    """构造 Redis 缓存 key

    格式:resume:active:{user_id}
    - 前缀 resume:active: 区分业务域,避免与其他 cache key 冲突
    - user_id 用 str() 转换:UUID 不能直接拼到字符串里(会变成 repr 形式)
    """
    return f"{_KEY_PREFIX}{user_id}"


class RedisResumeCache:
    """基于 Redis 的 Resume active 缓存

    使用方式:
        cache = RedisResumeCache()  # 默认用全局单例 redis
        # 或注入测试 Redis:
        # cache = RedisResumeCache(redis=fake_redis_client, ttl_seconds=60)
        await cache.set_active(user_id, response)
        cached = await cache.get_active(user_id)

    设计原则:
    - 构造时注入 Redis 客户端:生产环境走单例,测试可注入 fake
    - 所有方法均为 async:与 Service 层异步链对齐
    - 失败时静默降级:任何 Redis 异常都不影响业务路径
    - 满足 ResumeCacheProtocol:Type Checker 可识别(运行时 isinstance 也可识别)
    """

    def __init__(
        self,
        redis: Redis | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """初始化缓存

        Args:
            redis: Redis 异步客户端。None 时使用全局单例(redis_client_factory.client)
            ttl_seconds: 缓存 TTL(秒)。None 时从 settings.resume_cache_ttl_seconds 读取
        """
        # 懒加载:测试时显式传 redis,生产环境走单例
        self._redis: Redis = redis if redis is not None else redis_client_factory.client
        # TTL 同样支持覆盖:便于测试用短 TTL / 特殊场景禁用
        if ttl_seconds is None:
            ttl_seconds = get_settings().resume_cache_ttl_seconds
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        """对外暴露当前 TTL(只读),便于调试与监控"""
        return self._ttl_seconds

    # ==================== Protocol 实现 ====================

    async def get_active(self, user_id: uuid.UUID) -> ResumeResponse | None:
        """获取用户的活跃简历缓存

        行为:
        - Redis 命中 → 反序列化为 ResumeResponse 返回
        - Redis 未命中(返回 None)→ 返回 None
        - Redis 异常(超时/连接断开)→ logger.warning + 返回 None(fail-open)
        - 反序列化异常(数据格式升级导致旧 key 解析失败)→ logger.warning + 返回 None
          · 这里也当作 miss,避免脏数据卡死业务流程
          · 下次写入会用新格式覆盖

        Args:
            user_id: 用户 UUID

        Returns:
            命中的 ResumeResponse;未命中或失败 → None
        """
        # ---- 1. Redis GET ----
        try:
            raw = await self._redis.get(_make_key(user_id))
        except Exception as exc:
            logger.warning(
                "Resume 缓存读取失败 | user_id={} | exc_type={} | exc={}",
                user_id, type(exc).__name__, exc,
            )
            return None

        if raw is None:
            return None

        # ---- 2. JSON 反序列化 ----
        # 单独 try:与 Redis 异常分开处理,便于排查是网络问题还是数据问题
        try:
            return ResumeResponse.model_validate_json(raw)
        except Exception as exc:
            logger.warning(
                "Resume 缓存反序列化失败(可能 schema 升级) | user_id={} | exc_type={} | exc={}",
                user_id, type(exc).__name__, exc,
            )
            return None

    async def set_active(
        self,
        user_id: uuid.UUID,
        resume: ResumeResponse,
    ) -> None:
        """写入用户的活跃简历缓存

        行为:
        - 用 SETEX 同时设置值和 TTL,避免「先 SET 再 EXPIRE」非原子导致的永久驻留
        - 失败时 logger.warning,不抛异常(fail-open)

        Args:
            user_id: 用户 UUID
            resume: 待缓存的简历响应
        """
        try:
            await self._redis.setex(
                _make_key(user_id),
                self._ttl_seconds,
                resume.model_dump_json(),
            )
        except Exception as exc:
            logger.warning(
                "Resume 缓存写入失败 | user_id={} | exc_type={} | exc={}",
                user_id, type(exc).__name__, exc,
            )

    async def invalidate_active(self, user_id: uuid.UUID) -> None:
        """失效用户的活跃简历缓存

        行为:
        - DEL 是幂等的:key 不存在也返回 0,不报错
        - 失败时 logger.warning,不抛异常

        适用场景:写操作(创建/更新/删除/切换活跃)成功后调用,
        让下次 get_active 重新走 DB 加载最新数据,避免陈旧数据。

        Args:
            user_id: 用户 UUID
        """
        try:
            await self._redis.delete(_make_key(user_id))
        except Exception as exc:
            logger.warning(
                "Resume 缓存失效失败 | user_id={} | exc_type={} | exc={}",
                user_id, type(exc).__name__, exc,
            )


# ==================== 运行时 Protocol 校验 ====================

# 显式声明:虽然 Python 鸭子类型不需要,但 IDE / mypy 可能更友好
# 不强制要求:即使删掉这一行,Protocol 的结构化子类型仍生效
# 保留仅为提供 runtime_checkable 的备选入口
_ = ResumeCacheProtocol  # 防止 linter 报 unused import


__all__ = ["RedisResumeCache"]
