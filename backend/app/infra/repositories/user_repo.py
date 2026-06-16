"""User Repository（异步 PostgreSQL 仓储）

职责：
- 封装 users 表的 CRUD 操作，对 Domain Service 层提供统一的数据访问入口
- 仅做数据访问，不做业务校验、不抛业务异常（让 SQLAlchemy 异常冒泡到中间件）
- 不自动 commit：事务边界由 Service / Router 层显式控制

实现契约：
- 实现 domain/repositories/user.py 中的 UserRepositoryProtocol
  → Domain Service 只依赖 Protocol，不接触 SQLAlchemy
  → 单元测试可替换为 FakeUserRepository，无需拉起真实数据库

设计动机：
- Repository 模式隔离 ORM 细节：Service 层不直接接触 SQLAlchemy，
  未来切换 ORM 框架（如换成 SQLModel / Tortoise）只改这一层
- 异步 IO：避免 DB 查询阻塞 Event Loop，配合 asyncpg 实现高并发
- 关键字参数：避免 `create(email, password, name, ...)` 这种位置参数陷阱
  （命名参数在签名变动时不会静默错位）

SQL 索引使用（参考 user.py ORM 索引设计）：
- get_by_email / exists_by_email：走 ix_users_email 唯一索引，O(log N)
- get_by_id：走主键索引
- list：默认按 created_at 倒序，走 ix_users_created_at 索引
- count：走全表扫描（PostgreSQL 优化器在 InnoDB 估算不精确时回退 seq scan）
  后续如需精确计数可改用 EXPLAIN / 物化视图 / 缓存估算值

潜在风险：
- 业务层忘记 commit：数据写入 session 但未持久化
  → 防御：Service 层在 create/update/delete 后必须 await session.commit()
  → 防御：Router 层在请求结束时由 get_db_session 的 finally 关闭 session
  → 防御：开发环境开启 echo=True 让 SQL 落到日志便于追踪
- email 唯一冲突：重复注册会触发 IntegrityError
  → 防御：Service 层在 create 前调用 exists_by_email 提前检查
  → 防御：Service 层 catch IntegrityError 翻译为 ConflictError
- 跨 session 访问：ORM 对象绑定到 session A，跨 session 访问属性会触发 lazy load
  → 防御：查询时使用 await session.refresh(obj) 或显式 selectinload
  → 防御：响应 Service 层前调用 session.flush() + expire_on_commit=False
  （PgSessionFactory 已设置 expire_on_commit=False，commit 后属性不失效）
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.user import UserRepositoryProtocol
from app.infra.database.models.user import User


class UserRepository:
    """User 仓储

    使用方式：
        session = pg_session_factory.create_session()
        repo = UserRepository(session)
        user = await repo.create(email="x@y.com", password_hash="...")
        await session.commit()
        await session.close()

    设计原则：
    - 构造时注入 AsyncSession，单次请求共用同一个 session（事务一致性）
    - 所有方法均为 async，调用方必须 await
    - 不调用 commit/rollback：让 Service / Router 控制事务边界
    - 异常透传：IntegrityError / OperationalError 等由中间件统一处理
    - 实现 UserRepositoryProtocol（结构化子类型，无需显式继承）
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ==================== Create ====================

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        name: str | None = None,
        target_position: str | None = None,
        target_industry: str | None = None,
    ) -> User:
        """创建用户

        行为：
        - email / password_hash 必填，由调用方保证已校验格式
        - 可选字段默认为 None，对应 ORM 的 nullable=True
        - 主键 id 由 ORM default=uuid.uuid4 + 数据库 server_default=gen_random_uuid() 兜底
        - created_at / updated_at 由数据库 server_default=now() 自动填充
        - 调用 session.flush() 而非 commit：让 Service 层控制事务边界

        Args:
            email: 已归一化为小写的邮箱（由 validator.py 保证）
            password_hash: bcrypt 哈希值，60 字符左右
            name: 姓名，可选
            target_position: 目标岗位，可选
            target_industry: 目标行业，可选

        Returns:
            新创建的 User ORM 对象（已 flush，可安全访问 id/created_at/updated_at）

        Raises:
            IntegrityError: email 唯一约束冲突（重复注册）
        """
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=password_hash,
            name=name,
            target_position=target_position,
            target_industry=target_industry,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    # ==================== Read ====================

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """按主键查询用户

        使用 Session.get() 而非 select(User).where(id=?)：
        - Session.get() 优先从 identity map 取（同一 session 内已加载的实例），避免重复查询
        - 走主键索引 O(1)
        - 未找到时直接返回 None，不抛异常（与 select().where() 行为一致）

        Args:
            user_id: 用户 UUID

        Returns:
            User 实例，未找到返回 None
        """
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """按邮箱查询用户（登录/重置密码等高频场景）

        走 ix_users_email 唯一索引，O(log N)
        邮箱必须已归一化为小写（由 validator.py 在入口保证），
        本方法不做大小写转换，依赖调用方传入正确格式。

        Args:
            email: 已归一化的邮箱字符串

        Returns:
            User 实例，未找到返回 None
        """
        stmt = select(User).where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_email(self, email: str) -> bool:
        """检查邮箱是否已存在（注册前的快速校验）

        性能优化：
        - 使用 SELECT 1 + LIMIT 1 + exists() 而非 COUNT(*)
        - 数据库提前终止扫描，找到第一行即返回
        - 走唯一索引，最快 O(1)

        与 get_by_email 的选择：
        - 不需要 ORM 对象时用此方法，省去实例化开销
        - 需要 ORM 对象时用 get_by_email，逻辑更清晰

        Args:
            email: 已归一化的邮箱字符串

        Returns:
            True 表示邮箱已被注册，False 表示可用
        """
        stmt = select(User.id).where(User.email == email).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[User]:
        """分页查询用户列表

        默认按 created_at 倒序（最新注册的在前），符合管理后台列表展示习惯。
        走 ix_users_created_at 索引。

        Args:
            limit: 每页大小，默认 20
            offset: 偏移量，默认 0

        Returns:
            User 序列（空列表表示无数据）

        注意：
        - 不返回总数（count 单独调用，避免 SELECT COUNT(*) 的额外开销）
        - 不做权限过滤，调用方负责（管理后台场景）
        """
        if limit <= 0:
            return []
        stmt = (
            select(User)
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count(self) -> int:
        """统计用户总数

        走全表扫描（PG 在大表上可能 seq scan，百万级后建议用估算值或物化视图）。
        通常用于管理后台仪表盘，调用频率低。

        Returns:
            用户总数
        """
        stmt = select(func.count(User.id))
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    # ==================== Update ====================

    async def update_profile(
        self,
        user: User,
        *,
        name: str | None = None,
        target_position: str | None = None,
        target_industry: str | None = None,
    ) -> User:
        """更新用户资料（不包含 email / password_hash）

        email 和 password_hash 的更新走独立方法（update_email / update_password_hash），
        本方法只处理"普通资料"，避免一次调用混用不同安全等级的字段。

        行为：
        - 使用 ORM 属性赋值：SQLAlchemy 自动检测 dirty，flush 时生成 UPDATE SQL
        - 不指定值时跳过该字段（实现为 None sentinel 区分"不更新"和"清空"）

        Args:
            user: 已加载的 User 实例
            name: 新姓名，None 表示不更新
            target_position: 新目标岗位，None 表示不更新
            target_industry: 新目标行业，None 表示不更新

        Returns:
            更新后的 User 实例（updated_at 由 ORM onupdate=datetime.now 刷新）

        注意：
        - 本方法无法"清空"字段（清空和"不更新"都是 None）
        - 如需支持清空，使用 update_profile_with_clear(user, clear_fields=[...])
        """
        if name is not None:
            user.name = name
        if target_position is not None:
            user.target_position = target_position
        if target_industry is not None:
            user.target_industry = target_industry
        await self._session.flush()
        return user

    async def update_password_hash(
        self,
        user: User,
        new_password_hash: str,
    ) -> User:
        """更新用户密码哈希

        独立于 update_profile：
        - 密码更新是高敏感操作，单独方法便于审计（Service 层可记录改密事件）
        - 避免与普通资料更新混用导致意外覆盖

        Args:
            user: 已加载的 User 实例
            new_password_hash: 新的 bcrypt 哈希值

        Returns:
            更新后的 User 实例
        """
        user.password_hash = new_password_hash
        await self._session.flush()
        return user

    # ==================== Delete ====================

    async def delete(self, user: User) -> None:
        """删除用户

        行为：
        - 物理删除（非软删除）：PRD 暂未要求保留审计场景
        - 级联删除依赖 ORM relationship 配置，目前 User 无外键关系
        - 若未来 User 关联 Resume/Application 等，需在 Service 层先删子表

        Args:
            user: 已加载的 User 实例
        """
        await self._session.delete(user)
        await self._session.flush()

    async def delete_by_id(self, user_id: uuid.UUID) -> bool:
        """按 ID 删除用户

        便利方法：先 get 再 delete，未找到返回 False。
        等价于：
            user = await self.get_by_id(user_id)
            if user is None: return False
            await self.delete(user)
            return True

        Args:
            user_id: 用户 UUID

        Returns:
            True 表示删除成功，False 表示用户不存在
        """
        user = await self.get_by_id(user_id)
        if user is None:
            return False
        await self.delete(user)
        return True


__all__ = ["UserRepository"]
