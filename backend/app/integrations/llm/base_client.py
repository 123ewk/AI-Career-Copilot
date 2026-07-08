"""OpenAI 兼容格式 LLM 客户端基类

职责：
- 封装 /v1/chat/completions 的异步调用
- 提供超时、重试、错误处理等通用逻辑
- 子类只需提供 API base URL、API key、模型名、provider 名称

设计动机：
- Mimo / DeepSeek / OpenAI 都兼容 OpenAI Chat Completion 格式
- 复用基类避免 200+ 行重复代码
- 新增 provider 时只需继承基类并配置参数
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.exceptions import ExternalServiceError
from app.core.logger import logger

# 默认超时时间（秒）：LLM 调用耗时较长，30s 覆盖大部分场景
_DEFAULT_TIMEOUT: float = 30.0

# 默认最大重试次数
_DEFAULT_MAX_RETRIES: int = 3

# 默认重试间隔（秒）
_DEFAULT_RETRY_DELAY: float = 1.0


def _build_error_detail(
    provider: str,
    status_code: int,
    response_text: str,
    api_base: str,
) -> str:
    """构建错误详情"""
    truncated_text = response_text[:500] if len(response_text) > 500 else response_text
    return (
        f"{provider} API 调用失败 | "
        f"status={status_code} | "
        f"api_base={api_base} | "
        f"response={truncated_text}"
    )


class BaseOpenAICompatibleClient:
    """OpenAI 兼容格式 LLM 客户端基类

    子类必须在 __init__ 中设置：
    - self._provider: provider 显示名称（用于日志和错误信息）
    - self._api_base: API 基础 URL
    - self._api_key: API Key
    - self._model: 默认模型名
    """

    def __init__(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries

        # 子类负责设置这些属性
        self._provider: str = "unknown"
        self._api_base: str = ""
        self._api_key: str = ""
        self._model: str = ""

        self._client: httpx.AsyncClient | None = None

    def _init_client(self) -> httpx.AsyncClient:
        """延迟初始化 httpx 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api_base,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._timeout),
            )
            logger.info(
                "{} 客户端初始化 | api_base={} | model={} | timeout={}",
                self._provider,
                self._api_base,
                self._model,
                self._timeout,
            )
        return self._client

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """调用 Chat Completion API"""
        model = model or self._model
        logger.info(
            "{} API 调用开始 | model={} | messages_count={} | max_tokens={}",
            self._provider,
            model,
            len(messages),
            max_tokens,
        )

        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            request_body["response_format"] = response_format

        client = self._init_client()
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                response = await client.post(
                    "/chat/completions",
                    json=request_body,
                )

                if response.status_code != 200:
                    error_detail = _build_error_detail(
                        self._provider,
                        response.status_code,
                        response.text,
                        self._api_base,
                    )
                    logger.error(error_detail)
                    raise ExternalServiceError(
                        detail=f"{self._provider} API 调用失败",
                        error_code="EXT_003",
                        extra={
                            "status_code": response.status_code,
                            "response": response.text[:500],
                        },
                    )

                result = response.json()
                logger.info(
                    "{} API 调用成功 | model={} | tokens_used={}",
                    self._provider,
                    model,
                    result.get("usage", {}).get("total_tokens", "unknown"),
                )
                return result

            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "{} API 超时 | attempt={}/{} | timeout={}s",
                    self._provider,
                    attempt + 1,
                    self._max_retries,
                    self._timeout,
                )
                if attempt == self._max_retries - 1:
                    raise ExternalServiceError(
                        detail=f"{self._provider} API 调用超时",
                        error_code="EXT_004",
                        extra={
                            "timeout": self._timeout,
                            "max_retries": self._max_retries,
                        },
                    ) from exc

            except json.JSONDecodeError as exc:
                last_error = exc
                logger.error(
                    "{} API 响应解析失败 | attempt={}/{} | error={}",
                    self._provider,
                    attempt + 1,
                    self._max_retries,
                    str(exc),
                )
                raise ExternalServiceError(
                    detail=f"{self._provider} API 响应解析失败",
                    error_code="EXT_005",
                    extra={"error": str(exc)},
                ) from exc

            except ExternalServiceError:
                raise

            except Exception as exc:
                last_error = exc
                logger.error(
                    "{} API 调用异常 | attempt={}/{} | error={}",
                    self._provider,
                    attempt + 1,
                    self._max_retries,
                    str(exc),
                )
                if attempt == self._max_retries - 1:
                    raise ExternalServiceError(
                        detail=f"{self._provider} API 调用异常",
                        error_code="EXT_006",
                        extra={"error": str(exc)},
                    ) from exc

        raise ExternalServiceError(
            detail=f"{self._provider} API 调用失败",
            error_code="EXT_003",
            extra={"last_error": str(last_error) if last_error else "unknown"},
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("{} 客户端已关闭", self._provider)

    async def __aenter__(self) -> BaseOpenAICompatibleClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


__all__ = ["BaseOpenAICompatibleClient"]
