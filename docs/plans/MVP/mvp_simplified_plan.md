# MVP 精简计划 v1.2

> 目标：在 **4–6 周**内跑通「注册/登录 → 上传简历 → Boss 直聘提取岗位 → 分析 JD → 匹配简历 → 生成沟通话术 → 记录投递」的最小闭环。
> 原则：**已完成的基建和 Job/Match 代码保留，不再推倒重来；推迟一切「防御未来规模」的工程治理。**

---

## 1. 为什么要精简？

原 [development_plan.md](file:///g:/my/my_file/AI%20Career%20Copilot/docs/plans/development_plan.md) 的 Phase 1 虽然覆盖了 MVP 闭环，但混入了不少 Phase 2/3 才需要的能力：

- 3 套独立 Agent Consumer（Job / Match / Communication）
- WebSocket 实时推送 + Notification Consumer Bridge
- 5 套缓存协议（Resume / Job / Match / Communication / Session）
- BM25 + BGE 语义模型混合匹配
- Chrome Extension 多平台适配器（猎聘/智联/实习僧）
- Step 1.16 中的 ORM 泄露 Lint、DTO 转换测试、跨层隔离测试等工程治理

这些对**验证产品价值**都不是刚需，却会显著拉长首次闭环时间。

---

## 2. 当前已保留资产（不要重写）

| 资产 | 状态 | 说明 |
|------|------|------|
| 基础设施（Settings / Logger / DB / Redis / RabbitMQ） | 已完成 | Q1–Q9，含 Publisher Confirms、Consumer Registry、幂等消费 |
| ORM + Alembic | 已完成 | 7 张表 + Task 业务唯一键 |
| 中间件 | 已完成 | CORS / 异常 / 日志 / Request ID / 限流 / Auth |
| [Job Service + Job Analysis Agent](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/job/service.py) | 已完成 | 已异步化，可直接复用 |
| [Resume Service](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/resume/service.py) | 已完成 | 上传/解析/active resume 缓存已就绪 |
| [Match Scorer](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/match/scorer.py) | 已完成 | BM25 + 语义模型，但 MVP 默认只用 BM25 |
| [Match Service](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/match/service.py) | 空占位 | 需要实现 |
| [Communication Service](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/communication/service.py) | 空占位 | 需要实现 |
| [Extension 多平台适配器](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/) | 已存在 | MVP 只启用 Boss，其余禁用或移出 |

---

## 3. MVP 范围：Must Have vs Postpone

### Must Have（必须进入 MVP）

1. **用户认证**：注册 / 登录 / 刷新 Token
2. **简历模块**：上传 PDF/DOCX → 解析 → 查询 active resume
3. **岗位提取**：Boss 直聘页面提取 JD → 创建 Job
4. **JD 分析**：复用现有 Job Analysis Agent，异步返回结果
5. **简历匹配**：复用现有 Scorer，**默认仅 BM25**，语义模型作为可选开关
6. **沟通话术**：一个最小 LLM 调用，返回打招呼/回复模板
7. **投递记录**：最小 CRUD（创建 + 列表 + 状态更新）
8. **任务状态查询**：`GET /tasks/{task_id}` 轮询
9. **部署**：`docker-compose up` 一键启动后端 + 依赖

### Postpone to Phase 2（明确推迟）

| 功能 | 推迟理由 |
|------|---------|
| WebSocket 实时推送 | 轮询足够，MVP 不需要实时 |
| Notification Consumer + WS Bridge | 同上 |
| Job Analysis / Match / Communication / Session 缓存 | 默认路径走 DB，性能问题出现后再加 |
| 语义模型作为默认匹配方式 | 保留代码，但默认权重为 0，避免模型加载拖慢首次请求 |
| 多平台 Extension 适配 | 只验证 Boss 直聘即可 |
| Compliance Checker / PII Filter / Template Manager | 内嵌到 Prompt 里，不作为独立模块 |
| Step 1.16 中的 DTO 化 Lint / ORM 泄露规则 / 跨层隔离测试 | 工程治理，等团队规模扩大后再做 |
| Strategy Agent / 面试管理 / 工作流编排 | 明显是 Phase 2 |

---

## 4. 精简后的 Step 序列

### S1 — 补齐认证 + 简历闭环（第 1–1.5 周）

- 完成 [User Service / Repository / Router](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/user/service.py)
- 完成 [Resume Router](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/resume.py)
- 编写注册/登录/上传简历的单元 + 集成测试
- 验收：`POST /register` → `POST /login` → `POST /resumes/upload` → `GET /resumes/active` 能跑通

### S2 — 闭合 Job 分析端到端（第 2 周）

- 复用现有 Job Service + Consumer
- 保证 `POST /jobs/analyze` 返回 `202 + task_id`
- `GET /tasks/{task_id}` 能轮询到 `completed` 和 `analysis_result`
- 验收：从任意方式（curl / Extension /测试）提交 JD，能得到分析结果

### S3 — 闭合 Match 端到端（第 3 周）

- 实现 [Match Service](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/match/service.py) + Router + Consumer
- 默认使用 BM25；语义模型通过 `SEMANTIC_SCORER_ENABLED=true` 可选开启
- 返回：匹配分数、命中技能、缺失技能、优化建议
- 验收：`POST /match/calculate` → 轮询 task → 拿到匹配结果

### S4 — 闭合 Communication + Application（第 4 周）

- 实现 [Communication Service](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/communication/service.py) + Router + Consumer
- 内部合并为一个简单 LLM 调用：输入「岗位分析 + 匹配结果 + 用户简历摘要」，输出话术
- 实现 [Application Service / Router](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/workflow/service.py) 最小 CRUD
- 验收：拿到匹配结果后能生成话术，并能创建一条投递记录

### S5 — 精简 Extension 闭环（第 5 周）

- 只保留 [Boss 适配器](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/)
- 禁用/移除猎聘/智联/实习僧代码（可移入 `extension-v2/` 目录）
- SidePanel 只展示三块：岗位分析、匹配分数、沟通话术 + 一键复制
- Extension 通过轮询 `GET /tasks/{task_id}` 获取异步结果
- 验收：在 Boss 直聘岗位详情页，点击插件后能完整走完「提取 JD → 分析 → 匹配 → 话术」

### S6 — 集成测试 + 部署收尾（第 5.5–6 周）

- 编写端到端集成测试：注册 → 上传简历 → Extension 提取 JD → 分析 → 匹配 → 生成话术 → 记录投递
- `docker-compose.yml` 一键启动 PostgreSQL + Redis + RabbitMQ + Backend
- FastAPI Swagger 补充描述
- 生成 `docs/mvp/` 下的最简架构说明

---

## 5. 与原 Plan 的关键差异对照

| 原 Plan（development_plan.md） | 精简后 MVP |
|--------------------------------|-----------|
| 3 个独立 Agent 状态机 + Consumer | 对外 3 个 Router，内部先走一个简化流程；状态机保留扩展位 |
| WebSocket + Notification Consumer | 删掉，改用轮询 |
| 5 套缓存协议 | 只保留 active resume 缓存 |
| BM25 + BGE 语义模型默认混合 | 默认 BM25；语义模型可选关闭 |
| Extension 多平台适配 | 只保留 Boss 直聘 |
| Compliance / PII / Template 独立模块 | 内嵌到 Communication Prompt |
| Step 1.16 的 Lint / DTO 化 / ORM 隔离测试 | 全部推迟 |
| Application 状态机 | 最小 CRUD，无复杂状态流转 |

---

## 6. 验收标准（MVP 完成的 Definition of Done）

1. 新用户能在 1 分钟内完成注册 + 登录。
2. 用户能上传 PDF 或 DOCX 简历，并查询到 active resume。
3. 在 Boss 直聘岗位详情页，Extension 能自动提取岗位信息并发送到后端。
4. 后端异步完成 JD 分析、简历匹配、沟通话术生成，Extension 通过轮询拿到结果。
5. 用户能在 Extension 中一键复制沟通话术，并记录一次投递。
6. `docker-compose up` 能完整拉起后端 + 依赖服务。
7. 核心路径有单元测试覆盖，至少一条端到端集成测试通过。

---

## 7. 风险与 trade-off

| 风险 | 影响 | 应对 |
|------|------|------|
| Agent 内部合并后未来拆分成本 | 中 | 保留 `AgentService` Facade 和状态机扩展位，拆分时不改对外接口 |
| 轮询体验不如 WebSocket | 低 | MVP 用户量小，LLM 任务 5–30s，轮询完全可接受 |
| 默认关闭语义模型导致匹配质量下降 | 中 | 中文 JD 关键词密度高，BM25 已能覆盖 70% 场景；必要时临时开启 |
| 多平台代码已存在但被禁用 | 低 | 移到 `extension-v2/` 目录，不编译进 MVP 包 |
| 推迟工程治理导致后续技术债 | 中 | 在 Phase 2 开始的第一件事就是补 DTO 化 + Lint |

---

## 8. 下一步建议

1. 确认本精简计划后，按 S1 → S6 顺序执行。
2. 每个 Step 完成后写一个端到端集成测试，确保闭环没有回退。
3. 一旦 MVP 闭环跑通，立刻冻结 Phase 1，进入 Phase 2 增强（WebSocket、多平台、语义模型默认开启、工程治理）。
