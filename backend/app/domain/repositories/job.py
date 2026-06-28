"""Job 仓储抽象接口（Domain 层）

职责：
- 定义 Job 仓储的契约（Protocol），仅声明方法签名，不含实现
- 由 infra/repositories/job_repo.py 中的 JobRepository 实现
- Domain Service / UseCase 仅依赖本 Protocol，便于替换 ORM 或测试时 mock

设计动机：
- 与 ResumeRepositoryProtocol 保持一致的依赖倒置模式
- 支持 JSONB 技能/关键词搜索（GIN 索引加速）
- 支持 analysis_result 更新（Job Analysis Agent 回填）
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.infra.database.models.job import Job


@runtime_checkable
class JobRepositoryProtocol(Protocol):
    """Job 仓储接口

    所有方法均为 async：调用方必须 await
    不调用 commit/rollback：让 Service / Router 控制事务边界
    异常透传：IntegrityError / OperationalError 等由调用方 / 中间件统一处理
    """

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

        Args:
            title: 岗位名称
            company: 公司名称
            jd_text: JD 原文
            source: 来源平台（boss/liepin/zhilian/shixiseng）
            source_url: 原始链接（唯一约束，用于去重）
            salary_min: 最低薪资（K）
            salary_max: 最高薪资（K）
            location: 工作地点
            skills: 技能列表
            keywords: 关键词列表
            seniority: 资历要求
            difficulty: 难度评级

        Returns:
            新创建的 Job ORM 对象（已 flush）

        Raises:
            IntegrityError: source_url 重复时触发唯一索引冲突
        """
        ...

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        """按主键查询岗位，未找到返回 None"""
        ...

    async def get_by_source_url(self, source_url: str) -> Job | None:
        """按原始链接查询岗位（去重用），未找到返回 None"""
        ...

    async def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Job]:
        """分页查询岗位列表

        默认按 created_at 倒序（最新发现的在前）
        """
        ...

    async def count(self) -> int:
        """统计岗位总数"""
        ...

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

        由 Job Analysis Agent 完成后调用，回填 LLM 提取结果。
        None 哨兵语义：参数为 None 时跳过该字段（"不更新"）。

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
        ...

    async def search_by_skills(
        self,
        skills: list[str],
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Job]:
        """按技能搜索岗位（JSONB @> 包含查询）

        使用 GIN 索引 gin_jobs_skills 加速。

        Args:
            skills: 技能列表（任一匹配即可）
            limit: 每页大小
            offset: 偏移量

        Returns:
            匹配的 Job 序列
        """
        ...

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
        ...

    async def delete(self, job: Job) -> None:
        """物理删除岗位"""
        ...

    async def delete_by_id(self, job_id: uuid.UUID) -> bool:
        """按 ID 删除岗位，未找到返回 False"""
        ...


__all__ = ["JobRepositoryProtocol"]
