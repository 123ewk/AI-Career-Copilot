"""统一 LLM 客户端入口

职责：
- 根据 settings.llm_provider 自动选择底层 LLM 客户端
- 对外提供统一的 chat_completion 接口
- 所有业务代码（JobExtractor、CommunicationService、MatchService）统一通过此处调用 LLM

设计动机：
- 消除 LLM 调用碎片化，避免业务代码硬编码某个 provider
- 新增 provider 时只需在此处扩展，业务代码无需修改
- 保持与 MimoClient / DeepseekClient / OpenAIClient 完全一致的接口签名
"""

from __future__ import annotations

from typing import Any

from app.core.settings import get_settings
from app.integrations.llm.base_client import BaseOpenAICompatibleClient
from app.integrations.llm.deepseek_client import DeepseekClient
from app.integrations.llm.mimo_client import MimoClient
from app.integrations.llm.openai_client import OpenAIClient


class LLMClient:
    """统一 LLM 客户端

    用法:
        client = LLMClient()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
        )
        print(response["choices"][0]["message"]["content"])
        await client.close()

    支持的 provider：
    - mimo
    - deepseek
    - openai
    """

    def __init__(self) -> None:
        settings = get_settings()
        provider = settings.llm_provider.lower()
        if provider == "mimo":
            self._client: BaseOpenAICompatibleClient = MimoClient()
        elif provider == "deepseek":
            self._client = DeepseekClient()
        elif provider == "openai":
            self._client = OpenAIClient()
        else:
            raise ValueError(
                f"不支持的 LLM provider: {settings.llm_provider}. "
                f"支持的 provider: mimo, deepseek, openai"
            )

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """调用 Chat Completion API

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            model: 模型名称，None 时使用 provider 默认模型
            temperature: 温度参数（0-1），越低越确定
            max_tokens: 最大生成 Token 数
            response_format: 响应格式，如 {"type": "json_object"}

        Returns:
            API 响应字典，格式为 {"choices": [{"message": {"content": "..."}}]}
        """
        return await self._client.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    async def close(self) -> None:
        """关闭底层客户端"""
        await self._client.close()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


__all__ = ["LLMClient"]
