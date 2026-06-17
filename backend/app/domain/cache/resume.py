"""Resume 缓存抽象接口（Domain 层）

职责：
- 定义 Resume 缓存的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/cache/resume.py 中的 RedisResumeCache 实现
- Domain Service / UseCase 仅依赖本 Protocol，便于替换缓存后端或测试时 mock

设计动机：
- 依赖倒置：业务层（domain）不依赖基础设施层（infra）的具体实现
  → Service 层只 import Protocol，不知道底层用 Redis 还是 Memcached
- 易于测试：单元测试可以传一个 FakeResumeCache（内存 dict）实现 Protocol
  → 不必拉起真实 Redis 即可测试缓存命中/失效/回填逻辑
- 替换缓存后端的成本最小：未来切到 Memcached / 多级缓存时
  → 只需新写一个实现类，Service 层零改动

Protocol vs ABC 选择：
- 选 Protocol（结构化子类型）：不强制继承，duck typing
  → 即使 RedisResumeCache 没显式声明 implements Protocol，Type Checker 仍能识别
  → 与 Python "ask forgiveness not permission" 哲学一致

失败语义约定（重要）：
- 所有方法在 Redis 异常时必须静默吞掉并 logger.warning
- get_active 失败 → 返回 None（视作 miss）
- set_active / invalidate_active 失败 → 不抛异常
- Service 层可以无脑调用，不必额外 try/except
- 这与 rate_limit.py 的 fail-open 策略保持一致
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.domain.resume.models import ResumeResponse


@runtime_checkable
class ResumeCacheProtocol(Protocol):
    """Resume 缓存接口

    实现类必须满足：
    - 所有方法均为 async：调用方必须 await
    - 失败时静默降级：不抛异常、不影响业务
    - 线程/协程安全：可被多请求并发访问

    当前业务范围（Phase 1）：
    - 只缓存「当前活跃简历」（get_active_resume 的返回值）
    - 不缓存 list_by_user（命中率低、失效复杂）
    - 不缓存 get_by_id（越权风险、命中率一般）
    """

    async def get_active(self, user_id: uuid.UUID) -> ResumeResponse | None:
        """获取用户的活跃简历缓存

        Args:
            user_id: 用户 UUID

        Returns:
            命中的 ResumeResponse；未命中或任何异常 → 返回 None
        """
        ...

    async def set_active(
        self,
        user_id: uuid.UUID,
        resume: ResumeResponse,
    ) -> None:
        """写入用户的活跃简历缓存（覆盖已有）

        建议使用 SETEX：写入时同时设置 TTL，避免永久驻留。

        Args:
            user_id: 用户 UUID
            resume: 待缓存的简历响应
        """
        ...

    async def invalidate_active(self, user_id: uuid.UUID) -> None:
        """失效用户的活跃简历缓存

        写操作后调用：让下次 get_active 重新走 DB 加载最新数据。
        幂等：key 不存在也不报错。

        Args:
            user_id: 用户 UUID
        """
        ...


__all__ = ["ResumeCacheProtocol"]
