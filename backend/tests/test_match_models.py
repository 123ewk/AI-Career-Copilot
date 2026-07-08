"""Match DTO 单元测试

职责：
- 验证 MatchInput / MatchScoreDetail / MatchCalculateRequest 的字段校验
- 覆盖正常构造、权重校验、长度限制、类型错误等场景
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.match.models import (
    DEFAULT_BM25_WEIGHT,
    DEFAULT_SEMANTIC_WEIGHT,
    MATCH_TEXT_MAX_LENGTH,
    MAX_SCORE,
    MIN_SCORE,
    MatchCalculateRequest,
    MatchInput,
    MatchScoreDetail,
)


# ==================== Fixtures ====================


@pytest.fixture
def sample_input() -> MatchInput:
    """构造一个合法的 MatchInput 样本"""
    return MatchInput(
        job_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
        job_skills=["Python", "FastAPI"],
        job_keywords=["RAG", "Agent"],
        job_text="招聘 Python 后端工程师，负责 RAG 系统开发",
        resume_skills=["Python", "PostgreSQL"],
        resume_text="熟练使用 Python 和 FastAPI 开发后端服务",
        resume_experience_years=3,
    )


# ==================== MatchInput 测试 ====================


def test_match_input_valid(sample_input: MatchInput) -> None:
    """合法 MatchInput 应能正常构造"""
    assert sample_input.job_skills == ["Python", "FastAPI"]
    assert sample_input.resume_experience_years == 3


def test_match_input_default_optional_fields() -> None:
    """可选字段使用默认值时应合法"""
    inp = MatchInput(
        job_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
    )
    assert inp.job_skills == []
    assert inp.job_keywords == []
    assert inp.job_text == ""
    assert inp.resume_skills == []
    assert inp.resume_text == ""
    assert inp.resume_experience_years is None


def test_match_input_experience_years_out_of_range() -> None:
    """工作年限超出 [0, 50] 应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            resume_experience_years=100,
        )


def test_match_input_text_too_long() -> None:
    """文本超过最大长度应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            job_text="a" * (MATCH_TEXT_MAX_LENGTH + 1),
        )


def test_match_input_skill_item_too_long() -> None:
    """job_skills 单元素超长应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            job_skills=["a" * 101],
        )


def test_match_input_keyword_item_too_long() -> None:
    """job_keywords 单元素超长应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            job_keywords=["a" * 101],
        )


def test_match_input_resume_skill_item_too_long() -> None:
    """resume_skills 单元素超长应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            resume_skills=["a" * 101],
        )


# ==================== MatchScoreDetail 测试 ====================


def test_match_score_detail_valid() -> None:
    """合法 MatchScoreDetail 应能正常构造"""
    detail = MatchScoreDetail(
        job_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
        bm25_score=75.0,
        semantic_score=80.0,
        combined_score=78.0,
        weight_bm25=0.4,
        weight_semantic=0.6,
        scored_at=datetime.now(timezone.utc),
    )
    assert 0 <= detail.combined_score <= MAX_SCORE


def test_match_score_detail_score_out_of_range() -> None:
    """分数超出 [0, 100] 应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchScoreDetail(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            bm25_score=101.0,
            semantic_score=80.0,
            combined_score=90.0,
            weight_bm25=0.4,
            weight_semantic=0.6,
            scored_at=datetime.now(timezone.utc),
        )


def test_match_score_detail_weight_out_of_range() -> None:
    """权重超出 [0, 1] 应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchScoreDetail(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            bm25_score=50.0,
            semantic_score=50.0,
            combined_score=50.0,
            weight_bm25=1.5,
            weight_semantic=-0.5,
            scored_at=datetime.now(timezone.utc),
        )


# ==================== MatchCalculateRequest 测试 ====================


def test_match_calculate_request_default_weights(sample_input: MatchInput) -> None:
    """默认权重应符合 DEFAULT_BM25_WEIGHT / DEFAULT_SEMANTIC_WEIGHT"""
    req = MatchCalculateRequest(match_input=sample_input)
    assert req.weight_bm25 == DEFAULT_BM25_WEIGHT
    assert req.weight_semantic == DEFAULT_SEMANTIC_WEIGHT


def test_match_calculate_request_custom_weights(sample_input: MatchInput) -> None:
    """自定义合法权重应能构造"""
    req = MatchCalculateRequest(
        match_input=sample_input,
        weight_bm25=0.7,
        weight_semantic=0.3,
    )
    assert req.weight_bm25 == 0.7
    assert req.weight_semantic == 0.3


def test_match_calculate_request_weights_sum_not_one(sample_input: MatchInput) -> None:
    """权重和不等于 1.0 应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchCalculateRequest(
            match_input=sample_input,
            weight_bm25=0.5,
            weight_semantic=0.4,
        )


def test_match_calculate_request_weights_out_of_range(sample_input: MatchInput) -> None:
    """权重超出 [0, 1] 应抛 ValidationError"""
    with pytest.raises(ValidationError):
        MatchCalculateRequest(
            match_input=sample_input,
            weight_bm25=-0.1,
            weight_semantic=1.1,
        )
