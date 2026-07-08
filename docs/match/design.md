# Match 模块技术方案（Step 1.7.1 / 1.7.2）

## 1. 背景与目标

### 1.1 业务背景

AI Career Copilot 的核心链路之一是「岗位 - 简历」匹配。Step 1.7.1 与 1.7.2 的交付范围是：

- **Step 1.7.1**：定义匹配模块的输入输出 DTO，作为后续 Ranker / Strategy / Service / Router 的数据契约。
- **Step 1.7.2**：实现技能匹配度计算，采用 **BM25 关键词匹配 + NLP 语义相似度** 的融合方案。

本方案严格限制在上述两步范围内，不扩展 Ranker、Strategy、Service、Router 等其他模块。

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| 可解释 | 同时返回 `bm25_score` / `semantic_score` / `combined_score`，便于前端展示与问题定位 |
| 可测试 | 纯计算模块，无 DB / MQ / HTTP 依赖，可单测覆盖 ≥80% |
| 可配置 | 权重、模型路径、BM25 scale 均从 `.env` 读取，支持 A/B 实验 |
| 可启动 | 大模型懒加载，项目启动时不加载；模型失败时自动降级 |
| 高性能 | BM25 为纯 Python 毫秒级；语义模型 CPU 单次约 50-100ms |

---

## 2. 模块结构

```text
backend/app/domain/match/
├── __init__.py      # 公共接口导出
├── models.py        # DTO / Schema（MatchInput / MatchScoreDetail / MatchCalculateRequest）
├── scorer.py        # BM25Scorer / SemanticScorer / CombinedScorer / EmbeddingBackend
```

依赖方向：`api → service → scorer → models`，符合项目分层架构约束。

---

## 3. DTO 设计

### 3.1 MatchInput

`MatchInput` 是 Scorer 的唯一输入，字段均来自上游 Job / Resume 分析结果：

| 字段 | 来源 | 用途 |
|------|------|------|
| `job_id` / `resume_id` | Job / Resume | 结果回写与日志追踪，不参与计算 |
| `job_skills` / `job_keywords` | Job 分析结果 | BM25 的 query 侧 |
| `job_text` | Job JD 原文 | Semantic Scorer 的文本 A |
| `resume_skills` | Resume 分析结果 | BM25 的 doc 侧补充 |
| `resume_text` | Resume 原文 | Semantic Scorer 的文本 B |
| `resume_experience_years` | Resume | 为 Step 1.7.3+ 的 strategy 预留，当前不参与计算 |

设计要点：

- `skills` 与 `keywords` 分离：skills 偏向硬技能（Python / MySQL），keywords 偏向业务关键词（RAG / Agent）。
- 文本最大长度与 Job / Resume DTO 对齐，防止恶意长文本触发 OOM。
- `extra="forbid"` 拒绝未知字段，避免上游误传字段导致静默失败。

### 3.2 MatchScoreDetail

输出结构包含三个分数和权重回显：

```json
{
  "job_id": "...",
  "resume_id": "...",
  "bm25_score": 73.89,
  "semantic_score": 87.78,
  "combined_score": 82.22,
  "weight_bm25": 0.4,
  "weight_semantic": 0.6,
  "scored_at": "2026-06-30T10:00:00Z"
}
```

### 3.3 MatchCalculateRequest

在 `MatchInput` 基础上允许单次调用覆盖权重，便于后续 A/B 实验：

```python
class MatchCalculateRequest(BaseModel):
    match_input: MatchInput
    weight_bm25: float = 0.4
    weight_semantic: float = 0.6
```

权重校验：

- 各自在 `[0, 1]` 之间（Field 校验）
- 和必须等于 `1.0`（model_validator 校验，允许 1e-6 浮点误差）

---

## 4. BM25 关键词匹配

### 4.1 实现位置

[`backend/app/domain/match/scorer.py`](../../backend/app/domain/match/scorer.py) 中的 `BM25Scorer`。

### 4.2 分词策略

```python
_TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+")

def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN_PATTERN.findall(text.lower())
    return [token for token in tokens if not token.isdigit()]
```

- 统一小写：技能 "Python" 与 "python" 视为同一词。
- 正则匹配中文、英文、数字、下划线：避免标点成为独立 token。
- 过滤纯数字 token：技能匹配中 "3"、"2024" 通常无意义。
- **不过滤停用词**：技能标签通常很短，过滤停用词可能误删有效词（如 "C"、"R"）。

### 4.3 算法细节

原始 BM25 公式：

```
score(q, d) = Σ IDF(q_i) * [tf(q_i) * (k1 + 1)] / [tf(q_i) + k1 * (1 - b + b * L)]
```

本方案改动：

1. **固定 IDF = 1.0**：单文档场景下 rank-bm25 的 IDF 会退化为负数或零，导致得分异常。固定正 IDF 保留 TF 饱和特性。
2. **长度因子 = 1.0**：Scorer 每次只处理一份 doc，avgdl = doc_len，因此 L = 1。
3. **归一化到 [0, 100]**：原始 BM25 分数无上界，使用 `100 * (1 - exp(-raw / scale))` 映射。
   - 单调递增、有界、平滑
   - 多关键词命中时 raw score 更高，映射后分数也更高
   - `scale` 默认 5.0，控制分数增长速度

### 4.4 为什么不用 rank-bm25 库

`rank-bm25` 在多文档场景下工作良好，但本模块每次只匹配「一份岗位 - 一份简历」，IDF 会退化。因此选择自定义轻量实现，避免引入不必要的库依赖和负分问题。

---

## 5. NLP 语义相似度

### 5.1 实现位置

[`backend/app/domain/match/scorer.py`](../../backend/app/domain/match/scorer.py) 中的 `SemanticScorer` 与 `EmbeddingBackend`。

### 5.2 EmbeddingBackend 协议

```python
@runtime_checkable
class EmbeddingBackend(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...
```

通过 Protocol 解耦 Scorer 与具体模型：

| Backend | 用途 |
|---------|------|
| `SentenceTransformerBackend` | 生产环境，封装 `sentence-transformers` |
| `NullEmbeddingBackend` | 模型加载失败时降级，返回零向量 |
| `FakeEmbeddingBackend` | 单测注入，提供确定性向量 |

### 5.3 SentenceTransformerBackend 懒加载

```python
class SentenceTransformerBackend:
    def __init__(self, model_name_or_path: str) -> None:
        self._model_name_or_path = model_name_or_path
        self._model: object | None = None
        self._lock = threading.Lock()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self._load_model()
        embeddings = self._model.encode(texts, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]
```

- **项目启动不加载模型**：`__init__` 只保存配置。
- **第一次 encode 触发加载**：使用 `threading.Lock` + 双重检查锁定，保证并发下只加载一次。
- **延迟导入 sentence_transformers**：避免模块加载时强依赖 torch/transformers，方便在无模型环境跑 BM25 单测。

### 5.4 余弦相似度

纯 Python 实现，避免额外依赖：

```python
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
```

归一化：`semantic_score = max(0, similarity) * 100`。句向量模型输出已归一化，相似度实际 ∈ [0, 1]；取 max(0, *) 避免负相关噪声。

---

## 6. 融合策略

### 6.1 CombinedScorer

```python
combined = weight_bm25 * bm25_score + weight_semantic * semantic_score
```

默认权重：`bm25=0.4`, `semantic=0.6`。

选择语义权重更高的原因：

- 中文 JD 中同义词/近义表达更常见（如 "LLM 大模型"、"大语言模型"）。
- 语义模型能捕捉 BM25 无法匹配的近义技能描述。

### 6.2 权重校验

构造时校验：

- `0 ≤ weight_bm25, weight_semantic ≤ 1`
- `weight_bm25 + weight_semantic = 1.0`

---

## 7. 配置管理

所有配置集中在 [`backend/app/core/settings.py`](../../backend/app/core/settings.py)：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MATCH_BM25_WEIGHT` | 0.4 | BM25 权重 |
| `MATCH_SEMANTIC_WEIGHT` | 0.6 | 语义权重 |
| `SENTENCE_TRANSFORMER_MODEL` | `BAAI/bge-small-zh-v1.5` | 模型名或本地路径 |
| `SEMANTIC_SCORER_ENABLED` | True | 是否启用语义匹配 |
| `MATCH_BM25_SCALE` | 5.0 | BM25 归一化尺度 |

`.env` 示例：

```env
SENTENCE_TRANSFORMER_MODEL="E:\\MoTa_model\\XiangLiang\\bge-small-zh-v1.5"
```

---

## 8. 方案评估报告

### 8.1 候选方案对比

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **BM25 + Sentence Transformer（本方案）** | 关键词命中可解释；语义补全近义词；实现简单；无训练成本 | 语义模型 CPU 推理约 50-100ms/次；长文本需截断 | MVP、通用岗位匹配 |
| BM25 only | 极快（微秒级）；无需模型 | 无法理解近义词；对中文 JD 效果有限 | 快速筛选、模型未就绪 |
| 微调双塔模型（JobBERT / ResumeBERT） | 针对招聘场景精度更高 | 需要标注数据训练；训练/部署成本高；泛化性差 | 有领域标注数据后 |
| 交叉编码器（Cross-Encoder） | 精度通常高于双塔 | 每次匹配需拼接两段文本前向传播；成本高 | 最终精排、小批量 |
| TF-IDF + 词向量（Word2Vec/FastText） | 轻量；无需大模型 | 无法捕捉上下文；词向量质量依赖语料 | 资源受限场景 |
| LLM 直接打分（GPT-4/DeepSeek） | 理解力强；可生成解释 | 成本高；延迟高；结果不稳定 | 高价值岗位最终决策 |

### 8.2 性能指标对比

实测数据（Windows 11 / Python 3.12 / CPU / bge-small-zh-v1.5）详见：

- [Match Scorer 性能测试报告](./benchmark_report.md)

设计文档中不再重复粘贴具体数字，避免后续调参后两份文档数据不一致。

### 8.3 结论

当前采用 **BM25 + Sentence Transformer** 方案作为 Step 1.7.2 的最小可行实现，理由：

1. **无需训练**：直接复用开源 bge 模型，降低 MVP 落地成本。
2. **可解释**：BM25 分数对应硬技能命中，semantic 分数对应语义相关。
3. **可扩展**：后续可替换为微调双塔或交叉编码器，接口不变。
4. **可降级**：模型不可用时自动切 NullEmbeddingBackend，服务不中断。

后续优化方向：

- 批量编码：将同一岗位与多份简历匹配时，预计算岗位 embedding 可显著降低总延迟。
- 缓存 embedding：Redis 缓存热门岗位/简历 embedding。
- 领域微调：积累标注数据后训练招聘领域双塔模型。

---

## 9. 单元测试

测试文件：

- [`backend/tests/test_match_models.py`](../../backend/tests/test_match_models.py)
- [`backend/tests/test_match_scorer.py`](../../backend/tests/test_match_scorer.py)

覆盖率：97%（目标 ≥80%）。

覆盖场景：

- MatchInput / MatchScoreDetail / MatchCalculateRequest 字段校验
- BM25 命中/未命中/空输入/大小写/多关键词/scale 参数
- SemanticScorer 相同向量/正交向量/零向量/维度不匹配
- CombinedScorer 加权求和/权重非法/无 semantic scorer
- SentenceTransformerBackend 懒加载/加载失败/降级
