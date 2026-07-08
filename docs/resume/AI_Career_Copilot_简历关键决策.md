# AI Career Copilot 项目简历素材

> 本文档可直接复制到 Markdown 编辑器或简历模板中，导出为 PDF 使用。

---

## 一、项目概述（一句话版）

AI Career Copilot 是一个面向求职者的智能 Agent 平台，通过 Chrome Extension 感知招聘网站页面，结合 FastAPI 后端与 LangGraph Agent 运行时，完成「岗位发现 → JD 分析 → 简历匹配 → 沟通话术 → 投递记录」的完整闭环。

---

## 二、简历项目描述（推荐直接使用）

### 2.1 通用版（后端 + AI 应用实习通用）

**AI Career Copilot | 智能求职 Agent 平台**

独立负责后端核心模块与 Chrome Extension 工程落地，设计配置化 DOM 提取引擎，实现 Boss 直聘岗位信息 <500ms 抽取与动态页面监听；基于 FastAPI + SQLAlchemy 2.0 + RabbitMQ 搭建异步 LLM 任务流水线，封装 `LLMClient` 支持多模型切换；落地 BM25 可解释匹配与 LangGraph 多 HR 会话调度原型，统一 PostgreSQL + pgvector 长期记忆，完成岗位发现 → JD 分析 → 简历匹配 → 沟通话术 → 投递记录的完整 Agent 闭环。

---

### 2.2 投后端实习生专用版

**AI Career Copilot | 智能求职 Agent 平台**

独立负责后端核心模块与 Chrome Extension 工程落地，设计配置化 DOM 提取引擎，实现 Boss 直聘岗位信息 <500ms 抽取；基于 FastAPI + SQLAlchemy 2.0 + RabbitMQ 搭建异步 LLM 任务流水线，实现任务状态机、失败重试与削峰填谷；遵循 `api → service → repository → database` 分层架构，DTO 与 ORM 解耦，统一 request_id 链路追踪与结构化日志，完成岗位分析 → 简历匹配 → 沟通话术 → 投递记录的全流程闭环。

---

### 2.3 投 AI 应用 / Agent 实习生专用版

**AI Career Copilot | 智能求职 Agent 平台**

独立负责 AI Agent 后端与 Chrome Extension 工程落地，封装 `LLMClient` 统一调用 Mimo / DeepSeek / OpenAI，通过 JSON Schema 约束 JD 分析与沟通话术生成；设计 BM25 + 语义模型可插拔匹配，基于 Prompt 生成可执行优化建议；以 LangGraph 状态机抽象单个 HR 对话线程，设计优先级调度器控制多会话有序交流，统一 PostgreSQL + pgvector 长期记忆，完成岗位发现 → 分析 → 匹配 → 话术的完整 Agent 闭环。

---

## 三、关键决策与亮点（面试展开素材）

| 编号 | 关键决策 | 思考过程与价值 |
|------|---------|---------------|
| 1 | **预写 Selector + Parser，局部 LLM 兜底** | 放弃纯 AI Agent 自主决策，MVP 优先稳定、低成本、可控；配合 `MutationObserver` 监听 Vue SPA 动态 DOM，列表页提取成功率 ≥ 90%，单次 < 500ms |
| 2 | **海投模式：先批量创建，后补充 JD** | 后端允许 `jd_text` 为空并新增 `PATCH /api/jobs/{id}`，支持列表页无完整 JD 场景下的批量创建与异步补全 |
| 3 | **LLM 调用统一异步化** | 通过 RabbitMQ + Task 状态机处理 JD 分析、话术生成，避免 HTTP 长阻塞，支持失败重试与削峰填谷 |
| 4 | **LLMClient 多 Provider 抽象** | 支持 Mimo / DeepSeek / OpenAI 一键切换，统一接口与错误码，消除硬编码依赖 |
| 5 | **BM25 + 可解释匹配** | MVP 关闭语义模型，纯 BM25 输出匹配分、命中/缺失技能与 LLM 生成的可执行建议 |
| 6 | **长期记忆复用 PostgreSQL + pgvector** | 不引入独立向量数据库，降低运维复杂度与数据一致性风险 |
| 7 | **LangGraph 多 HR 会话调度** | 每个 HR 对话抽象为 thread，`interrupt` 等待外部输入，Scheduler 按优先级有序调度，避免同时骚扰多个 HR |
| 8 | **反检测与账号安全** | 提取不限速，主动操作严格限速：每日 120 次上限、3-5 次/分钟、随机延迟、连续失败 3 次熔断、24h 内容去重 |

---

## 四、技术栈

- **后端**：Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, asyncpg, Alembic
- **消息队列**：RabbitMQ, aio-pika
- **缓存**：Redis
- **数据库**：PostgreSQL, pgvector
- **LLM**：DeepSeek / OpenAI / Mimo，统一 LLMClient 封装
- **Agent**：LangGraph（未来多 HR 会话调度）
- **匹配**：BM25 + 可选语义模型（Sentence Transformers）
- **浏览器扩展**：Chrome Extension Manifest V3, Vue 3, TypeScript, Vite, Pinia
- **工程规范**：异步优先、分层架构、结构化日志、request_id 链路追踪

---

## 五、个人贡献边界

- 独立完成后端 API 设计与实现（Job / Resume / Match / Communication / Application / Task）
- 独立完成 Chrome Extension 工程底座与海投模式 DOM 解析
- 主导关键技术决策：数据提取策略、异步任务设计、长期记忆方案、多 HR 调度方案
- 编写 MVP 技术规格、端到端计划、架构设计文档

---

## 六、使用建议

1. **简历正文**：复制「2.1 通用版」，放在项目经历部分。
2. **投递后端岗位**：将通用版替换为「2.2 后端专用版」，并在面试中强调异步架构、分层设计、MQ 使用。
3. **投递 AI 应用岗位**：将通用版替换为「2.3 AI 应用专用版」，并在面试中强调 LangGraph、Prompt 设计、匹配算法。
4. **面试准备**：以「三、关键决策与亮点」为提纲，准备每个决策的 why / how / trade-off。
