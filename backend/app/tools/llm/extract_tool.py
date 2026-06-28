"""LLM 结构化提取工具

职责：
- 封装 LLM 调用，提供通用的结构化提取能力
- 支持任意 Pydantic 模型作为输出 schema
- 处理 Prompt 构建、LLM 调用、响应解析、Pydantic 校验

设计动机：
- 通用性：不同域（Job/Resume/Agent）都需要从文本提取结构化信息
  · 提取逻辑相同：Prompt → LLM → JSON → Pydantic 校验
  · 差异仅在：Prompt 模板、输出 schema、输入文本
  · 抽取为通用工具，避免重复代码
- 类型安全：使用泛型（Generic[T]）确保输出类型与 schema 一致
  · 调用方传入 Pydantic Model 类型，返回对应的实例
  · 编译时类型检查，减少运行时错误
- 可测试：依赖注入 LLM 客户端，便于 Mock 测试

使用方式：
```python
from app.tools.llm.extract_tool import LLMExtractTool
from app.domain.job.models import JobAnalysisResult

tool = LLMExtractTool()
result = await tool.extract(
    text="...",
    schema=JobAnalysisResult,
    prompt_template="请从以下文本提取：\n{text}\n输出格式：{schema}",
)
# result 是 JobAnalysisResult 实例
```

潜在风险：
- Prompt 注入：用户输入可能包含恶意 Prompt
  → 防御：输入文本用明确分隔符包裹（如 ## JD 内容 ##）
- JSON 解析失败：LLM 输出可能不是有效 JSON
  → 防御：重试 + 低温度 + response_format={"type": "json_object"}
- Pydantic 校验失败：LLM 输出可能不符合 schema
  → 防御：容忍额外字段（extra="ignore"），校验失败抛 ExternalServiceError
"""

from __future__ import annotations

import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.core.exceptions import ExternalServiceError
from app.core.logger import logger
from app.integrations.llm.mimo_client import MimoClient

# ==================== 泛型类型变量 ====================

T = TypeVar("T", bound=BaseModel)


# ==================== 内部常量 ====================

# 默认提取 Prompt 模板
# {text} 会被替换为输入文本
# {schema} 会被替换为 JSON schema 描述
_DEFAULT_PROMPT_TEMPLATE: str = """请从以下文本中提取结构化信息。

## 文本内容
{text}

## 输出要求
请严格按照以下 JSON schema 输出，不要包含其他内容：
{schema}

## 输出格式（JSON）
请直接输出 JSON，不要包含 markdown 代码块标记。"""


# ==================== 公共 API ====================

class LLMExtractTool(Generic[T]):
    """LLM 结构化提取工具

    用法:
        tool = LLMExtractTool()
        result = await tool.extract(
            text="...",
            schema=JobAnalysisResult,
            prompt_template="...",
        )

    设计为可复用实例:工具内部使用 MimoClient，可复用。
    """

    def __init__(self, llm_client: MimoClient | None = None) -> None:
        """初始化工具

        Args:
            llm_client: Mimo 客户端实例。None 时自动创建。
        """
        self._llm = llm_client or MimoClient()
        logger.info("LLMExtractTool 初始化完成")

    async def extract(
        self,
        text: str,
        schema: type[T],
        prompt_template: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> T:
        """从文本提取结构化信息

        Args:
            text: 输入文本
            schema: Pydantic Model 类型（输出 schema）
            prompt_template: Prompt 模板，{text} 和 {schema} 会被替换
                None 时使用默认模板
            temperature: 温度参数（0-1），越低越确定
            max_tokens: 最大生成 Token 数

        Returns:
            schema 类型的实例

        Raises:
            ExternalServiceError: LLM 调用失败或响应解析失败
        """
        logger.info(
            "LLM 提取开始 | schema={} | text_len={}",
            schema.__name__,
            len(text),
        )

        # 构建 Prompt
        prompt = self._build_prompt(text, schema, prompt_template)

        # 调用 LLM
        try:
            response = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except ExternalServiceError:
            logger.error("LLM 提取失败：LLM 调用失败")
            raise

        # 解析响应
        result = self._parse_response(response, schema)

        logger.info(
            "LLM 提取完成 | schema={} | fields={}",
            schema.__name__,
            list(schema.model_fields.keys()),
        )

        return result

    def _build_prompt(
        self,
        text: str,
        schema: type[T],
        prompt_template: str | None,
    ) -> str:
        """构建 Prompt

        Args:
            text: 输入文本
            schema: Pydantic Model 类型
            prompt_template: Prompt 模板

        Returns:
            完整的 Prompt 字符串
        """
        template = prompt_template or _DEFAULT_PROMPT_TEMPLATE

        # 生成 JSON schema 描述
        schema_json = schema.model_json_schema()
        schema_desc = json.dumps(schema_json, ensure_ascii=False, indent=2)

        # 替换模板变量
        prompt = template.format(text=text, schema=schema_desc)

        logger.debug(
            "Prompt 构建完成 | template_len={} | text_len={} | schema_len={}",
            len(template),
            len(text),
            len(schema_desc),
        )

        return prompt

    def _parse_response(
        self,
        response: dict[str, Any],
        schema: type[T],
    ) -> T:
        """解析 LLM 响应

        Args:
            response: LLM API 响应
            schema: Pydantic Model 类型

        Returns:
            schema 类型的实例

        Raises:
            ExternalServiceError: 响应格式错误
        """
        # 提取 content
        choices = response.get("choices", [])
        if not choices:
            logger.error("LLM 响应格式错误：choices 为空")
            raise ExternalServiceError(
                detail="LLM 响应格式错误：choices 为空",
                error_code="EXT_007",
                extra={"response": str(response)[:500]},
            )

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            logger.error("LLM 响应格式错误：content 为空")
            raise ExternalServiceError(
                detail="LLM 响应格式错误：content 为空",
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
                detail="LLM 响应 JSON 解析失败",
                error_code="EXT_009",
                extra={"content": content[:500], "error": str(exc)},
            ) from exc

        logger.debug("LLM 原始输出 | content={}", content[:500])

        # 使用 Pydantic 校验
        try:
            result = schema.model_validate(data)
        except Exception as exc:
            logger.error(
                "LLM 响应 Pydantic 校验失败 | data={} | error={}",
                str(data)[:500],
                str(exc),
            )
            raise ExternalServiceError(
                detail="LLM 响应格式校验失败",
                error_code="EXT_010",
                extra={"data": str(data)[:500], "error": str(exc)},
            ) from exc

        return result

    async def close(self) -> None:
        """关闭 LLM 客户端"""
        await self._llm.close()
        logger.info("LLMExtractTool 已关闭")

    async def __aenter__(self) -> LLMExtractTool[T]:
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口"""
        await self.close()
