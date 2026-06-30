# Agentic Job Copilot - 开发顺序规划

> 版本：v1.1.0
> 日期：2026-06-17
> 基于 PRD v1.0.0 制定
> 最近更新：v1.1.0 — 补完 Q9 业务幂等键（Task.business_id + 联合 unique + 静默 ACK）；新增 Step 1.16 业务 MQ 接入专题；更新决策表与风险表

---

## 当前项目状态（v1.1.0）

### 基础设施层 ✅ 已完成

- **目录骨架**：后端 6 层架构 + Extension 4 模块
- **核心代码**：
  - `core/`（settings / logger / exceptions / constants）：完整
  - `infra/database/`（postgres / redis / models）：完整
  - `infra/message_queue/`（connection / exchanges / publisher / consumer / registry）：完整
  - `infra/cache/`（resume / session 模式）：已建立 Protocol + Redis 模式
  - `main.py`：lifespan + 路由挂载
  - 依赖：`pyproject.toml` 已配置
- **数据库**：Alembic 框架就绪；首次迁移 `c528168c702d`（init） + Q9 迁移 `a1b2c3d4e5f6`（Task 业务唯一键）已就绪
- **MQ 拓扑**：copilot.agent.topic / copilot.task.direct / copilot.notification.fanout / copilot.dead_letter.exchange + 重试队列

### 业务层 ⚠️ 已搭脚手架但未接入

- `domain/*/service.py`：**全部为占位**（最大 799 bytes，全是注释 / 无函数体）
- `api/routers/*`：**除 auth/user 外基本为空 router**
- `@register("queue.x")` 装饰器：**0 处业务使用**（registry.py 本身已就绪）
- 业务层与 MQ 组合：**0 处调用 MessagePublisher**

### MQ 单元测试 ✅ 65+ 用例

- `test_publisher_confirms.py`（≥33）：Publisher Confirms 全部场景
- `test_consumer_idempotency.py`（≥8）：`DuplicateMessageError` 静默 ACK
- `test_consumer_retry.py`：重试 + DLQ
- `test_consumer_registry.py`：`@register` + ConsumerManager
- `test_exchanges_topology.py`：Exchange/Queue 拓扑
- `test_task_business_unique.py`（**Q9 新增 13 个**）：Task `(user_id, business_id)` 联合 unique + `insert_idempotent` + MQ 重投端到端

### 关键里程碑（Q1-Q9）

| 里程碑 | 主题 | 状态 | 关键交付 |
|--------|------|------|---------|
| Q1 | 基础设施 | ✅ 完成 | Settings / DB / Redis / Logger / Exceptions |
| Q2 | RabbitMQ 接入 | ✅ 完成 | Connection / Topology / Consumer / Registry |
| Q3 | ORM Model + 迁移 | ✅ 完成 | 7 张表 + init 迁移 |
| Q4 | 中间件层 | ✅ 完成 | CORS / Exception / Logging / Request ID / Rate Limit / Auth |
| Q5 | 认证模块 | ⚠️ 骨架 | service.py 占位，router 待实现 |
| Q6 | 简历模块 | ⚠️ 骨架 | Parser 待集成；缓存模式已建立 |
| Q7 | 缓存模式 | ✅ 完成 | Resume / Job / Session / Match / Communication 五模块 Protocol + Redis 模式 |
| Q8 | Publisher Confirms | ✅ 完成 | 33+ 用例，Nack/超时/重试/批量全覆盖 |
| **Q9** | **业务幂等消费** | **✅ 完成** | **`DuplicateMessageError` + `insert_idempotent` + Task `(user_id, business_id)` 联合 unique + 消费者基类静默 ACK + 13 个新测试** |
| Q10 | 业务 MQ 接入 | 🔴 未开始 | 见 [Step 1.16](#step-116---业务-mq-接入q9-关键收口) |

---

## 开发原则

1. **自底向上**：先基础设施 → 再领域服务 → 再 Agent → 再 API → 再 Extension
2. **逐层打通**：每完成一层，确保可运行、可测试
3. **核心闭环优先**：Phase 1 只做 MVP 闭环，不做增强功能
4. **一个模块一个 Commit**：严格遵循 Git 规范

---

## Phase 1 - MVP 核心闭环

> 目标：用户能注册登录 → 上传简历 → Extension 提取岗位 → Agent 分析 JD → 匹配简历 → 生成沟通话术 → 记录投递

### Step 1.1 - 项目基础设施

**目标**：后端能启动，数据库能连接，配置能加载

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.1.1 | Settings 配置类实现 | `core/settings.py` | 加载 `.env`，所有配置项有类型和默认值（含 RabbitMQ） |
| 1.1.2 | `.env.example` 模板 | `configs/.env.example` | 包含所有必需环境变量及注释（含 RabbitMQ） |
| 1.1.3 | PostgreSQL 连接池 | `infra/database/postgres.py` | async SQLAlchemy engine + session，支持连接池 |
| 1.1.4 | Redis 连接 | `infra/database/redis.py` | async Redis 客户端，支持连接池 |
| 1.1.5 | RabbitMQ 连接管理 | `infra/message_queue/connection.py` | aio-pika RobustConnection，自动重连，单例模式 |
| 1.1.6 | RabbitMQ Exchange/Queue 声明 | `infra/message_queue/exchanges.py` | 声明所有 Exchange + Queue + 绑定关系 |
| 1.1.7 | RabbitMQ 消息发布者 | `infra/message_queue/publisher.py` | 统一发送接口，持久化控制，**全量 Publisher Confirms**（aio-pika RobustChannel 默认开启 + 显式处理 Nack/超时），**message_id 注入**（业务侧 ID 复用，AMQP `message_id` 字段 + `headers["x-business-id"]`，**`_BATCH_ID_KEYS` 提取顺序 `business_id > task_id > notification_id > id > message_id`**），**`BatchPublishResult` 结构化返回**（`publish_batch` 部分失败不抛异常，调用方按 `has_failure` / `failure_rate` 决策补偿）。指数退避重试 1s → 2s → 4s。**Q9 已完成**。 |
| 1.1.8 | RabbitMQ 消息消费者 | `infra/message_queue/consumer.py` | 统一订阅接口，ACK/NACK，并发控制。**基类 `_on_message` 捕获 `DuplicateMessageError`（来自 Step 1.1.10）→ 静默 ACK + INFO 日志**（Q9 幂等消费关键路径）。**Q9 已完成**。 |
| 1.1.9 | Loguru 日志初始化 | `core/logger.py` | 结构化日志 + request_id + 文件轮转 |
| 1.1.10 | 全局异常体系 | `core/exceptions.py` | 基础异常类 + 业务异常 + HTTP 异常映射。**新增 `DuplicateMessageError` 异常**（继承 `Exception`，不混入 `AppException` 体系）：业务表 unique 约束拒绝的重复消息时抛出，**消费者基类识别后静默 ACK，不重试**。携带 `message_id` + `original_error` 用于日志排查。**Q9 已完成**。 |
| 1.1.11 | 常量定义 | `core/constants.py` | 岗位状态枚举、资历等级、难度等级等 |
| 1.1.12 | FastAPI 应用工厂 | `main.py` | app 创建、中间件注册、路由挂载、lifespan 管理（含 RabbitMQ 连接/断开、Consumer 启动/停止） |
| 1.1.13 | Alembic 初始化 | `alembic/` | 迁移框架就绪，`alembic.ini` 配置完成 |
| 1.1.14 | **Consumer Registry + Lifespan 集成** | `infra/message_queue/registry.py` + 改造 `main.py` | 消费者注册中心：`register(queue_name, handler)`；lifespan startup 自动 `start_all()` 拉起所有 Consumer（asyncio.create_task），shutdown 自动 `stop_all()` 优雅停止。注册信息从 `CONSUMER_REGISTRY` 装饰器收集，避免 main.py 硬编码。 |
| 1.1.15 | **MQ 基础测试** | `tests/test_message_queue.py` + `tests/test_publisher_confirms.py` + `tests/test_consumer_idempotency.py` + `tests/test_task_business_unique.py` + `tests/test_consumer_retry.py` + `tests/test_consumer_registry.py` + `tests/test_exchanges_topology.py` | **连接层**：连接建立/断开。**拓扑**：Exchange/Queue 声明幂等性。**Publisher**（`test_publisher_confirms.py`，≥33 用例）：`publish()` 业务 ID 注入 / `_BATCH_ID_KEYS` 优先级 `business_id > task_id > notification_id > id > message_id` / UUID 兜底 / `x-business-id` header / JSON 序列化 / PERSISTENT delivery mode / expiration ms→s 转换；Publisher Confirms 成功路径；Nack（`DeliveryError`）重试 + 重试耗尽抛；Confirm 超时（`asyncio.TimeoutError`）重试 + 重试耗尽抛；网络断开等通用异常重试；`publish_batch` 全成功/部分失败/全失败/空批量；`BatchPublishResult` 字段（`success`/`failed`/`has_failure`/`total`/`failure_rate`）与 frozen 不可变；Exchange 缓存。**Consumer**（`test_consumer_idempotency.py` + `test_consumer_retry.py` + `test_consumer_registry.py`）：手动 ACK/NACK/重试、死信路由、`DuplicateMessageError` 静默 ACK（不重试）、`@register` 装饰器、ConsumerManager 启停。**业务唯一键**（`test_task_business_unique.py`，13 用例）：Task `(user_id, business_id)` 联合 unique 索引静态校验 + `_ID_KEYS` 优先级 + `insert_idempotent` 业务流（首次/重复/不同用户/非 IntegrityError）+ MQ 重投端到端。`@pytest.mark.integration` 标记需 docker-compose 起 RabbitMQ 的集成测试。**Q9 已完成（累计 ≥ 65 个 MQ 相关单元测试）**。 |

**依赖关系**：1.1.1 → 1.1.2 → 1.1.3/1.1.4/1.1.5（并行）→ 1.1.6/1.1.7/1.1.8（顺序）→ 1.1.9/1.1.10/1.1.11（并行）→ 1.1.12 → 1.1.13 → 1.1.14 → 1.1.15

> **关键决策（已确认）**：Phase 1 全部 Agent 任务走 MQ 异步分发；Consumer 同进程内 asyncio 协程；前端通过 WebSocket 主动推送获取结果（见 Step 1.10.8 Bridge）。

---

### Step 1.2 - 数据模型与迁移

**目标**：核心数据表建好，ORM Model 定义完成

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.2.1 | User ORM Model | `infra/database/models/user.py` | 字段与 PRD §10.2 一致，含索引 |
| 1.2.2 | Job ORM Model | `infra/database/models/job.py` | 含 skills/keywords JSON 字段 |
| 1.2.3 | Resume ORM Model | `infra/database/models/resume.py` | 含 structured_data JSON 字段 |
| 1.2.4 | Application ORM Model | `infra/database/models/application.py` | 含状态枚举 + 状态更新时间 |
| 1.2.5 | AgentMemory ORM Model | `infra/database/models/agent_memory.py` | 含 embedding 向量字段（pgvector） |
| 1.2.6 | Session / Task ORM Model | `infra/database/models/session.py`, `task.py` | 会话与任务表。**Task 表特殊**（Q9 关键）：1) `id` UUID PK（系统自动生成，**不参与业务幂等**）；2) `user_id` 冗余 FK（`users.id` ON DELETE CASCADE，**从 `sessions.user_id` 同步**）；3) `session_id` FK（`sessions.id` ON DELETE CASCADE）；4) `business_id` String(100) NOT NULL（业务方按 `f"{task_type}:{business_key}"` 规则传入的稳定 ID，**MQ 重投幂等键**）；5) `status` 任务状态枚举；6) `payload` JSONB（输入参数）；7) `result` JSONB（输出结果）；8) `error_message` TEXT（失败原因）；9) `retry_count` INT（重试次数，配合 consumer 重试）。**索引**：`ix_tasks_session_id` / `ix_tasks_status` / `ix_tasks_created_at` / `ix_tasks_updated_at` / `ix_tasks_user_id` + **`uq_tasks_user_business (user_id, business_id) UNIQUE`**（Q9 核心约束，详见 Step 1.16）。 |
| 1.2.7 | 首次迁移脚本 + Task 业务唯一键迁移 | `backend/migrations/versions/c528168c702d_*.py` + `backend/migrations/versions/a1b2c3d4e5f6_*.py` | `c528168c702d`（init）：建所有表 + 索引 + 枚举。`a1b2c3d4e5f6`（**Q9**）：加 `tasks.user_id`（FK users.id CASCADE，**NOT NULL 假设开发期无脏数据**）+ `tasks.business_id`（String 100 NOT NULL）+ `ix_tasks_user_id` + `uq_tasks_user_business` 联合 unique 索引。`alembic upgrade head` 全部成功。 |
| 1.2.8 | Alembic 配置 | `backend/migrations/env.py` + `backend/alembic.ini` | env.py 配置 `target_metadata = Base.metadata`，从 `app.infra.database.models` 导入所有 ORM 自动检测 schema diff |

**依赖关系**：1.1.9 → 1.2.1~1.2.6（并行）→ 1.2.7

---

### Step 1.3 - 中间件层

**目标**：所有中间件可用，请求链路可追踪

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.3.1 | CORS 中间件 | `api/middleware/cors.py` | 允许 Extension 来源 |
| 1.3.2 | 全局异常中间件 | `api/middleware/exception.py` | 捕获所有异常，返回统一格式 |
| 1.3.3 | 请求日志中间件 | `api/middleware/logging.py` | 记录请求方法/路径/耗时/状态码 |
| 1.3.4 | Request ID 中间件 | `api/middleware/request_id.py` | 每个请求生成唯一 ID，贯穿日志 |
| 1.3.5 | 限流中间件 | `api/middleware/rate_limit.py` | 60 req/min/user，基于 Redis 计数 |
| 1.3.6 | Auth 中间件 | `api/middleware/auth.py` | JWT 解析 + 用户注入，白名单路径跳过 |

**依赖关系**：1.1.8 → 1.3.1~1.3.5（并行）→ 1.3.6（依赖 JWT 工具）

---

### Step 1.4 - 用户认证模块

**目标**：用户能注册、登录、刷新 Token（F-001）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.4.1 | User DTO / Schema | `domain/user/models.py` | 注册/登录/响应 Pydantic Model |
| 1.4.2 | User Validator | `domain/user/validator.py` | 邮箱格式、密码强度校验 |
| 1.4.3 | User Repository | `infra/repositories/user_repo.py` | CRUD + 按邮箱查询，async |
| 1.4.4 | User Service | `domain/user/service.py` | 注册（密码 bcrypt）、登录（JWT 签发）、刷新 Token |
| 1.4.5 | Auth Router | `api/routers/auth.py` | POST /register, /login, /refresh |
| 1.4.6 | 认证模块测试 | `tests/` | 注册/登录/刷新 正常+异常 用例 |

**依赖关系**：1.2.1 + 1.3.6 → 1.4.1~1.4.4（顺序）→ 1.4.5 → 1.4.6

---

### Step 1.5 - 简历模块

**目标**：用户能上传简历，自动解析为结构化数据（F-002）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.5.1 | Resume DTO / Schema | `domain/resume/models.py` | 上传/响应/结构化数据 Model |
| 1.5.2 | Resume Validator | `domain/resume/validator.py` | 文件类型/大小校验 |
| 1.5.3 | Resume Reader Tool | `tools/file/resume_reader.py` | 解析 PDF/DOCX → 结构化 JSON |
| 1.5.4 | PDF Reader | `tools/file/pdf_reader.py` | 异步读取 PDF 文本 |
| 1.5.5 | DOCX Reader | `tools/file/docx_reader.py` | 异步读取 DOCX 文本 |
| 1.5.6 | Resume Repository | `domain/resume/repository.py` + `infra/repositories/resume_repo.py` | CRUD，async |
| 1.5.7 | Resume Service | `domain/resume/service.py` | 上传 → 解析 → 存储 → 查询 |
| 1.5.8 | Resume Router | `api/routers/resume.py` | POST /upload, GET /, GET /{id} |
| 1.5.9 | 简历模块测试 | `tests/` | 上传/解析/查询 正常+异常 用例 |
| 1.5.10 | **Resume 缓存（active resume）** | `domain/cache/resume.py` + `infra/cache/resume.py` + 改造 `domain/resume/service.py` | Cache-Aside 模式,只缓存 active resume。key=`resume:active:{user_id}`,TTL=1800s。读路径:cache.get → miss 走 DB → setex 回填。写路径:upload/set_active/fill_structured_data/delete 完成后 invalidate。fail-open:Redis 异常一律降级到 DB。详见文末「Resume 缓存架构设计」小节 |

**依赖关系**：1.2.3 + 1.4 → 1.5.1~1.5.7（顺序）→ 1.5.8 → 1.5.9

---

### Step 1.6 - 岗位模块 + Job Analysis Agent ✅ 已完成

**目标**：岗位数据能存储，JD 能被 Agent 智能分析（F-003, F-004）

**完成状态**（2026-06-30）：
- 1.6.1~1.6.15 全部 15 个子任务交付完成
- 核心交付：Job DTO/Parser/Extractor/Repository/Service（异步化）/Agent（状态机）/Router（202+task_id）/Cache（fail-open）/TaskService/Consumer（5 步流水线）/集成测试
- 关键修复：`TaskService._VALID_TRANSITIONS` 允许 `PENDING → FAILED`，覆盖 MQ 投递失败 / Publisher 未注入 / 同步降级失败等「任务未开始即失败」场景
- 测试覆盖：91 个核心测试用例全部通过（job_service / job_async_flow / job_analysis_consumer / task_service / match_models / match_scorer）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.6.1 | Job DTO / Schema | `domain/job/models.py` | 岗位创建/分析结果/响应 Model |
| 1.6.2 | Job Parser | `domain/job/parser.py` | JD 文本预处理、分段 |
| 1.6.3 | Job Extractor | `domain/job/extractor.py` | 调用 LLM 提取技能/关键词/难度 |
| 1.6.4 | LLM Extract Tool | `tools/llm/extract_tool.py` | 封装 LLM 调用，结构化输出 |
| 1.6.5 | Web Search Tool | `tools/retrieval/web_search.py` | 搜索公司信息补充分析 |
| 1.6.6 | Job Repository | `domain/repositories/job.py` + `infra/repositories/job_repo.py` | CRUD + 按技能/关键词查询 |
| 1.6.7 | Job Service（异步化） | `domain/job/service.py` | **不再同步执行 Agent**。流程：创建 Job → 创建 Task(status=pending) → Publisher 发送 `agent.task.job_analysis` 消息 → 返回 `{job_id, task_id}`。同步部分仅做参数校验与 Job/Task 落库。RabbitMQ 不可用时降级为同步执行并打 warning。 |
| 1.6.8 | Job Analysis Agent | `domain/agent/service.py`（Facade）+ `domain/agent/job_analysis_agent.py`（实际状态机实现）+ `domain/agent/{prompts,policies,capabilities,models}.py`（预留扩展位） | LangGraph 状态机：PARSING → EXTRACTING → ANALYZING → COMPLETED（**纯计算函数，被 Consumer 调用**）。`service.py` 作为 AgentService Facade 统一对外暴露，`job_analysis_agent.py` 是 JobAnalysisAgent 具体实现（依赖注入 parser/extractor/web_search）。`prompts/policies/capabilities/models.py` 为后续 Agent 能力扩展预留，当前为空占位。 |
| 1.6.9 | Agent State 定义 | `runtime/state/agent_state.py` | Agent 运行状态枚举与转换 |
| 1.6.10 | Job Router + Task Router | `api/routers/jobs.py` + `api/routers/task.py` | POST /analyze 返回 `202 Accepted` + `{task_id}`；GET /tasks/{task_id} 查询任务状态由 `task.py` 路由提供（复用 Step 1.6.13 TaskService） |
| 1.6.11 | 岗位模块测试 | `tests/test_job_{parser,extractor,repo,service,router,analysis_agent,analysis_cache,analysis_consumer}.py` | 创建/分析/查询 正常+异常 用例；覆盖 Job Parser/Extractor/Repository/Service/Router/Agent/Cache/Consumer 各层单测 |
| 1.6.12 | **Job 分析结果缓存（LLM 产物）** | `domain/cache/job.py` + `infra/cache/job_analysis.py` + 改造 `domain/job/service.py`（注：原计划路径 `domain/repositories/job_analysis_cache.py` + `infra/cache/job_analysis_cache.py` 在 DDD 架构优化后调整为 `domain/cache/` + `infra/cache/` 命名空间，与 Resume 缓存结构对齐） | 沿用 Resume 缓存的 **Protocol + Redis + fail-open** 统一模式，只缓存 Job Analysis Agent 的 LLM 产出（推理结果）。key=`job:analysis:{job_id}`，TTL=3600s（分析结果稳定，可设更长）。读路径:cache.get → miss 走 DB → setex 回填。写路径:Agent `COMPLETED` 时直接 setex 覆盖；仅缓存 `completed` 状态结果，`pending`/`failed`/`analyzing` 不缓存。 |
| 1.6.13 | **Task Service（异步任务编排）** | `domain/task/service.py` + `infra/repositories/task_repo.py` | 任务生命周期管理：`create_task(type, payload)`、`mark_running(task_id)`、`mark_completed(task_id, result)`、`mark_failed(task_id, error)`、`get_task(task_id)`、`list_tasks(user_id, status, page)`。Task 表复用 Step 1.2.6。 |
| 1.6.14 | **Job Analysis Consumer** | `infra/message_queue/handlers/job_analysis.py` | 订阅 `copilot.agent.job_analysis`：1) `mark_running`；2) 调用 Job Analysis Agent（Step 1.6.8）；3) 落库 `Job.analysis_result` + 写缓存（Step 1.6.12）；4) `mark_completed`；5) Publisher 发 `agent.event.completed(task_id, result)`。失败重试 3 次后入死信。注册到 Step 1.1.14 Registry。 |
| 1.6.15 | **Job 异步流程集成测试** | `tests/test_job_async_flow.py` | 标记 `@pytest.mark.integration`：POST /analyze → 断言 202+task_id → 等待 Consumer 处理 → 断言 Task=completed + Job.analysis_result 非空 + cache 命中 + WebSocket 收到 event 通知（用 test client 模拟 WS）。 |

**依赖关系**：1.2.2 + 1.5 + 1.1.15（MQ 基础就绪）→ 1.6.1~1.6.7（顺序，前 6 个不变，1.6.7 改造为异步）→ 1.6.8~1.6.9 → 1.6.10 → 1.6.11 → 1.6.12 → 1.6.13 → 1.6.14（注册到 1.1.14 Registry）→ 1.6.15

> **MQ 改造点（1.6 第一个落地）**：从此 Step 开始所有 Agent 任务走"API 落库 Task → Publisher 发消息 → Consumer 执行 Agent → Consumer 落结果 → 发完成事件"的标准流水线。下游 1.7/1.8 完全复用该模式。

---

### Step 1.7 - 匹配模块 + Resume Agent

**目标**：简历与岗位能匹配，生成匹配分数和优化建议（F-005, F-006）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.7.1 | Match DTO / Schema | `domain/match/` | 匹配请求/结果/优化建议 Model |
| 1.7.2 | Scorer | `domain/match/scorer.py` | 技能匹配度计算 + 语义匹配度计算 |
| 1.7.3 | Ranker | `domain/match/ranker.py` | 多岗位排序 |
| 1.7.4 | Strategy | `domain/match/strategy.py` | 优化建议生成策略 |
| 1.7.5 | LLM Classify Tool | `tools/llm/classify_tool.py` | 技能分类/匹配度评估 |
| 1.7.6 | RAG Tool | `tools/retrieval/rag_tool.py` | 语义检索相似岗位/简历 |
| 1.7.7 | Vector Search | `tools/retrieval/vector_search.py` | pgvector 向量检索 |
| 1.7.8 | Match Service（异步化） | `domain/match/service.py` | **不再同步执行 Agent**。流程：参数校验 → 创建 Task(type=resume_match) → Publisher 发送 `agent.task.resume_match` 消息 → 返回 `{task_id}`。复用 Step 1.6.13 Task Service。 |
| 1.7.9 | Resume Agent | `domain/agent/service.py` | LangGraph 状态机：PARSING_RESUME → MATCHING → ANALYZING_GAP → GENERATING_SUGGESTIONS（**纯计算函数，被 Consumer 调用**） |
| 1.7.10 | Match Router | `api/routers/match.py` | POST /calculate 返回 `202 Accepted` + `{task_id}` |
| 1.7.11 | 匹配模块测试 | `tests/` | 匹配/排序/建议 正常+异常 用例 |
| 1.7.12 | **Match Consumer** | `infra/message_queue/handlers/resume_match.py` | 订阅 `copilot.agent.resume_match`：1) mark_running；2) 调用 Resume Agent（Step 1.7.9）；3) 落库 Match 结果；4) mark_completed；5) 发 `agent.event.completed`。注册到 Step 1.1.14 Registry。失败重试 3 次后入死信。 |

**依赖关系**：1.6 + 1.6.13（Task Service 复用）→ 1.7.1~1.7.8（顺序，1.7.8 改造为异步）→ 1.7.9 → 1.7.10 → 1.7.11 → 1.7.12

---

### Step 1.8 - 沟通模块 + Communication Agent

**目标**：能生成打招呼内容和回复模板，经过合规检查（F-007）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.8.1 | Communication DTO / Schema | `domain/communication/` | 话术生成请求/响应 Model |
| 1.8.2 | Compliance Checker | `domain/communication/compliance_checker.py` | 检查虚假经历/夸大描述 |
| 1.8.3 | Template Manager | `domain/communication/template_manager.py` | 模板管理（打招呼/回复/面试邀约） |
| 1.8.4 | LLM Rewrite Tool | `tools/llm/rewrite_tool.py` | 个性化改写 |
| 1.8.5 | PII Filter Tool | `tools/validation/pii_filter.py` | 个人隐私信息过滤 |
| 1.8.6 | Content Checker Tool | `tools/validation/content_checker.py` | 内容合规检查 |
| 1.8.7 | Communication Service（异步化） | `domain/communication/service.py` | **不再同步执行 Agent**。流程：参数校验 → 创建 Task(type=communication) → Publisher 发送 `agent.task.communication` 消息 → 返回 `{task_id}`。复用 Step 1.6.13 Task Service。 |
| 1.8.8 | Communication Agent | `domain/agent/service.py` | LangGraph 状态机：GENERATING → COMPLIANCE_CHECK → FILTERING_PII → COMPLETED（**纯计算函数，被 Consumer 调用**） |
| 1.8.9 | Communication Router | `api/routers/agent.py` | POST /communication/generate 返回 `202 Accepted` + `{task_id}` |
| 1.8.10 | 沟通模块测试 | `tests/` | 生成/合规/过滤 正常+异常 用例 |
| 1.8.11 | **Communication Consumer** | `infra/message_queue/handlers/communication.py` | 订阅 `copilot.agent.communication`：1) mark_running；2) 调用 Communication Agent（Step 1.8.8）；3) 落库沟通话术；4) mark_completed；5) 发 `agent.event.completed`。注册到 Step 1.1.14 Registry。失败重试 3 次后入死信。 |

**依赖关系**：1.7 + 1.6.13 → 1.8.1~1.8.7（顺序，1.8.7 改造为异步）→ 1.8.8 → 1.8.9 → 1.8.10 → 1.8.11

---

### Step 1.9 - 投递记录模块

**目标**：投递记录可管理，状态可更新（F-008）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.9.1 | Application ORM Model 补充 | `infra/database/models/application.py` | 与 1.2.4 对齐，含状态转换约束 |
| 1.9.2 | Application DTO / Schema | `domain/workflow/models.py` | 创建/更新/查询 Model |
| 1.9.3 | Application Repository | `infra/repositories/` | CRUD + 按状态/用户查询 |
| 1.9.4 | Application Service | `domain/workflow/service.py` | 创建投递 → 状态更新 → 查询列表 |
| 1.9.5 | Application Router | `api/routers/` | GET /, POST /, PATCH /{id} |
| 1.9.6 | 投递模块测试 | `tests/` | 创建/更新/查询 正常+异常 用例 |

**依赖关系**：1.2.4 + 1.8 → 1.9.1~1.9.5（顺序）→ 1.9.6

---

### Step 1.10 - Agent 会话管理

**目标**：Agent 会话能创建、恢复、销毁（F-009）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.10.1 | Session DTO / Schema | `domain/session/models.py` | 会话创建/状态/响应 Model |
| 1.10.2 | Session Repository | `domain/repositories/session.py` + `infra/repositories/session_repo.py` | CRUD |
| 1.10.3 | Session Service | `domain/session/service.py` | 创建 → 恢复 → 销毁 |
| 1.10.4 | Agent Router 补充 | `api/routers/agent.py` | POST /chat, POST /task, GET /task/{id} |
| 1.10.5 | WebSocket 端点 | `api/routers/agent.py` | WS /ws/agent/{session_id} |
| 1.10.6 | 会话模块测试 | `tests/` | 创建/恢复/销毁/WS 正常+异常 用例 |
| 1.10.7 | **Session/Task 状态缓存** | `domain/repositories/session_cache.py` + `infra/cache/session_cache.py` + 改造 `domain/session/service.py` | 沿用 **Protocol + Redis + fail-open** 统一模式，缓存 Agent 会话与任务状态（高 WebSocket 轮询场景）。key=`session:state:{session_id}` / `task:state:{task_id}`，TTL=600s（10 分钟，状态机推进频繁）。读路径:cache.get → miss 走 DB → setex 回填。写路径:状态机推进（create/update/complete/fail）后 invalidate。 |
| 1.10.8 | **Notification Consumer + WebSocket Bridge** | `infra/message_queue/handlers/notification.py` + `api/realtime/connection_manager.py` + `api/realtime/ws_router.py` | 订阅 `copilot.agent.event.*`（所有 agent 事件 fanout 过来）：1) 从 event payload 解析 user_id；2) 查 ConnectionManager 中该 user 的所有 WS 连接；3) 推送 `{type: "task_completed", task_id, payload}` JSON。ConnectionManager 维护 `user_id → set[WebSocket]` 映射，支持多端登录。WS 端点 `ws://host/ws/agent/{user_id}`，握手时校验 JWT。注册到 Step 1.1.14 Registry。 |
| 1.10.9 | **MQ + WS 端到端测试** | `tests/test_mq_ws_bridge.py` | 标记 `@pytest.mark.integration`：启动 test server → 模拟 Extension 建立 WS → 触发 Job Analysis 异步流程 → 断言 100ms 内 WS 收到 `task_completed` 事件 + payload 含 result。同时测试多端连接（同一 user 多个 WS 都能收到）。 |

**依赖关系**：1.9 + 1.6.14 + 1.7.12 + 1.8.11（所有 Agent Consumer 都已实现）→ 1.10.1~1.10.7（顺序）→ 1.10.8（必须先有 Agent Consumer 在发事件）→ 1.10.9

---

### Step 1.11 - Chrome Extension 基础框架（Boss 直聘）

**目标**：Extension 能在 Boss 直聘页面提取岗位数据，与后端通信

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.11.1 | Manifest 配置 | `extension/src/manifest.json` | 权限、content_scripts、background 配置 |
| 1.11.2 | Vite 构建配置 | `extension/vite.config.ts` | crxjs 插件 + Vue + Tailwind |
| 1.11.3 | Boss 选择器定义 | `extension/src/modules/boss/selector.ts` | 岗位详情页 CSS 选择器 |
| 1.11.4 | Boss DOM 解析 | `extension/src/modules/boss/parser.ts` | 提取岗位名称/公司/薪资/JD |
| 1.11.5 | Boss 页面操作 | `extension/src/modules/boss/actions.ts` | 点击/滚动/输入 |
| 1.11.6 | Boss 适配器 | `extension/src/modules/boss/adapter.ts` | 统一接口适配 |
| 1.11.7 | DOM Engine | `extension/src/core/dom_engine.ts` | 通用 DOM 解析引擎 |
| 1.11.8 | Chrome Message | `extension/src/messaging/chrome_message.ts` | Content ↔ Background 消息通信 |
| 1.11.9 | WebSocket Client | `extension/src/messaging/websocket_client.ts` | 与后端 WS 通信 |
| 1.11.10 | Background Service Worker | `extension/src/background/service_worker.ts` | 消息路由 + 任务监听 |
| 1.11.11 | Content Script | `extension/src/content/content.ts` | 页面检测 + 数据上报 |
| 1.11.12 | SidePanel UI - 分析结果 | `extension/src/components/` | 岗位分析结果展示组件 |
| 1.11.13 | SidePanel UI - 匹配结果 | `extension/src/components/` | 匹配分数/缺失技能展示 |
| 1.11.14 | SidePanel UI - 沟通话术 | `extension/src/components/` | 话术展示 + 一键复制 |
| 1.11.15 | Pinia Store | `extension/src/stores/` | 全局状态管理 |
| 1.11.16 | Extension 端到端测试 | 手动验证 | Boss 直聘页面完整流程 |

**依赖关系**：1.10 → 1.11.1~1.11.6（Boss 适配）+ 1.11.7~1.11.11（通信层，并行）→ 1.11.12~1.11.15（UI 层）→ 1.11.16

---

### Step 1.12 - MVP 集成测试与收尾

**目标**：端到端闭环可运行

| 序号 | 任务 | 验收标准 |
|------|------|---------|
| 1.12.1 | 后端集成测试 | 完整闭环：注册 → 上传简历 → 分析岗位 → 匹配 → 生成话术 → 投递 |
| 1.12.2 | Extension + 后端联调 | Boss 直聘页面完整流程可运行 |
| 1.12.3 | Docker Compose | PostgreSQL + Redis + RabbitMQ + Backend 一键启动 |
| 1.12.4 | API 文档 | FastAPI Swagger 可用，所有端点有描述 |
| 1.12.5 | MVP 里程碑文档 | `docs/` 下生成架构/模块流程/异步原理文档 |

---

### Step 1.16 - 业务 MQ 接入（Q9 关键收口）

> **本 Step 解决的核心问题**：Step 1.1 已搭好 MQ 基础设施（publisher/consumer/registry/topology/幂等键 + 静默 ACK），但**业务层（Service / Router）零接入**。`domain/*/service.py` 全部为空壳，`@register` 装饰器未被任何业务函数使用。本 Step 定义业务接入的标准模式与契约。
>
> **接入模板与 1.6.7/1.6.13/1.6.14/1.7.12/1.8.11 的关系**：本 Step 抽出通用接入模板；具体业务场景（Job Analysis / Resume Match / Communication）由后续 Step 按模板实现。

**目标**：业务 Service 落 Task + Publisher 发消息 + Consumer 执行 + 落结果 + 发完成事件 的标准流水线可复用

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1.16.1 | **业务接入契约文档** | `docs/architecture/mq_integration_contract.md` | 明确"业务方传 `business_id`"的具体规则：`f"{task_type}:{business_key}"` 命名、稳定性要求（同一操作重试 ID 一致）、`user_id` 同步来源（必须从 session 同步，禁止信任客户端）、失败重投后业务方是否能重试成功的判定标准。包含反例（错误用 `id` UUID、错误用 `f"{user_id}:{timestamp}"`）。 |
| 1.16.2 | **Task Service 骨架** | `domain/task/service.py` + `domain/task/dto.py` + `infra/repositories/task_repo.py` | 任务生命周期：`create_task(type, business_id, payload, session)`（内部用 `insert_idempotent`）、`get_task(task_id)`、`mark_running(task_id)`、`mark_completed(task_id, result)`、`mark_failed(task_id, error)`、`list_tasks(user_id, status, page)`。**这是后续所有 Agent 业务复用的公共组件**。 |
| 1.16.3 | **Task Service 单元测试** | `tests/test_task_service.py` | create_task：首次成功 / 同 business_id 重复抛 `DuplicateMessageError` / 不同 user 同 business_id 允许 / 字段缺失抛 `ValueError`；mark_* 状态机：合法转换 + 非法转换抛 `TaskStateError`；get/list 分页正确。 |
| 1.16.4 | **业务接入模板：Service 层** | `domain/_template_async_service.py`（注释参考） | Service 异步化标准模式：参数校验 → `TaskService.create_task(type, business_id, payload, session)` → `await publisher.publish(exchange, routing_key, body, message_id=business_id)` → 返回 `{task_id}`。降级：MQ 不可用 try/except 后降级同步执行并 `logger.warning`（参考 Step 1.6.7）。 |
| 1.16.5 | **业务接入模板：Consumer 层** | `infra/message_queue/_template_handler.py`（注释参考） + `tests/test_consumer_handler_template.py` | Consumer 标准模式（5 步流水线）：1) `mark_running`；2) 调用纯计算函数（Agent / LLM 工具）；3) 落结果到业务表（`Job.analysis_result` 等）；4) `mark_completed` + 写缓存（若有）；5) Publisher 发 `agent.event.completed(task_id, result)`。**注册**：`@register("copilot.agent.{type}", max_retries=3, retry_base_delay_ms=10000)`。失败重试 3 次后入死信。 |
| 1.16.6 | **业务接入模板：Router 层** | `api/_template_async_router.py`（注释参考） | 异步化 Router 标准模式：`POST /xxx` 返回 `202 Accepted` + `{task_id}`（不是 200 + result），新增 `GET /tasks/{task_id}` 轮询状态。**为什么 202 而非 200**：Agent 任务耗时 5s+ 必异步，202 告知客户端"已接受，请轮询"。 |
| 1.16.7 | **`insert_idempotent` 业务使用示例集** | `domain/common/idempotent.py`（示例注释） + `tests/test_idempotent_usage_examples.py` | 3 个典型场景的端到端测试：1) Task 创建（同 business_id 重投静默 ACK）；2) Resume 上传（同 file_hash 已存在去重）；3) Job 抓取（同 source_url 不重复入库）。每个场景包含"首次成功 + 重复抛 DuplicateMessageError + 消费者基类静默 ACK"三断言。 |
| 1.16.8 | **业务接入冒烟测试** | `tests/test_business_mq_smoke.py` | `@pytest.mark.integration`：起 test RabbitMQ + 真实业务 Consumer → POST 接口 → 等待 Consumer 处理 → 断言 Task 状态 `completed` + 业务表有结果 + `agent.event.completed` 事件被发出。**这是"基础设施 ↔ 业务层"联通的最低标准**。 |
| 1.16.9 | **业务 DTO 化规约**（方案 2） | `domain/common/dto.py`（helper）+ `domain/*/dto.py`（每域一个） | 强制 Service 方法返回 Pydantic DTO，**禁止返回 ORM 实例**。在 `domain/common/dto.py` 提供 `to_dto(orm_obj, dto_class)` 工具方法封装 `model_validate`。**根因**：[2026-06-17 复盘](file:///g:/my/my_file/AI%20Career%20Copilot/docs/issues/2026-06-17-sqlalchemy-orm-expire-missinggreenlet.md)：ORM 实例跨 session/跨层边界后访问属性会触发 `MissingGreenlet`；`expire_on_commit=False` 只解决 commit 后场景，不解决 session 关闭后场景。**Service 层统一模式**：`async with pg_session_factory() as session: ... return TaskDTO.model_validate(task)`。**测试**：`tests/test_dto_conversion.py` 覆盖所有域的 ORM → DTO 转换路径。 |
| 1.16.10 | **跨层禁传 ORM 规约 + Lint 规则**（方案 3） | `tools/lint/no_orm_leak.py`（自研 ruff 规则） + `tests/test_layer_isolation.py` | **规约**：1) Router handler 返回类型必须是 Pydantic BaseModel；2) Service 跨方法传数据必须 DTO/dict；3) Consumer 接收的 message body 是 dict/Pydantic（天然安全）。**Lint 实现**：`ast` 扫描所有 `api/routers/*.py`，断言 handler 返回值类型含 `BaseModel` 子串。**CI 集成**：`pre-commit hook` + `pytest tests/test_layer_isolation.py`。**测试覆盖**：每个新建 Service 都跑 ORM 泄露检测。**触发场景**：开发 PR 时自动拦截，避免 `MissingGreenlet` 类 bug 进入生产。 |
| 1.16.11 | **Consumer ORM 边界专项测试** | `tests/test_consumer_orm_isolation.py` | `@pytest.mark.integration`：真实 PG + 真实 RabbitMQ + 真实 Consumer。**核心场景**：1) Consumer 收到消息后用 `pg_session_factory.create_session()` 开启新 session 处理；2) `handle_message` 返回**前**必须把 ORM 实例转 DTO 并放入 publish 的消息体；3) Consumer 结束 `ack()` 后，session 关闭，**禁止**在 Consumer 外访问 ORM 实例。**断言**：模拟 session 关闭后访问场景，确认无 `MissingGreenlet`。这是端到端验证 1.16.9 + 1.16.10 真实生效的关键测试。 |

**依赖关系**：1.1（MQ 基础设施）+ 1.2.6/1.2.7（Task 表业务 unique 约束）+ 1.1.10（`DuplicateMessageError`）+ 1.1.8（消费者基类静默 ACK）→ **1.16.1（契约文档）** → 1.16.2/1.16.3（Task Service） → 1.16.4/1.16.5/1.16.6（接入模板） → 1.16.7（使用示例） → 1.16.8（冒烟测试） → 后续业务 Step 1.6.7/1.6.13/1.6.14/1.7.12/1.8.11

> **关键契约**（必须在 1.16.1 文档中明确）：**业务方必须传 `business_id`** —— 这是 MQ 幂等消费的前置条件。调用方（API/Service）违反契约（不传 / 传 `id` UUID / 传时间戳）的后果是：MQ 重投时业务被重复执行。**Lint / 类型检查 / 集成测试** 三层防御缺一不可。

---

## Phase 2 - 增强功能

> 前置条件：Phase 1 全部完成并通过验收

### Step 2.1 - Agent Runtime 完善

**目标**：Agent 运行时核心能力补全（支撑后续高级功能）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.1.1 | Planner | `runtime/planner/planner.py`, `task_graph.py` | 任务分解为 DAG，支持依赖排序 |
| 2.1.2 | Executor + Dispatcher | `runtime/executor/executor.py`, `dispatcher.py` | 并行执行、超时控制、失败重试 |
| 2.1.3 | Retry + Timeout | `runtime/executor/retry.py`, `timeout.py` | 指数退避重试、超时中断 |
| 2.1.4 | Event Bus | `runtime/events/bus.py`, `publisher.py`, `subscriber.py` | 发布订阅模式，Agent 间通信 |
| 2.1.5 | Short-term Memory | `runtime/memory/short_term.py` | 会话内上下文记忆 |
| 2.1.6 | Long-term Memory | `runtime/memory/long_term.py` | 跨会话持久化记忆 |
| 2.1.7 | Vector Memory | `runtime/memory/vector_memory.py` | 基于向量检索的语义记忆 |
| 2.1.8 | Checkpoint + Recovery | `runtime/checkpoints/manager.py`, `recovery.py` | 执行检查点保存与恢复 |
| 2.1.9 | Observer | `runtime/observer/logger.py`, `metrics.py`, `tracing.py` | 执行可观测性 |
| 2.1.10 | Scheduler | `runtime/scheduler/` | 并发控制、优先级调度、队列管理 |
| 2.1.11 | Workflow Engine | `runtime/workflow/` | DAG 执行引擎 |

---

### Step 2.2 - Career Strategy Agent

**目标**：投递 ≥10 次后自动生成策略报告（F-010）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.2.1 | Strategy DTO / Schema | `domain/match/strategy.py` | 策略报告 Model |
| 2.2.2 | LLM Summarize Tool | `tools/llm/summarize_tool.py` | 数据摘要生成 |
| 2.2.3 | Strategy Agent | `domain/agent/service.py` | COLLECTING_DATA → ANALYZING → GENERATING_STRATEGY → COMPLETED |
| 2.2.4 | Strategy Router | `api/routers/` | GET /strategy/report |
| 2.2.5 | 策略模块测试 | `tests/` | 策略生成/触发 正常+异常 用例 |

---

### Step 2.3 - 面试管理

**目标**：面试安排、提醒、面经记录（F-011）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.3.1 | Interview ORM Model | `infra/database/models/` | 面试记录表 |
| 2.3.2 | Interview DTO / Schema | `domain/` | 面试创建/更新/查询 Model |
| 2.3.3 | Interview Service | `domain/` | 面试 CRUD + 状态管理 |
| 2.3.4 | Interview Router | `api/routers/` | CRUD 端点 |
| 2.3.5 | 面试提醒 | `integrations/notification/` | 邮件/Webhook 通知 |
| 2.3.6 | 面试模块测试 | `tests/` | CRUD + 提醒 正常+异常 用例 |

---

### Step 2.4 - 多平台适配

**目标**：支持猎聘、智联、实习僧（F-012）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.4.1 | 猎聘适配器 | `extension/src/modules/liepin/` | 4 模块（selector/parser/actions/adapter） |
| 2.4.2 | 智联适配器 | `extension/src/modules/zhilian/` | 4 模块 |
| 2.4.3 | 实习僧适配器 | `extension/src/modules/shixisheng/` | 4 模块 |
| 2.4.4 | 后端招聘平台集成 | `integrations/recruitment/` | 各平台数据标准化 |
| 2.4.5 | 多平台测试 | 手动验证 | 每个平台可正常提取岗位数据 |

---

### Step 2.5 - 实时状态跟踪

**目标**：跟踪投递后状态变化，推送通知（F-013）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.5.1 | 状态检测服务 | `domain/` | 定时检测投递状态 |
| 2.5.2 | WebSocket 推送 | `api/routers/agent.py` | 状态变更实时推送 |
| 2.5.3 | 邮件通知 | `integrations/notification/email.py` | 状态变更邮件 |
| 2.5.4 | Webhook 通知 | `integrations/notification/webhook.py` | 外部 Webhook 推送 |
| 2.5.5 | 状态跟踪测试 | `tests/` | 状态变更 + 通知 正常用例 |

---

### Step 2.6 - Agent 长期记忆

**目标**：跨会话保留用户偏好和求职历史（F-014）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.6.1 | Memory Interface | `runtime/memory/interface.py` | 统一记忆接口 |
| 2.6.2 | Long-term Memory 实现 | `runtime/memory/long_term.py` | PostgreSQL 持久化 |
| 2.6.3 | Vector Memory 实现 | `runtime/memory/vector_memory.py` | pgvector 语义检索 |
| 2.6.4 | 记忆集成到 Agent | `domain/agent/` | 新会话可引用历史交互 |
| 2.6.5 | 记忆模块测试 | `tests/` | 存储/检索/遗忘 正常用例 |

---

### Step 2.7 - 工作流编排

**目标**：支持自定义 Agent 工作流（F-015）

| 序号 | 任务 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 2.7.1 | Workflow DAG | `runtime/workflow/dag.py`, `node.py`, `edge.py` | DAG 定义与校验 |
| 2.7.2 | Workflow Engine | `runtime/workflow/workflow_engine.py` | DAG 执行引擎 |
| 2.7.3 | Workflow Service | `domain/workflow/service.py` | 工作流定义/执行/查询 |
| 2.7.4 | Workflow Router | `api/routers/workflow.py` | CRUD + 执行端点 |
| 2.7.5 | 工作流测试 | `tests/` | 定义/执行/状态查询 正常用例 |

---

## Phase 3 - 优化与高级功能

> 前置条件：Phase 2 全部完成并通过验收

### Step 3.1 - 简历自动优化（F-016）

- 基于目标岗位自动修改简历内容
- 依赖：Resume Agent + Workflow Engine

### Step 3.2 - 市场趋势分析（F-017）

- 分析招聘市场趋势和薪资水平
- 依赖：大量岗位数据 + Web Search Tool

### Step 3.3 - 模拟面试（F-018）

- AI 驱动的模拟面试练习
- 依赖：Communication Agent + LLM

### Step 3.4 - 社区经验分享（F-019）

- 用户间求职经验分享
- 依赖：用户系统 + 内容管理

### Step 3.5 - 多语言支持（F-020）

- 支持英文 JD 分析和简历优化
- 依赖：LLM 多语言能力

### Step 3.6 - 性能优化与压测

- 并发优化、缓存策略、数据库索引优化
- 压测验证 ≥ 100 并发用户

---

## 开发依赖关系总览

```
Phase 1（MVP 闭环，MQ 异步化）:
  1.1 基础设施（含 RabbitMQ + Consumer Registry + MQ 基础测试 1.1.14/15）
    → 1.2 数据模型
      → 1.3 中间件
        → 1.4 用户认证
          → 1.5 简历模块
            → 1.6 岗位 + Job Analysis Agent
                  ├── 1.6.7  Job Service 异步化（落 Task + 发消息）
                  ├── 1.6.13 Task Service（异步任务公共组件）
                  ├── 1.6.14 Job Analysis Consumer（执行 Agent + 发完成事件）
                  └── 1.6.15 异步流程集成测试
              → 1.7 匹配 + Resume Agent
                  ├── 1.7.8  Match Service 异步化
                  └── 1.7.12 Match Consumer
                → 1.8 沟通 + Communication Agent
                  ├── 1.8.7  Communication Service 异步化
                  └── 1.8.11 Communication Consumer
                  → 1.9 投递记录
                    → 1.10 Agent 会话 + WS Bridge
                          ├── 1.10.7  Session/Task 状态缓存
                          ├── 1.10.8  Notification Consumer + WebSocket Bridge（消费所有 agent.event.*）
                          └── 1.10.9  MQ + WS 端到端测试
                      → 1.11 Chrome Extension
                        → 1.12 集成测试

  ── MQ 横切关系 ──
  API 落 Task (1.6.13) → Publisher 发任务消息 → Consumer 执行业务 (1.6.14/1.7.12/1.8.11)
       → Consumer 发完成事件 → Notification Consumer (1.10.8) → WebSocket 推 Extension

Phase 2（增强）:
  2.1 Agent Runtime 完善（独立，可与 2.2~2.7 并行准备）
  2.2~2.7 各增强模块（依赖 2.1 部分能力）
  ── Phase 2 新增 MQ 用法 ──
  2.2.3 Strategy Agent → agent.task.strategy 队列
  2.3.5 面试提醒       → copilot.notification.email / webhook 队列
  2.5.2 状态变更推送   → agent.event.* + Notification Consumer

Phase 3（优化）:
  3.1~3.6 各高级功能（依赖 Phase 2）
```

---

## 关键风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| LLM API 不稳定/延迟高 | Agent 分析/生成超时 | 超时重试 + 降级策略 + 缓存 |
| 招聘网站 DOM 变更 | Extension 解析失败 | 选择器配置化 + 变更告警 |
| pgvector 性能不足 | 语义检索慢 | 索引优化 + 考虑独立向量库 |
| 简历解析准确率低 | 结构化数据质量差 | 多格式适配 + 人工校验兜底 |
| 并发安全 | 状态竞争/数据不一致 | 乐观锁 + 状态机约束 |
| RabbitMQ 不可用 | Agent 任务无法分发 | 连接重试（RobustConnection）+ 死信队列 + **降级为同步执行并打 warning**（Service 层 try/except） |
| 缓存击穿 | 高并发下大量请求穿透到 DB | 写后失效 + 30min TTL 兜底 + Redis fail-open 降级 |
| 缓存与 DB 不一致 | 短暂返回陈旧数据 | TTL 兜底（30min 内自愈）+ 监控命中率 |
| **MQ 重复消费** | Agent 被执行多次，结果重复落库 | **业务方传 `business_id` + 业务表 `(user_id, business_id)` 联合 unique 约束**：`IntegrityError` → `DuplicateMessageError` → 消费者基类静默 ACK（不重试、不进死信）。见 Step 1.16 业务接入契约。 |
| **业务方违反 `business_id` 契约** | MQ 重投时业务被重复执行（业务方传 `id` UUID / 时间戳 / 不传） | **三层防御**：1) 契约文档（Step 1.16.1）+ Reviewer 培训；2) 静态检查：`Task` Service 构造时 `business_id` 必填非空；3) 集成测试（Step 1.16.8 冒烟测试）：同 `business_id` 重发两条消息，断言只产生 1 条 DB 记录。 |
| **WebSocket 断连导致通知丢失** | 前端错过 task_completed 事件，体验降级 | Notification Consumer 推送失败入 fallback 队列 + Extension 端 `GET /api/tasks/{id}?since=<last_id>` 增量拉取兜底 |
| **Task 长期 pending（消费者卡死）** | 用户一直看不到结果 | Task 增加 `heartbeat_at` 字段，Consumer 每 30s 刷新；超 5min 无心跳自动 mark_failed 并发告警 |
| **同进程 Consumer 与 Web 抢资源** | Web 请求延迟升高 | Consumer 限制并发（asyncio.Semaphore=10）+ LLM 调用集中走 httpx 连接池监控 |
| **business_id 不稳定（业务方传时间戳/UUID）** | 同一操作重试时 ID 不同，unique 约束失效，重复消费 | Step 1.16.1 契约文档明确禁止；Step 1.16.7 集成测试用同 `business_id` 重发，断言 1 条 DB 记录 |
| **SQLAlchemy ORM `MissingGreenlet` 风险** | 访问 session 已关闭的 ORM 实例属性触发 `MissingGreenlet: greenlet_spawn has not been called`，破坏 Event Loop | **方案 1（已实施）**：[`expire_on_commit=False` 在 postgres.py:100](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/infra/database/postgres.py#L100)，**仅防 commit 后 expire，不防 session 关闭后**。**方案 2/3（待 Q10 实施）**：Service 返回 Pydantic DTO + 跨层禁传 ORM 实例。**复盘**：[docs/issues/2026-06-17-sqlalchemy-orm-expire-missinggreenlet.md](file:///g:/my/my_file/AI%20Career%20Copilot/docs/issues/2026-06-17-sqlalchemy-orm-expire-missinggreenlet.md)。**关键事实**：`expire_on_commit=False` 不是万能药，只解决 commit 后场景；session 关闭后访问属性仍会触发，**唯一彻底解决是 DTO 化**。 |
| **业务方不知道何时提交 commit** | commit 时机错乱，事务粒度过大/过小 | 业务层统一使用 `async with pg_session_factory() as session:` + `async with session.begin():` 上下文管理器；Consumer 在 `handle_message` 入口 begin，结束 commit |

---

## 每个 Step 交付物

1. **代码**：符合编码规范的实现代码
2. **测试**：单元测试 + 集成测试（覆盖正常/异常/边界）
3. **Commit**：符合 Git 规范的提交（`feat(module): description`）
4. **技术复盘**（核心模块）：核心逻辑 + 关键技术点 + 潜在风险

---

## RabbitMQ 架构设计

### 定位

RabbitMQ 作为项目统一的**消息队列中间件**，承担以下职责：

| 职责 | 说明 | 替代方案对比 |
|------|------|-------------|
| Agent 任务分发 | API 层将分析/匹配/生成任务投递到队列，消费者异步执行 | 比 BackgroundTasks 可靠（持久化、重试、死信） |
| Event Bus | Agent 间事件通信（任务完成、状态变更） | 比进程内 pub/sub 可跨进程、可持久化 |
| 通知投递 | 邮件/Webhook 异步发送，避免阻塞主流程 | 比同步发送更可靠（重试、堆积缓冲） |
| 死信处理 | 失败消息统一进入死信队列，便于排查和重放 | 无死信则消息丢失无法追溯 |

Redis 仅保留**缓存**和**会话状态**职责，不再承担队列功能。

### Exchange / Queue 拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                        RabbitMQ                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  copilot.agent.topic (topic)                                     │
│    ├── agent.task.job_analysis  → copilot.agent.job_analysis     │
│    ├── agent.task.resume_match  → copilot.agent.resume_match     │
│    ├── agent.task.communication → copilot.agent.communication    │
│    ├── agent.task.strategy      → copilot.agent.strategy         │
│    ├── agent.event.started      │                                │
│    ├── agent.event.completed    │                                │
│    └── agent.event.failed       │                                │
│                                                                  │
│  copilot.task.direct (direct)                                    │
│    ├── agent.task.job_analysis  → copilot.agent.job_analysis     │
│    ├── agent.task.resume_match  → copilot.agent.resume_match     │
│    ├── agent.task.communication → copilot.agent.communication    │
│    └── agent.task.strategy      → copilot.agent.strategy         │
│                                                                  │
│  copilot.notification.fanout (fanout)                            │
│    ├── copilot.notification.email                                │
│    └── copilot.notification.webhook                              │
│                                                                  │
│  copilot.dead_letter.exchange (direct)                           │
│    └── dead_letter → copilot.dead_letter                         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 消息流转示例

**JD 分析流程**：
```
API 收到 POST /api/jobs/analyze
  → Publisher 发送消息到 copilot.agent.topic (routing_key=agent.task.job_analysis)
  → copilot.agent.job_analysis 队列收到消息
  → Consumer 消费，调用 Job Analysis Agent
  → Agent 完成，发布 agent.event.completed 事件
  → 通知队列收到事件，推送 WebSocket 给前端
```

### 文件结构

```
infra/message_queue/
├── __init__.py          # 包初始化（导出 MessagePublisher、BatchPublishResult）
├── connection.py        # 连接管理（单例、RobustConnection、自动重连）
├── exchanges.py         # Exchange/Queue/RoutingKey 定义与声明
├── publisher.py         # 消息发布者（统一发送接口、持久化、Publisher Confirms、message_id 注入、BatchPublishResult 结构化返回、指数退避重试）
└── consumer.py          # 消息消费者（订阅、ACK/NACK、并发控制）
```

### 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 连接方式 | RobustConnection | 内置自动重连，无需手动处理网络抖动 |
| Exchange 类型 | topic + direct + fanout | topic 用于事件分类，direct 用于精确分发，fanout 用于广播通知 |
| 消息持久化 | durable=True | RabbitMQ 重启后队列和消息不丢失 |
| 死信队列 | 所有业务队列配置 DLX | 失败消息可追溯、可重放，避免静默丢失 |
| prefetch_count | 10 | 防止消费者一次性拉取过多消息导致内存溢出 |
| 消息确认 | 手动 ACK | 确保消息处理成功后再确认，失败可重入队列 |
| **Publisher 可靠性** | **全量 Publisher Confirms（Q9）** | aio-pika `RobustChannel` 默认 `publisher_confirms=True`；`await exchange.publish()` 已等待 Broker `Basic.Ack`，Nack 抛 `DeliveryError`、Confirm 超时走 `asyncio.wait_for(timeout=10s)`。**不使用 AMQP 事务**：吞吐差 10×，且批量中间失败无法回滚 |
| **Consumer 端幂等** | **业务表 unique 约束 + DuplicateMessageError 静默 ACK（Q9）** | 业务方传 `business_id`（推荐 f"{task_type}:{business_key}"）；Task 表加 `(user_id, business_id)` 联合 unique 索引（migration `a1b2c3d4e5f6`）；重复 INSERT 触发 `IntegrityError` → `insert_idempotent` 转换为 `DuplicateMessageError` → 消费者基类静默 ACK（不重试，不进死信）。**message_id 复用业务 ID**：Publisher 写入 AMQP `message_id` + `headers["x-business-id"]`，便于日志追踪。 |
| **批量发布** | **`BatchPublishResult` 结构化返回（Q9）** | `publish_batch` 不抛异常，返回 `(success: list[str], failed: list[tuple[str, Exception]])`，调用方按 `has_failure` / `failure_rate` 决策补偿 |
| **business_id 提取顺序** | **`business_id > task_id > notification_id > id > message_id`** | `business_id` 是业务方按规则传入的稳定 ID（`f"{task_type}:{business_key}"`），专用幂等键；`id` 在 Task 表是 UUID PK 每次新生成不参与业务去重。提取顺序在 `publisher._BATCH_ID_KEYS` 和 `idempotent._ID_KEYS` **严格一致**。 |
| **`DuplicateMessageError` 异常层级** | **继承 `Exception` 而非 `AppException`** | `AppException` 专用于 API 错误响应（4xx/5xx）；`DuplicateMessageError` 是 MQ 消费者内部控制流信号，不应混入 HTTP 错误体系。消费者基类单独识别此异常 → 静默 ACK（不重试）。 |
| **Task 表 `user_id` 冗余** | **不 join sessions，user_id 直接冗余在 tasks 表** | `(user_id, business_id)` 联合 unique 索引必须 user_id 直接在 tasks 表上，PG 索引不支持跨表 join。Service 层在创建 Task 时从 session_id 同步 user_id（数据一致性由应用层保证）。 |
| **Task 表幂等键 vs 业务层 idempotent** | **Task 表 unique 约束 + `insert_idempotent` helper 共同保证** | DB unique 是原子硬约束（最终防线），`insert_idempotent` 是类型化包装（语义清晰、把 `IntegrityError` 转 `DuplicateMessageError`）。两者缺一不可：少了 DB 约束则有 TOCTOU 竞态；少了 helper 则调用方要重复写 try/except。 |

### 依赖

- **aio-pika >= 9.5.0**：Python 异步 RabbitMQ 客户端
- **RabbitMQ Server >= 3.12**：消息代理服务

---

## Resume 缓存架构设计（Step 1.5.10）

### 定位

Resume 缓存是简历域的**读性能优化层**，承担以下职责：

| 职责 | 说明 | 不做什么 |
|------|------|---------|
| 加速 `get_active_resume` | 高频热路径，匹配岗位/生成话术都先查 active | 不缓存 list_by_user（命中率低、失效复杂） |
| 减少 DB 压力 | structured_data 单条可能 50KB，频繁拉取浪费连接 | 不缓存 get_by_id（越权风险、命中率一般） |
| 写后自愈 | 写操作 invalidate + 30min TTL 兜底 | 不做 Write-Through 同步双写 |
| Redis 不可用时降级 | fail-open，绝不让缓存抖动变成 5xx | 不抛业务异常、不阻塞请求 |

> 与 RabbitMQ 的关系：RabbitMQ 负责**任务异步分发**（Agent 任务、通知），Redis 负责**热数据缓存**。两者职责互不重叠。

### 数据流

```
                       ┌─────────────────────┐
                       │    ResumeService    │
                       │  (Domain 层编排)    │
                       └──────────┬──────────┘
                                  │
                ┌─────────────────┼─────────────────┐
                │ get 路径         │ write 路径        │
                │                 │                  │
        ┌───────▼──────┐    ┌────▼──────────┐
        │  Redis 缓存  │    │  Repository   │
        │  (hit/miss)  │    │  (PostgreSQL) │
        └───────▲──────┘    └────▲──────────┘
                │                 │
                │ miss 时         │ commit
                │ 回填 setex      │ 后 invalidate
                └─────────────────┘
```

### 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 缓存范围 | **仅 active resume** | 命中率最高（每次匹配都查）；list 命中率低、失效复杂 |
| 缓存模式 | **Cache-Aside（写时删除）** | 简单、几乎无一致性窗口问题；Write-Through 失败难处理 |
| 缓存粒度 | **ResumeResponse（DTO）而非 ORM** | ORM 有 lazy-load 语义、绑定 session，跨 session 反序列化异常；Pydantic JSON 安全 |
| Key 设计 | `resume:active:{user_id}` | 命名空间隔离（`resume:active:` 前缀），便于 `KEYS resume:*` 排查 |
| TTL | **1800s（30min）** | 失效逻辑漏掉时 30min 内自愈；可配（`resume_cache_ttl_seconds`） |
| 序列化 | `model_dump_json()` / `model_validate_json()` | Pydantic v2 标准方法；自带类型校验 |
| 失败策略 | **fail-open** | Redis 抖动不应让业务变 5xx；与 rate_limit.py 风格一致 |
| 失效点 | 4 个写入口（upload/set_active/fill_structured_data/delete） | 任何改变 active 的写操作都要失效 |

### 文件结构

```
backend/app/
├── domain/
│   ├── cache/                     # Domain 层缓存抽象
│   │   ├── resume.py              # ResumeCacheProtocol（已实现）
│   │   ├── job.py                 # JobAnalysisCacheProtocol（Step 1.6.12 待实现）
│   │   ├── match.py               # MatchCacheProtocol（Step 1.7.12 待实现）
│   │   ├── communication.py       # TemplateCacheProtocol（Step 1.8.11 待实现）
│   │   └── session.py             # SessionCacheProtocol（Step 1.10.7 待实现）
│   └── resume/
│       └── service.py                # Service 协调缓存与 Repository
└── infra/
    └── cache/
        ├── __init__.py
        └── resume.py                  # Redis 实现（Infra 层）
```

### 失效矩阵

| 写操作 | 是否需要 invalidate | 说明 |
|--------|--------------------|------|
| `upload_resume` | ✅ | 新简历自动激活，旧 active 失效 |
| `set_active_resume` | ✅ | active 切换，旧 active 失效 |
| `fill_structured_data` | ✅（统一失效） | 仅当更新的是 active 才必要；统一失效更简单 |
| `delete_resume` | ✅（统一失效） | 仅当删除的是 active 才必要；统一失效更简单 |
| `get_resume` / `list_resumes` | — | 读路径，不写 |

### 一致性边界（必须主动承认）

| 场景 | 影响 | 防御 |
|------|------|------|
| 写后失效与读穿透竞态 | 写完 DEL，读端正好回查 DB 拿到旧值并 setex | 30min TTL 兜底；业务上简历变更不频繁 |
| Redis 不可用 | 缓存层静默降级到 DB | logger.warning + 业务不受影响 |
| 反序列化失败 | 旧 key 格式不匹配 | 当作 miss，下次写入覆盖 |
| schema 升级 | 旧 key 解析失败 | 同上 |

> 何时升级到 Pub/Sub 广播失效：当真实业务出现"用户投诉看到陈旧数据"且监控命中率异常时再考虑。当前不值得引入。

### 性能预期

| 场景 | 无缓存 | 有缓存 |
|------|-------|-------|
| active resume 单次查询 | 10-30ms（含 JSONB 反序列化） | 1-3ms（Redis GET + JSON 反序列化） |
| 1000 QPS 时 DB QPS | 1000 | ≈10（命中率 99% 时） |
| 内存占用 | 0 | 50KB × 在线用户数 |

### 监控指标（后续可加）

| 指标 | 采集方式 | 告警阈值 |
|------|---------|---------|
| 缓存命中率 | `cache_hit / (cache_hit + cache_miss)` | <80% 告警 |
| Redis 调用失败率 | `redis_error / redis_call` | >1% 告警 |
| 平均延迟 | `get_active_resume` p50/p99 | p99 > 50ms 告警 |

### 依赖

- **redis >= 8.0.0**：Python 异步 Redis 客户端（已在 `pyproject.toml` 声明）
- **Redis Server >= 7.0**：缓存服务（与 Step 1.1.4 共享）
- 不新增第三方依赖

### 测试覆盖

`backend/tests/test_resume_cache.py`（12 个用例）：

| 类别 | 用例 |
|------|------|
| Protocol 校验 | FakeResumeCache 实现 ResumeCacheProtocol |
| Key 格式 | `resume:active:{uuid}` |
| Fake cache 行为 | set/get/invalidate/幂等 |
| Redis 异常容错 | get/set/invalidate 三种异常都不抛 |
| 反序列化容错 | 非法 JSON 视为 miss |
| 正确调用 Redis | SETEX 携带 TTL、DEL 用正确 key |
