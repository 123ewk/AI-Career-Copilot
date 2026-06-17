"""Resume 仓储抽象接口（Domain 层）

职责：
- 定义 Resume 仓储的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/repositories/resume_repo.py 中的 ResumeRepository 实现
- Domain Service / UseCase 仅依赖本 Protocol，便于替换 ORM 或测试时 mock

设计动机：
- 依赖倒置：业务层（domain）不依赖基础设施层（infra）的具体实现
  → Service 层只 import Protocol，不知道底层用 SQLAlchemy 还是其他 ORM
- 易于测试：单元测试可以传一个 FakeResumeRepository 实现 Protocol
  → 不必拉起真实数据库即可测试 ResumeService 业务逻辑
- 替换 ORM 的成本最小：未来切到 SQLModel / Tortoise ORM 时
  → 只需新写一个实现类，Service 层零改动

Protocol vs ABC 选择：
- 选 Protocol（结构化子类型）：不强制继承，duck typing
  → ResumeRepository 即使没显式声明 implements Protocol，Type Checker 仍能识别
  → 与 Python "ask forgiveness not permission" 哲学一致
- ABC（名义子类型）：需要显式继承 + @abstractmethod
  → 优点：运行时 isinstance 检查；缺点：增加耦合
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.infra.database.models.resume import Resume


# 装饰器的唯一功能：给 Protocol 开启运行时动态校验能力，允许你在代码执行阶段，用 isinstance(obj, 协议类) 判断对象是否具备协议要求的所有方法 / 属性。
# 运行时只会检查两点，不校验方法参数、返回值类型（类型只在静态阶段校验）：
# 对象是否拥有协议定义的全部方法名；
# 对象是否拥有协议定义的全部属性。
@runtime_checkable
class ResumeRepositoryProtocol(Protocol):
    """Resume 仓储接口

    所有方法均为 async：调用方必须 await
    不调用 commit/rollback：让 Service / Router 控制事务边界
    异常透传：IntegrityError / OperationalError 等由调用方 / 中间件统一处理
    """

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

        Args:
            user_id: 所属用户 UUID
            raw_text: 简历原文（PDF/DOCX 解析后纯文本）
            structured_data: 结构化数据（教育/工作/项目），默认空 dict
            skills: 技能列表，默认空列表
            experience_years: 工作年限，None 表示未知
            is_active: 是否设为当前活跃简历，默认为 True

        Returns:
            新创建的 Resume ORM 对象（已 flush，可安全访问 id/created_at）

        Raises:
            IntegrityError: 同用户重复设置 is_active=True 触发部分唯一索引冲突
        """
        ...

    async def get_by_id(self, resume_id: uuid.UUID) -> Resume | None:
        """按主键查询简历，未找到返回 None"""
        ...

    async def get_active_by_user(self, user_id: uuid.UUID) -> Resume | None:
        """查询指定用户的当前活跃简历

        走 uq_resumes_user_active 部分唯一索引（user_id, is_active=TRUE），
        同一用户最多一条 is_active=True 的记录，未找到返回 None
        """
        ...

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Resume]:
        """分页查询指定用户的所有简历

        默认按 created_at 倒序（最新上传的在前），走 ix_resumes_user_id + ix_resumes_created_at
        """
        ...

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """统计指定用户的简历总数"""
        ...

    async def update(
        self,
        resume: Resume,
        *,
        structured_data: dict | list | None = None,
        skills: list[str] | None = None,
        experience_years: int | None = None,
    ) -> Resume:
        """更新简历内容（不含 is_active）

        用于 Agent 解析完成后回填结构化数据，或用户手动编辑技能/年限。
        raw_text 不允许通过此方法修改（需走"重新上传"流程）。

        未传字段保持原值（None 哨兵区分"不更新"和"清空"语义：
        structured_data 不允许清空，skills / experience_years 可传 None 显式清空）
        """
        ...

    async def set_active(self, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
        """切换用户的活跃简历（"更换简历"）

        语义：
        - 将该用户所有 is_active=True 的记录置为 False
        - 将指定 resume_id 记录的 is_active 置为 True
        - 由数据库部分唯一索引 uq_resumes_user_active 保证同用户最多一条活跃

        Args:
            user_id: 用户 UUID
            resume_id: 目标简历 UUID（必须属于该用户，否则 raise）

        Returns:
            切换后的 Resume 实例（is_active=True）

        Raises:
            ValueError: resume_id 不属于该用户
        """
        ...

    async def delete(self, resume: Resume) -> None:
        """物理删除简历（非软删除）"""
        ...

    async def delete_by_id(self, resume_id: uuid.UUID) -> bool:
        """按 ID 删除简历，未找到返回 False"""
        ...


__all__ = ["ResumeRepositoryProtocol"]
