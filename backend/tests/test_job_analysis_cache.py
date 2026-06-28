"""Job Analysis Cache 单元测试

职责：
- 测试 JobAnalysisCacheProtocol 的契约
- 测试 RedisJobAnalysisCache 的 Redis 操作
- 覆盖正常流程、fail-open 降级、序列化/反序列化

测试策略：
- Mock Redis 客户端避免真实 Redis 依赖
- 验证 SETEX 调用（原子写入 + TTL）
- 验证 fail-open：Redis 异常时返回 None / 静默吞掉
- 验证 Pydantic 序列化/反序列化正确性
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.job.models import JobAnalysisResult


# ==================== 测试数据 ====================

SAMPLE_JOB_ID = uuid.uuid4()

SAMPLE_ANALYSIS = JobAnalysisResult(
    skills=["Python", "FastAPI", "PostgreSQL"],
    keywords=["AI应用开发", "后端开发"],
    difficulty="hard",
    seniority="senior",
)


# ==================== Fixtures ====================


@pytest.fixture
def mock_redis():
    """模拟 Redis 异步客户端"""
    redis = AsyncMock()
    redis.get.return_value = None
    redis.setex.return_value = True
    redis.delete.return_value = 1
    return redis


@pytest.fixture
def cache(mock_redis):
    """RedisJobAnalysisCache 实例"""
    from app.infra.cache.job_analysis import RedisJobAnalysisCache

    return RedisJobAnalysisCache(redis=mock_redis, ttl_seconds=3600)


# ==================== Get ====================


class TestJobAnalysisCacheGet:
    """缓存读取测试"""

    async def test_get_hit(
        self,
        cache,
        mock_redis,
    ) -> None:
        """缓存命中：反序列化返回 JobAnalysisResult"""
        mock_redis.get.return_value = SAMPLE_ANALYSIS.model_dump_json()

        result = await cache.get(SAMPLE_JOB_ID)

        assert result is not None
        assert result.skills == SAMPLE_ANALYSIS.skills
        assert result.difficulty == SAMPLE_ANALYSIS.difficulty
        mock_redis.get.assert_awaited_once()

    async def test_get_miss(
        self,
        cache,
        mock_redis,
    ) -> None:
        """缓存未命中：返回 None"""
        mock_redis.get.return_value = None

        result = await cache.get(SAMPLE_JOB_ID)

        assert result is None

    async def test_get_redis_error_returns_none(
        self,
        cache,
        mock_redis,
    ) -> None:
        """Redis 异常时返回 None（fail-open）"""
        mock_redis.get.side_effect = ConnectionError("Redis 连接断开")

        result = await cache.get(SAMPLE_JOB_ID)

        assert result is None

    async def test_get_deserialization_error_returns_none(
        self,
        cache,
        mock_redis,
    ) -> None:
        """反序列化失败时返回 None（fail-open）"""
        mock_redis.get.return_value = "invalid json"

        result = await cache.get(SAMPLE_JOB_ID)

        assert result is None

    async def test_get_uses_correct_key(
        self,
        cache,
        mock_redis,
    ) -> None:
        """使用正确的 Redis key 格式"""
        mock_redis.get.return_value = None

        await cache.get(SAMPLE_JOB_ID)

        call_args = mock_redis.get.call_args
        key = call_args[0][0]
        assert f"job:analysis:{SAMPLE_JOB_ID}" == key


# ==================== Set ====================


class TestJobAnalysisCacheSet:
    """缓存写入测试"""

    async def test_set_success(
        self,
        cache,
        mock_redis,
    ) -> None:
        """写入成功：调用 SETEX"""
        await cache.set(SAMPLE_JOB_ID, SAMPLE_ANALYSIS)

        mock_redis.setex.assert_awaited_once()
        call_args = mock_redis.setex.call_args
        key = call_args[0][0]
        ttl = call_args[0][1]
        value = call_args[0][2]

        assert f"job:analysis:{SAMPLE_JOB_ID}" == key
        assert ttl == 3600
        assert SAMPLE_ANALYSIS.skills[0] in value

    async def test_set_redis_error_does_not_raise(
        self,
        cache,
        mock_redis,
    ) -> None:
        """Redis 异常时不抛异常（fail-open）"""
        mock_redis.setex.side_effect = ConnectionError("Redis 连接断开")

        # 不应抛异常
        await cache.set(SAMPLE_JOB_ID, SAMPLE_ANALYSIS)

    async def test_set_serializes_analysis(
        self,
        cache,
        mock_redis,
    ) -> None:
        """序列化包含完整的分析结果"""
        await cache.set(SAMPLE_JOB_ID, SAMPLE_ANALYSIS)

        call_args = mock_redis.setex.call_args
        value = call_args[0][2]

        # 验证 JSON 包含关键字段
        assert "Python" in value
        assert "hard" in value
        assert "senior" in value


# ==================== Invalidate ====================


class TestJobAnalysisCacheInvalidate:
    """缓存失效测试"""

    async def test_invalidate_success(
        self,
        cache,
        mock_redis,
    ) -> None:
        """失效成功：调用 DEL"""
        await cache.invalidate(SAMPLE_JOB_ID)

        mock_redis.delete.assert_awaited_once()

    async def test_invalidate_redis_error_does_not_raise(
        self,
        cache,
        mock_redis,
    ) -> None:
        """Redis 异常时不抛异常（fail-open）"""
        mock_redis.delete.side_effect = ConnectionError("Redis 连接断开")

        # 不应抛异常
        await cache.invalidate(SAMPLE_JOB_ID)

    async def test_invalidate_uses_correct_key(
        self,
        cache,
        mock_redis,
    ) -> None:
        """使用正确的 Redis key 格式"""
        await cache.invalidate(SAMPLE_JOB_ID)

        call_args = mock_redis.delete.call_args
        key = call_args[0][0]
        assert f"job:analysis:{SAMPLE_JOB_ID}" == key


# ==================== TTL ====================


class TestJobAnalysisCacheTTL:
    """TTL 配置测试"""

    async def test_default_ttl(
        self,
        mock_redis,
    ) -> None:
        """默认 TTL 从 settings 读取"""
        from app.infra.cache.job_analysis import RedisJobAnalysisCache

        with patch("app.infra.cache.job_analysis.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(job_analysis_cache_ttl_seconds=7200)
            cache = RedisJobAnalysisCache(redis=mock_redis)

        assert cache.ttl_seconds == 7200

    async def test_custom_ttl(
        self,
        mock_redis,
    ) -> None:
        """自定义 TTL 覆盖 settings"""
        from app.infra.cache.job_analysis import RedisJobAnalysisCache

        cache = RedisJobAnalysisCache(redis=mock_redis, ttl_seconds=1800)

        assert cache.ttl_seconds == 1800
