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
- 单例模式：客户端实例可复用，避免重复创建连接池

API 兼容性：
- Mimo API 兼容 OpenAI Chat Completion 格式
- 请求格式：POST /v1/chat/completions
- 响应格式：{"choices": [{"message": {"content": "..."}}]}

潜在风险：
- API 超时：LLM 调用耗时较长（5-30s），需设置合理超时
  → 防御：默认 30s 超时，可配置
- 响应格式变化：API 升级可能导致响应结构变化
  → 防御：使用 Pydantic 校验响应，容忍额外字段
- Token 成本：长文本输入消耗大量 Token
  → 防御：限制输入长度，Prompt 精简
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.exceptions import ExternalServiceError
from app.core.logger import logger
from app.core.settings import get_settings

# ==================== 内部常量 ====================

# 默认超时时间（秒）：LLM 调用耗时较长，30s 覆盖大部分场景
_DEFAULT_TIMEOUT: float = 30.0

# 默认最大重试次数
_DEFAULT_MAX_RETRIES: int = 3

# 默认重试间隔（秒）
_DEFAULT_RETRY_DELAY: float = 1.0


# ==================== 内部辅助函数 ====================

def _build_error_detail(
    status_code: int,
    response_text: str,
    api_base: str,
) -> str:
    """构建错误详情

    Args:
        status_code: HTTP 状态码
        response_text: 响应文本
        api_base: API 基础 URL

    Returns:
        错误详情字符串
    """
    # 截断响应文本，避免日志过长
    truncated_text = response_text[:500] if len(response_text) > 500 else response_text
    return (
        f"Mimo API 调用失败 | "
        f"status={status_code} | "
        f"api_base={api_base} | "
        f"response={truncated_text}"
    )


# ==================== 公共 API ====================

class MimoClient:
    """Mimo API 异步客户端

    用法:
        client = MimoClient()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
        )
        print(response["choices"][0]["message"]["content"])
        await client.close()

    设计为可复用实例:客户端内部维护连接池，重复创建实例没有副作用，
    但建议复用以减少连接开销。
    """

    def __init__(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        """初始化客户端

        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        settings = get_settings()
        self._api_base = settings.mimo_api_base
        self._api_key = settings.mimo_api_key
        self._model = settings.mimo_model
        self._timeout = timeout
        self._max_retries = max_retries

        # 创建异步 HTTP 客户端
        self._client = httpx.AsyncClient(
            base_url=self._api_base,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self._timeout),
        )

        logger.info(
            "Mimo 客户端初始化 | api_base={} | model={} | timeout={}",
            self._api_base,
            self._model,
            self._timeout,
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
            model: 模型名称，None 时使用默认模型
            temperature: 温度参数（0-1），越低越确定
            max_tokens: 最大生成 Token 数
            response_format: 响应格式，如 {"type": "json_object"}

        Returns:
            API 响应字典，格式为 {"choices": [{"message": {"content": "..."}}]}

        Raises:
            ExternalServiceError: API 调用失败（超时、解析错误、HTTP 错误）
        """
        model = model or self._model
        logger.info(
            "Mimo API 调用开始 | model={} | messages_count={} | max_tokens={}",
            model,
            len(messages),
            max_tokens,
        )

        # 构建请求体
        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            request_body["response_format"] = response_format

        # 重试循环
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._client.post(
                    "/chat/completions",
                    json=request_body,
                )

                # 检查 HTTP 状态码
                if response.status_code != 200:
                    error_detail = _build_error_detail(
                        response.status_code,
                        response.text,
                        self._api_base,
                    )
                    logger.error(error_detail)
                    raise ExternalServiceError(
                        detail="Mimo API 调用失败",
                        error_code="EXT_003",
                        extra={
                            "status_code": response.status_code,
                            "response": response.text[:500],
                        },
                    )

                # 解析响应
                result = response.json()
                logger.info(
                    "Mimo API 调用成功 | model={} | tokens_used={}",
                    model,
                    result.get("usage", {}).get("total_tokens", "unknown"),
                )
                return result

            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "Mimo API 超时 | attempt={}/{} | timeout={}s",
                    attempt + 1,
                    self._max_retries,
                    self._timeout,
                )
                # 最后一次重试失败，抛出异常
                if attempt == self._max_retries - 1:
                    raise ExternalServiceError(
                        detail="Mimo API 调用超时",
                        error_code="EXT_004",
                        extra={
                            "timeout": self._timeout,
                            "max_retries": self._max_retries,
                        },
                    ) from exc

            except json.JSONDecodeError as exc:
                last_error = exc
                logger.error(
                    "Mimo API 响应解析失败 | attempt={}/{} | error={}",
                    attempt + 1,
                    self._max_retries,
                    str(exc),
                )
                # JSON 解析失败通常是响应格式问题，不重试
                raise ExternalServiceError(
                    detail="Mimo API 响应解析失败",
                    error_code="EXT_005",
                    extra={"error": str(exc)},
                ) from exc

            except ExternalServiceError:
                # 已经是 ExternalServiceError，直接抛出
                raise

            except Exception as exc:
                last_error = exc
                logger.error(
                    "Mimo API 调用异常 | attempt={}/{} | error={}",
                    attempt + 1,
                    self._max_retries,
                    str(exc),
                )
                # 最后一次重试失败，抛出异常
                if attempt == self._max_retries - 1:
                    raise ExternalServiceError(
                        detail="Mimo API 调用异常",
                        error_code="EXT_006",
                        extra={"error": str(exc)},
                    ) from exc

        # 理论上不会到这里，但作为防御性编程
        raise ExternalServiceError(
            detail="Mimo API 调用失败",
            error_code="EXT_003",
            extra={"last_error": str(last_error) if last_error else "unknown"},
        )

    async def close(self) -> None:
        """关闭 HTTP 客户端

        在应用关闭时调用，释放连接池资源。
        """
        await self._client.aclose()
        logger.info("Mimo 客户端已关闭")

    async def __aenter__(self) -> MimoClient:
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口"""
        await self.close()
