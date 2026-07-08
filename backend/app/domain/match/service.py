"""简历-岗位匹配 Service

职责：
- 编排「查 active resume + 查 job + 构造 MatchInput + 调用 scorer + LLM 生成建议」
- 提供同步匹配接口，供 Match Router 调用

设计动机：
- 轻量计算走同步：BM25 纯 CPU，<100ms；LLM 生成 suggestions 约 1-3s，MVP 可接受
- suggestions 由 LLM 生成，比固定模板更有针对性
- 命中/缺失技能用轻量规则计算，避免 LLM 幻觉
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.core.logger import logger
from app.core.settings import get_settings
from app.domain.job.service import JobService
from app.domain.match.models import (
    MatchComputeRequest,
    MatchInput,
    MatchResultResponse,
    MatchScoreDetail,
)
from app.domain.match.scorer import CombinedScorer, create_default_scorer
from app.domain.resume.service import ResumeService
from app.integrations.llm.llm_client import LLMClient

# ==================== 内部常量 ====================

# LLM 生成 suggestions 的 Prompt 模板
_SUGGESTIONS_PROMPT_TEMPLATE: str = """你是一位求职辅导助手。请根据以下岗位信息和简历信息，给出 3-5 条针对性强、可执行的建议，帮助求职者提高与该岗位的匹配度。

要求：
1. 建议必须基于简历真实信息，不要编造简历中没有的内容。
2. 如果简历已经匹配得很好，可以给出面试准备建议或沟通策略。
3. 如果简历有缺失，建议如何补充（例如学习某项技能、补充某个项目经历）。
4. 输出 JSON 数组，每条建议是一个字符串，不要包含其他内容。

## 岗位信息
- 岗位标题：{job_title}
- 公司名称：{company_name}
- 岗位技能要求：{job_skills}
- 岗位关键词：{job_keywords}
- 岗位 JD 摘要：{job_text}

## 简历信息
- 简历技能：{resume_skills}
- 匹配分数：{combined_score}/100（BM25: {bm25_score}/100）
- 命中技能：{matched_skills}
- 缺失技能：{missing_skills}

## 输出格式
[
  "建议1",
  "建议2",
  "建议3"
]
"""


# ==================== 公共 API ====================

class MatchService:
    """简历-岗位匹配 Service"""

    def __init__(
        self,
        session: Any,
        scorer: CombinedScorer | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        """初始化匹配 Service

        Args:
            session: 数据库异步会话
            scorer: 匹配打分器，None 时自动创建默认 BM25 scorer
            llm_client: LLM 客户端，None 时自动创建
        """
        self._session = session
        self._resume_service = ResumeService(session)
        self._job_service = JobService(session)
        self._scorer = scorer or self._create_default_scorer()
        self._llm = llm_client or LLMClient()

    @staticmethod
    def _create_default_scorer() -> CombinedScorer:
        """创建默认 scorer：MVP 强制使用纯 BM25"""
        settings = get_settings()
        return create_default_scorer(
            semantic_enabled=False,
            weight_bm25=settings.match_bm25_weight,
            weight_semantic=settings.match_semantic_weight,
        )

    async def compute_match(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        resume_id: uuid.UUID | None = None,
    ) -> MatchResultResponse:
        """计算简历与岗位的匹配度

        Args:
            user_id: 用户 ID
            job_id: 岗位 ID
            resume_id: 简历 ID，None 时使用用户活跃简历

        Returns:
            MatchResultResponse

        Raises:
            ResourceNotFoundError: 未找到简历或岗位
        """
        logger.info(
            "匹配计算开始 | user_id={} | job_id={} | resume_id={}",
            user_id,
            job_id,
            resume_id,
        )

        # 1. 取简历
        if resume_id:
            resume = await self._resume_service.get_resume(
                user_id=user_id, resume_id=resume_id
            )
        else:
            resume = await self._resume_service.get_active_resume(user_id=user_id)

        if resume is None:
            raise ResourceNotFoundError(
                detail="未找到可用简历，请先上传简历",
                error_code="RES_001",
            )

        # 2. 取岗位
        job = await self._job_service.get_job(job_id=job_id)
        if job is None:
            raise ResourceNotFoundError(
                detail="岗位不存在",
                error_code="JOB_001",
            )

        # 3. 构造 MatchInput
        analysis = job.analysis
        if analysis:
            job_skills = analysis.skills or []
            job_keywords = analysis.keywords or []
            job_text = analysis.summary if hasattr(analysis, "summary") else job.jd_text
        else:
            job_skills = job.skills or []
            job_keywords = job.keywords or []
            job_text = job.jd_text or ""

        match_input = MatchInput(
            job_id=job_id,
            resume_id=resume.id,
            job_skills=job_skills,
            job_keywords=job_keywords,
            job_text=job_text,
            resume_skills=resume.skills or [],
            resume_text=resume.raw_text or "",
        )

        # 4. 打分
        score_detail = self._scorer.score(match_input)

        # 5. 轻量规则计算命中/缺失技能
        matched_skills, missing_skills = self._compute_skill_insights(
            job_skills=job_skills,
            resume_skills=resume.skills or [],
        )

        # 6. LLM 生成建议
        suggestions = await self._generate_suggestions(
            job=job,
            score_detail=score_detail,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            resume_skills=resume.skills or [],
            resume_text=resume.raw_text or "",
        )

        logger.info(
            "匹配计算完成 | user_id={} | job_id={} | resume_id={} | combined_score={}",
            user_id,
            job_id,
            resume.id,
            score_detail.combined_score,
        )

        return MatchResultResponse(
            job_id=job_id,
            resume_id=resume.id,
            score_detail=score_detail,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            suggestions=suggestions,
        )

    async def compute_match_for_request(
        self,
        user_id: uuid.UUID,
        request: MatchComputeRequest,
    ) -> MatchResultResponse:
        """根据 API 请求计算匹配度"""
        return await self.compute_match(
            user_id=user_id,
            job_id=request.job_id,
            resume_id=request.resume_id,
        )

    @staticmethod
    def _compute_skill_insights(
        job_skills: list[str],
        resume_skills: list[str],
    ) -> tuple[list[str], list[str]]:
        """计算命中技能和缺失技能

        规则：
        - 大小写不敏感比较
        - job_skills 中在 resume_skills 出现的为命中
        - 未出现的为缺失
        - 最多各返回 10 个
        """
        normalized_resume_skills = {skill.lower().strip() for skill in resume_skills}

        matched: list[str] = []
        missing: list[str] = []

        for skill in job_skills:
            normalized = skill.lower().strip()
            if normalized and normalized in normalized_resume_skills:
                matched.append(skill)
            else:
                missing.append(skill)

        return matched[:10], missing[:10]

    async def _generate_suggestions(
        self,
        job: Any,
        score_detail: MatchScoreDetail,
        matched_skills: list[str],
        missing_skills: list[str],
        resume_skills: list[str],
        resume_text: str,
    ) -> list[str]:
        """调用 LLM 生成匹配建议"""
        analysis = job.analysis
        job_skills = analysis.skills if analysis else job.skills or []
        job_keywords = analysis.keywords if analysis else job.keywords or []
        job_text = (
            analysis.summary
            if analysis and hasattr(analysis, "summary")
            else job.jd_text or ""
        )

        # 控制文本长度，避免 Token 超限
        max_text_len = 3000
        if len(job_text) > max_text_len:
            job_text = job_text[:max_text_len] + "..."
        if len(resume_text) > max_text_len:
            resume_text = resume_text[:max_text_len] + "..."

        prompt = _SUGGESTIONS_PROMPT_TEMPLATE.format(
            job_title=job.title or "未知岗位",
            company_name=job.company or "未知公司",
            job_skills=", ".join(job_skills) if job_skills else "无",
            job_keywords=", ".join(job_keywords) if job_keywords else "无",
            job_text=job_text or "无",
            resume_skills=", ".join(resume_skills) if resume_skills else "无",
            combined_score=score_detail.combined_score,
            bm25_score=score_detail.bm25_score,
            matched_skills=", ".join(matched_skills) if matched_skills else "无",
            missing_skills=", ".join(missing_skills) if missing_skills else "无",
        )

        try:
            response = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
        except ExternalServiceError:
            logger.error("匹配建议生成失败：LLM 调用失败")
            return ["建议生成暂时不可用，请稍后重试。"]

        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not content:
            logger.error("匹配建议生成失败：LLM 响应 content 为空")
            return ["建议生成暂时不可用，请稍后重试。"]

        try:
            data = json.loads(content)
            if isinstance(data, list):
                suggestions = [str(item) for item in data if item]
            elif isinstance(data, dict) and "suggestions" in data:
                suggestions = [str(item) for item in data["suggestions"] if item]
            else:
                suggestions = [str(value) for value in data.values() if value]
        except json.JSONDecodeError as exc:
            logger.error(
                "匹配建议 JSON 解析失败 | content={} | error={}",
                content[:500],
                str(exc),
            )
            return ["建议生成暂时不可用，请稍后重试。"]

        return suggestions[:5]

    async def close(self) -> None:
        """关闭 LLM 客户端"""
        await self._llm.close()


__all__ = ["MatchService"]
