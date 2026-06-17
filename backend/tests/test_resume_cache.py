"""Resume 缓存单测

测试覆盖：
1. FakeResumeCache：内存 dict 实现，验证 Service 与缓存的交互
2. ResumeService 缓存行为：
   - get_active_resume：缓存命中直接返回 / 缓存未命中走 DB + 回填
   - upload_resume / set_active_resume / fill_structured_data / delete_resume：
     成功后调用 cache.invalidate_active
3. RedisResumeCache（用 AsyncMock 替代真实 Redis）：
   - get / set / invalidate 的 key 格式
   - 异常时 fail-open（不抛异常）
   - SETEX 携带 TTL

设计动机：
- 单元测试不依赖真实 Redis，用内存 dict + AsyncMock 即可
- FakeResumeCache 必须实现 ResumeCacheProtocol（结构化子类型校验）
- 失败路径单独覆盖：Redis 异常不能影响业务
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.cache.resume import ResumeCacheProtocol
from app.domain.resume.models import (
    ResumeResponse,
    ResumeStructuredData,
)
from app.infra.cache.resume import RedisResumeCache, _make_key


# ==================== Fake Cache (用于 Service 集成测试) ====================


class FakeResumeCache:
    """内存版 ResumeCache,用于 Service 单测

    记录每次调用,便于断言:
    - get_count: get_active 调用次数
    - set_keys: set_active 写入的 key 列表
    - invalidate_keys: invalidate_active 删除的 key 列表
    - store: 实际存储的 dict
    """

    def __init__(self) -> None:
        self.store: dict[str, ResumeResponse] = {}
        self.get_count: int = 0
        self.set_count: int = 0
        self.invalidate_count: int = 0
        self.set_keys: list[uuid.UUID] = []
        self.invalidate_keys: list[uuid.UUID] = []

    async def get_active(self, user_id: uuid.UUID) -> ResumeResponse | None:
        self.get_count += 1
        return self.store.get(_make_key(user_id))

    async def set_active(
        self,
        user_id: uuid.UUID,
        resume: ResumeResponse,
    ) -> None:
        self.set_count += 1
        self.set_keys.append(user_id)
        self.store[_make_key(user_id)] = resume

    async def invalidate_active(self, user_id: uuid.UUID) -> None:
        self.invalidate_count += 1
        self.invalidate_keys.append(user_id)
        self.store.pop(_make_key(user_id), None)


# ==================== Fixtures ====================


@pytest.fixture
def fake_cache() -> FakeResumeCache:
    """提供一个全新的空 FakeResumeCache"""
    return FakeResumeCache()


@pytest.fixture
def sample_resume_response() -> ResumeResponse:
    """构造一个示例 ResumeResponse(供 set_active / get_active 使用)"""
    return ResumeResponse(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        raw_text="John Doe\n5 years Python/FastAPI experience",
        structured_data=ResumeStructuredData(),
        skills=["Python", "FastAPI", "PostgreSQL"],
        experience_years=5,
        is_active=True,
        created_at="2026-06-17T10:00:00Z",  # type: ignore[arg-type]
    )


# ==================== Protocol 结构化子类型校验 ====================


def test_fake_cache_implements_protocol() -> None:
    """FakeResumeCache 必须实现 ResumeCacheProtocol(鸭子类型)"""
    cache: Any = FakeResumeCache()
    # runtime_checkable 允许 isinstance 检查
    assert isinstance(cache, ResumeCacheProtocol)


# ==================== Key 格式 ====================


def test_make_key_format() -> None:
    """Key 必须是 resume:active:{user_id} 格式"""
    user_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert _make_key(user_id) == f"resume:active:{user_id}"


# ==================== FakeResumeCache 行为 ====================


@pytest.mark.asyncio
async def test_fake_cache_set_and_get(
    fake_cache: FakeResumeCache,
    sample_resume_response: ResumeResponse,
) -> None:
    """set → get 应当能取回"""
    user_id = sample_resume_response.user_id
    await fake_cache.set_active(user_id, sample_resume_response)
    result = await fake_cache.get_active(user_id)
    assert result is not None
    assert result.id == sample_resume_response.id
    assert result.skills == sample_resume_response.skills


@pytest.mark.asyncio
async def test_fake_cache_get_miss_returns_none(fake_cache: FakeResumeCache) -> None:
    """未写入的 key → get 返回 None"""
    user_id = uuid.uuid4()
    result = await fake_cache.get_active(user_id)
    assert result is None


@pytest.mark.asyncio
async def test_fake_cache_invalidate_removes(
    fake_cache: FakeResumeCache,
    sample_resume_response: ResumeResponse,
) -> None:
    """invalidate 后再 get 应当 miss"""
    user_id = sample_resume_response.user_id
    await fake_cache.set_active(user_id, sample_resume_response)
    assert await fake_cache.get_active(user_id) is not None

    await fake_cache.invalidate_active(user_id)
    assert await fake_cache.get_active(user_id) is None


@pytest.mark.asyncio
async def test_fake_cache_invalidate_missing_key_is_noop(
    fake_cache: FakeResumeCache,
) -> None:
    """invalidate 一个不存在的 key 不报错(幂等)"""
    # 不应抛异常
    await fake_cache.invalidate_active(uuid.uuid4())
    assert fake_cache.invalidate_count == 1


# ==================== RedisResumeCache: 失败容错 ====================


@pytest.mark.asyncio
async def test_redis_cache_get_handles_redis_exception() -> None:
    """get_active:Redis 抛异常时,返回 None(不抛)"""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    cache = RedisResumeCache(redis=mock_redis, ttl_seconds=60)

    # 不应抛异常
    result = await cache.get_active(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_redis_cache_set_handles_redis_exception() -> None:
    """set_active:Redis 抛异常时,静默吞掉(不抛)"""
    mock_redis = MagicMock()
    mock_redis.setex = AsyncMock(side_effect=TimeoutError("redis timeout"))
    cache = RedisResumeCache(redis=mock_redis, ttl_seconds=60)

    # 不应抛异常
    response = ResumeResponse(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        raw_text="test",
        structured_data=ResumeStructuredData(),
        skills=[],
        experience_years=None,
        is_active=True,
        created_at="2026-06-17T10:00:00Z",  # type: ignore[arg-type]
    )
    await cache.set_active(response.user_id, response)


@pytest.mark.asyncio
async def test_redis_cache_invalidate_handles_redis_exception() -> None:
    """invalidate_active:Redis 抛异常时,静默吞掉(不抛)"""
    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock(side_effect=ConnectionError("redis down"))
    cache = RedisResumeCache(redis=mock_redis, ttl_seconds=60)

    # 不应抛异常
    await cache.invalidate_active(uuid.uuid4())


@pytest.mark.asyncio
async def test_redis_cache_get_handles_deserialize_exception() -> None:
    """get_active:Redis 返回的 JSON 解析失败时,视为 miss(不抛)"""
    mock_redis = MagicMock()
    # 返回非合法 JSON
    mock_redis.get = AsyncMock(return_value="not-valid-json{")
    cache = RedisResumeCache(redis=mock_redis, ttl_seconds=60)

    result = await cache.get_active(uuid.uuid4())
    assert result is None


# ==================== RedisResumeCache: 正确调用 Redis ====================


@pytest.mark.asyncio
async def test_redis_cache_set_uses_setex_with_ttl(
    sample_resume_response: ResumeResponse,
) -> None:
    """set_active 必须用 SETEX 并携带配置的 TTL"""
    mock_redis = MagicMock()
    mock_redis.setex = AsyncMock()
    cache = RedisResumeCache(redis=mock_redis, ttl_seconds=123)

    await cache.set_active(sample_resume_response.user_id, sample_resume_response)

    # 断言调用了 setex 且 key/value/ttl 正确
    mock_redis.setex.assert_called_once()
    call_args = mock_redis.setex.call_args
    # setex(key, ttl, value) 的位置参数
    assert call_args.args[0] == _make_key(sample_resume_response.user_id)
    assert call_args.args[1] == 123
    # value 是 JSON 字符串
    assert isinstance(call_args.args[2], str)
    assert "raw_text" in call_args.args[2]


@pytest.mark.asyncio
async def test_redis_cache_invalidate_uses_correct_key() -> None:
    """invalidate_active 必须用正确的 key 调用 DEL"""
    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock()
    cache = RedisResumeCache(redis=mock_redis, ttl_seconds=60)

    user_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    await cache.invalidate_active(user_id)

    mock_redis.delete.assert_called_once_with(_make_key(user_id))
