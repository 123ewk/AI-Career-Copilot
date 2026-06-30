"""Job 分析结果缓存抽象接口（Domain 层）

职责：
- 定义 Job 分析结果缓存的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/cache/job_analysis.py 中的 RedisJobAnalysisCache 实现
- Domain Service / UseCase 仅依赖本 Protocol，便于替换缓存后端或测试时 mock

设计动机：
- 依赖倒置：业务层（domain）不依赖基础设施层（infra）的具体实现
- 易于测试：单元测试可以传一个 FakeJobAnalysisCache（内存 dict）实现 Protocol
- 与 ResumeCacheProtocol 保持一致的分层模式

失败语义约定（重要）：
- 所有方法在 Redis 异常时必须静默吞掉并 logger.warning
- get 失败 → 返回 None（视作 miss）
- set 失败 → 不抛异常
- Service 层可以无脑调用，不必额外 try/except
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.domain.job.models import JobAnalysisResult


@runtime_checkable
class JobAnalysisCacheProtocol(Protocol):
    """Job 分析结果缓存接口

    实现类必须满足：
    - 所有方法均为 async：调用方必须 await
    - 失败时静默降级：不抛异常、不影响业务
    - 线程/协程安全：可被多请求并发访问
    """

    async def get(self, job_id: uuid.UUID) -> JobAnalysisResult | None:
        """获取岗位分析结果缓存

        Args:
            job_id: 岗位 UUID

        Returns:
            命中的 JobAnalysisResult；未命中或任何异常 → 返回 None
        """
        ...

    async def set(
        self,
        job_id: uuid.UUID,
        analysis: JobAnalysisResult,
        ttl_seconds: int | None = None,
    ) -> None:
        """写入岗位分析结果缓存

        建议使用 SETEX：写入时同时设置 TTL，避免永久驻留。

        Args:
            job_id: 岗位 UUID
            analysis: JobAnalysisResult 实例
            ttl_seconds: 缓存过期时间（秒）。None 时由实现类决定默认 TTL
        """
        ...

    async def invalidate(self, job_id: uuid.UUID) -> None:
        """删除岗位分析结果缓存

        用于 force=True 重新分析时主动失效缓存。
        幂等：key 不存在也不报错。

        Args:
            job_id: 岗位 UUID
        """
        ...


__all__ = ["JobAnalysisCacheProtocol"]
