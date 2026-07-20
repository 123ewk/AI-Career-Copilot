"""Conversation 仓储抽象接口（Domain 层）

职责：
- 定义 Conversation 仓储的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/repositories/conversation_repo.py 中的 ConversationRepository 实现
- Domain Service 仅依赖本 Protocol，便于替换 ORM 或测试时 mock

设计动机：
- 与 JobRepositoryProtocol 保持一致的依赖倒置模式
- sync_messages 按 (user_id, job_id, recruiter_name) 查找或创建，
  支持 DOM 全量快照的幂等同步语义
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.infra.database.models.conversation import Conversation


@runtime_checkable
class ConversationRepositoryProtocol(Protocol):
    """Conversation 仓储接口

    所有方法均为 async：调用方必须 await
    不调用 commit/rollback：让 Service 层控制事务边界
    异常透传：IntegrityError / OperationalError 等由调用方 / 中间件统一处理
    """

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        recruiter_name: str,
        job_id: uuid.UUID | None = None,
        recruiter_id: str | None = None,
        channel: str = "boss",
        messages: list[dict] | None = None,
    ) -> Conversation:
        """创建对话记录

        Args:
            user_id: 用户ID
            recruiter_name: 招聘方姓名
            job_id: 关联岗位ID（可选）
            recruiter_id: BOSS 平台招聘方用户ID（可选）
            channel: 渠道标识
            messages: 初始消息列表

        Returns:
            新创建的 Conversation ORM 对象（已 flush）
        """
        ...

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """按主键查询对话，未找到返回 None"""
        ...

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Conversation]:
        """分页查询用户的对话列表

        默认按 last_message_at 倒序（最近活跃的在前）。
        """
        ...

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """统计用户对话总数"""
        ...

    async def get_by_job_and_recruiter(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None,
        recruiter_name: str,
    ) -> Conversation | None:
        """按 (user_id, job_id, recruiter_name) 查找对话（幂等同步用）

        job_id 为 None 时按 (user_id, recruiter_name) 匹配。
        """
        ...

    async def update_messages(
        self,
        conversation: Conversation,
        messages: list[dict],
        *,
        last_message_at: str | None = None,
    ) -> Conversation:
        """全量覆盖消息列表（DOM 快照语义）

        Args:
            conversation: 已加载的 Conversation 实例
            messages: 最新的消息列表
            last_message_at: 最后消息时间（ISO 格式字符串）

        Returns:
            更新后的 Conversation 实例
        """
        ...

    async def delete(self, conversation: Conversation) -> None:
        """物理删除对话"""
        ...

    async def delete_by_id(self, conversation_id: uuid.UUID) -> bool:
        """按 ID 删除对话，未找到返回 False"""
        ...


__all__ = ["ConversationRepositoryProtocol"]
