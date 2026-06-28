"""Job Repository（异步 PostgreSQL 仓储）

职责：
- 封装 jobs 表的 CRUD 操作，对 Domain Service 层提供统一的数据访问入口
- 仅做数据访问，不做业务校验、不抛业务异常
- 不自动 commit：事务边界由 Service / Router 层显式控制

实现契约：
- 实现 domain/repositories/job.py 中的 JobRepositoryProtocol
- 与 ResumeRepository 保持一致的设计模式

设计动机：
- Repository 模式隔离 ORM 细节
- JSONB 搜索使用 PostgreSQL @> 包含操作符，配合 GIN 索引
- analysis_result 更新使用 None 哨兵语义（与 Resume.update 一致）
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.job import JobRepositoryProtocol
from app.infra.database.models.job import Job


class JobRepository:
    """Job 仓储

    使用方式：
        session = pg_session_factory.create_session()
        repo = JobRepository(session)
        job = await repo.create(title=..., company=..., ...)
        await session.commit()

    设计原则：
    - 构造时注入 AsyncSession，单次请求共用同一个 session
    - 所有方法均为 async，调用方必须 await
    - 不调用 commit/rollback：让 Service / Router 控制事务边界
    - 异常透传：IntegrityError / OperationalError 等由中间件统一处理
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ==================== Create ====================

    async def create(
        self,
        *,
        title: str,
        company: str,
        jd_text: str,
        source: str,
        source_url: str | None = None,
        salary_min: int | None = None,
        salary_max: int | None = None,
        location: str | None = None,
        skills: list[str] | None = None,
        keywords: list[str] | None = None,
        seniority: str | None = None,
        difficulty: str | None = None,
    ) -> Job:
        """创建岗位记录

        行为：
        - 主键 id 由 ORM default=uuid.uuid4 + 数据库 server_default=gen_random_uuid() 兜底
        - created_at 由数据库 server_default=now() 自动填充
        - 调用 session.flush() 而非 commit：让 Service 层控制事务边界

        Args:
            title: 岗位名称
            company: 公司名称
            jd_text: JD 原文
            source: 来源平台
            source_url: 原始链接（唯一约束）
            salary_min: 最低薪资（K）
            salary_max: 最高薪资（K）
            location: 工作地点
            skills: 技能列表
            keywords: 关键词列表
            seniority: 资历要求
            difficulty: 难度评级

        Returns:
            新创建的 Job 实例（已 flush）

        Raises:
            IntegrityError: source_url 重复时触发唯一索引冲突
        """
        job = Job(
            id=uuid.uuid4(),
            title=title,
            company=company,
            jd_text=jd_text,
            source=source,
            source_url=source_url,
            salary_min=salary_min,
            salary_max=salary_max,
            location=location,
            skills=skills if skills is not None else [],
            keywords=keywords if keywords is not None else [],
            seniority=seniority,
            difficulty=difficulty,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    # ==================== Read ====================

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        """按主键查询岗位

        使用 Session.get() 优先从 identity map 取，避免重复查询。
        """
        return await self._session.get(Job, job_id)

    async def get_by_source_url(self, source_url: str) -> Job | None:
        """按原始链接查询岗位（去重用）

        走 ix_jobs_source_url 唯一索引，O(log N)。
        """
        stmt = select(Job).where(Job.source_url == source_url)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Job]:
        """分页查询岗位列表

        默认按 created_at 倒序（最新发现的在前），走 ix_jobs_created_at 索引。
        """
        if limit <= 0:
            return []
        stmt = (
            select(Job)
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count(self) -> int:
        """统计岗位总数"""
        stmt = select(func.count(Job.id))
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    # ==================== Update ====================

    async def update_analysis(
        self,
        job: Job,
        *,
        analysis_result: dict | None = None,
        skills: list[str] | None = None,
        keywords: list[str] | None = None,
        seniority: str | None = None,
        difficulty: str | None = None,
    ) -> Job:
        """更新岗位分析结果

        行为：
        - 使用 ORM 属性赋值：SQLAlchemy 自动检测 dirty，flush 时生成 UPDATE SQL
        - None 哨兵语义：参数为 None 时跳过该字段（"不更新"）
        - 由 Job Analysis Agent 完成后调用，回填 LLM 提取结果

        Args:
            job: 已加载的 Job 实例
            analysis_result: 完整分析结果（JSONB）
            skills: 提取的技能列表
            keywords: 提取的关键词列表
            seniority: 资历要求
            difficulty: 难度评级

        Returns:
            更新后的 Job 实例
        """
        if analysis_result is not None:
            job.analysis_result = analysis_result
        if skills is not None:
            job.skills = skills
        if keywords is not None:
            job.keywords = keywords
        if seniority is not None:
            job.seniority = seniority
        if difficulty is not None:
            job.difficulty = difficulty
        await self._session.flush()
        return job

    # ==================== Search ====================

    async def search_by_skills(
        self,
        skills: list[str],
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Job]:
        """按技能搜索岗位（JSONB @> 包含查询）

        使用 GIN 索引 gin_jobs_skills 加速。
        @> 操作符检查 JSONB 数组是否包含指定元素。

        Args:
            skills: 技能列表（任一匹配即可）
            limit: 每页大小
            offset: 偏移量

        Returns:
            匹配的 Job 序列
        """
        if not skills or limit <= 0:
            return []
        # 构造 OR 条件：skills @> '["Python"]' OR skills @> '["FastAPI"]'
        conditions = [
            Job.skills.op("@>")(f'["{skill}"]') for skill in skills
        ]
        stmt = (
            select(Job)
            .where(or_(*conditions))
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def search_by_keywords(
        self,
        keywords: list[str],
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Job]:
        """按关键词搜索岗位（JSONB @> 包含查询）

        使用 GIN 索引 gin_jobs_keywords 加速。

        Args:
            keywords: 关键词列表（任一匹配即可）
            limit: 每页大小
            offset: 偏移量

        Returns:
            匹配的 Job 序列
        """
        if not keywords or limit <= 0:
            return []
        conditions = [
            Job.keywords.op("@>")(f'["{kw}"]') for kw in keywords
        ]
        stmt = (
            select(Job)
            .where(or_(*conditions))
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(max(0, offset))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ==================== Delete ====================

    async def delete(self, job: Job) -> None:
        """物理删除岗位"""
        await self._session.delete(job)
        await self._session.flush()

    async def delete_by_id(self, job_id: uuid.UUID) -> bool:
        """按 ID 删除岗位，未找到返回 False"""
        job = await self.get_by_id(job_id)
        if job is None:
            return False
        await self.delete(job)
        return True


__all__ = ["JobRepository"]
