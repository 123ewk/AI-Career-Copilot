# AI Career Copilot MVP 技术规格与边界讨论（v1.1）

> 本文档基于 [MVP 端到端实现计划](file:///g:/my/my_file/AI%20Career%20Copilot/.trae/documents/mvp_end_to_end_plan.md) 展开，重点回答三个问题：
> 1. **用什么技术** 实现每个模块？
> 2. **边界画在哪里** —— 本次做多少、不做多少？
> 3. **关键实现细节与风险** 是什么？

---

## 1. 总体目标与边界

### 1.1 目标

跑通「注册/登录 → 上传简历 → 创建岗位 → JD 分析 → 简历匹配 → 生成沟通话术 → 记录投递」的完整 MVP 流程。

### 1.2 交付范围（In Scope）

| 范围 | 说明 |
|------|------|
| 后端 API | FastAPI，补齐 Match / Communication / Application 三个域 |
| 统一 LLM 客户端 | 封装 `LLMClient`，支持 mimo / deepseek / openai 切换 |
| MQ 消费者 | Communication 异步消费者注册并运行 |
| Extension 代码精简 | 只保留 Boss 直聘适配器，其余平台移入 `extension-v2/` |
| 端到端集成测试 | 1 条真实 LLM 链路测试，依赖本地 `docker-compose up` |

### 1.3 明确不做（Out of Scope）

| 不做项 | 原因 |
|--------|------|
| Extension UI 大改造 | 只保证核心功能可运行，不做复杂的 UI  redesign |
| 多平台适配器（猎聘/智联/实习僧） | 移出到 `extension-v2/`，MVP 只验证 Boss 直聘 |
| 语义模型默认启用 | MVP 严格使用纯 BM25，`SEMANTIC_SCORER_ENABLED=false` |
| 复杂工作流编排 | 不使用 workflow/state-machine 框架，按简单 Service 调用实现 |
| 通知/邮件系统 | 仅保留已有事件队列，不新增通知消费者 |
| 简历结构化 Agent 回填 | 当前 `ResumeService` 只存 `raw_text`，结构化数据留空，MVP 不补齐 |
| 自动打招呼 / 自动聊天 / 自动发简历 | 这是完整产品愿景，MVP 阶段用户手动触发：Extension 提取 JD → 后端分析/匹配/生成话术 → 用户确认后记录投递 |

---

## 2. 技术栈与选型理由

### 2.1 后端框架与运行时

| 技术 | 用途 | 选型理由 |
|------|------|----------|
| **FastAPI** | Web 框架 | 已有基础，异步原生，Pydantic 校验与 Swagger 自动生成 |
| **Pydantic v2** | DTO / 配置校验 | 项目已统一使用，Settings 也基于 pydantic-settings |
| **SQLAlchemy 2.0 + asyncpg** | ORM / 数据库访问 | 已有 Repository 模式，保持异步会话管理 |
| **aio-pika** | RabbitMQ 客户端 | 已有 Publisher/Consumer/Registry 基础，复用 |
| **redis-py(async)** | Redis 缓存 | 已有 Resume/JobAnalysis 缓存实现 |
| **uvicorn** | ASGI 服务器 | 开发 `uvicorn main:app --reload` |

### 2.2 LLM 与模型

| 技术 | 用途 | 选型理由 |
|------|------|----------|
| **LLMClient 统一封装** | 切换 provider | 消除 `JobExtractor` 硬编码 MimoClient，新增 provider 改一处 |
| **DeepSeek / OpenAI 兼容客户端** | JD 分析、话术生成 | 两者都兼容 `/v1/chat/completions`，复用 `MimoClient` 的调用结构 |
| **BM25 自研实现** | 简历-岗位匹配 | 已在 `scorer.py` 实现，无需外部库 |
| **Sentence Transformers（可选）** | 语义匹配 | MVP 关闭，但代码保留，Phase 2 可开启 |

### 2.3 前端 / Extension

| 技术 | 用途 | 选型理由 |
|------|------|----------|
| **Chrome Extension Manifest V3** | 浏览器插件 | 现有代码基于 MV3，本次只精简不改造 |
| **Boss 直聘适配器** | 岗位信息提取 | MVP 只验证一个平台，降低测试面 |

---

## 3. 架构设计

### 3.1 模块关系图

```text
┌─────────────────────────────────────────────────────────────────────┐
│                           FastAPI 应用层                              │
│  /api/auth      /api/resumes      /api/jobs      /api/tasks          │
│  /api/match     /api/communication /api/applications                  │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│                        Domain Service 层                             │
│  UserService  ResumeService  JobService  MatchService                │
│  CommunicationService  ApplicationService  TaskService               │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│                      Repository / Cache 层                           │
│  SQLAlchemy Repository (PostgreSQL)  +  Redis Cache                  │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│                     Integration / Agent 层                           │
│  LLMClient → MimoClient / DeepseekClient / OpenAIClient              │
│  JobExtractor  →  JD 分析                                            │
│  CombinedScorer → BM25 + 可选语义匹配                                │
└──────────────────┬──────────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────────┐
│                      Message Queue 层                                │
│  RabbitMQ：Job Analysis Queue / Communication Queue / DLX / Retry    │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 端到端数据流

```text
1. 用户注册/登录
   POST /api/auth/register  →  返回 user + access_token/refresh_token (HttpOnly Cookie)

2. 上传简历
   POST /api/resumes/upload  →  PDF/DOCX 解析 → raw_text → 自动设为 active resume

3. 创建岗位
   POST /api/jobs  →  JobService.create_job (source_url 去重)

4. JD 分析（异步）
   POST /api/jobs/analyze  →  创建 Task → 发 MQ → 返回 202 + task_id
   JobAnalysisConsumer  →  LLMClient.chat_completion → 提取 skills/keywords/difficulty/seniority
   → 更新 jobs.analysis_result → mark_completed

5. 简历匹配（同步）
   POST /api/match/compute  →  MatchService 取 active resume + job
   → CombinedScorer(BM25) → 返回 score_detail + matched_skills + missing_skills + suggestions

6. 生成沟通话术（异步）
   POST /api/communication/generate  →  创建 Task → 发 MQ → 返回 202 + task_id
   CommunicationConsumer  →  LLMClient.chat_completion(JSON) → 返回 greeting/follow_up/full_script

7. 记录投递
   用户在前端/Extension 点击「记录投递」（确认要投递该岗位）
   POST /api/applications  →  创建 Application，状态默认 APPLIED，并设置 applied_at

> 完整产品愿景中，未来会实现「AI Agent 自动在 Boss 直聘上匹配岗位→打招呼→聊天→要简历再发简历→遇到未知信息暂停等用户输入」。MVP 阶段只实现手动触发：Extension 提取 JD → 后端分析/匹配/生成话术 → 用户确认后记录投递。
```

---

## 4. 关键模块详细设计

### 4.1 统一 LLMClient

**新增文件：** `backend/app/integrations/llm/llm_client.py`

```python
class LLMClient:
    def __init__(self) -> None:
        provider = get_settings().llm_provider
        if provider == "mimo":
            self._client = MimoClient()
        elif provider == "deepseek":
            self._client = DeepseekClient()
        elif provider == "openai":
            self._client = OpenAIClient()
        else:
            raise ValueError(f"不支持的 LLM provider: {provider}")

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._client.chat_completion(...)

    async def close(self) -> None:
        await self._client.close()
```

**边界讨论：**
- `LLMClient` 只封装 Chat Completion，不封装 Embedding。语义模型由 `SentenceTransformerBackend` 直接管理。
- 底层三个客户端接口必须完全一致：`chat_completion(messages, model=None, temperature=0.1, max_tokens=2000, response_format=None) -> dict`。
- `JobExtractor` 从 `MimoClient` 改为 `LLMClient`，保持调用方式不变。

### 4.2 DeepseekClient / OpenAIClient

**文件：** `backend/app/integrations/llm/deepseek_client.py`、`openai_client.py`

- 结构完全复用 `MimoClient`。
- 配置从 `settings.deepseek_*` / `settings.openai_*` 读取。
- 统一错误码：`EXT_003` ~ `EXT_010` 可继续复用，或新增 `EXT_011` 区分 provider。

**边界讨论：**
- 是否引入 `openai` 官方 SDK？**建议不用**。当前 `MimoClient` 用 `httpx` 直接调用，代码轻量；引入 SDK 会增加依赖且三个 provider 行为不易统一。
- 是否支持流式输出？**MVP 不支持**。岗位分析和话术生成都要求 JSON 完整输出，流式反而增加解析复杂度。

### 4.3 MatchService + Router

**文件：**
- `backend/app/domain/match/service.py`
- `backend/app/domain/match/models.py`（扩展 DTO）
- `backend/app/api/routers/match.py`

**核心方法：**

```python
class MatchService:
    def __init__(self, session, scorer: CombinedScorer | None = None):
        self._session = session
        self._resume_service = ResumeService(session)
        self._job_service = JobService(session)
        self._scorer = scorer or create_default_scorer(semantic_enabled=False)

    async def compute_match(
        self,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        resume_id: uuid.UUID | None = None,
    ) -> MatchResultResponse:
        # 1. 取简历
        if resume_id:
            resume = await self._resume_service.get_resume(user_id=user_id, resume_id=resume_id)
        else:
            resume = await self._resume_service.get_active_resume(user_id=user_id)
        if resume is None:
            raise ResourceNotFoundError("未找到可用简历")

        # 2. 取岗位
        job = await self._job_service.get_job(job_id)

        # 3. 构造 MatchInput
        analysis = job.analysis_result
        job_skills = analysis.skills if analysis else job.skills or []
        job_keywords = analysis.keywords if analysis else job.keywords or []
        job_text = analysis.summary if analysis and hasattr(analysis, "summary") else job.jd_text

        match_input = MatchInput(
            job_id=job_id,
            resume_id=resume.id,
            job_skills=job_skills,
            job_keywords=job_keywords,
            job_text=job_text,
            resume_skills=resume.skills or [],
            resume_text=resume.raw_text or "",
        )

        # 4. 打分
        score_detail = self._scorer.score(match_input)

        # 5. 轻量规则计算命中/缺失/建议
        matched_skills, missing_skills, suggestions = self._compute_insights(
            job_skills, resume.skills or []
        )

        return MatchResultResponse(
            job_id=job_id,
            resume_id=resume.id,
            score_detail=score_detail,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            suggestions=suggestions,
        )
```

**DTO 扩展：**

```python
class MatchComputeRequest(BaseModel):
    job_id: uuid.UUID
    resume_id: uuid.UUID | None = None

class MatchResultResponse(BaseModel):
    job_id: uuid.UUID
    resume_id: uuid.UUID
    score_detail: MatchScoreDetail
    matched_skills: list[str]
    missing_skills: list[str]
    suggestions: list[str]
```

**边界讨论：**
- **同步还是异步？** 同步。BM25 纯 CPU 计算，<100ms，走同步接口用户体验更好。
- **语义模型是否启用？** MVP 不启用，`create_default_scorer(semantic_enabled=False)` 强制使用 `NullEmbeddingBackend`。
- **matched/missing skills 规则：** 不调用 LLM。基于 job_skills 与 resume.skills 的字符串小写交集/差集，最多返回前 10 个。
- **suggestions 生成规则：** 调用 LLM 生成。Prompt 包含岗位 JD、岗位要求、简历技能/经历、匹配分数，让 LLM 给出 3-5 条自然、可执行的建议（例如「简历中已提到 FastAPI，但 JD 要求 Docker，建议补充一个容器化部署项目」）。这样比固定模板更有针对性，也避免生硬的八股建议。
- **未分析岗位如何处理？** 若 `job.analysis_result` 为空，回退到 `job.skills`/`job.keywords`/`job.jd_text`。

> **注意：** MatchService 调用 LLM 生成 suggestions 与项目规则「LLM 调用走 MQ」是否冲突？此处属于同步接口内轻量 LLM 调用，与 Communication 的长文本生成不同。为避免阻塞，MVP 中可接受（DeepSeek API 通常 1-3s 返回）；若后续发现超时问题，再改为异步或缓存。


### 4.4 CommunicationService + Router + Consumer

**新增文件：**
- `backend/app/domain/communication/models.py`
- `backend/app/domain/communication/service.py`
- `backend/app/api/routers/communication.py`
- `backend/app/infra/message_queue/handlers/communication.py`

**DTO：**

```python
class CommunicationGenerateRequest(BaseModel):
    job_id: uuid.UUID
    session_id: uuid.UUID
    resume_id: uuid.UUID | None = None
    tone: str = "professional"  # friendly / professional

class CommunicationScriptResponse(BaseModel):
    job_id: uuid.UUID
    resume_id: uuid.UUID | None
    greeting: str
    follow_up: str
    full_script: str

class CommunicationGenerateResponse(BaseModel):
    task_id: uuid.UUID
    status: str  # pending
```

**Prompt 设计要点：**
- 风格：自然、口语化，像实习生在 Boss 直聘上真实聊天，不是面试答辩或专业问答。
- 输入：岗位 title/company、jd_text 摘要、analysis.skills/keywords、resume.raw_text 摘要、resume.skills、匹配分数。
- 关键约束：话术必须基于简历真实信息生成。例如简历中是 Python/FastAPI 背景，就不能生成 Spring Boot/Java 相关话术；没有大厂实习就不要提大厂经历。
- 输出 JSON Schema：`{"greeting": "...", "follow_up": "...", "full_script": "..."}`。
  - `greeting`：初次打招呼，控制在 3 行以内，包含「身份 + 匹配点 + 低压力请求」。例如："您好！我是 XX 大学大三学生，熟悉 Python 和 FastAPI，看到贵司后端实习岗很契合。简历已附，方便时麻烦您看看～"
  - `follow_up`：HR 已读未回或回复后的跟进/回复模板，例如补充说明到岗时间、实习时长。
  - `full_script`：把 greeting + follow_up 串成一段完整对话参考。
- `response_format={"type": "json_object"}`。
- 温度 0.3 左右，平衡创造性与稳定性。

**参考话术样本（实习场景）：**

1. **初次打招呼**
   > "您好！我是 XX 大学计算机专业大三学生，有 6 个月 Python Web 项目经验，熟悉 FastAPI 和 PostgreSQL。看到贵司后端实习岗非常契合，简历已附，方便时麻烦您看看～"

2. **已读未回跟进**
   > "您好，打扰您了！我是投递 XX 岗位的 XXX，看到您已查看我的消息，想请问下简历筛选是否有进展？若有进一步沟通的机会，我随时可以配合面试，感谢您的考虑！"

3. **HR 问"简单介绍一下自己"**
   > "好的～我是 XX 大学大三学生，专业是计算机科学与技术。之前做过一个基于 Python + FastAPI 的校园项目，负责后端接口和数据库设计。对贵司这个岗位很感兴趣，希望能有机会聊聊。"

4. **HR 问"什么时候能到岗？实习多久？"**
   > "我目前课已经比较少，下周就可以到岗，至少可以实习 3-6 个月，时间比较稳定。"

Sources:
- [牛客：跟HR说什么能被秒回？](https://m.nowcoder.com/feed/main/detail/0b3ff263d07d4eb69ed420ff39194112)
- [PHP中文网：Boss直聘提高打招呼回复](https://m.php.cn/faq/2181954.html)
- [掘金：三段实习经历告诉你找实习的真相](https://juejin.cn/post/7437531314701549608)

**Consumer 注册：**

```python
@register(
    QUEUE_AGENT_COMMUNICATION,
    prefetch_count=1,
    max_retries=3,
    retry_base_delay_ms=10_000,
)
async def handle_communication(body: dict) -> None:
    ...
```

**边界讨论：**
- **是否复用 Job Analysis 的 AgentService？** 不复用。Communication 逻辑简单直接调用 LLM，不需要 AgentState/WebSearch 等复杂编排。
- **话术是否持久化？** 本次只把结果写入 `task.result`，不新建 communication 表。Extension 拿到话术后自行决定是否缓存。
- **失败重试策略？** 同 JobAnalysis，max_retries=3，10s 基础退避。

### 4.5 Application 域

**新增文件：**
- `backend/app/domain/repositories/application.py`
- `backend/app/infra/repositories/application_repo.py`
- `backend/app/domain/application/models.py`
- `backend/app/domain/application/service.py`
- `backend/app/api/routers/applications.py`

**DTO：**

```python
class ApplicationCreateRequest(BaseModel):
    job_id: uuid.UUID
    match_score: float | None = None
    notes: str | None = None

class ApplicationUpdateRequest(BaseModel):
    status: ApplicationStatus
    notes: str | None = None

class ApplicationResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    job_id: uuid.UUID
    status: ApplicationStatus
    match_score: float | None
    applied_at: datetime | None
    status_updated_at: datetime
    notes: str | None
    created_at: datetime

class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int
    limit: int
    offset: int
```

**Service 核心方法：**

```python
class ApplicationService:
    async def create_application(
        self, user_id, job_id, match_score=None, notes=None
    ) -> ApplicationResponse:
        # 默认状态 COMMUNICATION_READY
        # 若 notes 包含已投递标记，可改为 APPLIED？→ 本次不自动推断，由 PATCH 更新
        ...

    async def list_applications(self, user_id, limit=20, offset=0) -> ApplicationListResponse
    async def get_application(self, application_id, user_id) -> ApplicationResponse
    async def update_status(self, application_id, user_id, status, notes=None) -> ApplicationResponse
```

**边界讨论：**
- **创建语义：** 用户点击「记录投递」即表示确认要投递该岗位，后端此时创建 Application。
- **初始状态：** `APPLIED`，并且同时设置 `applied_at` 为当前时间。因为用户的动作本身就是投递意愿。
- **是否校验 job 存在？** 创建时建议校验 `job_id` 存在，但不做强关联约束（ORM 已有 FK）。
- **去重：** ORM 中 `(user_id, job_id)` 已设唯一索引，重复创建会抛 IntegrityError，Service 翻译为 `ConflictError`。
- **与完整产品愿景的关系：** 未来 AI Agent 会自动在 Boss 直聘上匹配岗位、打招呼、聊天、在 HR 要简历时发简历、遇到未知信息时暂停等用户输入。MVP 阶段只实现手动触发：Extension 提取 JD → 后端分析/匹配/生成话术 → 用户确认后记录投递。

---

## 5. 路由挂载与消费者注册

### 5.1 main.py 修改

```python
from app.api.routers import applications, communication  # 新增
from app.infra.message_queue.handlers import communication  # noqa: F401  # 触发 @register

# 挂载
app.include_router(communication.router, prefix="/api/communication", tags=["沟通话术"])
app.include_router(applications.router, prefix="/api/applications", tags=["投递记录"])
```

### 5.2 MQ 拓扑扩展

在 `backend/app/infra/message_queue/exchanges.py` 中新增：

```python
QUEUE_AGENT_COMMUNICATION = "copilot.agent.communication"
ROUTING_AGENT_COMMUNICATION = "agent.communication"
```

并在 `declare_all()` 中声明该队列，绑定到 `EXCHANGE_AGENT`。

---

## 6. Extension 精简

### 6.1 目录调整

```text
extension/
  src/
    modules/
      boss/          ← 保留
      liepin/        ← 移出
      zhilian/       ← 移出
      shixisheng/    ← 移出

extension-v2/        ← 新建
  src/
    modules/
      liepin/
      zhilian/
      shixisheng/
```

### 6.2 入口文件 Guard

在 `extension/src/background/index.ts` 或平台分发逻辑中加入：

```typescript
if (platform !== "boss") {
  console.warn(`MVP 暂不支持平台: ${platform}`);
  return;
}
```

### 6.3 Extension 端到端调用方式（方案 B：最小可运行版本）

MVP 只保留 Boss 直聘适配器，并让它真的能跑起来。核心流程：

1. Extension 从 Boss 页面提取 JD（title/company/jd_text/source_url 等）
2. 调用 `POST /api/jobs` 创建岗位
3. 调用 `POST /api/jobs/analyze` → 拿到 `task_id` → 轮询 `/api/tasks/{task_id}` 到 completed
4. 用户点击「匹配」→ 调用 `POST /api/match/compute` → 展示匹配分和 suggestions
5. 用户点击「生成话术」→ 调用 `POST /api/communication/generate` → 轮询 task 到 completed → 展示 greeting/follow_up/full_script
6. 用户点击「记录投递」→ 调用 `POST /api/applications`

**边界讨论：**
- 本次不做自动打招呼、自动聊天、自动发简历。
- 不实现复杂的状态机或 UI 改造，以最小可用为目标。
- Extension 端不缓存岗位/话术，每次操作直接调后端。
- 话术展示时要明确提示用户"请根据你的实际情况修改后再发送"。
- 用户未登录时，Extension 引导到 Web 登录后再操作。

---

## 7. 数据库与迁移

### 7.1 表状态

| 表 | 状态 | 说明 |
|----|------|------|
| users | 已存在 | 注册/登录 |
| resumes | 已存在 | 简历上传 |
| jobs | 已存在 | 岗位创建/分析 |
| tasks | 已存在 | 异步任务状态 |
| applications | 模型已存在 | 需确认数据库中是否已建表 |

### 7.2 迁移检查

启动前执行：

```bash
cd backend
alembic current
alembic revision --autogenerate -m "add applications table"  # 如缺失
alembic upgrade head
```

---

## 8. 配置与依赖

### 8.1 .env 关键项

```ini
# LLM  provider：mimo / deepseek / openai
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_key
DEEPSEEK_API_BASE=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# 匹配模块：MVP 纯 BM25
MATCH_BM25_WEIGHT=1.0
MATCH_SEMANTIC_WEIGHT=0.0
SEMANTIC_SCORER_ENABLED=false
MATCH_BM25_SCALE=5.0
```

**注意：** `match_bm25_weight + match_semantic_weight` 必须等于 `1.0`，由 `Settings.model_validator` 校验。

### 8.2 不新增第三方依赖

- `LLMClient` 复用 `httpx` + `pydantic`。
- `BM25` 已在 `scorer.py` 自研实现。
- `Communication` 不需要新增模板引擎或 prompt 管理库。

---

## 9. 端到端集成测试

### 9.1 测试文件

`backend/tests/integration/test_mvp_end_to_end.py`

### 9.2 前置条件

```bash
docker-compose up -d  # 启动 PG/Redis/RabbitMQ
# 确认 backend/app/configs/.env 中 LLM_PROVIDER + API key 已配置
```

### 9.3 测试步骤

```python
@pytest.mark.integration
async def test_mvp_end_to_end():
    async with AsyncClient(app=app, base_url="http://test") as client:
        # 1. 注册
        await client.post("/api/auth/register", json={...})

        # 2. 上传简历（真实测试文件）
        await client.post("/api/resumes/upload", files={...})

        # 3. 创建岗位
        job = await client.post("/api/jobs", json={...})

        # 4. 分析岗位
        analyze = await client.post("/api/jobs/analyze", json={...})
        await poll_task(client, analyze.json()["task_id"])

        # 5. 匹配
        match = await client.post("/api/match/compute", json={"job_id": job_id})
        assert match.json()["score_detail"]["combined_score"] >= 0

        # 6. 生成话术
        comm = await client.post("/api/communication/generate", json={...})
        script = await poll_task(client, comm.json()["task_id"])
        assert "greeting" in script["result"]

        # 7. 记录投递
        app = await client.post("/api/applications", json={"job_id": job_id})

        # 8. 验证列表
        apps = await client.get("/api/applications")
        assert len(apps.json()["items"]) == 1
```

### 9.4 运行方式

```bash
# 集成测试（真实 LLM）
pytest backend/tests/integration/test_mvp_end_to_end.py -v -m integration

# 单元测试回归
pytest backend/tests/unit/ -q
```

---

## 10. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 真实 LLM 集成测试不稳定/慢 | 中 | 加 `pytest.mark.integration`，默认不跑；CI 通过环境变量控制 |
| 语义模型默认关闭导致匹配分偏低 | 低 | MVP 明确只用 BM25，命中/缺失技能仍可解释 |
| MQ 消费者未注册导致任务不消费 | 中 | `main.py` 显式 import consumer 模块；启动日志检查 consumer 数量 |
| Application 表未创建 | 低 | 执行 alembic migration 检查 |
| LLMClient 替换后 JobExtractor 行为变化 | 中 | 保持调用签名一致；替换后跑 Job 分析单测验证 |
| Extension 代码移动后构建失败 | 低 | 移动后检查 manifest 和入口文件引用 |
| `.env` 中 `MATCH_BM25_WEIGHT=1.0` 但 `MATCH_SEMANTIC_WEIGHT=0.0` 与默认值 0.4/0.6 冲突 | 中 | 在 `.env` 中显式设置，并确保和为 1.0 |

---

## 11. 已确认决策

| 序号 | 问题 | 决策 |
|------|------|------|
| 1 | LLM Provider 默认值 | 保持 `deepseek` |
| 2 | Communication 话术 tone | 自然、口语化，贴近 Boss 直聘实习闲聊场景。不局限于 professional/friendly，而是直接按"真实实习生聊天"风格生成 |
| 3 | Match 的 `suggestions` | 调用 LLM 生成，Prompt 包含岗位 JD、简历信息、匹配分数，输出 3-5 条自然建议 |
| 4 | Application 创建语义 | 用户点击「记录投递」即创建 Application，状态 `APPLIED`，同时设置 `applied_at` |
| 5 | Extension 范围 | 只做 Boss 直聘，使用方案 B（最小可运行版本），能在 Boss 直聘页面上完成提取 JD → 分析 → 匹配 → 话术 → 记录投递 |
| 6 | 集成测试 LLM | 使用 mimo 的 API key 进行真实 LLM 调用测试 |

### 11.1 完整产品愿景（MVP 之后）

你描述的完整流程是：

> 用户说明要投递什么岗位 → 系统分析岗位需求 → 去 Boss 直聘等平台匹配岗位（离用户越近越好） → 每个职位自动打招呼 → HR 回复后 AI 简单交流 → HR 要简历时再发简历 → 遇到不知道的个人信息时暂停，等用户输入回答后继续 → 最多聊 30 分钟。

**MVP 阶段只实现手动触发版本：**
- Extension 在 Boss 直聘页面提取 JD；
- 后端提供分析/匹配/生成话术/记录投递接口；
- 用户手动点击按钮完成每一步；
- 不实现自动打招呼、自动聊天、自动发简历、人机协作暂停。

这些高级功能后续按 Agent 工作流逐步实现。

---

## 12. 验收标准

1. `POST /api/auth/register` + `POST /api/auth/login` 成功。
2. `POST /api/resumes/upload` 成功并返回 active resume。
3. `POST /api/jobs` 创建岗位 + `POST /api/jobs/analyze` 异步分析 + 轮询 task 返回 completed。
4. `POST /api/match/compute` 返回匹配分数、命中/缺失技能、建议。
5. `POST /api/communication/generate` 异步生成话术 + 轮询 task 返回脚本内容。
6. `POST /api/applications` 创建投递记录 + `GET /api/applications` 列表可见。
7. 至少一条端到端集成测试通过。
8. `docker-compose up` 能拉起后端 + 依赖服务。
9. Extension 目录下只剩 Boss 直聘适配器，其他平台代码已移入 `extension-v2/`。
