"""Match Scorer（Step 1.7.2）

职责：
- 计算「岗位 - 简历」的技能匹配度
- 基础层：BM25 关键词匹配（rank-bm25）
- 语义层：Sentence Transformer 句向量余弦相似度
- 融合层：加权求和得到最终匹配分数

设计动机：
- 纯计算模块：不依赖 DB / MQ / HTTP / 文件 IO，可被单元测试、Service、Consumer 直接调用
- BM25 负责精确技能命中：对「Python」「FastAPI」等硬技能标签敏感、可解释
- 语义模型负责近义理解：捕捉「LLM 大模型」「大语言模型」等同义表达
- 模型懒加载：项目启动时不加载大模型，第一次打分请求时才加载，后续复用同一实例
- 降级机制：模型加载失败时自动使用 NullEmbeddingBackend，保证核心路径可用

关键算法：
1. BM25 归一化：原始 BM25 分数无上界，使用 `100 * (1 - exp(-raw / scale))` 映射到 [0, 100]
   - scale 默认 5.0，可通过 CombinedScorer 构造函数调整
   - 该函数单调递增、有界、平滑，适合跨 query 比较
2. 语义归一化：余弦相似度 ∈ [-1, 1]，使用 `max(0, sim) * 100` 映射到 [0, 100]
   - 原因：句向量模型输出已归一化，相似度实际 ∈ [0, 1]；取 max(0, *) 避免负相关噪声
3. 综合分：`combined = weight_bm25 * bm25_score + weight_semantic * semantic_score`

并发安全：
- SentenceTransformer 模型加载使用 threading.Lock，防止多个协程/线程同时加载导致内存浪费
- 模型加载完成后，encode() 本身无共享状态修改，可被多个调用并发读取
- 注意：encode() 是 CPU 密集型同步调用，在 async Consumer 中应通过 loop.run_in_executor 或 asyncio.to_thread 调用

潜在风险：
- 首次 encode() 会触发模型加载，可能耗时 1-3s；调用方需有超时或 loading 提示
- BM25 对中文未分词文本效果有限，因此 query 侧使用结构化 skills/keywords，doc 侧使用完整文本 + skills
- 模型路径不存在时：通过 NullEmbeddingBackend 降级，semantic_score 固定为 0，combined 分仍有 BM25 贡献
"""

import math
import re
import threading
from typing import Final, Protocol, runtime_checkable

from app.domain.match.models import (
    DEFAULT_BM25_WEIGHT,
    DEFAULT_SEMANTIC_WEIGHT,
    MAX_SCORE,
    MIN_SCORE,
    MatchInput,
    MatchScoreDetail,
    utc_now,
)

# ==================== 异常 ====================


class ScorerError(Exception):
    """Scorer 模块基础异常

    为什么不用 app.core.exceptions：
    - scorer.py 是纯计算模块，不依赖 HTTP 错误体系
    - Service 层可捕获后转换为 InfrastructureException / BusinessException
    """


class ModelLoadError(ScorerError):
    """句向量模型加载失败"""


class EncodingError(ScorerError):
    """文本编码失败"""


# ==================== 常量 ====================

# 默认 embedding 维度（用于 NullEmbeddingBackend）
# 实际维度由模型决定，这里仅作降级兜底
DEFAULT_EMBEDDING_DIM: Final[int] = 384

# BM25 超参数
BM25_K1: Final[float] = 1.5
BM25_B: Final[float] = 0.75
# 单文档场景下 rank-bm25 的 IDF 会退化为负数或零，
# 因此使用固定正 IDF，保留 BM25 的 TF 饱和特性
BM25_FIXED_IDF: Final[float] = 1.0

# BM25 原始分数映射到 [0, 100] 的尺度参数
# 越大，相同 raw score 映射后的分数越低；用于调节 BM25 与 semantic 分的动态范围
DEFAULT_BM25_SCALE: Final[float] = 5.0

# 分词正则：匹配中文、英文、数字、下划线字符
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\w\u4e00-\u9fff]+")


# ==================== Embedding Backend Protocol ====================


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Embedding 后端协议

    设计：
    - 通过 Protocol 解耦 Scorer 与具体模型实现
    - 单测可注入 FakeEmbeddingBackend 提供确定性向量
    - 生产环境使用 SentenceTransformerBackend
    - 模型不可用时降级为 NullEmbeddingBackend
    """

    def encode(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为向量列表

        参数：
            texts: 待编码文本列表

        返回：
            与 texts 等长的向量列表，每个向量维度一致

        异常：
            EncodingError: 编码失败时抛出
        """
        ...


class NullEmbeddingBackend:
    """空 Embedding 后端（降级用）

    用途：
    - 模型加载失败时兜底，避免整个匹配流程崩溃
    - 单测中提供可预测的零向量输出

    注意：
    - 返回零向量，语义相似度为 0，semantic_score 归一化后为 0
    """

    def __init__(self, dim: int = DEFAULT_EMBEDDING_DIM) -> None:
        self._dim = dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        """返回与输入等长的零向量"""
        return [[0.0] * self._dim for _ in texts]


class SentenceTransformerBackend:
    """Sentence Transformers 封装

    懒加载机制：
    - __init__ 只保存配置，不加载模型
    - 第一次调用 encode() 时才加载 SentenceTransformer
    - 使用 threading.Lock 保证并发下只加载一次

    模型路径：
    - 支持 HuggingFace 模型名（如 "BAAI/bge-small-zh-v1.5"），首次自动下载缓存
    - 支持本地绝对路径，不触发网络下载
    """

    def __init__(self, model_name_or_path: str) -> None:
        self._model_name_or_path = model_name_or_path
        self._model: object | None = None
        self._lock = threading.Lock()

    def _load_model(self) -> object:
        """加载句向量模型

        为什么延迟导入：
        - sentence-transformers 会拉入 torch/transformers 等重依赖
        - 延迟导入可避免模块加载时失败（如无模型环境跑单测 BM25 部分）
        """
        try:
            # 延迟导入，避免 import 时强依赖 sentence-transformers
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ModelLoadError(
                f"sentence-transformers 未安装，无法加载模型 {self._model_name_or_path}"
            ) from exc

        try:
            model = SentenceTransformer(self._model_name_or_path)
        except Exception as exc:
            raise ModelLoadError(
                f"加载模型 {self._model_name_or_path} 失败: {exc}"
            ) from exc

        return model

    def encode(self, texts: list[str]) -> list[list[float]]:
        """编码文本列表"""
        if self._model is None:
            with self._lock:
                # 双重检查锁定：避免多个线程同时进入加载逻辑
                if self._model is None:
                    self._model = self._load_model()

        try:
            # SentenceTransformer.encode 返回 numpy.ndarray，转为 Python list
            embeddings = self._model.encode(texts, convert_to_numpy=True)
            return [emb.tolist() for emb in embeddings]
        except Exception as exc:
            raise EncodingError(f"文本编码失败: {exc}") from exc


# ==================== BM25 Scorer ====================


class BM25Scorer:
    """BM25 关键词匹配打分器

    职责：
    - 对 query 文本和 doc 文本做 BM25 打分
    - 将 BM25 raw score 归一化到 [0, 100]

    分词策略：
    - 按中文/英文/数字词元切分，统一小写
    - 不过滤停用词：技能标签通常很短，过滤停用词可能误删有效词（如 "C"、"R"）

    算法说明：
    - 使用 BM25 的 TF 组件：tf * (k1 + 1) / (tf + k1 * (1 - b + b * L))
    - 单文档场景下 IDF 退化为负/零，因此使用固定正 IDF=1.0
    - 归一化：使用 `100 * (1 - exp(-raw / scale))` 将无上界的 raw score 映射到 [0, 100]
      - 该函数单调递增、有界、平滑，且多关键词命中的 raw score 更高，映射后分数也更高
    """

    def __init__(self, scale: float = DEFAULT_BM25_SCALE) -> None:
        """初始化 BM25Scorer

        参数：
            scale: 归一化尺度参数，必须大于 0。越大，相同 raw score 映射后的分数越低。
        """
        if scale <= 0:
            raise ValueError(f"BM25 scale 必须大于 0，当前为 {scale}")
        self._scale = scale

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词

        策略：
        - 统一小写：BM25 对大小写敏感，技能 "Python" 与 "python" 应视为同一词
        - 正则匹配中文、英文、数字、下划线：避免标点符号成为独立 token
        - 过滤纯数字 token：技能匹配中 "3" "2024" 等数字通常无意义
        """
        tokens = _TOKEN_PATTERN.findall(text.lower())
        return [token for token in tokens if not token.isdigit()]

    def score(self, query: str, doc: str) -> float:
        """计算 query 与 doc 的 BM25 匹配分数

        参数：
            query: 查询文本（如岗位 skills + keywords 拼接）
            doc: 文档文本（如简历原文 + 简历 skills 拼接）

        返回：
            归一化后的分数，范围 [0, 100]
        """
        query_tokens = self._tokenize(query)
        doc_tokens = self._tokenize(doc)

        if not query_tokens or not doc_tokens:
            return MIN_SCORE

        # 统计 doc 中每个 token 的词频
        doc_freqs: dict[str, int] = {}
        for token in doc_tokens:
            doc_freqs[token] = doc_freqs.get(token, 0) + 1

        doc_len = len(doc_tokens)
        # 单文档场景，avgdl 等于 doc_len，因此 L = 1
        length_factor = 1.0  # (1 - b + b * doc_len / avgdl)

        total_score = 0.0
        for token in query_tokens:
            tf = doc_freqs.get(token, 0)
            if tf == 0:
                continue

            # BM25 TF 组件（含长度归一化）
            tf_component = (
                tf * (BM25_K1 + 1)
                / (tf + BM25_K1 * length_factor)
            )
            total_score += BM25_FIXED_IDF * tf_component

        # 将无上界的 raw BM25 score 映射到 [0, 100]
        # 选择 1 - exp(-x/scale) 的原因：
        # - 单调递增、有界、平滑
        # - 多关键词命中时 raw score 更高，映射后分数也更高
        # - scale 控制分数增长速度，便于与 semantic 分对齐动态范围
        normalized = (1.0 - math.exp(-total_score / self._scale)) * MAX_SCORE
        return max(MIN_SCORE, min(MAX_SCORE, normalized))


# ==================== Semantic Scorer ====================


class SemanticScorer:
    """语义相似度打分器

    职责：
    - 使用句向量模型编码两段文本
    - 计算余弦相似度并归一化到 [0, 100]

    设计：
    - 通过 EmbeddingBackend Protocol 注入，解耦模型实现
    - 不缓存 embedding：调用方（如 Service）可缓存 job_text embedding 以提升批量匹配性能
    """

    def __init__(self, backend: EmbeddingBackend) -> None:
        self._backend = backend

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度

        为什么不用 numpy：
        - scorer.py 是纯计算模块，尽量减少外部依赖
        - 向量维度通常 384/768，纯 Python 计算开销可忽略
        """
        if len(a) != len(b):
            raise EncodingError(
                f"向量维度不一致: {len(a)} vs {len(b)}"
            )

        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b):
            dot += x * y
            norm_a += x * x
            norm_b += y * y

        if norm_a == 0.0 or norm_b == 0.0:
            # 零向量视为完全不相关
            return 0.0

        return dot / math.sqrt(norm_a * norm_b)

    def score(self, text_a: str, text_b: str) -> float:
        """计算两段文本的语义相似度

        参数：
            text_a: 文本 A（如岗位 JD 原文）
            text_b: 文本 B（如简历原文）

        返回：
            归一化后的分数，范围 [0, 100]
        """
        text_a = text_a.strip()
        text_b = text_b.strip()

        if not text_a or not text_b:
            return MIN_SCORE

        embeddings = self._backend.encode([text_a, text_b])
        if len(embeddings) != 2:
            raise EncodingError(
                f"Embedding backend 返回向量数量错误: 期望 2，实际 {len(embeddings)}"
            )

        similarity = self._cosine_similarity(embeddings[0], embeddings[1])
        # 归一化到 [0, 100]：负相似度视为 0
        normalized = max(0.0, similarity) * MAX_SCORE
        return max(MIN_SCORE, min(MAX_SCORE, normalized))


# ==================== Combined Scorer ====================


class CombinedScorer:
    """综合匹配度打分器

    职责：
    - 组合 BM25 与语义相似度分数
    - 按配置权重加权求和
    - 返回结构化的 MatchScoreDetail

    输入处理：
    - BM25 query: 岗位 skills + 岗位 keywords
    - BM25 doc: 简历原文 + 简历 skills
    - 语义文本 A: 岗位 JD 原文
    - 语义文本 B: 简历原文
    """

    def __init__(
        self,
        bm25_scorer: BM25Scorer | None = None,
        semantic_scorer: SemanticScorer | None = None,
        weight_bm25: float = DEFAULT_BM25_WEIGHT,
        weight_semantic: float = DEFAULT_SEMANTIC_WEIGHT,
    ) -> None:
        if not (0.0 <= weight_bm25 <= 1.0):
            raise ValueError(f"weight_bm25 必须在 [0, 1] 之间，当前为 {weight_bm25}")
        if not (0.0 <= weight_semantic <= 1.0):
            raise ValueError(
                f"weight_semantic 必须在 [0, 1] 之间，当前为 {weight_semantic}"
            )
        if abs(weight_bm25 + weight_semantic - 1.0) > 1e-6:
            raise ValueError(
                f"权重和必须等于 1.0，当前为 {weight_bm25 + weight_semantic}"
            )

        self._bm25_scorer = bm25_scorer or BM25Scorer()
        self._semantic_scorer = semantic_scorer
        self._weight_bm25 = weight_bm25
        self._weight_semantic = weight_semantic

    def score(self, match_input: MatchInput) -> MatchScoreDetail:
        """计算岗位与简历的匹配分数

        参数：
            match_input: 匹配输入

        返回：
            MatchScoreDetail，包含 bm25_score / semantic_score / combined_score
        """
        # BM25 query：岗位侧结构化标签
        query_parts = match_input.job_skills + match_input.job_keywords
        query = " ".join(query_parts)

        # BM25 doc：简历原文 + 简历技能
        doc_parts = [match_input.resume_text]
        if match_input.resume_skills:
            doc_parts.append(" ".join(match_input.resume_skills))
        doc = " ".join(part for part in doc_parts if part)

        bm25_score = self._bm25_scorer.score(query, doc)

        # 语义相似度
        if self._semantic_scorer is not None:
            semantic_score = self._semantic_scorer.score(
                match_input.job_text, match_input.resume_text
            )
        else:
            semantic_score = MIN_SCORE

        # 加权融合
        combined = (
            self._weight_bm25 * bm25_score
            + self._weight_semantic * semantic_score
        )
        combined = round(max(MIN_SCORE, min(MAX_SCORE, combined)), 2)

        return MatchScoreDetail(
            job_id=match_input.job_id,
            resume_id=match_input.resume_id,
            bm25_score=round(bm25_score, 2),
            semantic_score=round(semantic_score, 2),
            combined_score=combined,
            weight_bm25=self._weight_bm25,
            weight_semantic=self._weight_semantic,
            scored_at=utc_now(),
        )


def create_default_scorer(
    model_name_or_path: str | None = None,
    weight_bm25: float = DEFAULT_BM25_WEIGHT,
    weight_semantic: float = DEFAULT_SEMANTIC_WEIGHT,
    fallback_on_error: bool = True,
    semantic_enabled: bool | None = None,
) -> CombinedScorer:
    """创建默认 CombinedScorer

    参数：
        model_name_or_path: 句向量模型名或本地路径；为 None 时从 Settings 读取
        weight_bm25: BM25 权重
        weight_semantic: 语义权重
        fallback_on_error: 模型加载失败时是否降级为 NullEmbeddingBackend
        semantic_enabled: 是否启用语义匹配；为 None 时从 Settings 读取

    返回：
        配置好的 CombinedScorer

    设计：
    - 提供工厂函数，避免调用方直接处理 EmbeddingBackend 细节
    - 模型路径默认从 Settings（环境变量 / .env）读取，禁止硬编码
    - fallback_on_error=True 时，模型加载失败不抛异常，保证服务可启动
    - semantic_enabled=False 时直接跳过模型加载，使用 NullEmbeddingBackend，用于纯 BM25 压测或模型未就绪场景
    """
    # 延迟导入 Settings，避免 scorer.py 模块加载时形成循环依赖
    from app.core.settings import get_settings

    settings = get_settings()

    if model_name_or_path is None:
        model_name_or_path = settings.sentence_transformer_model
    if semantic_enabled is None:
        semantic_enabled = settings.semantic_scorer_enabled

    bm25_scorer = BM25Scorer()

    backend: EmbeddingBackend
    if not semantic_enabled:
        # 明确关闭语义匹配：不加载模型，避免首请求延迟与模型依赖
        backend = NullEmbeddingBackend()
    else:
        try:
            backend = SentenceTransformerBackend(model_name_or_path)
            # 尝试触发加载，捕获失败
            backend.encode(["test"])
        except ModelLoadError:
            if not fallback_on_error:
                raise
            backend = NullEmbeddingBackend()

    semantic_scorer = SemanticScorer(backend)
    return CombinedScorer(
        bm25_scorer=bm25_scorer,
        semantic_scorer=semantic_scorer,
        weight_bm25=weight_bm25,
        weight_semantic=weight_semantic,
    )


__all__ = [
    # 异常
    "ScorerError",
    "ModelLoadError",
    "EncodingError",
    # 常量
    "DEFAULT_BM25_SCALE",
    "DEFAULT_EMBEDDING_DIM",
    # Embedding Backend
    "EmbeddingBackend",
    "NullEmbeddingBackend",
    "SentenceTransformerBackend",
    # Scorer
    "BM25Scorer",
    "SemanticScorer",
    "CombinedScorer",
    "create_default_scorer",
]
