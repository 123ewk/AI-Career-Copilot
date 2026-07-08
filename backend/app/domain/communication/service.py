"""沟通话术生成 Service

职责：
- 基于岗位分析结果、简历信息、匹配结果生成自然风格的沟通话术
- 提供同步生成和异步任务封装两种能力

设计动机：
- 话术生成依赖 LLM，按项目规则走 MQ 异步任务
- Prompt 必须基于简历真实信息，避免编造用户背景
- 风格为自然实习聊天风，贴近 Boss 直聘真实沟通场景
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.exceptions import ExternalServiceError, ResourceNotFoundError
from app.core.logger import logger
from app.domain.communication.models import (
    CommunicationGenerateRequest,
    CommunicationScriptResponse,
)
from app.domain.job.service import JobService
from app.domain.resume.service import ResumeService
from app.domain.task.service import TaskService
from app.integrations.llm.llm_client import LLMClient

# ==================== 内部常量 ====================

# 生成沟通话术的 Prompt 模板
_COMMUNICATION_PROMPT_TEMPLATE: str = """你是一位正在 Boss 直聘上找实习的大学生。请根据以下岗位信息和你的简历信息，生成自然、口语化的沟通话术。

要求：
1. 话术必须完全基于简历真实信息，绝对不要编造简历中没有的技能或经历。
2. 风格要像真实实习生在 Boss 直聘上聊天：自然、有礼貌、不卑不亢、不过度正式。
3. 初次打招呼控制在 3 行以内，突出 1-2 个与岗位最匹配的亮点。
4. 跟进/回复话术用于 HR 已读未回或简单回复后的场景。
5. 输出严格 JSON 格式：{{"greeting": "...", "follow_up": "...", "full_script": "..."}}

## 岗位信息
- 岗位标题：{job_title}
- 公司名称：{company_name}
- 岗位技能要求：{job_skills}
- 岗位关键词：{job_keywords}
- 岗位 JD 摘要：{job_text}

## 你的简历信息
- 你的技能：{resume_skills}
- 你的简历摘要：{resume_text}

## 输出格式
{{
  "greeting": "初次打招呼话术，3行以内",
  "follow_up": "跟进或回复话术",
  "full_script": "把 greeting 和 follow_up 串成一段完整参考对话"
}}
"""


# ==================== 公共 API ====================

class CommunicationService:
    """沟通话术生成 Service"""

    def __init__(
        self,
        session: Any,
        llm_client: LLMClient | None = None,
    ) -> None:
        """初始化 Communication Service

        Args:
            session: 数据库异步会话
            llm_client: LLM 客户端，None 时自动创建
        """
        self._session = session
        self._resume_service = ResumeService(session)
        self._job_service = JobService(session)
        self._llm = llm_client or LLMClient()

    async def generate_script(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        resume_id: uuid.UUID | None = None,
    ) -> CommunicationScriptResponse:
        """生成沟通话术

        Args:
            user_id: 用户 ID
            job_id: 岗位 ID
            resume_id: 简历 ID，None 时使用用户活跃简历

        Returns:
            CommunicationScriptResponse

        Raises:
            ResourceNotFoundError: 未找到简历或岗位
            ExternalServiceError: LLM 调用失败
        """
        logger.info(
            "生成沟通话术 | user_id={} | job_id={} | resume_id={}",
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

        # 3. 构造 Prompt
        analysis = job.analysis
        if analysis:
            job_skills = analysis.skills or []
            job_keywords = analysis.keywords or []
            job_text = analysis.summary if hasattr(analysis, "summary") else job.jd_text
        else:
            job_skills = job.skills or []
            job_keywords = job.keywords or []
            job_text = job.jd_text or ""

        # 控制文本长度
        max_text_len = 3000
        if len(job_text) > max_text_len:
            job_text = job_text[:max_text_len] + "..."
        resume_text = resume.raw_text or ""
        if len(resume_text) > max_text_len:
            resume_text = resume_text[:max_text_len] + "..."

        prompt = _COMMUNICATION_PROMPT_TEMPLATE.format(
            job_title=job.title or "未知岗位",
            company_name=job.company or "未知公司",
            job_skills=", ".join(job_skills) if job_skills else "无",
            job_keywords=", ".join(job_keywords) if job_keywords else "无",
            job_text=job_text or "无",
            resume_skills=", ".join(resume.skills or []) if resume.skills else "无",
            resume_text=resume_text or "无",
        )

        # 4. 调用 LLM
        response = await self._llm.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )

        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not content:
            raise ExternalServiceError(
                detail="LLM 响应 content 为空",
                error_code="EXT_008",
            )

        # 5. 解析并校验
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error(
                "沟通话术 JSON 解析失败 | content={} | error={}",
                content[:500],
                str(exc),
            )
            raise ExternalServiceError(
                detail="沟通话术 JSON 解析失败",
                error_code="EXT_009",
                extra={"content": content[:500], "error": str(exc)},
            ) from exc

        try:
            script = CommunicationScriptResponse(
                job_id=job_id,
                resume_id=resume.id,
                greeting=str(data.get("greeting", "")),
                follow_up=str(data.get("follow_up", "")),
                full_script=str(data.get("full_script", "")),
            )
        except Exception as exc:
            logger.error(
                "沟通话术 Pydantic 校验失败 | data={} | error={}",
                str(data)[:500],
                str(exc),
            )
            raise ExternalServiceError(
                detail="沟通话术格式校验失败",
                error_code="EXT_010",
                extra={"data": str(data)[:500], "error": str(exc)},
            ) from exc

        logger.info(
            "生成沟通话术完成 | user_id={} | job_id={} | resume_id={}",
            user_id,
            job_id,
            resume.id,
        )
        return script

    async def generate_script_async(
        self,
        user_id: uuid.UUID,
        request: CommunicationGenerateRequest,
    ) -> dict[str, Any]:
        """创建异步生成话术任务

        Args:
            user_id: 用户 ID
            request: 生成请求

        Returns:
            包含 task_id 和 status 的字典
        """
        task_service = TaskService(self._session)
        business_id = f"communication:job-{request.job_id}:resume-{request.resume_id or 'active'}"

        task = await task_service.create_task(
            user_id=user_id,
            session_id=request.session_id,
            task_type="communication_generate",
            business_id=business_id,
            input_data={
                "job_id": str(request.job_id),
                "resume_id": str(request.resume_id) if request.resume_id else None,
                "tone": request.tone,
            },
        )

        return {
            "task_id": task.id,
            "status": task.status,
        }

    async def close(self) -> None:
        """关闭 LLM 客户端"""
        await self._llm.close()


__all__ = ["CommunicationService"]
