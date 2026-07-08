"""Mimo LLM API 异步客户端

职责：
- 封装 Mimo Chat Completion API 的异步调用
- 提供统一的 LLM 调用接口，供 Job Extractor 等模块使用
- 处理 API 错误、超时、重试等异常场景

设计动机：
- httpx 而非 LangChain：直接调用 API 更轻量，避免 LangChain 的额外抽象层
  · 本项目 LLM 调用场景简单（Chat Completion），无需 LangChain 的复杂编排
  · httpx.AsyncClient 原生支持异步，性能更优
  · 减少依赖，降低维护成本
- 异步优先：所有 IO 操作使用 async/await，不阻塞 Event Loop
- 复用 BaseOpenAICompatibleClient：Mimo / DeepSeek / OpenAI 均兼容 OpenAI Chat Completion 格式

API 兼容性：
- Mimo API 兼容 OpenAI Chat Completion 格式
- 请求格式：POST /v1/chat/completions
- 响应格式：{"choices": [{"message": {"content": "..."}}]}
"""

from __future__ import annotations

from typing import Any

from app.core.settings import get_settings
from app.integrations.llm.base_client import BaseOpenAICompatibleClient


class MimoClient(BaseOpenAICompatibleClient):
    """Mimo API 异步客户端

    用法:
        client = MimoClient()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
        )
        print(response["choices"][0]["message"]["content"])
        await client.close()

    设计为可复用实例：客户端内部维护连接池，重复创建实例没有副作用，
    但建议复用以减少连接开销。
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """初始化客户端

        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        super().__init__(timeout=timeout, max_retries=max_retries)
        settings = get_settings()
        self._provider = "Mimo"
        self._api_base = settings.mimo_api_base
        self._api_key = settings.mimo_api_key
        self._model = settings.mimo_model

    async def __aenter__(self) -> MimoClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


__all__ = ["MimoClient"]
