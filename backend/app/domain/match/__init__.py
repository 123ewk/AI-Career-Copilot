"""Match 模块公共接口

导出 DTO 与 Scorer，便于上层 Service / Router 使用：
- from app.domain.match import MatchInput, CombinedScorer
"""

from app.domain.match.models import (
    DEFAULT_BM25_WEIGHT,
    DEFAULT_SEMANTIC_WEIGHT,
    MATCH_KEYWORDS_MAX_LENGTH,
    MATCH_SKILLS_MAX_LENGTH,
    MATCH_TEXT_MAX_LENGTH,
    MAX_SCORE,
    MIN_SCORE,
    MatchCalculateRequest,
    MatchInput,
    MatchScoreDetail,
)
from app.domain.match.scorer import (
    BM25Scorer,
    CombinedScorer,
    EmbeddingBackend,
    NullEmbeddingBackend,
    SemanticScorer,
    SentenceTransformerBackend,
    create_default_scorer,
)

__all__ = [
    # DTO
    "MatchInput",
    "MatchScoreDetail",
    "MatchCalculateRequest",
    # 常量
    "MATCH_TEXT_MAX_LENGTH",
    "MATCH_SKILLS_MAX_LENGTH",
    "MATCH_KEYWORDS_MAX_LENGTH",
    "DEFAULT_BM25_WEIGHT",
    "DEFAULT_SEMANTIC_WEIGHT",
    "MAX_SCORE",
    "MIN_SCORE",
    # Scorer
    "EmbeddingBackend",
    "NullEmbeddingBackend",
    "SentenceTransformerBackend",
    "BM25Scorer",
    "SemanticScorer",
    "CombinedScorer",
    "create_default_scorer",
]
