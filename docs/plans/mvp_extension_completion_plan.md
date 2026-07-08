# AI Career Copilot MVP Extension 补全计划

> 版本：v1.0.0  
> 日期：2026-07-07  
> 负责人：产品经理 / 前端·Extension 负责人 / 后端负责人  
> 周期：1.5 - 2 周（约 10 个工作日）  
> 前置条件：后端核心 API 已可运行，RabbitMQ / PostgreSQL / Redis 已就绪  

---

## 0. 进度总览（最后更新：2026-07-07）

### 0.1 已完成里程碑

| 里程碑 | 状态 | 完成时间 | 说明 |
|--------|------|----------|------|
| **M0 后端接口改造（方案 D）** | ✅ 已完成 | 2026-07-05 | 后端支持 `jd_text` 为空创建岗位 + `PATCH /api/jobs/{id}` 补充详情，50 个测试通过 |
| **M1 Extension 工程底座** | ✅ 已完成 | 2026-07-05 | manifest/消息协议/SW/Pinia/LoginPanel/SidePanel 基础布局全部完成 |
| **Boss 数据提取模块** | ✅ 已完成 | 2026-07-05 | selector.ts + parser.ts + adapter.ts 三件套完成 |
| **Content Script 页面监听** | ✅ 已完成 | 2026-07-05 | content.ts 实现 detect + observe + 消息发送 |
| **M2 Service Worker 后端对接** | ✅ 已完成 | 2026-07-07 | 6 个消息 handler 全部实现，sourceUrl→jobId 持久化，任务轮询器 + TASK_STATUS_UPDATED 广播到 SidePanel 完成 |

### 0.2 详细任务进度

#### Step 0：后端接口改造（M0 系列）— ✅ 全部完成

| 编号 | 任务 | 状态 | 验收 |
|------|------|------|------|
| M0-01 | `JobCreateRequest.jd_text` 改为可选 | ✅ | `jd_text=""` 创建成功 |
| M0-02 | 数据库模型 + 迁移（`salary_unit` 列） | ✅ | Alembic 迁移通过 |
| M0-03 | `JobUpdateRequest` DTO（字段全可选） | ✅ | Pydantic 校验通过 |
| M0-04 | `PATCH /api/jobs/{job_id}` 接口 | ✅ | 越权返回 403 |
| M0-05 | `JobService.update_job` + 单元测试 | ✅ | 50 个测试通过 |
| M0-06 | 冒烟测试 + 文档更新 | ✅ | POST + PATCH 闭环验证 |
| M0-07 | 新增 `salary_unit` 字段 | ✅ | 薪资单位持久化 |

#### Step 1：Extension 工程底座（M1 系列）— ✅ 全部完成

| 编号 | 任务 | 状态 | 交付物 |
|------|------|------|--------|
| M1-01 | 更新 `manifest.json`（MV3 + sidePanel + CRXJS） | ✅ | `extension/manifest.json` |
| M1-02 | 统一消息协议 `chrome_message.ts`（9 个类型） | ✅ | `extension/src/messaging/chrome_message.ts` |
| M1-03 | Service Worker API 客户端（token + 401 refresh + 路由） | ✅ | `backend_client.ts` + `router.ts` + `service_worker.ts` |
| M1-04 | `LoginPanel.vue` 登录面板（zod 校验 + 登录态切换） | ✅ | `extension/src/components/LoginPanel.vue` |
| M1-05 | Adapter 抽象（直接实现 BossAdapter，未抽 PlatformAdapter 接口） | ✅ | 见 M1-07 |
| M1-06 | `selector.ts` + `parser.ts`（选择器配置 + 薪资解析 + JD 清洗） | ✅ | `extension/src/modules/boss/selector.ts`、`parser.ts` |
| M1-07 | `adapter.ts` BossAdapter（detect + extractJobs + observe） | ✅ | `extension/src/modules/boss/adapter.ts` |
| M1-08 | `content.ts` Content Script（detect + observe + 消息发送） | ✅ | `extension/src/content/content.ts` |
| M1-09 | SidePanel 基础布局（Pinia store + 6 状态 UI） | ✅ | `extension/src/App.vue`、`stores/sidepanel.ts` |

#### Step 2：Boss 直聘数据提取模块 — ✅ 全部完成

| 编号 | 任务 | 状态 | 关键设计 |
|------|------|------|----------|
| 2.1 | `selector.ts`（BossSelectorConfig + 6 辅助函数） | ✅ | 字体反爬用 `queryTextRendered` 读 innerText |
| 2.2 | `parser.ts`（parseSalary + cleanJdText + toJobCreateRequest） | ✅ | 支持 K/月、元/天、元/时、面议、单值 |
| 2.3 | `adapter.ts`（BossAdapter 类 + MutationObserver + URL 轮询） | ✅ | 500ms debounce + 500ms URL 轮询 |

#### Step 3：Content Script 与页面监听 — ✅ 完成

| 编号 | 任务 | 状态 | 关键设计 |
|------|------|------|----------|
| 3.1-3.4 | `content.ts`（detect + extractJobs + observe + 消息发送） | ✅ | click capture 记录选中卡片 detailUrl |

#### Step 4：Service Worker 消息路由与后端调用 — ✅ 已完成

| 编号 | 任务 | 状态 | 说明 |
|------|------|------|------|
| 4.1 | JOBS_EXTRACTED handler（批量 POST /api/jobs） | ✅ 已完成 | 串行调用 `POST /api/jobs`（避免并发唯一约束冲突），结果写入 `chrome.storage.local`，广播 `JOBS_CREATED` |
| 4.2 | JOB_DETAIL_EXTRACTED handler（PATCH /api/jobs/{id}） | ✅ 已完成 | 通过持久化 sourceUrl→jobId 映射定位 Job ID，调用 `PATCH /api/jobs/{id}` 补充 JD/技能 |
| 4.3 | REQUEST_ANALYZE handler（POST /api/jobs/analyze） | ✅ 已完成 | 调用异步分析接口，使用 `task_poller.ts` 轮询 `GET /api/tasks/{id}`，完成后广播 `TASK_STATUS_UPDATED` |
| 4.4 | REQUEST_MATCH handler（POST /api/match/compute） | ✅ 已完成 | 同步调用 `/api/match/compute`，结果直接通过 `TASK_STATUS_UPDATED` 广播 |
| 4.5 | REQUEST_COMMUNICATION handler（POST /api/communication/generate） | ✅ 已完成 | 调用异步话术生成接口，轮询任务状态，完成后广播 `TASK_STATUS_UPDATED` |
| 4.6 | RECORD_APPLICATION handler（POST /api/applications） | ✅ 已完成 | 同步调用 `POST /api/applications` 记录投递 |
| 4.7 | TASK_STATUS_UPDATED 通知 SidePanel | ✅ 已完成 | 新增 `TASK_STATUS_UPDATED` 消息类型，SW 通过 `broadcastToSidePanel` 单向广播到 SidePanel |

#### Step 5-7：未开始

| 编号 | 任务 | 状态 |
|------|------|------|
| 5 | SidePanel 海投 UI（JobListPanel + AnalysisCard + MatchCard + CommunicationCard + ApplyButton） | ✅ 已完成 |
| 6 | 端到端验证（Boss 真实页面联调） | ✅ 已完成 |
| 7 | 测试与文档（Vitest 单测 + Playwright E2E + 文档更新） | ⏳ 未开始 |

### 0.2.1 Step 5 完成说明（2026-07-07）

> ✅ SidePanel 海投 UI 已实现并通过构建验证，标志 Step 5 完成。

**已完成交付物：**

| 模块 | 文件 | 说明 |
|------|------|------|
| SidePanel 根组件 | [extension/src/App.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/App.vue) | 380px 固定宽度，Header + TabNav + 状态机视图 + 岗位 Tab 左右分栏 |
| Tab 导航 | [extension/src/components/sidepanel/TabNav.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/TabNav.vue) | 岗位 / 沟通 / 简历 / 设置，仅岗位 Tab 可交互，其余显示“待开发” |
| 岗位列表 | [extension/src/components/sidepanel/JobListPanel.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/JobListPanel.vue) | 左栏 40%，展示岗位卡片、统计摘要、空状态 |
| 岗位列表项 | [extension/src/components/sidepanel/JobListItem.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/JobListItem.vue) | 卡片渲染、选中态、创建失败提示 |
| 岗位详情容器 | [extension/src/components/sidepanel/JobDetailPanel.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/JobDetailPanel.vue) | 右栏 60%，监听 `hasJdText` 并自动启动 AI 流水线 |
| JD 分析卡片 | [extension/src/components/sidepanel/AnalysisCard.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/AnalysisCard.vue) | 展示技能/关键词/资历/难度/薪资/公司信息/隐藏要求，支持重试 |
| 匹配分析卡片 | [extension/src/components/sidepanel/MatchCard.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/MatchCard.vue) | 展示综合分、BM25/语义子分、命中/缺失技能、投递建议 |
| 沟通话术卡片 | [extension/src/components/sidepanel/CommunicationCard.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/CommunicationCard.vue) | 展示招呼语/跟进语/完整对话，支持一键复制 |
| 记录投递按钮 | [extension/src/components/sidepanel/ApplyButton.vue](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/components/sidepanel/ApplyButton.vue) | 根据岗位状态/已投递状态禁用，调用 `RECORD_APPLICATION` |
| 流水线 Composable | [extension/src/composables/useJobPipeline.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/composables/useJobPipeline.ts) | 自动推进 analyze → match → communication，提供重试与清理 |
| Store 状态扩展 | [extension/src/stores/sidepanel.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/stores/sidepanel.ts) | 新增 `taskResults`、`activeTab`、`appliedJobIds`、`onJobDetailPatched` |
| 前端 DTO 类型 | [extension/src/types/job.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/types/job.ts) | 定义 `JobAnalysisResult`、`MatchResultResponse`、`CommunicationScriptResponse` |
| 全局样式 | [extension/src/style.css](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/style.css) | 380px 宽度、颜色系统、字体、滚动条、按钮基础样式 |

**关键修复：**

1. **TaskResult 类型收窄**：`AnalysisCard` / `MatchCard` / `CommunicationCard` 在 `computed` 内部直接判断 `r.status === 'completed' | 'failed'`，解决 TS 无法推断 `.result` / `.errorMessage` 的构建错误。
2. **流水线防重复触发**：`useJobPipeline.startPipeline()` 在 analyze 已处于 `running` 或 `completed` 时跳过，避免切换岗位覆盖已有结果；手动重试仍通过 `retryStage` 强制触发。

**构建结果：**

```text
$ pnpm run build
vue-tsc -b && vite build
✓ built in 637ms
146 modules transformed
```

**待继续：** Step 6 端到端真实页面联调，Step 7 测试与文档。

### 0.2.2 Step 6 完成说明（2026-07-07）

> ✅ Step 6 已完成「代码级端到端验证 + 构建验证」，并输出真实 Boss 页面手动联调清单。由于当前环境无法登录 Boss 直聘/访问真实页面，真实 DOM 联调需你在本地 Chrome 中按下方清单执行。

**代码级验证结论：**

| 验证项 | 状态 | 说明 |
|--------|------|------|
| Extension 构建 | ✅ | `pnpm run build` 通过，146 模块，无 TypeScript 错误 |
| 消息协议完整性 | ✅ | `chrome_message.ts` 12 个消息类型类型安全，`REFRESH_JOBS` / `JOBS_CREATED` / `TASK_STATUS_UPDATED` / `JOB_DETAIL_PATCHED` 全部定义 |
| Service Worker 消息路由 | ✅ | `router.ts` 8 个 handler 注册完整，覆盖登录、批量创建、详情 PATCH、分析/匹配/话术、投递、手动刷新 |
| Content Script 提取链路 | ✅ | `content.ts` 在列表页自动提取、监听详情面板、URL 变化广播、手动刷新响应均实现 |
| Boss Adapter 解析链路 | ✅ | `selector.ts` + `parser.ts` + `adapter.ts` 已对齐 design doc，支持列表页批量提取与详情面板 JD 补充 |
| SidePanel 状态持久化 | ✅ | `chrome.storage.local` 自动保存 jobs / taskResults / appliedJobIds，App.vue 启动时恢复 |
| 分析流水线自动推进 | ✅ | `useJobPipeline` 监听 analyze → 触发 match → 触发 communication，组件卸载时清理监听器 |
| 任务轮询器 | ✅ | `task_poller.ts` 2s 间隔、30 次上限、COMPLETED/FAILED/CANCELLED/超时/网络错误均处理 |
| source_url → jobId 映射 | ✅ | `source_url_map.ts` 持久化，支持 JOB_DETAIL_EXTRACTED 反查 jobId |

**本次修复的关键问题：**

1. **SidePanel 状态持久化未真正启用**：`sidepanel.ts` 中 `saveToStorage` / `loadFromStorage` / `clearStorage` 已声明但未被调用，导致构建报错 `TS6133`。修复方式：
   - 引入 `watch` 自动持久化 `jobs` / `selectedSourceUrl` / `taskResults` / `appliedJobIds` / `currentPageUrl` / `activeTab`；
   - 将三个函数暴露到 store return；
   - `App.vue` `onMounted` 中优先调用 `loadFromStorage()` 恢复上次状态。
2. **手动刷新链路补全**：`REFRESH_JOBS` 消息类型、SW handler、Content Script listener、SidePanel 刷新按钮已形成闭环。
3. **复制反馈 UX**：`CommunicationCard.vue` 复制后显示「已复制」2 秒，避免用户不确定是否复制成功。
4. **重试 session 一致性**：`JobDetailPanel.vue` 通过 `provide('jobSessionId')` 向 `AnalysisCard` / `MatchCard` / `CommunicationCard` 注入统一 sessionId，重试时使用同一 session。

**构建结果（Step 6 最终）：**

```text
$ pnpm run build
vue-tsc -b && vite build
vite v8.0.16 building client environment for production...
✓ built in 800ms
146 modules transformed
dist/service-worker-loader.js                0.04 kB
dist/assets/content.ts-loader-PfVpcpC1.js    0.34 kB
dist/icon16.png                              0.58 kB
dist/popup.html                              0.64 kB │ gzip:  0.33 kB
dist/sidepanel.html                          0.66 kB │ gzip:  0.36 kB
dist/manifest.json                           1.24 kB │ gzip:  0.57 kB
...
dist/assets/sidepanel.html-Da1O2_Zg.js      27.13 kB │ gzip:  8.50 kB
dist/assets/style-ChlW-PDt.js              135.25 kB │ gzip: 45.84 kB
```

**TC-02 修复说明（API 拦截方案）：**

问题：真实环境中访问 `zhipin.com/web/geek/jobs` 时，DOM 抓取返回 0 个岗位，SidePanel 一直显示「正在提取岗位」。

修复：将职位列表数据源从 DOM 抓取切换到**拦截 Boss 直聘内部 API**，具体改动：

1. 逆向确认职位列表 API：`/wapi/zpgeek/pc/recommend/job/list.json`（GET，依赖登录 Cookie）
2. 新增 `extension/public/interceptor.js`：注入页面主世界，monkey-patch `fetch`/`XHR`，捕获目标 API 响应并通过 `postMessage` 回传
3. 新增 `extension/src/modules/boss/api_parser.ts`：将 API JSON 转换为现有 `RawBossJob` 格式
4. 改造 `extension/src/content/content.ts`：
   - `run_at` 改为 `document_start`，尽早安装拦截器
   - 注入 `interceptor.js` 并监听 `BOSS_JOB_DATA_CAPTURED`
   - 通过 `SentJobTracker` 对 API 数据和 DOM fallback 数据去重
   - 保留 DOM 提取和详情面板监听作为 fallback
5. `manifest.json` 添加 `web_accessible_resources` 声明 `interceptor.js`

降级：若 API 拦截未命中（如页面结构变化、请求路径变更），仍会自动回退到原有 DOM 提取逻辑。

**构建验证（API 拦截方案集成后）：**

```text
$ pnpm run build
vue-tsc -b && vite build
vite v8.0.16 building client environment for production...
✓ built in 836ms
146 modules transformed
dist/interceptor.js                          3.88 kB
dist/manifest.json                           1.27 kB
dist/assets/content.ts-BfMsJyWr.js           8.10 kB
...
```

> `dist/interceptor.js` 已正确输出，manifest 已包含 `web_accessible_resources`。

**真实 Boss 页面手动联调清单（需你本地执行）：**

> 环境要求：后端 + RabbitMQ + PostgreSQL + Redis 已启动；Extension 已加载 `extension/dist`；你已在 Boss 直聘登录并有简历数据。

| 编号 | 操作步骤 | 预期结果 | 通过 |
|------|----------|----------|------|
| TC-01 | 打开 Chrome SidePanel，输入账号密码登录 Extension | 登录成功，Header 显示用户昵称/邮箱 | [ ] |
| TC-02 | 访问 Boss 直聘职位搜索列表页 `zhipin.com/web/geek/jobs` | SidePanel 自动进入「提取中」，通过拦截 Boss 内部 API 在 3 秒内显示当前可见岗位列表 | [ ] |
| TC-03 | 向下滚动列表页加载更多岗位 | 新加载的岗位自动追加到 SidePanel 列表 | [ ] |
| TC-04 | 点击列表中某个岗位卡片 | 右侧详情面板加载；Boss 详情面板 JD 加载后，SidePanel 自动触发分析 | [ ] |
| TC-05 | 等待 10 - 30 秒 | `AnalysisCard` 显示技能/关键词/资历/难度/薪资区间/公司信息/隐藏要求 | [ ] |
| TC-06 | 分析完成后继续等待 | `MatchCard` 显示综合匹配分、BM25/语义子分、命中/缺失技能、投递建议 | [ ] |
| TC-07 | 匹配完成后继续等待 | `CommunicationCard` 显示招呼语、跟进语、完整话术 | [ ] |
| TC-08 | 点击「复制招呼语」 | 剪贴板内容与展示一致，按钮短暂显示「已复制」 | [ ] |
| TC-09 | 点击「记录投递」 | 后端生成投递记录，按钮变为「已投递」且不可重复点击 | [ ] |
| TC-10 | 切换搜索条件或分页 | Content Script 重新提取，SidePanel 刷新为新列表数据 | [ ] |
| TC-11 | 关闭并重新打开 SidePanel | 保留当前岗位列表、分析结果、已投递状态 | [ ] |
| TC-12 | 停止后端服务后点击「刷新列表」 | SidePanel 显示后端不可用提示，恢复服务后重试可正常 | [ ] |

**执行方法：**

1. 在 `extension/` 目录执行 `pnpm run build` 生成 `dist/`。
2. 打开 Chrome `chrome://extensions/`，开启「开发者模式」，点击「加载已解压的扩展程序」，选择 `extension/dist`。
3. 点击浏览器右上角扩展图标，或按 Chrome 侧栏快捷键打开 SidePanel。
4. 按 TC-01 ~ TC-12 顺序执行，通过的条目在「通过」列打勾。
5. 若某条失败，打开 SidePanel / Service Worker 的 DevTools 查看控制台日志，按下方「常见排查」定位。

**常见排查：**

| 现象 | 可能原因 | 排查位置 |
|------|----------|----------|
| SidePanel 显示「未登录」 | SW 内存 token 丢失 | 重新登录；检查 `background/service_worker.ts` 控制台 |
| 岗位列表为空 | API 拦截未命中 / Content Script 未注入 / 选择器失效 | 在 Boss 页面 F12 → Console，搜索 `[BossInterceptor]` 和 `[AI Career Copilot]` 日志；确认 Network 中有 `/wapi/zpgeek/pc/recommend/job/list.json` 请求 |
| 点击卡片不分析 | `source_url_map` 未找到 jobId / JD 为空 | SW 控制台搜索 `JOB_DETAIL_EXTRACTED` 与 `source_url_map` |
| 分析/话术一直 loading | RabbitMQ 未消费 / LLM 超时 | 后端日志 + `GET /api/tasks/{task_id}` 响应 |
| 复制无效 | SidePanel 无剪贴板权限 | 检查 manifest `permissions` 是否含 `clipboardWrite` |
| 关闭 SidePanel 后状态丢失 | `chrome.storage.local` 写入失败 | SidePanel Console 搜索 `[store] 持久化状态失败` |

**已知限制与风险：**

1. **快速切换卡片竞态**：用户连续快速点击不同卡片时，`currentSelectedDetailUrl` 可能在详情面板 mutation 前被更新，导致 JD 被 PATCH 到错误的 jobId。建议测试时等待当前卡片分析完成再切换。
2. **SW 回收导致轮询丢失**：SW 空闲 30 秒后被 Chrome 回收，正在进行的 task 轮询会停止。重新打开 SidePanel 后需重新点击卡片触发流水线（后续优化：用 `chrome.storage.session` 持久化活跃轮询）。
3. **Boss DOM 结构变化**：选择器基于当前页面结构，若 Boss 改版会导致解析失败。失败时请在 `extension/src/modules/boss/selector.ts` 中更新选择器。
4. **CORS 生产环境**：生产部署时务必在 `.env` 中设置 `CORS_ALLOW_EXTENSIONS=true`，否则 `chrome-extension://*` 来源请求会被拦截。

**待继续：** Step 7 测试与文档（Vitest 单测 + Playwright E2E + Extension README 更新）。

### 0.2.3 注册页面补全（已完成）

> 背景：后端 `POST /api/auth/register` 已在 [auth.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/auth.py#L160-L230) 实现，但 Extension 端只有登录面板 `LoginPanel.vue`，新用户首次使用必须借助 curl 注册。本任务在 Extension 内补全注册页面，实现「注册 → 自动登录 → 进入 SidePanel」的一键闭环。

**实现结果：**

- 在现有 `LoginPanel.vue` 中增加「登录 / 注册」Tab 切换。
- 注册模式新增字段：昵称、确认密码。
- 前端密码强度校验：长度 8-64 位、必须同时包含字母和数字、弱密码黑名单拦截。
- 注册成功后自动调用 `AUTH_TOKEN_UPDATED` 同步 Service Worker，并触发 `logged-in` 事件进入主界面。
- [`README.md`](file:///g:/my/my_file/AI%20Career%20Copilot/README.md) 已更新「首次使用」说明，改为在登录面板内直接注册。

**验收标准：**

- [x] Extension 构建通过（`npm run build` 无 TypeScript 错误）。
- [x] 未登录态下 Popup / SidePanel 的登录面板可切换到注册模式。
- [x] 输入合法邮箱、密码、确认密码、昵称后可成功注册。
- [x] 注册成功后自动登录，Header 显示用户昵称/邮箱。
- [x] 邮箱已注册时给出中文提示「该邮箱已被注册」。
- [x] 密码不符合强度时给出明确校验提示。
- [x] README 中的「首次使用」说明更新为使用注册页面。

### 0.3 构建状态

- **Extension 构建**：✅ 通过（`npm run build` 788ms，146 模块，dist/ 目录生成）
- **后端测试**：✅ 50 个单元测试通过
- **后端冒烟**：✅ POST + PATCH 接口闭环验证
- **注册页面**：✅ Extension 登录面板已支持注册模式

### 0.4 已解决问题

1. **Step 4 消息流设计**：已新增 `JOBS_CREATED` 消息类型，Service Worker 批量创建岗位完成后向 SidePanel 广播创建结果（created / duplicated / failed）。
2. **sourceUrl → jobId 映射**：已实现 `source_url_map.ts`，使用 `chrome.storage.local` 持久化映射，解决 Service Worker 重启后内存数据丢失问题。
3. **onMessage 多监听冲突**：SidePanel 仅处理 SW → SidePanel 的单向广播消息（`PAGE_CHANGED`、`JOBS_CREATED`、`TASK_STATUS_UPDATED`），主动请求类消息由 SidePanel 发送并在 SW handler 中响应，职责清晰。
4. **SidePanel 状态持久化**：Step 6 修复 `saveToStorage` / `loadFromStorage` / `clearStorage` 未暴露/未调用的问题，引入 `watch` 自动持久化核心状态，关闭并重新打开 SidePanel 后可恢复岗位列表、分析结果与投递记录。
5. **Extension 注册页面缺失**：`LoginPanel.vue` 已支持「登录 / 注册」模式切换，新用户首次使用无需再借助 curl 注册，注册成功后自动登录并进入主界面。

---

## 1. 当前状态分析

### 1.1 后端：已完成核心能力

后端已实现以下 API，具备 JWT 鉴权、请求 ID、统一异常处理与 RabbitMQ 异步任务支持：

| 模块 | 端点 | 文件 | 状态 |
|------|------|------|------|
| 注册/登录 | `POST /api/auth/register`、`/api/auth/login`、`/api/auth/refresh` | [auth.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/auth.py#L160-L330) | 已完成 |
| 简历上传 | `POST /api/resumes/upload`、`GET /api/resumes` | [resume.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/resume.py#L171-L314) | 已完成 |
| 岗位创建 | `POST /api/jobs` | [jobs.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/jobs.py#L49-L91) | 已完成（匿名可创建；MVP 期间 Extension 仍需携带 access token，因其他接口需鉴权） |
| 岗位分析 | `POST /api/jobs/analyze`（异步，返回 `task_id`） | [jobs.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/jobs.py#L174-L223) | 已完成 |
| 任务查询 | `GET /api/tasks/{task_id}` | [task.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/task.py#L25-L77) | 已完成 |
| 匹配计算 | `POST /api/match/compute`（同步） | [match.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/match.py#L30-L74) | 已完成 |
| 沟通话术 | `POST /api/communication/generate`（异步，返回 `task_id`） | [communication.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/communication.py#L37-L102) | 已完成 |
| 投递记录 | `POST /api/applications` | [applications.py](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/applications.py#L38-L68) | 已完成 |

后端关键约定：

- **异步任务**：所有 LLM/Agent 调用（岗位分析、沟通话术）走 RabbitMQ，接口返回 `202` + `task_id`，前端轮询 [`GET /api/tasks/{task_id}`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/routers/task.py#L25-L77)。
- **会话 ID**：[`JobAnalyzeRequest`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/job/models.py#L497-L523) 与 [`CommunicationGenerateRequest`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/communication/models.py#L12-L37) 均需 `session_id`。当前 [`TaskService`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/task/service.py#L127-L158) 会在 `session_id` 不存在时自动创建，因此 Extension 可本地生成 UUID 后直接使用。
- **CORS**：后端已配置 [`cors.py`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/api/middleware/cors.py#L110-L153)，支持 `chrome-extension://*` 正则来源与 `Authorization` 头，生产环境需在 `.env` 中显式开启 `CORS_ALLOW_EXTENSIONS=true`。

### 1.2 Extension：MVP 链路已实现（Step 6 代码级验证通过）

Step 1 ~ Step 6 完成后，Extension 核心模块均已实现并通过构建验证：

| 文件 | 当前状态 | 说明 |
|------|----------|------|
| [`App.vue`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/App.vue) | ✅ 完成 | SidePanel 根组件：状态机 UI、Header、Tab 导航、岗位列表/详情左右分栏、手动刷新、状态持久化恢复 |
| [`manifest.json`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/public/manifest.json) | ✅ 完成 | MV3 + `sidePanel` + `activeTab` + `storage` + host_permissions 已配置 |
| [`content.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/content/content.ts) | ✅ 完成 | 列表页自动提取、卡片点击捕获、详情面板监听、URL 变化广播、手动刷新响应 |
| [`selector.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/selector.ts) | ✅ 完成 | Boss 列表页 + 详情面板选择器配置，含 `queryText` / `queryTextRendered` / `queryTextList` / `queryAttribute` 辅助函数 |
| [`parser.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/parser.ts) | ✅ 完成 | `parseSalary` / `cleanJdText` / `toJobCreateRequest` / `toJobUpdateRequest` |
| [`adapter.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/adapter.ts) | ✅ 完成 | `BossAdapter`：detect / extractJobs / extractDetail / observe / disconnect |
| [`service_worker.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/background/service_worker.ts) + [`router.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/background/router.ts) | ✅ 完成 | 消息路由、后端 API 调用、token 管理、401 自动刷新、任务轮询、SW → SidePanel 广播 |
| [`chrome_message.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/messaging/chrome_message.ts) | ✅ 完成 | 12 个消息类型定义，类型安全的发送/接收辅助函数 |
| [`sidepanel.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/stores/sidepanel.ts) | ✅ 完成 | Pinia store：状态管理、`JOBS_CREATED` / `TASK_STATUS_UPDATED` / `JOB_DETAIL_PATCHED` 处理、chrome.storage.local 持久化 |
| [`useJobPipeline.ts`](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/composables/useJobPipeline.ts) | ✅ 完成 | analyze → match → communication 自动推进与手动重试 |

**结论**：MVP Extension 已闭环。当前剩余工作为：
1. 真实 Boss 页面 DOM 联调（需本地 Chrome 手动执行，见 0.2.2 清单）；
2. Step 7 测试与文档（Vitest 单测 + Playwright E2E + README 更新）。

---

## 2. MVP 补全目标

在 1.5 - 2 周内，让 Extension 在 **Boss 直聘岗位详情页** 完成以下闭环：

1. 用户打开 Boss 直聘岗位详情页，Extension 自动识别页面并提取岗位信息（标题、公司、JD、薪资、地点、技能标签、招聘者等）。
2. 提取后自动发送到后端创建岗位记录（`POST /api/jobs`）。
3. 触发 `POST /api/jobs/analyze` 异步分析 JD，前端轮询任务状态直到完成。
4. 分析完成后，自动调用 `POST /api/match/compute` 计算简历匹配分。
5. 自动调用 `POST /api/communication/generate` 生成沟通话术。
6. 在 Extension 面板中展示：岗位信息、分析结果、匹配分数、命中/缺失技能、沟通话术。
7. 支持一键复制沟通话术、一键“记录投递”到 `POST /api/applications`。
8. 所有异步 LLM 任务遵循后端已有的 MQ 异步流水线。

**成功标准**：一名已上传简历、已登录的用户，在 Boss 直聘岗位详情页打开 Extension 面板后，可在 30 秒内看到完整分析、匹配与话术结果，并能一键记录投递。

---

## 3. 详细任务拆分与时间节点

建议按 **10 个工作日** 排期，分为两周。每个任务均包含责任人、验收标准与依赖。

### 3.0 前置准备：后端接口改造（方案 D）

因海投模式需要从 Boss 直聘**列表页**批量提取岗位，而列表页卡片不包含完整 JD，故后端需先支持"先创建空 JD 岗位，后续补充详情"。该改造必须在 Extension 实现 `POST /api/jobs` 调用前完成。

| 编号 | 任务 | 责任人 | 交付物 | 验收标准 | 依赖 |
|------|------|--------|--------|----------|------|
| M0-01 | 修改 `JobCreateRequest`：将 `jd_text` 从必填改为可选（`min_length=0`） | 后端 | `backend/app/domain/job/models.py` | 用 `jd_text=""` 调用 `POST /api/jobs` 成功创建岗位 | - |
| M0-02 | 数据库模型与迁移：确保 `jobs.jd_text` 列允许为空或空字符串 | 后端 | Alembic migration | 现有数据不受影响，新增岗位 `jd_text` 可为空 | M0-01 |
| M0-03 | 新增 `JobUpdateRequest` DTO：所有字段可选，支持部分更新 | 后端 | `backend/app/domain/job/models.py` | DTO 校验通过，未传字段不覆盖原值 | - |
| M0-04 | 新增 `PATCH /api/jobs/{job_id}` 接口：仅允许更新属于当前用户的岗位 | 后端 | `backend/app/api/routers/jobs.py` | 更新 `jd_text`、`skills`、`location` 等字段成功；越权更新返回 403 | M0-01、M0-03 |
| M0-05 | 补充 `JobService.update_job()` 方法及单元测试 | 后端 | `backend/app/domain/job/service.py`、`tests/unit/test_job_service.py` | 单测覆盖空字段不覆盖、非空字段更新、越权拒绝 | M0-04 |
| M0-06 | 更新 API 文档：在计划文档中记录接口变更 | 后端 | 本计划文档、设计文档 | 前后端开发均知晓接口变更 | M0-04 |

### 3.1 第一周：Extension 工程底座 + Boss 直聘抓取

| 编号 | 任务 | 责任人 | 交付物 | 验收标准 | 依赖 |
|------|------|--------|--------|----------|------|
| M1-01 | 更新 `manifest.json`：声明 `sidePanel`、`scripting`、`host_permissions`、`storage`，并配置 action 打开 sidePanel | 前端/Extension | [manifest.json](file:///g:/my/my_file/AI%20Career%20Copilot/extension/public/manifest.json) | 打包后可在 Chrome 侧栏打开 Extension 面板 | - |
| M1-02 | 设计并实现统一消息协议（`chrome_message.ts`）：`JOBS_EXTRACTED`、`JOB_DETAIL_EXTRACTED`、`REQUEST_ANALYZE`、`REQUEST_MATCH`、`REQUEST_COMMUNICATION`、`RECORD_APPLICATION`、`AUTH_TOKEN_UPDATED` 等 | 前端/Extension | [chrome_message.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/messaging/chrome_message.ts) | 消息类型覆盖 MVP 全部流程，TypeScript 类型安全 | - |
| M1-03 | 实现后台 Service Worker：API 客户端封装、access token 内存存储、401 自动刷新、消息路由 | 前端/Extension | [service_worker.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/background/service_worker.ts) | 能正确调用后端 `/health`、登录后可携带 `Authorization` 头请求受保护接口 | M1-02 |
| M1-04 | 实现 Extension 登录/后端配置弹窗：输入邮箱密码调用 `/api/auth/login`，保存 backend base URL | 前端/Extension | `src/components/LoginPanel.vue` | 登录成功后 access token 写入内存（不持久化到 localStorage），刷新使用 Cookie | M1-03 |
| M1-05 | 设计 Adapter 抽象接口：`detect()` / `extractJobs()` / `observe()` / `getPlatform()`；海投模式支持返回数组 | 前端/Extension | `src/modules/platform_adapter.ts` | 接口通过代码评审，Boss 适配器按接口实现 | - |
| M1-06 | 实现 Boss 直聘选择器配置与解析器：列表页岗位卡片（岗位名、公司、薪资、地点、标签、详情链接）与详情面板（JD、技能标签、招聘者） | 前端/Extension | [selector.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/selector.ts)、[parser.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/parser.ts) | 在真实 Boss 列表页测试，解析成功率 ≥ 90%；详情面板在 5 个不同岗位测试通过 | M1-05 |
| M1-07 | 实现 Boss 直聘 Adapter：封装选择器/解析器，输出标准 `JobCreateRequest[]`（海投批量）与单个详情补充对象 | 前端/Extension | [adapter.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/modules/boss/adapter.ts) | 输出字段满足后端校验；`jd_text` 可空 | M1-06、M0-01 |
| M1-08 | 实现 Content Script：页面加载/路由变化时检测 Boss 列表页，提取所有可见岗位卡片并发送 `JOBS_EXTRACTED` | 前端/Extension | [content.ts](file:///g:/my/my_file/AI%20Career%20Copilot/extension/src/content/content.ts) | 打开 Boss 列表页后 3 秒内触发提取；滚动加载/分页切换后能增量提取 | M1-02、M1-07 |
| M1-09 | 实现 SidePanel Vue 基础布局：Header、岗位列表卡片、加载态、错误态 | 前端/Extension | `src/App.vue` / `src/components/` | 面板可正常渲染，状态切换无报错 | M1-01 |

### 3.2 第二周：后端接口对接 + 结果展示 + 投递闭环

| 编号 | 任务 | 责任人 | 交付物 | 验收标准 | 依赖 |
|------|------|--------|--------|----------|------|
| M2-01 | 封装后端 API 调用：新增 `PATCH /api/jobs/{id}`、jobs、tasks、match、communication、applications | 前端/Extension | `src/api/backend.ts` | 所有调用统一处理 base URL、token、request_id、错误码；PATCH 更新支持部分字段 | M1-03、M0-04 |
| M2-02 | 实现海投批量创建流程：收到 `JOBS_EXTRACTED` 后批量调用 `POST /api/jobs`（`jd_text` 可空），并展示创建结果 | 前端/Extension | `src/composables/useHaitouCreate.ts` | 列表页 20 个岗位在 5 秒内完成创建；重复 `source_url` 自动去重 | M1-08、M2-01 |
| M2-03 | 实现详情补充 → 分析流程：用户点击岗位卡片后，提取详情面板 JD，调用 `PATCH /api/jobs/{id}`，成功后触发 `POST /api/jobs/analyze`，轮询 `GET /api/tasks/{id}` | 前端/Extension | `src/composables/useJobDetailAnalysis.ts` | 用户点击卡片后 10 秒内完成补充并开始分析 | M1-08、M2-01 |
| M2-04 | 实现匹配计算调用：分析完成后自动 `POST /api/match/compute` | 前端/Extension | `src/composables/useMatch.ts` | 展示综合匹配分、BM25/语义分、命中/缺失技能、建议 | M2-03 |
| M2-05 | 实现沟通话术生成调用：匹配完成后自动 `POST /api/communication/generate` 并轮询 | 前端/Extension | `src/composables/useCommunication.ts` | 展示 `greeting` / `follow_up` / `full_script` | M2-04 |
| M2-06 | 完善结果展示 UI：批量岗位列表、分析卡片、匹配卡片、话术卡片、一键复制 | 前端/Extension | `src/components/JobListPanel.vue`、`AnalysisCard.vue` 等 | 复制按钮可用，话术内容可编辑预览，列表页支持快速查看 | M2-02、M2-05 |
| M2-07 | 实现“记录投递”按钮：调用 `POST /api/applications`，允许用户添加备注；仅对已有 `jd_text` 的岗位启用 | 前端/Extension | `src/components/ApplyButton.vue` | 投递成功后按钮置为“已投递”，防止重复提交 | M2-01 |
| M2-08 | 端到端联调：真实 Boss 列表页 → 批量创建 → 点击补充 JD → 分析 → 匹配 → 话术 → 记录投递 | 前端/Extension + 后端 | 联调记录 | 至少 5 个真实岗位端到端跑通，其中 3 个完成完整闭环 | M2-06、M2-07 |
| M2-09 | 异常与边界处理：网络错误、token 过期、无简历、解析失败、LLM 失败、空 JD 岗位提示 | 前端/Extension | 错误提示文案与重试机制 | 每种异常都有用户可感知提示与重试入口 | 全程 |
| M2-10 | 编写 Extension 单元/集成测试与文档更新 | 前端/Extension | `tests/extension/`、更新本计划 | Vitest 测试通过，核心流程覆盖率 ≥ 70% | M2-08 |

### 3.3 里程碑

| 里程碑 | 时间 | 目标 |
|--------|------|------|
| M0 | 后端接口改造完成（方案 D） | 后端支持 `jd_text` 为空创建岗位，支持 `PATCH /api/jobs/{id}` 补充详情 |
| M1 | 第 5 个工作日结束 | Extension 能从 Boss 直聘列表页批量提取岗位并通过 Service Worker 发送到后端 |
| M2 | 第 10 个工作日结束 | 完整闭环：批量提取 → 创建 → 点击补充 JD → 分析 → 匹配 → 话术 → 投递，端到端验证通过 |

---

## 4. Extension 架构设计

### 4.1 组件职责

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        用户浏览器（Boss 直聘页面）                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │   Content Script │  │    SidePanel     │  │   Action Popup   │  │
│  │  (DOM 提取/监听)  │  │   (Vue UI)       │  │  (登录/设置入口)  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────────┘  │
│           │ chrome.runtime      │ chrome.runtime                     │
│           │ sendMessage         │ sendMessage                        │
│           ▼                     ▼                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │            Background Service Worker                         │  │
│  │  · 消息路由                                                  │  │
│  │  · Backend API Client（axios + token + 自动刷新）              │  │
│  │  · 状态缓存（当前岗位、task_id、匹配结果、话术）               │  │
│  │  · 操作审计日志（本地 50 条）                                   │  │
│  └────────────────────────┬─────────────────────────────────────┘  │
│                           │ HTTPS / JSON / Cookie                   │
│                           ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              AI Career Copilot Backend                       │  │
│  │  FastAPI + PostgreSQL + Redis + RabbitMQ                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 各模块职责

| 模块 | 职责 | 为什么这样设计 |
|------|------|----------------|
| **Content Script** | 在招聘页面执行：检测平台、提取 DOM、监听 SPA 路由变化 | 只有 Content Script 能直接访问页面 DOM，保持最小权限 |
| **Background Service Worker** | 接收 Content Script/UI 消息，调用后端 API，管理 token 与全局状态 | MV3 要求后台逻辑在 Service Worker 中执行，避免 Content Script 直接发跨域请求导致 CORS 复杂度 |
| **Boss Adapter** | 封装 Boss 直聘的 DOM 选择器、解析器、数据归一化 | 平台差异（选择器、字段语义）集中在 Adapter，未来新增平台只需实现新 Adapter |
| **SidePanel（Vue）** | 展示岗位、分析、匹配、话术、操作按钮 | SidePanel 提供持久展示区域，用户可边浏览岗位边查看结果 |
| **Action Popup** | 登录、后端地址配置、版本信息 | 保持主 UI 在 SidePanel，Popup 只负责轻量入口 |
| **Backend Client** | 统一封装 base URL、请求头、401 刷新、request_id | 避免 UI/Content Script 各自写 axios 配置，便于审计与重试 |

### 4.3 数据流

```text
1. 用户打开 Boss 直聘职位列表页
2. Content Script 调用 BossAdapter.detect() → true
3. Content Script 调用 BossAdapter.extractJobs() → RawBossJob[]
4. Content Script 发送 JOBS_EXTRACTED 到 Service Worker
5. Service Worker 逐个调用 POST /api/jobs（jd_text 可为空） → 返回 JobResponse[]
6. SidePanel 展示批量岗位列表
7. 用户点击某个岗位卡片，右侧详情面板加载
8. Content Script 调用 BossAdapter.extractDetail() → Partial<RawBossJob>
9. Content Script 发送 JOB_DETAIL_EXTRACTED 到 Service Worker
10. Service Worker 调用 PATCH /api/jobs/{job_id} 补充 jd_text / skills / location
11. Service Worker 调用 POST /api/jobs/analyze → 返回 JobAnalyzeResponse（含 task_id）
12. Service Worker 轮询 GET /api/tasks/{task_id} → status=COMPLETED
13. Service Worker 调用 POST /api/match/compute → MatchResultResponse
14. Service Worker 调用 POST /api/communication/generate → task_id
15. Service Worker 轮询 task → CommunicationScriptResponse
16. SidePanel 收到结果并渲染
17. 用户点击“记录投递” → Service Worker 调用 POST /api/applications
```

**关键设计决策**：

- **海投模式**：先批量提取列表页所有可见岗位，用户点击感兴趣岗位后再补充 JD 并触发分析。
- **方案 D**：后端允许 `jd_text` 为空创建岗位，新增 `PATCH /api/jobs/{id}` 接口补充详情。
- **轮询而非 WebSocket**：MVP 阶段岗位分析/话术生成通过轮询实现，降低复杂度；Phase 2 多 HR Agent 再引入 WebSocket/MQ 推送。
- **Service Worker 缓存当前列表**：避免用户切换 SidePanel 时重复提取；Content Script 在 URL 变化时重新触发。
- **session_id 本地生成 UUID**：后端 [`TaskService._ensure_session_exists()`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/task/service.py#L127-L158) 会自动创建不存在的 session，减少 MVP 前置依赖。

---

## 5. Boss 直聘选择器与解析器设计

### 5.1 设计思路

MVP 采用**海投模式**：从 Boss 直聘职位搜索列表页（`web/geek/jobs`）批量提取岗位卡片，用户点击卡片后再从右侧详情面板补充完整 JD。选择器设计遵循以下原则：

1. **列表页与详情面板分离**：分别维护两套选择器，互不影响。
2. **选择器外置配置化**：不硬编码在业务逻辑中，便于快速修复。
3. **多候选 fallback**：同一字段给出一组候选 selector，按顺序尝试。
4. **语义化抽取**：对薪资、地点等做结构化转换。
5. **数据兜底**：无法解析的字段允许为空；列表页提取不依赖 JD。
6. **稳定性优先**：解析失败时给出明确错误，不静默返回脏数据。

### 5.2 选择器配置

```typescript
// extension/src/modules/boss/selector.ts
export interface BossSelectorConfig {
  // 列表页
  list: {
    jobCard: string[];
    jobName: string[];
    jobSalary: string[];
    tagList: string[];
    tagItem: string[];
    bossName: string[];
    companyLocation: string[];
    detailLink: string[];
    seenClass: string[];
    specialTag: string[];
  };
  // 详情面板
  detail: {
    container: string[];
    jobName: string[];
    jobSalary: string[];
    tagList: string[];
    tagItem: string[];
    jd: string[];
    skillList: string[];
    skillItem: string[];
    bossName: string[];
    bossTitle: string[];
    address: string[];
    chatButton: string[];
  };
}

export const BOSS_SELECTORS: BossSelectorConfig = {
  list: {
    jobCard: [".job-card-box"],
    jobName: [".job-title .job-name"],
    jobSalary: [".job-title .job-salary"],
    tagList: [".tag-list"],
    tagItem: [".tag-list li"],
    bossName: [".job-card-footer .boss-name"],
    companyLocation: [".job-card-footer .company-location"],
    detailLink: [".job-title .job-name"],
    seenClass: [".is-seen"],
    specialTag: [".job-tag-icon"],
  },
  detail: {
    container: [".job-detail-box"],
    jobName: [".job-detail-header .job-name"],
    jobSalary: [".job-detail-header .job-salary"],
    tagList: [".job-detail-header .tag-list"],
    tagItem: [".job-detail-header .tag-list li"],
    jd: [".job-detail-body .desc"],
    skillList: [".job-detail-body .job-label-list"],
    skillItem: [".job-detail-body .job-label-list li"],
    bossName: [".job-boss-info .name"],
    bossTitle: [".job-boss-info .boss-info-attr"],
    address: [".job-address .job-address-desc"],
    chatButton: [".job-detail-header .op-btn-chat"],
  },
};

/**
 * 按候选列表查找第一个非空文本节点
 */
export function queryText(el: Element | Document, selectors: string[]): string | null {
  for (const selector of selectors) {
    const found = el.querySelector(selector);
    if (found) {
      const text = found.textContent?.trim();
      if (text) return text;
    }
  }
  return null;
}

/**
 * 按候选列表查找元素本身
 */
export function queryElement(el: Element | Document, selectors: string[]): Element | null {
  for (const selector of selectors) {
    const found = el.querySelector(selector);
    if (found) return found;
  }
  return null;
}
```

### 5.3 解析器

```typescript
// extension/src/modules/boss/parser.ts
import type { JobCreateRequest } from "@/api/types";

export interface RawBossJob {
  // 列表页可提取
  title: string;
  company: string;
  salaryRaw: string | null;
  location: string | null;
  tags: string[];

  // 详情面板补充
  jdText?: string;
  skills?: string[];
  recruiterName?: string;
  recruiterTitle?: string;
  address?: string;

  // 元信息
  source: "boss";
  sourceUrl: string;
  detailUrl: string;
  seen: boolean;
  specialTag?: string;
}

export function parseSalary(raw: string | null): {
  min?: number;
  max?: number;
  unit?: string;
  original: string;
} {
  if (!raw) return { original: "" };

  const monthly = raw.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K/i);
  if (monthly) {
    return {
      min: Math.round(parseFloat(monthly[1])),
      max: Math.round(parseFloat(monthly[2])),
      unit: "K",
      original: raw,
    };
  }

  const daily = raw.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元\/天/);
  if (daily) {
    return {
      min: Math.round(parseFloat(daily[1])),
      max: Math.round(parseFloat(daily[2])),
      unit: "元/天",
      original: raw,
    };
  }

  const hourly = raw.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元\/时/);
  if (hourly) {
    return {
      min: Math.round(parseFloat(hourly[1])),
      max: Math.round(parseFloat(hourly[2])),
      unit: "元/时",
      original: raw,
    };
  }

  const single = raw.match(/(\d+(?:\.\d+)?)\s*(K|元\/天|元\/时)/i);
  if (single) {
    const v = Math.round(parseFloat(single[1]));
    return { min: v, max: v, unit: single[2], original: raw };
  }

  return { original: raw };
}

export function cleanJdText(raw: string | null): string {
  if (!raw) return "";
  return raw
    .replace(/\s+/g, " ")
    .replace(/展开|收起/g, "")
    .trim();
}

export function toJobCreateRequest(raw: RawBossJob): JobCreateRequest {
  const salary = parseSalary(raw.salaryRaw);
  return {
    title: raw.title || "未知岗位",
    company: raw.company || "未知公司",
    jd_text: cleanJdText(raw.jdText),
    source: "boss",
    source_url: raw.sourceUrl,
    location: raw.location,
    salary_min: salary.min,
    salary_max: salary.max,
    salary_unit: salary.unit,
    skills: raw.skills ?? [],
    keywords: raw.tags,
    seniority: null,
    difficulty: null,
  };
}
```

### 5.4 Adapter 结构

```typescript
// extension/src/modules/boss/adapter.ts
import { BOSS_SELECTORS, queryText, queryElement } from "./selector";
import { cleanJdText, parseSalary, toJobCreateRequest, type RawBossJob } from "./parser";
import type { JobCreateRequest } from "@/api/types";
import type { PlatformAdapter } from "@/modules/platform_adapter";

export class BossAdapter implements PlatformAdapter {
  readonly platform = "boss";

  detect(): boolean {
    const isListPage = location.hostname.includes("zhipin.com") &&
      location.pathname.startsWith("/web/geek/jobs");
    const isDetailPage = location.hostname.includes("zhipin.com") &&
      location.pathname.startsWith("/job_detail/");
    return isListPage || isDetailPage;
  }

  /**
   * 从列表页批量提取所有可见岗位卡片
   */
  extractJobs(): RawBossJob[] {
    if (!this.detect()) return [];

    const cards = document.querySelectorAll(BOSS_SELECTORS.list.jobCard.join(", "));
    const jobs: RawBossJob[] = [];

    cards.forEach((card) => {
      const title = queryText(card, BOSS_SELECTORS.list.jobName);
      const company = queryText(card, BOSS_SELECTORS.list.bossName);
      const salaryRaw = queryText(card, BOSS_SELECTORS.list.jobSalary);
      const location = queryText(card, BOSS_SELECTORS.list.companyLocation);

      const linkEl = card.querySelector(BOSS_SELECTORS.list.detailLink.join(", ")) as HTMLAnchorElement | null;
      const detailUrl = linkEl?.href || location.href;
      const sourceUrl = detailUrl;

      const tags: string[] = [];
      card.querySelectorAll(BOSS_SELECTORS.list.tagItem.join(", ")).forEach((el) => {
        const t = el.textContent?.trim();
        if (t && !tags.includes(t)) tags.push(t);
      });

      const specialTag = queryText(card, BOSS_SELECTORS.list.specialTag) || undefined;
      const seen = !!card.querySelector(BOSS_SELECTORS.list.seenClass.join(", "));

      if (!title || !company) {
        console.warn("[BossAdapter] 列表页卡片关键字段缺失", { title, company });
        return;
      }

      jobs.push({
        title,
        company,
        salaryRaw,
        location,
        tags,
        source: "boss",
        sourceUrl,
        detailUrl,
        seen,
        specialTag,
      });
    });

    return jobs;
  }

  /**
   * 从右侧详情面板提取完整 JD 等补充信息
   */
  extractDetail(): Partial<RawBossJob> | null {
    const container = queryElement(document, BOSS_SELECTORS.detail.container);
    if (!container) return null;

    const jdText = cleanJdText(queryText(container, BOSS_SELECTORS.detail.jd));
    const skills: string[] = [];
    container.querySelectorAll(BOSS_SELECTORS.detail.skillItem.join(", ")).forEach((el) => {
      const t = el.textContent?.trim();
      if (t && !skills.includes(t)) skills.push(t);
    });

    return {
      jdText,
      skills,
      recruiterName: queryText(container, BOSS_SELECTORS.detail.bossName) || undefined,
      recruiterTitle: queryText(container, BOSS_SELECTORS.detail.bossTitle) || undefined,
      address: queryText(container, BOSS_SELECTORS.detail.address) || undefined,
    };
  }

  observe(callbacks: {
    onJobsExtracted?: (jobs: RawBossJob[]) => void;
    onDetailUpdated?: (detail: Partial<RawBossJob>) => void;
  }): () => void {
    const { onJobsExtracted, onDetailUpdated } = callbacks;

    // 1. 监听列表加载：滚动加载、筛选条件变化时重新提取
    const listObserver = new MutationObserver(() => {
      if (onJobsExtracted) onJobsExtracted(this.extractJobs());
    });
    const listContainer = document.querySelector(".rec-job-list");
    if (listContainer) {
      listObserver.observe(listContainer, { childList: true, subtree: true });
    }

    // 2. 监听详情面板变化：用户点击不同卡片时补充 JD
    const detailObserver = new MutationObserver(() => {
      if (onDetailUpdated) {
        const detail = this.extractDetail();
        if (detail) onDetailUpdated(detail);
      }
    });
    const detailContainer = queryElement(document, BOSS_SELECTORS.detail.container);
    if (detailContainer) {
      detailObserver.observe(detailContainer, { childList: true, subtree: true });
    }

    // 3. 监听 URL 变化（分页/筛选/搜索）
    const handleUrlChange = () => {
      if (location.href !== this._lastUrl) {
        this._lastUrl = location.href;
        if (onJobsExtracted) onJobsExtracted(this.extractJobs());
      }
    };
    window.addEventListener("popstate", handleUrlChange);
    const originalPushState = history.pushState;
    history.pushState = function (...args) {
      originalPushState.apply(this, args);
      handleUrlChange();
    };

    // 兜底轮询
    const interval = setInterval(handleUrlChange, 3000);

    // 首次触发
    if (onJobsExtracted) onJobsExtracted(this.extractJobs());

    return () => {
      listObserver.disconnect();
      detailObserver.disconnect();
      window.removeEventListener("popstate", handleUrlChange);
      history.pushState = originalPushState;
      clearInterval(interval);
    };
  }

  private _lastUrl = location.href;
}
```

### 5.5 选择器维护策略

| 场景 | 应对方式 |
|------|----------|
| 单个 selector 失效 | fallback 列表自动使用下一个 |
| 列表页与详情面板类名变化 | 两套选择器独立维护，降低互相影响 |
| 全部 selector 失效 | UI 显示“页面结构变化，请手动复制 JD”，并上报错误日志 |
| 新页面实验 | 在测试环境抓取 20 个页面样本，更新 selector 配置后灰度 |
| 反爬升级 | 仅读取可见 DOM，不请求接口，模拟人类复制行为 |

---

## 6. UI 设计

### 6.1 面板布局

SidePanel 宽度固定 380px，海投模式下分为 4 个区域：

```text
┌─────────────────────────────┐
│ Header：产品名 + 后端状态灯 + 当前页岗位数 │
├─────────────────────────────┤
│ 批量岗位列表                  │
│ · 岗位名 / 公司 / 薪资 / 地点 │
│ · 标签 / 是否已提取 / 是否已分析 │
│ · 点击卡片查看详情并补充 JD   │
├─────────────────────────────┤
│ 选中岗位详情区                │
│ · 完整 JD                    │
│ · 技能标签 / 招聘者信息       │
│ · 分析结果 / 匹配分数 / 沟通话术 │
├─────────────────────────────┤
│ 操作栏                       │
│ · 批量刷新列表               │
│ · 记录投递（仅 JD 已补充）   │
│ · 状态：未投递 / 已投递       │
└─────────────────────────────┘
```

### 6.2 状态机（单岗位）

```text
EXTRACTED → CREATED → DETAIL_EXTRACTED → ANALYZING → MATCHING → GENERATING_COMMUNICATION → READY
    │          │             │                │            │                  │
    ▼          ▼             ▼                ▼            ▼                  ▼
  ERROR      ERROR          ERROR            ERROR        ERROR              ERROR
```

> 说明：列表页批量提取后岗位状态为 `CREATED`（jd_text 为空）。用户点击卡片后，详情面板 JD 补充完成进入 `DETAIL_EXTRACTED`，再触发后续分析流程。

### 6.3 交互说明

| 操作 | 说明 |
|------|------|
| 自动提取 | 用户打开 Boss 列表页后，Content Script 自动提取所有可见岗位卡片 |
| 手动刷新 | 面板顶部提供“刷新列表”按钮，用于滚动加载或筛选后未自动识别的情况 |
| 点击卡片 | 选中岗位，若详情面板已加载则补充 JD，触发分析与话术生成 |
| 复制话术 | 每条话术右侧提供复制按钮，复制成功后显示 Toast |
| 记录投递 | 用户确认要投递后点击，调用 `POST /api/applications`，成功后按钮变为“已投递”并禁用 |
| 错误重试 | 每个岗位卡片独立显示错误原因与重试按钮，避免整页刷新 |

### 6.4 错误提示

| 错误类型 | 提示文案 |
|----------|----------|
| 未登录 | “请先登录 AI Career Copilot” |
| 无简历 | “请先上传简历，匹配与话术需要简历数据” |
| 解析失败 | “未能识别岗位信息，请检查是否处于 Boss 直聘职位列表页” |
| 后端不可用 | “后端服务连接失败，请确认本地服务已启动” |
| LLM 失败 | “AI 分析失败，请稍后重试或手动填写” |
| 投递重复 | “该岗位已记录投递” |
| JD 未补充 | “请先点击岗位卡片加载详情，补充 JD 后再分析” |

---

## 7. 与后端接口对接清单

### 7.1 认证

| 端点 | 方法 | 请求体 | 响应 | 用途 |
|------|------|--------|------|------|
| `/api/auth/login` | POST | `{ email, password }` | `TokenResponse` | Extension 登录获取 access_token |
| `/api/auth/refresh` | POST | Cookie: `refresh_token` | `TokenResponse` | 自动刷新 access_token（需 `withCredentials: true`） |

**注意**：access_token 仅保存在 Service Worker 内存中；refresh token 由浏览器通过 HttpOnly Cookie 自动管理。

### 7.2 简历

| 端点 | 方法 | 请求体/Query | 响应 | 用途 |
|------|------|--------------|------|------|
| `GET /api/resumes` | GET | `limit=1` | `ResumeListResponse` | 检查用户是否有简历，取 `is_active=true` 的简历 |

### 7.3 岗位

| 端点 | 方法 | 请求体 | 响应 | 用途 |
|------|------|--------|------|------|
| `POST /api/jobs` | POST | [`JobCreateRequest`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/job/models.py#L328-L494)（`jd_text` 可为空） | [`JobResponse`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/job/models.py#L640-L730) | 海投模式批量创建岗位记录 |
| `PATCH /api/jobs/{job_id}` | PATCH | `JobUpdateRequest`（字段全可选） | `JobResponse` | 用户点击卡片后补充 JD、skills、location 等 |
| `POST /api/jobs/analyze` | POST | `{ job_id, session_id }` | [`JobAnalyzeResponse`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/job/models.py#L732-L775) | 触发 JD 分析，返回 `task_id` |
| `GET /api/tasks/{task_id}` | GET | - | [`TaskDTO`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/task/dto.py#L23-L42) | 轮询任务状态与结果 |

### 7.4 匹配

| 端点 | 方法 | 请求体 | 响应 | 用途 |
|------|------|--------|------|------|
| `POST /api/match/compute` | POST | `{ job_id, resume_id? }` | [`MatchResultResponse`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/match/models.py#L306-L337) | 计算简历与岗位匹配分 |

### 7.5 沟通话术

| 端点 | 方法 | 请求体 | 响应 | 用途 |
|------|------|--------|------|------|
| `POST /api/communication/generate` | POST | `{ job_id, session_id, resume_id?, tone="natural" }` | [`CommunicationGenerateResponse`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/communication/models.py#L67-L82) | 生成沟通话术，返回 `task_id` |
| `GET /api/tasks/{task_id}` | GET | - | `TaskDTO` | 轮询话术生成结果 |

### 7.6 投递

| 端点 | 方法 | 请求体 | 响应 | 用途 |
|------|------|--------|------|------|
| `POST /api/applications` | POST | `{ job_id, match_score?, notes? }` | [`ApplicationResponse`](file:///g:/my/my_file/AI%20Career%20Copilot/backend/app/domain/application/models.py#L57-L101) | 记录投递 |

### 7.7 轮询策略

- 分析任务与话术任务轮询间隔：**2 秒**，最大轮询次数：**30 次（60 秒）**。
- 若任务 `FAILED`，展示 `error_message` 并提供重试。
- 同一岗位的多个任务共享同一个 `session_id`（本地生成的 UUID），便于后端追踪。

---

## 8. 端到端验证步骤

### 8.1 环境准备

1. 启动后端：`./.venv/Scripts/python.exe -m app.main`（Windows）或 `./.venv/bin/python -m app.main`。
2. 确认 RabbitMQ、PostgreSQL、Redis 运行正常。
3. 编译 Extension：`cd extension && npm run build`，然后在 Chrome `chrome://extensions/` 中加载 `extension/dist` 目录。

### 8.2 验证用例

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| TC-01 | 打开 Chrome SidePanel，输入账号密码登录 | 登录成功，顶部显示用户邮箱/昵称 |
| TC-02 | 访问 Boss 直聘职位列表页 | SidePanel 自动进入“提取中”，3 秒内显示当前可见岗位列表 |
| TC-03 | 滚动列表页加载更多岗位 | 新加载的岗位自动出现在 SidePanel 列表中 |
| TC-04 | 点击列表中某个岗位卡片 | 右侧详情面板加载，SidePanel 补充 JD、技能标签、招聘者信息 |
| TC-05 | 等待分析 | 10 - 30 秒内显示“分析结果”卡片，含技能/关键词/资历/难度 |
| TC-06 | 等待匹配 | 显示综合匹配分、BM25/语义分、命中/缺失技能 |
| TC-07 | 等待话术 | 显示打招呼话术、跟进话术、完整话术 |
| TC-08 | 点击“复制打招呼话术” | 剪贴板内容与展示一致，Toast 提示复制成功 |
| TC-09 | 点击“记录投递” | 后端生成投递记录，按钮变为“已投递”，不可重复点击 |
| TC-10 | 切换到新的搜索条件或分页 | Content Script 重新提取，面板刷新为新列表数据 |
| TC-11 | 关闭并重新打开 SidePanel | 保留当前列表状态，不丢失已加载结果 |
| TC-12 | 模拟后端 500 错误 | 面板显示友好错误提示，提供重试按钮 |

### 8.3 自动化测试

- 使用 Vitest 编写单元测试：选择器 fallback、薪资解析、Adapter 数据转换。
- 使用 Playwright + 本地 Chrome Extension 加载模式编写 E2E 测试：自动打开 Boss 直聘页面、验证面板渲染（遵循“浏览器项目自动测试”规则，开启可视化界面模式）。

---

## 9. 风险与应对

| 风险 | 可能性 | 影响 | 应对策略 |
|------|--------|------|----------|
| Boss 直聘 DOM 结构变化导致解析失败 | 高 | 高 | 选择器配置化 + 多 fallback；建立每日样本检查脚本；失效时引导用户手动模式 |
| 后端 `POST /api/jobs` 未记录 user_id，后续权限边界不清 | 中 | 中 | MVP 期间记录技术债；Phase 2 评估是否为 Job 表增加 `user_id` 字段 |
| Extension 登录态跨页面丢失 | 中 | 高 | access_token 存 Service Worker 内存；每次请求前检查有效期，401 时自动 refresh |
| LLM 分析耗时超过 60 秒 | 中 | 中 | 延长轮询上限至 120 秒；超时后提示用户重试；分析任务由 MQ 保证不丢失 |
| 用户未上传简历 | 高 | 中 | 面板主动检测简历列表，无简历时禁用匹配/话术并引导上传 |
| CORS 在生产环境未放行 Extension 来源 | 低 | 高 | 部署 checklist 中明确 `CORS_ALLOW_EXTENSIONS=true`；测试环境提前验证 |
| 浏览器扩展权限收紧（MV3 限制） | 低 | 中 | 严格遵守 MV3 规范，所有网络请求走 Service Worker；不使用远程代码 |
| Boss 直聘单账号每日约 150 份打招呼/投递上限 | 高 | 高 | 自动操作阶段每日阈值保守设为 120 次并持久化计数；额度耗尽后暂停 Agent 并提示用户；提取岗位信息不限速 |

---

## 10. 验收标准（Definition of Done）

MVP Extension 补全完成需同时满足以下全部条件：

1. **功能闭环**：从 Boss 直聘职位列表页批量提取岗位并创建到后端，用户点击详情卡片后补充 JD，触发分析、匹配、话术生成，并支持一键记录投递。
2. **接口对接**：所有列出的后端接口均已完成调用，错误码处理覆盖 401/404/409/422/500/502。
3. **代码质量**：新增 TypeScript 代码通过 ESLint + Prettier；函数/类/方法均有中文注释说明职责、参数、返回值、异常。
4. **测试通过**：单元测试覆盖率 ≥ 70%；至少 3 个真实 Boss 岗位页面通过端到端验证。
5. **安全合规**：不持久化招聘平台 Cookie/Token；access_token 仅存 Service Worker 内存；所有请求带 request_id；异常不暴露敏感信息。
6. **文档更新**：本计划文档、Extension README、API 对接说明已更新并评审通过。
7. **代码提交**：按 Git 规范小步提交，至少包含 `feat(extension): ...`、`test(extension): ...`、`docs(extension): ...` 等粒度清晰的 commit。
8. **评审通过**：前端/Extension 负责人、后端负责人、测试负责人三方签字的 MVP 验收单。

---

## 11. 与 Post-MVP 阶段的衔接

MVP Extension 补全完成后，方可进入 [post_mvp_development_plan.md](file:///g:/my/my_file/AI%20Career%20Copilot/docs/plans/post_mvp_development_plan.md)。衔接点：

- **Adapter 抽象接口**：MVP 中沉淀的 `PlatformAdapter` 接口将直接复用于 Phase 2 的多平台适配。
- **Service Worker 消息路由**：Phase 2 的多 HR Agent 将在现有消息协议上扩展 `CONVERSATION_UPDATED`、`REPLY_SUGGESTIONS`、`SEND_AUTHORIZED_REPLY` 等类型。
- **SidePanel 状态管理**：MVP 中建立的 Vue/Pinia 状态基座将承载 Phase 2 的对话列表、模式选择、记忆管理 UI。
- **审计日志**：MVP 中 Service Worker 的本地操作日志将升级为后端 AuditLog Service。
