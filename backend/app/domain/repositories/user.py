"""User 仓储抽象接口（Domain 层）

职责：
- 定义 User 仓储的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/repositories/user_repo.py 中的 UserRepository 实现
- Domain Service / UseCase 仅依赖本 Protocol，便于替换 ORM 或测试时 mock

设计动机：
- 依赖倒置：业务层（domain）不依赖基础设施层（infra）的具体实现
  → Service 层只 import Protocol，不知道底层用 SQLAlchemy 还是其他 ORM
- 易于测试：单元测试可以传一个 FakeUserRepository 实现 Protocol
  → 不必拉起真实数据库即可测试 UserService 业务逻辑
- 替换 ORM 的成本最小：未来切到 SQLModel / Tortoise ORM 时
  → 只需新写一个实现类，Service 层零改动

Protocol vs ABC 选择：
- 选 Protocol（结构化子类型）：不强制继承，duck typing
  → UserRepository 即使没显式声明 implements Protocol，Type Checker 仍能识别
  → 与 Python "ask forgiveness not permission" 哲学一致
- ABC（名义子类型）：需要显式继承 + @abstractmethod
  → 优点：运行时 isinstance 检查；缺点：增加耦合
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.infra.database.models.user import User

# 装饰器的唯一功能：给 Protocol 开启运行时动态校验能力，允许你在代码执行阶段，用 isinstance(obj, 协议类) 判断对象是否具备协议要求的所有方法 / 属性。
# 运行时只会检查两点，不校验方法参数、返回值类型（类型只在静态阶段校验）：
# 对象是否拥有协议定义的全部方法名；
# 对象是否拥有协议定义的全部属性。
@runtime_checkable
class UserRepositoryProtocol(Protocol):
    """User 仓储接口

    所有方法均为 async：调用方必须 await
    不调用 commit/rollback：让 Service / Router 控制事务边界
    异常透传：IntegrityError / OperationalError 等由调用方 / 中间件统一处理
    """

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

        Args:
            email: 已归一化为小写的邮箱
            password_hash: bcrypt 哈希值
            name: 姓名，可选
            target_position: 目标岗位，可选
            target_industry: 目标行业，可选

        Returns:
            新创建的 User ORM 对象（已 flush，可安全访问 id/created_at）

        Raises:
            IntegrityError: email 唯一约束冲突（重复注册）
        """
        ...

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """按主键查询用户，未找到返回 None"""
        ...

    async def get_by_email(self, email: str) -> User | None:
        """按邮箱查询用户（登录/重置密码场景），未找到返回 None"""
        ...

    async def exists_by_email(self, email: str) -> bool:
        """检查邮箱是否已存在（注册前的快速校验）"""
        ...

    async def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[User]:
        """分页查询用户列表（按 created_at 倒序）"""
        ...

    async def count(self) -> int:
        """统计用户总数"""
        ...

    async def update_profile(
        self,
        user: User,
        *,
        name: str | None = None,
        target_position: str | None = None,
        target_industry: str | None = None,
    ) -> User:
        """更新用户资料（不含 email / password_hash）"""
        ...

    async def update_password_hash(
        self,
        user: User,
        new_password_hash: str,
    ) -> User:
        """更新用户密码哈希（独立方法便于审计）"""
        ...

    async def delete(self, user: User) -> None:
        """物理删除用户（非软删除）"""
        ...

    async def delete_by_id(self, user_id: uuid.UUID) -> bool:
        """按 ID 删除用户，未找到返回 False"""
        ...


__all__ = ["UserRepositoryProtocol"]
