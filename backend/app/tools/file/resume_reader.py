"""Resume Reader Tool

把已入库的简历"读"出来供 Agent 使用,LangChain 工具封装。

与旧版 resume_parser.py(已删除)的区别:
- 不再读文件、不再解析 PDF/DOCX
- 直接从 DB 查 raw_text(上传时已解析并入库)
- 业务前提:简历已通过 POST /api/resume/upload 入库

设计动机:
- AI 视角的"读简历" = 查 DB,不需要重新解析文件
- user_id 通过闭包绑定,LLM 不可篡改(防越权)
- 工厂模式:每个请求一个独立 session,避免跨请求状态污染
"""

from __future__ import annotations

import json
import uuid

from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.infra.repositories.resume_repo import ResumeRepository


def make_get_resume_content_tool(
    *,
    user_id: uuid.UUID,
    db_session: AsyncSession,
) -> BaseTool:
    """构造一个绑定到当前用户/session 的「查询简历」工具。

    Args:
        user_id: 当前用户 UUID(Router 层从 JWT 注入,通过闭包传给工具)
        db_session: 当前请求的异步 DB session

    Returns:
        绑定好上下文的 LangChain BaseTool,签名仅暴露 resume_id
    """
    repo = ResumeRepository(db_session)

    @tool
    async def get_resume_content(resume_id: str) -> str:
        """查询已入库的简历原文,返回 JSON 字符串。

        业务前提:简历已通过上传接口入库。本工具仅做 DB 查询,不做文件解析。

        Args:
            resume_id: 简历 UUID 字符串(从用户消息或上下文获取)

        Returns:
            JSON 字符串:
            - 成功:{"resume_id","raw_text","skills","experience_years","is_active"}
            - 失败:{"error": "..."}(便于 LLM 识别并反馈给用户)
        """
        # ---- 1. UUID 格式校验 ----
        try:
            resume_uuid = uuid.UUID(resume_id)
        except ValueError:
            return json.dumps(
                {"error": f"resume_id 格式错误:{resume_id}"},
                ensure_ascii=False,
            )

        # ---- 2. DB 查询(带所有权校验,防越权) ----
        resume = await repo.get_by_id(resume_uuid)
        if resume is None or resume.user_id != user_id:
            logger.info(
                "Agent 查询简历被拒绝 | user_id={} | resume_id={}",
                user_id,
                resume_uuid,
            )
            return json.dumps(
                {"error": f"简历 {resume_id} 不存在或无权访问"},
                ensure_ascii=False,
            )

        # ---- 3. 返回 AI 友好的 JSON ----
        # raw_text 必返回:Agent 匹配/优化建议需要原文
        # 排除 user_id / created_at / structured_data:减少噪声 + 防越权信息泄露
        return json.dumps(
            {
                "resume_id": str(resume.id),
                "raw_text": resume.raw_text,
                "skills": resume.skills,
                "experience_years": resume.experience_years,
                "is_active": resume.is_active,
            },
            ensure_ascii=False,
        )

    return get_resume_content


__all__ = ["make_get_resume_content_tool"]
