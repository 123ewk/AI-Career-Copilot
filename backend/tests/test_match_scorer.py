"""Match Scorer 单元测试

职责：
- 验证 BM25Scorer / SemanticScorer / CombinedScorer 的打分逻辑
- 使用 FakeEmbeddingBackend 避免加载真实句向量模型
- 覆盖正常命中、未命中、空输入、权重变化、模型降级等场景
"""

from __future__ import annotations

import math
import uuid
from typing import Final
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.domain.match.models import MatchInput, MatchScoreDetail, utc_now
from app.domain.match.scorer import (
    BM25Scorer,
    CombinedScorer,
    EncodingError,
    ModelLoadError,
    NullEmbeddingBackend,
    SemanticScorer,
    SentenceTransformerBackend,
    create_default_scorer,
)


# ==================== Fixtures ====================


@pytest.fixture
def sample_match_input() -> MatchInput:
    """构造一个用于 Scorer 测试的 MatchInput"""
    return MatchInput(
        job_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
        job_skills=["Python", "FastAPI"],
        job_keywords=["RAG"],
        job_text="招聘 Python 后端工程师，负责 RAG 系统开发",
        resume_skills=["Python", "PostgreSQL"],
        resume_text="熟练使用 Python 和 FastAPI 开发后端服务",
        resume_experience_years=3,
    )


class FakeEmbeddingBackend:
    """测试用 Embedding Backend

    通过可控的向量输出验证 SemanticScorer 行为：
    - identical: 两段文本返回相同向量，相似度应为 100
    - orthogonal: 两段文本返回正交向量，相似度应为 0
    """

    def __init__(self, mode: str = "identical", dim: int = 4) -> None:
        if mode not in {"identical", "orthogonal", "zero"}:
            raise ValueError(f"不支持的 mode: {mode}")
        self._mode = mode
        self._dim = dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for i, _ in enumerate(texts):
            if self._mode == "zero":
                embeddings.append([0.0] * self._dim)
            elif self._mode == "identical":
                # 所有文本返回相同向量
                embeddings.append([1.0] * self._dim)
            else:  # orthogonal
                vec = [0.0] * self._dim
                vec[i % self._dim] = 1.0
                embeddings.append(vec)
        return embeddings


class CountingEmbeddingBackend:
    """记录 encode 调用次数的 Backend，用于验证懒加载"""

    def __init__(self) -> None:
        self.call_count = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.call_count += 1
        return [[1.0, 0.0] for _ in texts]


# ==================== BM25Scorer 测试 ====================


def test_bm25_hit_higher_than_miss() -> None:
    """关键词命中得分应高于未命中"""
    scorer = BM25Scorer()
    hit = scorer.score("Python", "Python 后端开发工程师")
    miss = scorer.score("Python", "Java 前端开发工程师")
    assert hit > miss
    assert 0 <= hit <= 100
    assert 0 <= miss <= 100


def test_bm25_multiple_hits_higher_than_single() -> None:
    """命中多个关键词应高于命中一个"""
    scorer = BM25Scorer()
    single = scorer.score("Python", "Python 开发")
    multi = scorer.score("Python FastAPI", "Python FastAPI 开发")
    assert multi > single


def test_bm25_empty_query_returns_zero() -> None:
    """空 query 应返回 0"""
    scorer = BM25Scorer()
    assert scorer.score("", "Python 开发") == 0.0


def test_bm25_empty_doc_returns_zero() -> None:
    """空 doc 应返回 0"""
    scorer = BM25Scorer()
    assert scorer.score("Python", "") == 0.0


def test_bm25_case_insensitive() -> None:
    """BM25 分词应统一小写，大小写不敏感"""
    scorer = BM25Scorer()
    lower = scorer.score("python", "python 开发")
    upper = scorer.score("Python", "PYTHON 开发")
    assert lower == upper


def test_bm25_scale_affects_score() -> None:
    """scale 越大，相同输入得分应越低"""
    query, doc = "Python FastAPI", "Python FastAPI 开发"
    strict = BM25Scorer(scale=1.0).score(query, doc)
    loose = BM25Scorer(scale=10.0).score(query, doc)
    assert strict > loose


def test_bm25_invalid_scale() -> None:
    """scale <= 0 应抛 ValueError"""
    with pytest.raises(ValueError):
        BM25Scorer(scale=0.0)


# ==================== SemanticScorer 测试 ====================


def test_semantic_identical_vectors() -> None:
    """相同向量应返回 100"""
    backend = FakeEmbeddingBackend(mode="identical")
    scorer = SemanticScorer(backend)
    assert scorer.score("text a", "text b") == 100.0


def test_semantic_orthogonal_vectors() -> None:
    """正交向量应返回 0"""
    backend = FakeEmbeddingBackend(mode="orthogonal")
    scorer = SemanticScorer(backend)
    assert scorer.score("text a", "text b") == 0.0


def test_semantic_empty_text_returns_zero() -> None:
    """空文本应返回 0"""
    backend = FakeEmbeddingBackend(mode="identical")
    scorer = SemanticScorer(backend)
    assert scorer.score("", "text") == 0.0
    assert scorer.score("text", "") == 0.0


def test_semantic_wrong_embedding_count() -> None:
    """Backend 返回向量数量错误应抛 EncodingError"""
    class BadBackend:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0]]  # 只返回一个向量

    scorer = SemanticScorer(BadBackend())
    with pytest.raises(EncodingError):
        scorer.score("a", "b")


def test_semantic_zero_vectors() -> None:
    """零向量应返回 0，不触发除零"""
    backend = FakeEmbeddingBackend(mode="zero")
    scorer = SemanticScorer(backend)
    assert scorer.score("a", "b") == 0.0


def test_semantic_mismatched_dimensions() -> None:
    """向量维度不一致应抛 EncodingError"""
    class MismatchBackend:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0], [1.0, 0.0, 0.0]]

    scorer = SemanticScorer(MismatchBackend())
    with pytest.raises(EncodingError):
        scorer.score("a", "b")


# ==================== CombinedScorer 测试 ====================


def test_combined_score_weighted_sum(sample_match_input: MatchInput) -> None:
    """综合分应为加权求和"""
    bm25 = BM25Scorer()
    semantic = SemanticScorer(FakeEmbeddingBackend(mode="identical"))
    scorer = CombinedScorer(
        bm25_scorer=bm25,
        semantic_scorer=semantic,
        weight_bm25=0.4,
        weight_semantic=0.6,
    )
    detail = scorer.score(sample_match_input)

    expected = round(0.4 * detail.bm25_score + 0.6 * detail.semantic_score, 2)
    assert detail.combined_score == expected
    assert isinstance(detail, MatchScoreDetail)


def test_combined_without_semantic_scorer(sample_match_input: MatchInput) -> None:
    """不传入 semantic_scorer 时，semantic_score 应为 0"""
    scorer = CombinedScorer(semantic_scorer=None, weight_bm25=1.0, weight_semantic=0.0)
    detail = scorer.score(sample_match_input)
    assert detail.semantic_score == 0.0
    assert detail.combined_score == round(detail.bm25_score, 2)


def test_combined_weight_changes_result(sample_match_input: MatchInput) -> None:
    """不同权重应产生不同综合分"""
    semantic = SemanticScorer(FakeEmbeddingBackend(mode="identical"))
    scorer1 = CombinedScorer(
        semantic_scorer=semantic, weight_bm25=0.0, weight_semantic=1.0
    )
    scorer2 = CombinedScorer(
        semantic_scorer=semantic, weight_bm25=1.0, weight_semantic=0.0
    )
    detail1 = scorer1.score(sample_match_input)
    detail2 = scorer2.score(sample_match_input)
    assert detail1.combined_score == 100.0
    assert detail2.combined_score == detail2.bm25_score


def test_combined_invalid_weights() -> None:
    """权重不合法应抛 ValueError"""
    with pytest.raises(ValueError):
        CombinedScorer(weight_bm25=0.5, weight_semantic=0.4)


def test_combined_score_within_bounds(sample_match_input: MatchInput) -> None:
    """所有分数应在 [0, 100] 内"""
    semantic = SemanticScorer(FakeEmbeddingBackend(mode="identical"))
    scorer = CombinedScorer(semantic_scorer=semantic)
    detail = scorer.score(sample_match_input)
    assert 0 <= detail.bm25_score <= 100
    assert 0 <= detail.semantic_score <= 100
    assert 0 <= detail.combined_score <= 100


# ==================== Embedding Backend 测试 ====================


def test_null_embedding_backend() -> None:
    """NullEmbeddingBackend 返回零向量"""
    backend = NullEmbeddingBackend(dim=4)
    embeddings = backend.encode(["a", "b"])
    assert len(embeddings) == 2
    assert embeddings[0] == [0.0, 0.0, 0.0, 0.0]
    assert embeddings[1] == [0.0, 0.0, 0.0, 0.0]


def test_sentence_transformer_backend_lazy_load() -> None:
    """SentenceTransformerBackend 应懒加载模型"""
    backend = SentenceTransformerBackend("dummy-model")
    assert backend._model is None

    # 模拟 SentenceTransformer 类，验证加载被触发
    mock_model = MagicMock()
    # encode 返回 numpy ndarray，模拟真实模型行为
    mock_model.encode.return_value = np.array([[1.0, 0.0]])

    with patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_st.return_value = mock_model
        embeddings = backend.encode(["hello"])

    assert backend._model is mock_model
    assert embeddings == [[1.0, 0.0]]
    mock_st.assert_called_once_with("dummy-model")


def test_sentence_transformer_backend_load_error() -> None:
    """模型加载失败应抛 ModelLoadError"""
    backend = SentenceTransformerBackend("nonexistent-model")
    with patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_st.side_effect = OSError("model not found")
        with pytest.raises(ModelLoadError):
            backend.encode(["hello"])


def test_create_default_scorer_fallback() -> None:
    """create_default_scorer 在模型加载失败时应降级"""
    with patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_st.side_effect = OSError("model not found")
        scorer = create_default_scorer(
            model_name_or_path="bad-model", fallback_on_error=True
        )

    detail = scorer.score(
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            job_skills=["Python"],
            resume_skills=["Python"],
            job_text="Python",
            resume_text="Python",
        )
    )
    assert detail.semantic_score == 0.0
    assert detail.bm25_score > 0.0


def test_create_default_scorer_no_fallback() -> None:
    """create_default_scorer fallback_on_error=False 时应抛出异常"""
    with patch(
        "sentence_transformers.SentenceTransformer"
    ) as mock_st:
        mock_st.side_effect = OSError("model not found")
        with pytest.raises(ModelLoadError):
            create_default_scorer(
                model_name_or_path="bad-model", fallback_on_error=False
            )


def test_create_default_scorer_semantic_disabled() -> None:
    """semantic_enabled=False 时不加载模型，semantic_score 为 0"""
    scorer = create_default_scorer(semantic_enabled=False)
    detail = scorer.score(
        MatchInput(
            job_id=uuid.uuid4(),
            resume_id=uuid.uuid4(),
            job_skills=["Python"],
            resume_skills=["Python"],
            job_text="Python",
            resume_text="Python",
        )
    )
    assert detail.semantic_score == 0.0
    assert detail.bm25_score > 0.0


# ==================== 工具函数测试 ====================


def test_utc_now_returns_utc() -> None:
    """utc_now 应返回带 timezone.utc 的时间"""
    now = utc_now()
    assert now.tzinfo is not None
