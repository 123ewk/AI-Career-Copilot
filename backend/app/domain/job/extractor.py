"""JD 信息提取器

职责：
- 调用 LLM（Mimo）从 JD 文本中提取结构化信息
- 输出 JobAnalysisResult（skills/keywords/difficulty/seniority）
- 为后续的简历匹配提供数据基础

设计动机：
- LLM 而非正则：JD 信息提取是语义理解任务，需要理解上下文
  · 正则无法处理「熟悉 Python，了解 Django」这种隐含难度的信息
  · LLM 可以从整体语境推断 difficulty 和 seniority
  · 提取结果更准确、更全面
- Prompt 工程：精心设计的 Prompt 可以提高提取准确率
  · 明确输出格式（JSON），减少解析错误
  · 提供示例，引导 LLM 输出结构化数据
  · 限制输出长度，控制 Token 成本
- Pydantic 校验：LLM 输出可能不符合预期格式
  · 使用 JobAnalysisResult 校验，容忍额外字段
  · 校验失败时返回默认值，不抛异常

潜在风险：
- API 超时：LLM 调用耗时较长（5-30s）
  → 防御：MimoClient 已内置超时和重试机制
- 响应格式变化：API 升级可能导致响应结构变化
  → 防御：Pydantic 校验 + extra="ignore" 容忍额外字段
- Token 成本：长文本输入消耗大量 Token
  → 防御：限制输入长度（50KB），Prompt 精简
- 提取不准确：LLM 可能遗漏或误判某些信息
  → 防御：返回默认值，不抛异常，允许人工修正
"""

from __future__ import annotations

import json
from typing import Any

from app.core.exceptions import ExternalServiceError
from app.core.logger import logger
from app.domain.job.models import (
    JOB_JD_TEXT_MAX_LENGTH,
    JobAnalysisResult,
)
from app.integrations.llm.mimo_client import MimoClient

# ==================== 内部常量 ====================

# 提取 Prompt 模板
_EXTRACTION_PROMPT_TEMPLATE: str = """你是一个专业的招聘分析师。请从以下 JD（Job Description）中提取结构化信息。

## JD 内容
{jd_text}

## 提取要求
1. **skills**: 技术技能列表（如 Python、FastAPI、PostgreSQL、Docker）
   - 只提取明确提到的技术技能
   - 不要包含通用能力（如沟通能力、团队合作）
   - 每个技能用标准名称（如用 "Python" 不用 "python 编程"）

2. **keywords**: 行业/领域关键词（如 AI应用开发、RAG、Agent、大模型）
   - 提取行业术语、技术领域、业务方向
   - 不要与 skills 重复

3. **difficulty**: 难度评级
   - easy: 入门级，简单 CRUD，无复杂架构
   - medium: 中级，需要一定经验，有架构要求
   - hard: 高级，复杂系统设计，深度技术栈
   - expert: 专家级，前沿技术，架构师级别

4. **seniority**: 资历要求
   - intern: 实习生
   - entry: 应届生/初级（0-1年）
   - junior: 初级（1-3年）
   - mid: 中级（3-5年）
   - senior: 高级（5-8年）
   - lead: 技术负责人（8年以上）
   - principal: 首席/架构师（10年以上）

## 输出格式（JSON）
{{
    "skills": ["skill1", "skill2"],
    "keywords": ["keyword1", "keyword2"],
    "difficulty": "medium",
    "seniority": "mid"
}}

请严格按照上述 JSON 格式输出，不要包含其他内容。"""


# ==================== 公共 API ====================

class JobExtractor:
    """JD 信息提取器

    用法:
        extractor = JobExtractor()
        result = await extractor.extract(jd_text)
        print(result.skills)
        print(result.difficulty)

    设计为可复用实例:提取器内部使用 MimoClient，可复用。
    """

    def __init__(self, llm_client: MimoClient | None = None) -> None:
        """初始化提取器

        Args:
            llm_client: Mimo 客户端实例。None 时自动创建。
        """
        self._llm = llm_client or MimoClient()
        logger.info("JobExtractor 初始化完成")

    async def extract(self, jd_text: str) -> JobAnalysisResult:
        """从 JD 文本提取结构化信息

        Args:
            jd_text: JD 原始文本

        Returns:
            JobAnalysisResult: 提取结果

        Raises:
            ValidationError: 文本为空或超过长度限制
            ExternalServiceError: LLM 调用失败
        """
        logger.info("JD 信息提取开始 | text_len={}", len(jd_text))

        # 构建 Prompt
        prompt = self._build_extraction_prompt(jd_text)

        # 调用 LLM
        try:
            response = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # 低温度，提高确定性
                max_tokens=1000,
                response_format={"type": "json_object"},  # 强制 JSON 输出
            )
        except ExternalServiceError:
            logger.error("JD 信息提取失败：LLM 调用失败")
            raise

        # 解析响应
        result = self._parse_llm_response(response)

        logger.info(
            "JD 信息提取完成 | skills_count={} | keywords_count={} | difficulty={} | seniority={}",
            len(result.skills),
            len(result.keywords),
            result.difficulty,
            result.seniority,
        )

        return result

    def _build_extraction_prompt(self, jd_text: str) -> str:
        """构建提取 Prompt

        Args:
            jd_text: JD 原始文本

        Returns:
            完整的 Prompt 字符串
        """
        # 限制输入长度，避免 Token 超限
        if len(jd_text) > JOB_JD_TEXT_MAX_LENGTH:
            jd_text = jd_text[:JOB_JD_TEXT_MAX_LENGTH]
            logger.warning(
                "JD 文本过长，已截断 | original_len={} | truncated_len={}",
                len(jd_text),
                JOB_JD_TEXT_MAX_LENGTH,
            )

        return _EXTRACTION_PROMPT_TEMPLATE.format(jd_text=jd_text)

    def _parse_llm_response(self, response: dict[str, Any]) -> JobAnalysisResult:
        """解析 LLM 响应

        Args:
            response: LLM API 响应

        Returns:
            JobAnalysisResult: 解析结果

        Raises:
            ExternalServiceError: 响应格式错误
        """
        # 提取 content
        choices = response.get("choices", [])
        if not choices:
            logger.error("LLM 响应格式错误：choices 为空")
            raise ExternalServiceError(
                detail="Mimo API 响应格式错误：choices 为空",
                error_code="EXT_007",
                extra={"response": str(response)[:500]},
            )

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            logger.error("LLM 响应格式错误：content 为空")
            raise ExternalServiceError(
                detail="Mimo API 响应格式错误：content 为空",
                error_code="EXT_008",
                extra={"response": str(response)[:500]},
            )

        # 解析 JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error(
                "LLM 响应 JSON 解析失败 | content={} | error={}",
                content[:500],
                str(exc),
            )
            raise ExternalServiceError(
                detail="Mimo API 响应 JSON 解析失败",
                error_code="EXT_009",
                extra={"content": content[:500], "error": str(exc)},
            ) from exc

        logger.debug("LLM 原始输出 | content={}", content[:500])

        # 使用 Pydantic 校验
        try:
            result = JobAnalysisResult.model_validate(data)
        except Exception as exc:
            logger.error(
                "LLM 响应 Pydantic 校验失败 | data={} | error={}",
                str(data)[:500],
                str(exc),
            )
            raise ExternalServiceError(
                detail="Mimo API 响应格式校验失败",
                error_code="EXT_010",
                extra={"data": str(data)[:500], "error": str(exc)},
            ) from exc

        return result

    async def close(self) -> None:
        """关闭 LLM 客户端"""
        await self._llm.close()
        logger.info("JobExtractor 已关闭")

    async def __aenter__(self) -> JobExtractor:
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口"""
        await self.close()
