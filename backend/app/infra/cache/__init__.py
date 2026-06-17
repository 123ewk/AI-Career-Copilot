"""Infra 缓存层

职责：
- 提供各业务域的缓存实现（Redis / Memcached / 多级缓存等）
- 隔离「缓存协议」与「缓存实现」：domain 层只 import Protocol，不接触具体技术

当前实现：
- RedisResumeCache: 基于 Redis 的 Resume active 缓存
- 后续可扩展：JobCache / SessionCache / 等

设计动机：
- 与 infra/repositories/ 平级：仓库是「数据库访问层」，缓存是「高性能数据访问层」
- 共享 settings / logger / 客户端工厂，避免重复连接管理
"""

from app.infra.cache.resume import RedisResumeCache

__all__ = ["RedisResumeCache"]
