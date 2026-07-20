"""Conversation Repository（异步 PostgreSQL 仓储）

职责：
- 封装 conversations 表的 CRUD 操作
- 仅做数据访问，不做业务校验、不抛业务异常
- 不自动 commit：事务边界由 Service 层显式控制

实现契约：
- 实现 domain/repositories/conversation.py 中的 ConversationRepositoryProtocol

设计动机：
- Repository 模式隔离 ORM 细节
- update_messages 使用全量覆盖（DOM 是快照，非增量）
- get_by_job_and_recruiter 支持幂等同步
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.conversation import ConversationRepositoryProtocol
from app.infra.database.models.conversation import Conversation


class ConversationRepository:
    """Conversation 仓储

    使用方式：
        session = pg_session_factory.create_session()
        repo = ConversationRepository(session)
        conv = await repo.create(user_id=..., recruiter_name=...)
        await session.commit()

    设计原则：
    - 构造时注入 AsyncSession，单次请求共用同一个 session
    - 所有方法均为 async，调用方必须 await
    - 不调用 commit/rollback：让 Service 层控制事务边界
    - 异常透传：IntegrityError / OperationalError 等由中间件统一处理
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ==================== Create ====================

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

        行为：
        - 主键 id 由 ORM default=uuid.uuid4 + 数据库 server_default=gen_random_uuid() 兜底
        - created_at/updated_at 由数据库 server_default=now() 自动填充
        - 调用 session.flush() 而非 commit：让 Service 层控制事务边界
        """
        conversation = Conversation(
            id=uuid.uuid4(),
            user_id=user_id,
            job_id=job_id,
            recruiter_name=recruiter_name,
            recruiter_id=recruiter_id,
            channel=channel,
            messages=messages if messages is not None else [],
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    # ==================== Read ====================

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """按主键查询对话

        使用 Session.get() 优先从 identity map 取，避免重复查询。
        """
        return await self._session.get(Conversation, conversation_id)

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Conversation]:
        """分页查询用户的对话列表

        默认按 last_message_at 倒序（最近活跃的在前），
        last_message_at 为 NULL 的排最后。
        走 ix_conversations_user_id 索引。
        """
        if limit <= 0:
            return []
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_message_at.desc().nullslast())
            .limit(limit)
            .offset(max(0, offset))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """统计用户对话总数"""
        stmt = select(func.count(Conversation.id)).where(
            Conversation.user_id == user_id
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_by_job_and_recruiter(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID | None,
        recruiter_name: str,
    ) -> Conversation | None:
        """按 (user_id, job_id, recruiter_name) 查找对话（幂等同步用）

        走 ix_conversations_user_job 复合索引。
        job_id 为 None 时按 (user_id, recruiter_name) 匹配。
        """
        conditions = [
            Conversation.user_id == user_id,
            Conversation.recruiter_name == recruiter_name,
        ]
        if job_id is not None:
            conditions.append(Conversation.job_id == job_id)
        else:
            conditions.append(Conversation.job_id.is_(None))

        stmt = select(Conversation).where(*conditions)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ==================== Update ====================

    async def update_messages(
        self,
        conversation: Conversation,
        messages: list[dict],
        *,
        last_message_at: str | None = None,
    ) -> Conversation:
        """全量覆盖消息列表（DOM 快照语义）

        行为：
        - 使用 ORM 属性赋值：SQLAlchemy 自动检测 dirty，flush 时生成 UPDATE SQL
        - messages 全量覆盖而非 append：DOM 提取是快照，非增量
        - updated_at 由 ORM event 自动更新（如果配置了）或手动设置

        Args:
            conversation: 已加载的 Conversation 实例
            messages: 最新的消息列表
            last_message_at: 最后消息时间（ISO 格式字符串）

        Returns:
            更新后的 Conversation 实例
        """
        conversation.messages = messages
        if last_message_at is not None:
            conversation.last_message_at = datetime.fromisoformat(last_message_at)
        conversation.updated_at = datetime.now()
        await self._session.flush()
        return conversation

    # ==================== Delete ====================

    async def delete(self, conversation: Conversation) -> None:
        """物理删除对话"""
        await self._session.delete(conversation)
        await self._session.flush()

    async def delete_by_id(self, conversation_id: uuid.UUID) -> bool:
        """按 ID 删除对话，未找到返回 False"""
        conversation = await self.get_by_id(conversation_id)
        if conversation is None:
            return False
        await self.delete(conversation)
        return True


__all__ = ["ConversationRepository"]
