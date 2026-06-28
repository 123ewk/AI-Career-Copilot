"""Job Extractor 单元测试

职责：
- 测试 JD 信息提取功能（skills/keywords/difficulty/seniority）
- 使用 unittest.mock 模拟 LLM API 调用
- 覆盖正常流程、边界条件、异常流程

测试策略：
- Mock LLM：使用 unittest.mock 模拟 API 响应，避免真实 API 调用
- 正常流程：标准 JD 提取
- 边界条件：空文本、超长文本
- 异常流程：API 超时、响应解析失败
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import ExternalServiceError
from app.domain.job.extractor import JobExtractor
from app.domain.job.models import JobAnalysisResult

# ==================== Fixtures ====================

@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """模拟 LLM 客户端"""
    client = AsyncMock()
    return client


@pytest.fixture
def extractor(mock_llm_client: AsyncMock) -> JobExtractor:
    """JobExtractor 实例（使用模拟 LLM 客户端）"""
    return JobExtractor(llm_client=mock_llm_client)


# ==================== 测试数据 ====================

# 标准 JD 文本
STANDARD_JD = """
职位描述：
1. 负责公司核心系统的架构设计与开发
2. 参与技术方案评审和代码 Review
3. 指导初级开发人员，提升团队技术水平

任职要求：
1. 本科及以上学历，计算机相关专业
2. 5 年以上 Python 开发经验
3. 熟悉 FastAPI、SQLAlchemy、Redis 等技术栈
4. 有大型项目架构设计经验优先

福利待遇：
1. 五险一金
2. 带薪年假 15 天
3. 年终奖 3-6 个月
"""

# LLM 模拟响应
MOCK_LLM_RESPONSE = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {
                        "skills": ["Python", "FastAPI", "SQLAlchemy", "Redis"],
                        "keywords": ["架构设计", "系统开发", "代码 Review"],
                        "difficulty": "hard",
                        "seniority": "senior",
                    },
                    ensure_ascii=False,
                )
            }
        }
    ],
    "usage": {
        "total_tokens": 500,
    },
}


# ==================== 正常流程 ====================

class TestJobExtractorNormalFlow:
    """正常流程测试"""

    async def test_extract_standard_jd(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """标准 JD 提取"""
        # 设置模拟响应
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        # 执行提取
        result = await extractor.extract(STANDARD_JD)

        # 验证结果
        assert isinstance(result, JobAnalysisResult)
        assert "Python" in result.skills
        assert "FastAPI" in result.skills
        assert "架构设计" in result.keywords
        assert result.difficulty == "hard"
        assert result.seniority == "senior"

        # 验证 LLM 调用参数
        mock_llm_client.chat_completion.assert_called_once()
        call_args = mock_llm_client.chat_completion.call_args
        assert call_args.kwargs["temperature"] == 0.1
        assert call_args.kwargs["max_tokens"] == 1000
        assert call_args.kwargs["response_format"] == {"type": "json_object"}

    async def test_extract_returns_valid_pydantic_model(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """提取结果符合 Pydantic 模型"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        result = await extractor.extract(STANDARD_JD)

        # 验证 Pydantic 模型校验通过
        assert result.model_dump() is not None
        assert len(result.skills) <= 100
        assert len(result.keywords) <= 100

    async def test_extract_with_extra_fields(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回额外字段时正常处理"""
        # 添加额外字段
        response_with_extra = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "skills": ["Python"],
                                "keywords": ["AI"],
                                "difficulty": "medium",
                                "seniority": "mid",
                                "extra_field": "should be ignored",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = response_with_extra

        # 应该正常处理，忽略额外字段
        result = await extractor.extract(STANDARD_JD)
        assert result.skills == ["Python"]


# ==================== 边界条件 ====================

class TestJobExtractorEdgeCases:
    """边界条件测试"""

    async def test_extract_with_minimal_response(
        self,
        extractor: JobExtractor,
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

        result = await extractor.extract(STANDARD_JD)
        assert result.skills == []
        assert result.keywords == []
        assert result.difficulty is None
        assert result.seniority is None

    async def test_extract_with_long_jd(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """超长 JD 文本"""
        long_jd = "A" * 60000  # 超过 50KB 限制
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        # 应该正常处理（内部会截断）
        result = await extractor.extract(long_jd)
        assert result is not None

        # 验证 Prompt 中的 JD 被截断
        call_args = mock_llm_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert len(prompt) < 60000


# ==================== 异常流程 ====================

class TestJobExtractorExceptions:
    """异常流程测试"""

    async def test_extract_llm_timeout(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 调用超时"""
        mock_llm_client.chat_completion.side_effect = ExternalServiceError(
            detail="Mimo API 调用超时",
            error_code="EXT_004",
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            await extractor.extract(STANDARD_JD)

        assert exc_info.value.error_code == "EXT_004"

    async def test_extract_llm_http_error(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM HTTP 错误"""
        mock_llm_client.chat_completion.side_effect = ExternalServiceError(
            detail="Mimo API 调用失败",
            error_code="EXT_003",
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            await extractor.extract(STANDARD_JD)

        assert exc_info.value.error_code == "EXT_003"

    async def test_extract_invalid_json_response(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回无效 JSON"""
        invalid_response = {
            "choices": [
                {
                    "message": {
                        "content": "This is not valid JSON"
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = invalid_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await extractor.extract(STANDARD_JD)

        assert exc_info.value.error_code == "EXT_009"

    async def test_extract_empty_choices(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回空 choices"""
        empty_choices_response = {
            "choices": []
        }
        mock_llm_client.chat_completion.return_value = empty_choices_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await extractor.extract(STANDARD_JD)

        assert exc_info.value.error_code == "EXT_007"

    async def test_extract_empty_content(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回空 content"""
        empty_content_response = {
            "choices": [
                {
                    "message": {
                        "content": ""
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = empty_content_response

        with pytest.raises(ExternalServiceError) as exc_info:
            await extractor.extract(STANDARD_JD)

        assert exc_info.value.error_code == "EXT_008"

    async def test_extract_invalid_seniority_value(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回无效的 seniority 值"""
        invalid_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "skills": ["Python"],
                                "keywords": ["AI"],
                                "difficulty": "medium",
                                "seniority": "invalid_value",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = invalid_response

        # Pydantic 校验应该失败
        with pytest.raises(ExternalServiceError):
            await extractor.extract(STANDARD_JD)

    async def test_extract_invalid_difficulty_value(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """LLM 返回无效的 difficulty 值"""
        invalid_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "skills": ["Python"],
                                "keywords": ["AI"],
                                "difficulty": "invalid_value",
                                "seniority": "mid",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        mock_llm_client.chat_completion.return_value = invalid_response

        # Pydantic 校验应该失败
        with pytest.raises(ExternalServiceError):
            await extractor.extract(STANDARD_JD)


# ==================== Prompt 构建 ====================

class TestJobExtractorPrompt:
    """Prompt 构建测试"""

    async def test_prompt_contains_jd_text(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """Prompt 包含 JD 文本"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        await extractor.extract(STANDARD_JD)

        # 验证 Prompt 包含 JD 内容
        call_args = mock_llm_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "职位描述" in prompt
        assert "任职要求" in prompt
        assert "Python" in prompt

    async def test_prompt_requests_json_format(
        self,
        extractor: JobExtractor,
        mock_llm_client: AsyncMock,
    ) -> None:
        """Prompt 要求 JSON 格式输出"""
        mock_llm_client.chat_completion.return_value = MOCK_LLM_RESPONSE

        await extractor.extract(STANDARD_JD)

        # 验证 Prompt 包含 JSON 格式要求
        call_args = mock_llm_client.chat_completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "JSON" in prompt
        assert "skills" in prompt
        assert "keywords" in prompt
        assert "difficulty" in prompt
        assert "seniority" in prompt
