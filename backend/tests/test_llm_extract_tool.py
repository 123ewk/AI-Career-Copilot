"""LLM Extract Tool 单元测试

职责：
- 测试通用 LLM 结构化提取功能
- 使用 unittest.mock 模拟 LLM API 调用
- 覆盖正常流程、边界条件、异常流程

测试策略：
- Mock LLM：使用 unittest.mock 模拟 API 响应，避免真实 API 调用
- 泛型类型：验证 Generic[T] 类型安全
- Pydantic 校验：验证输出符合 schema
"""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.core.exceptions import ExternalServiceError
from app.tools.llm.extract_tool import LLMExtractTool

# ==================== Fixtures ====================


@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """模拟 LLM 客户端"""
    return AsyncMock()


@pytest.fixture
def tool(mock_llm_client: AsyncMock) -> LLMExtractTool:
    """LLMExtractTool 实例（使用模拟 LLM 客户端）"""
    return LLMExtractTool(llm_client=mock_llm_client)


# ==================== 测试用 Pydantic 模型 ====================


class SimpleResult(BaseModel):
    """简单测试模型"""

    name: str
    age: int


class ComplexResult(BaseModel):
    """复杂测试模型"""

    skills: list[str]
    keywords: list[str]
    difficulty: str | None = None
    seniority: str | None = None


# ==================== 测试数据 ====================

# 标准输入文本
SAMPLE_TEXT = "张三，25岁，Python 开发工程师"

# LLM 模拟响应
MOCK_LLM_RESPONSE = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {"name": "张三", "age": 25},
                    ensure_ascii=False,
                )
            }
        }
    ],
    "usage": {"total_tokens": 100},
}


# ==================== 正常流程 ====================


class TestLLMExtractToolNormalFlow:
    """正常流程测试"""

    async def test_extract_simple_model(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """简单模型提取"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        result = await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert isinstance(result, SimpleResult)
        assert result.name == "张三"
        assert result.age == 25

    async def test_extract_complex_model(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """复杂模型提取"""
        complex_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "skills": ["Python", "FastAPI"],
                                "keywords": ["AI", "Web"],
                                "difficulty": "medium",
                                "seniority": "mid",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = complex_response

        result = await tool.extract(text=SAMPLE_TEXT, schema=ComplexResult)

        assert isinstance(result, ComplexResult)
        assert "Python" in result.skills
        assert result.difficulty == "medium"
        assert result.seniority == "mid"

    async def test_extract_with_custom_prompt_template(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """自定义 Prompt 模板"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE
        custom_template = "提取信息：\n{text}\n格式：{schema}"

        await tool.extract(
            text=SAMPLE_TEXT,
            schema=SimpleResult,
            prompt_template=custom_template,
        )

        call_args = mock_llm_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "提取信息" in prompt

    async def test_extract_llm_call_parameters(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """验证 LLM 调用参数"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        await tool.extract(
            text=SAMPLE_TEXT,
            schema=SimpleResult,
            temperature=0.5,
            max_tokens=500,
        )

        call_args = mock_llm_client.chat_completion.call_args
        assert call_args.kwargs["temperature"] == 0.5
        assert call_args.kwargs["max_tokens"] == 500
        assert call_args.kwargs["response_format"] == {"type": "json_object"}


# ==================== 边界条件 ====================


class TestLLMExtractToolEdgeCases:
    """边界条件测试"""

    async def test_extract_with_extra_fields(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回额外字段时正常处理"""
        response_with_extra = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "name": "张三",
                                "age": 25,
                                "extra_field": "ignored",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = response_with_extra

        result = await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert result.name == "张三"
        assert result.age == 25

    async def test_extract_with_minimal_response(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回最小响应"""
        minimal_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "skills": [],
                                "keywords": [],
                                "difficulty": None,
                                "seniority": None,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = minimal_response

        result = await tool.extract(text=SAMPLE_TEXT, schema=ComplexResult)

        assert result.skills == []
        assert result.keywords == []
        assert result.difficulty is None

    async def test_extract_prompt_contains_schema(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """Prompt 包含 JSON schema 描述"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        call_args = mock_llm_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "name" in prompt
        assert "age" in prompt

    async def test_extract_prompt_contains_text(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """Prompt 包含输入文本"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        call_args = mock_llm_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert SAMPLE_TEXT in prompt


# ==================== 异常流程 ====================


class TestLLMExtractToolExceptions:
    """异常流程测试"""

    async def test_extract_llm_timeout(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 调用超时"""
        mock_llm_client.chat_completion.side_effect = ExternalServiceError(
            detail="Mimo API 调用超时",
            error_code="EXT_004",
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_004"

    async def test_extract_llm_http_error(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM HTTP 错误"""
        mock_llm_client.chat_completion.side_effect = ExternalServiceError(
            detail="Mimo API 调用失败",
            error_code="EXT_003",
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_003"

    async def test_extract_invalid_json_response(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回无效 JSON"""
        invalid_response = {
            "choices": [{"message": {"content": "This is not valid JSON"}}]
        }
        mock_llm_client.chat_completion.return_value = invalid_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_009"

    async def test_extract_empty_choices(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回空 choices"""
        empty_choices_response = {"choices": []}
        mock_llm_client.chat_completion.return_value = empty_choices_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_007"

    async def test_extract_empty_content(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回空 content"""
        empty_content_response = {
            "choices": [{"message": {"content": ""}}]
        }
        mock_llm_client.chat_completion.return_value = empty_content_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_008"

    async def test_extract_pydantic_validation_error(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回不符合 schema 的数据"""
        invalid_schema_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"name": "张三", "age": "not_a_number"},
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = invalid_schema_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_010"

    async def test_extract_missing_required_fields(
        self,
        tool: LLMExtractTool,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回缺少必填字段"""
        missing_fields_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"name": "张三"},
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = missing_fields_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)

        assert exc_info.value.error_code == "EXT_010"


# ==================== 异步上下文管理器 ====================


class TestLLMExtractToolContextManager:
    """异步上下文管理器测试"""

    async def test_async_context_manager(self, mock_llm_client: AsyncMock) -> None:
        """异步上下文管理器正常工作"""
        async with LLMExtractTool(llm_client=mock_llm_client) as tool:
            mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE
            result = await tool.extract(text=SAMPLE_TEXT, schema=SimpleResult)
            assert result.name == "张三"

    async def test_close_method(self, mock_llm_client: AsyncMock) -> None:
        """close 方法调用 LLM 客户端 close"""
        tool = LLMExtractTool(llm_client=mock_llm_client)
        await tool.close()
        mock_llm_client.close.assert_called_once()
