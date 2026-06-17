"""Resume Repository（异步 PostgreSQL 仓储）

职责：
- 封装 resumes 表的 CRUD 操作，对 Domain Service 层提供统一的数据访问入口
- 仅做数据访问，不做业务校验、不抛业务异常（让 SQLAlchemy 异常冒泡到中间件）
- 不自动 commit：事务边界由 Service / Router 层显式控制

实现契约：
- 实现 domain/repositories/resume.py 中的 ResumeRepositoryProtocol
  → Domain Service 只依赖 Protocol，不接触 SQLAlchemy
  → 单元测试可替换为 FakeResumeRepository，无需拉起真实数据库

设计动机：
- Repository 模式隔离 ORM 细节：Service 层不直接接触 SQLAlchemy，
  未来切换 ORM 框架（如换成 SQLModel / Tortoise）只改这一层
- 异步 IO：避免 DB 查询阻塞 Event Loop，配合 asyncpg 实现高并发
- 关键字参数：避免 `create(user_id, raw_text, ...)` 这种位置参数陷阱
  （命名参数在签名变动时不会静默错位）
- "is_active 单调不变量"：通过 create / set_active 两个入口的内部协作，
  保证同用户最多一条 is_active=True，由数据库部分唯一索引兜底

SQL 索引使用（参考 resume.py ORM 索引设计）：
- get_by_id：走主键索引
- get_active_by_user：走 uq_resumes_user_active 部分唯一索引（user_id, is_active=TRUE），O(log N)
- list_by_user：走 ix_resumes_user_id + ix_resumes_created_at
- count_by_user：走全表扫描（PG 优化器在数据量大时可能 seq scan）

潜在风险：
- 业务层忘记 commit：数据写入 session 但未持久化
  → 防御：Service 层在 create/update/delete 后必须 await session.commit()
- is_active 唯一性：并发场景下两个请求可能同时设置活跃简历
  → 防御：set_active 内部先批量 UPDATE is_active=False 再 UPDATE is_active=True
  → 防御：数据库 uq_resumes_user_active 部分唯一索引兜底（违反时抛 IntegrityError）
- 跨 session 访问：ORM 对象绑定到 session A，跨 session 访问属性会触发 lazy load
  → 防御：PgSessionFactory 已设置 expire_on_commit=False
- 大结构化数据：structured_data 可能数百 KB，频繁读取会拖慢列表接口
  → 防御：list_by_user 走 Summary 投影的场景应在 Service 层用 selectinload 或独立 query
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.database.models.resume import Resume


class ResumeRepository:
    """Resume 仓储

    使用方式：
        session = pg_session_factory.create_session()
        repo = ResumeRepository(session)
        resume = await repo.create(user_id=..., raw_text=...)
        await session.commit()
        await session.close()

    设计原则：
    - 构造时注入 AsyncSession，单次请求共用同一个 session（事务一致性）
    - 所有方法均为 async，调用方必须 await
    - 不调用 commit/rollback：让 Service / Router 控制事务边界
    - 异常透传：IntegrityError / OperationalError 等由中间件统一处理
    - 实现 ResumeRepositoryProtocol（结构化子类型，无需显式继承）
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ==================== Create ====================

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        raw_text: str,
        structured_data: dict | list | None = None,
        skills: list[str] | None = None,
        experience_years: int | None = None,
        is_active: bool = True,
    ) -> Resume:
        """上传简历（创建一条简历记录）

        行为：
        - raw_text 必填，由调用方保证已校验非空
        - 可选字段默认为 None（与 ORM nullable 字段对应）
        - structured_data 默认空 dict {}，skills 默认空列表 []
        - 主键 id 由 ORM default=uuid.uuid4 + 数据库 server_default=gen_random_uuid() 兜底
        - created_at 由数据库 server_default=now() 自动填充
        - 调用 session.flush() 而非 commit：让 Service 层控制事务边界

        is_active 特殊处理：
        - 当 is_active=True 时，自动将该用户其余 is_active=True 的记录置为 False
        - 避免触发数据库部分唯一索引 uq_resumes_user_active
        - 整个操作在调用方 session 的事务内原子完成

        Args:
            user_id: 所属用户 UUID
            raw_text: 简历原文（PDF/DOCX 解析后纯文本）
            structured_data: 结构化数据（教育/工作/项目），None 视为 {}
            skills: 技能列表，None 视为 []
            experience_years: 工作年限，None 表示未知
            is_active: 是否设为当前活跃简历，默认为 True

        Returns:
            新创建的 Resume ORM 对象（已 flush，可安全访问 id/created_at）

        Raises:
            IntegrityError: 极端并发场景下仍可能冲突，由调用方 / 中间件处理
        """
        if is_active:
            # 同事务内批量取消该用户其余活跃简历，避免部分唯一索引冲突
            await self._session.execute(
                update(Resume)
                .where(Resume.user_id == user_id, Resume.is_active.is_(True))
                .values(is_active=False)
            )

        resume = Resume(
            id=uuid.uuid4(),
            user_id=user_id,
            raw_text=raw_text,
            structured_data=structured_data if structured_data is not None else {},
            skills=skills if skills is not None else [],
            experience_years=experience_years,
            is_active=is_active,
        )
        self._session.add(resume)
        await self._session.flush()
        return resume

    # ==================== Read ====================

    async def get_by_id(self, resume_id: uuid.UUID) -> Resume | None:
        """按主键查询简历

        使用 Session.get() 而非 select(Resume).where(id=?)：
        - Session.get() 优先从 identity map 取（同一 session 内已加载的实例），避免重复查询
        - 走主键索引 O(1)
        - 未找到时直接返回 None

        Args:
            resume_id: 简历 UUID

        Returns:
            Resume 实例，未找到返回 None
        """
        return await self._session.get(Resume, resume_id)

    async def get_active_by_user(self, user_id: uuid.UUID) -> Resume | None:
        """查询指定用户的当前活跃简历

        走 uq_resumes_user_active 部分唯一索引（user_id, is_active=TRUE）：
        - 同一用户最多一条 is_active=True
        - 部分索引在 WHERE is_active=TRUE 时才生效，PG 查询计划器可识别

        Args:
            user_id: 用户 UUID

        Returns:
            活跃 Resume 实例，未找到返回 None
        """
        stmt = select(Resume).where(
            Resume.user_id == user_id,
            Resume.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Resume]:
        """分页查询指定用户的所有简历

        默认按 created_at 倒序（最新上传的在前），符合列表展示习惯。
        走 ix_resumes_user_id + ix_resumes_created_at 复合索引。

        Args:
            user_id: 用户 UUID
            limit: 每页大小，默认 20
            offset: 偏移量，默认 0

        Returns:
            Resume 序列（空列表表示无数据）
        """
        if limit <= 0:
            return []
        stmt = (
            select(Resume)
            .where(Resume.user_id == user_id)
            .order_by(Resume.created_at.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """统计指定用户的简历总数

        走 ix_resumes_user_id 索引（虽然 PG 在大表上仍可能回退 seq scan，
        简历量级（每人几条到几十条）通常很低，性能可接受）。

        Args:
            user_id: 用户 UUID

        Returns:
            简历总数
        """
        stmt = select(func.count(Resume.id)).where(Resume.user_id == user_id)
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    # ==================== Update ====================

    async def update(
        self,
        resume: Resume,
        *,
        structured_data: dict | list | None = None,
        skills: list[str] | None = None,
        experience_years: int | None = None,
    ) -> Resume:
        """更新简历内容（不含 is_active / raw_text）

        行为：
        - 使用 ORM 属性赋值：SQLAlchemy 自动检测 dirty，flush 时生成 UPDATE SQL
        - None 哨兵语义：参数为 None 时跳过该字段（"不更新"）
        - raw_text 不允许通过此方法修改（需走"重新上传"流程）
        - is_active 走 set_active 方法，事务一致性更强

        字段更新约定：
        - structured_data: None=不更新，传入 dict/list 即覆盖
        - skills: None=不更新，传入 list 即覆盖（空列表 [] 表示清空）
        - experience_years: None=不更新，传入 int 即覆盖

        Args:
            resume: 已加载的 Resume 实例
            structured_data: 新结构化数据
            skills: 新技能列表
            experience_years: 新工作年限

        Returns:
            更新后的 Resume 实例
        """
        if structured_data is not None:
            resume.structured_data = structured_data
        if skills is not None:
            resume.skills = skills
        if experience_years is not None:
            resume.experience_years = experience_years
        await self._session.flush()
        return resume

    async def set_active(self, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
        """切换用户的活跃简历（"更换简历"）

        语义：
        - 验证 resume_id 必须属于 user_id（防越权）
        - 批量 UPDATE 该用户所有 is_active=True 的记录为 False
        - UPDATE 指定 resume_id 记录的 is_active 为 True
        - 整个操作在调用方 session 事务内原子完成
        - 由数据库 uq_resumes_user_active 部分唯一索引兜底并发安全

        实现顺序：先批量 deactivate 再 activate。
        - 即使目标简历本身就是 active，先 deactivate 再 activate 也是无副作用的
        - 避免在 SQLAlchemy 1.x 中"先 SELECT 再 UPDATE" 的额外往返

        Args:
            user_id: 用户 UUID
            resume_id: 目标简历 UUID（必须属于该用户）

        Returns:
            切换后的 Resume 实例（is_active=True）

        Raises:
            ValueError: resume_id 不存在或不属于该用户
        """
        resume = await self._session.get(Resume, resume_id)
        if resume is None or resume.user_id != user_id:
            raise ValueError(
                f"简历 {resume_id} 不存在或不属于用户 {user_id}"
            )

        # 取消该用户其余活跃简历
        await self._session.execute(
            update(Resume)
            .where(Resume.user_id == user_id, Resume.is_active.is_(True))
            .values(is_active=False)
        )
        # 激活目标简历
        resume.is_active = True
        await self._session.flush()
        return resume

    # ==================== Delete ====================

    async def delete(self, resume: Resume) -> None:
        """物理删除简历（非软删除）

        行为：
        - 物理删除：PRD 暂未要求保留审计场景
        - 若简历为该用户唯一活跃记录，删除后该用户将没有活跃简历
          （这是预期的"用户主动删除"语义）
        - Resume 无外键关系：级联删除依赖 ORM relationship 配置，
          若未来 Resume 关联 Application/Analysis 等，需在 Service 层先删子表

        Args:
            resume: 已加载的 Resume 实例
        """
        await self._session.delete(resume)
        await self._session.flush()

    async def delete_by_id(self, resume_id: uuid.UUID) -> bool:
        """按 ID 删除简历

        便利方法：先 get 再 delete，未找到返回 False。
        等价于：
            resume = await self.get_by_id(resume_id)
            if resume is None: return False
            await self.delete(resume)
            return True

        Args:
            resume_id: 简历 UUID

        Returns:
            True 表示删除成功，False 表示简历不存在
        """
        resume = await self.get_by_id(resume_id)
        if resume is None:
            return False
        await self.delete(resume)
        return True


__all__ = ["ResumeRepository"]
