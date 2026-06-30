"""Job Analysis 缓存的 Redis 实现（Infra 层）

职责：
- 封装 Redis GET / SETEX / DEL 操作，对 MQ Handler 提供 fail-open 缓存接口
- 序列化为 Pydantic JSON：避免 ORM 对象跨 session / 跨进程访问的安全问题

设计动机：
- 与 RedisResumeCache 同构：infra 层只做「数据访问」，不做业务
- 缓存 DTO 而非 ORM：JobAnalysisResult 是 Agent 真实产出
- fail-open 模式：Redis 不可用时降级到 DB，绝不应让缓存抖动变成 5xx

实现契约：
- 所有方法在异常时静默吞掉 + logger.warning
- key 格式：job:analysis:{job_id}
- value 格式：JobAnalysisResult.model_dump_json()
- TTL：由 settings.job_analysis_cache_ttl_seconds 决定，默认 3600s

潜在风险：
- 反序列化失败：可能因 schema 升级导致旧 key 解析失败
  → 防御：解析失败视为 miss，让下次写入覆盖
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

from app.core.logger import logger
from app.core.settings import get_settings
from app.domain.job.models import JobAnalysisResult
from app.infra.database.redis import redis_client_factory


# 缓存 key 前缀
_KEY_PREFIX: str = "job:analysis:"


def _make_key(job_id: uuid.UUID) -> str:
    """构造 Redis 缓存 key

    格式: job:analysis:{job_id}
    """
    return f"{_KEY_PREFIX}{job_id}"


class RedisJobAnalysisCache:
    """基于 Redis 的 Job Analysis 缓存

    使用方式:
        cache = RedisJobAnalysisCache()  # 默认用全局单例 redis
        # 或注入测试 Redis:
        # cache = RedisJobAnalysisCache(redis=fake_redis_client, ttl_seconds=60)
        await cache.set(job_id, analysis_result)
        cached = await cache.get(job_id)

    设计原则:
    - 构造时注入 Redis 客户端:生产环境走单例,测试可注入 fake
    - 所有方法均为 async:与 Service 层异步链对齐
    - 失败时静默降级:任何 Redis 异常都不影响业务路径
    - 满足 JobAnalysisCacheProtocol:Type Checker 可识别
    """

    def __init__(
        self,
        redis: Redis | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """初始化缓存

        Args:
            redis: Redis 异步客户端。None 时使用全局单例(redis_client_factory.client)
            ttl_seconds: 缓存 TTL(秒)。None 时从 settings.job_analysis_cache_ttl_seconds 读取
        """
        self._redis: Redis = redis if redis is not None else redis_client_factory.client
        if ttl_seconds is None:
            ttl_seconds = get_settings().job_analysis_cache_ttl_seconds
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        """对外暴露当前 TTL(只读),便于调试与监控"""
        return self._ttl_seconds

    # ==================== Protocol 实现 ====================

    async def get(self, job_id: uuid.UUID) -> JobAnalysisResult | None:
        """获取岗位分析结果缓存

        行为:
        - Redis 命中 → 反序列化为 JobAnalysisResult 返回
        - Redis 未命中 → 返回 None
        - Redis 异常 → logger.warning + 返回 None (fail-open)
        - 反序列化异常 → logger.warning + 返回 None

        Args:
            job_id: 岗位 UUID

        Returns:
            命中的 JobAnalysisResult;未命中或失败 → None
        """
        try:
            raw = await self._redis.get(_make_key(job_id))
        except Exception as exc:
            logger.warning(
                "Job Analysis 缓存读取失败 | job_id={} | exc_type={} | exc={}",
                job_id, type(exc).__name__, exc,
            )
            return None

        if raw is None:
            return None

        try:
            return JobAnalysisResult.model_validate_json(raw)
        except Exception as exc:
            logger.warning(
                "Job Analysis 缓存反序列化失败 | job_id={} | exc_type={} | exc={}",
                job_id, type(exc).__name__, exc,
            )
            return None

    async def set(
        self,
        job_id: uuid.UUID,
        analysis: JobAnalysisResult,
        ttl_seconds: int | None = None,
    ) -> None:
        """写入岗位分析结果缓存

        行为:
        - 用 SETEX 同时设置值和 TTL
        - 失败时 logger.warning,不抛异常 (fail-open)

        Args:
            job_id: 岗位 UUID
            analysis: 待缓存的分析结果
            ttl_seconds: 缓存 TTL(秒)。None 时使用构造时传入或 settings 中的默认值
        """
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        try:
            await self._redis.setex(
                _make_key(job_id),
                ttl,
                analysis.model_dump_json(),
            )
        except Exception as exc:
            logger.warning(
                "Job Analysis 缓存写入失败 | job_id={} | exc_type={} | exc={}",
                job_id, type(exc).__name__, exc,
            )

    async def invalidate(self, job_id: uuid.UUID) -> None:
        """失效岗位分析结果缓存

        行为:
        - DEL 是幂等的:key 不存在也返回 0,不报错
        - 失败时 logger.warning,不抛异常

        Args:
            job_id: 岗位 UUID
        """
        try:
            await self._redis.delete(_make_key(job_id))
        except Exception as exc:
            logger.warning(
                "Job Analysis 缓存失效失败 | job_id={} | exc_type={} | exc={}",
                job_id, type(exc).__name__, exc,
            )


__all__ = ["RedisJobAnalysisCache"]
