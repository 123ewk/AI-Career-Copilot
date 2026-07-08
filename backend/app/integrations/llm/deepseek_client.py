"""DeepSeek LLM API 异步客户端

职责：
- 封装 DeepSeek Chat Completion API 的异步调用
- 与 MimoClient / OpenAIClient 保持完全一致的接口签名

API 兼容性：
- DeepSeek API 兼容 OpenAI Chat Completion 格式
- 请求格式：POST /chat/completions
- 响应格式：{"choices": [{"message": {"content": "..."}}]}
"""

from __future__ import annotations

from typing import Any

from app.core.settings import get_settings
from app.integrations.llm.base_client import BaseOpenAICompatibleClient


class DeepseekClient(BaseOpenAICompatibleClient):
    """DeepSeek API 异步客户端

    用法:
        client = DeepseekClient()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
        )
        print(response["choices"][0]["message"]["content"])
        await client.close()
    """

    def __init__(
        self,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        """初始化客户端

        Args:
            timeout: 请求超时时间（秒）。DeepSeek 推理模型可能耗时较长，默认 60s
            max_retries: 最大重试次数
        """
        super().__init__(timeout=timeout, max_retries=max_retries)
        settings = get_settings()
        self._provider = "DeepSeek"
        self._api_base = settings.deepseek_api_base
        self._api_key = settings.deepseek_api_key
        self._model = settings.deepseek_model

    async def __aenter__(self) -> DeepseekClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


__all__ = ["DeepseekClient"]
