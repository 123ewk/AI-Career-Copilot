# Agentic Job Copilot - 项目需求文档（PRD）

> 版本：v1.0.0
> 日期：2026-06-13
> 状态：Draft

---

## 1. 项目概述

### 1.1 产品定位

**Agentic Job Copilot** 是一个智能求职 Agent 平台，帮助求职者完成从岗位发现、岗位分析、简历优化、沟通回复、投递跟踪到面试管理的全流程求职辅助。

**本质**：Human + Agent 协同求职系统

**不是**：自动投简历机器人

### 1.2 核心价值主张

| 维度 | 传统招聘平台 | Agentic Job Copilot |
|------|-------------|---------------------|
| 职位展示 | ✅ | ✅ |
| 简历投递 | ✅ | ✅ |
| 聊天沟通 | ✅ | ✅ |
| 岗位分析 | ❌ | ✅ |
| 岗位匹配 | ❌ | ✅ |
| 求职策略 | ❌ | ✅ |
| 历史经验沉淀 | ❌ | ✅ |
| 持续跟踪 | ❌ | ✅ |

**核心解决**：信息 → 决策 → 行动 的完整链路

### 1.3 与普通 RAG 项目的本质区别

| 维度 | RAG 系统 | Agentic Job Copilot |
|------|----------|---------------------|
| 驱动方式 | 问题驱动 | 目标驱动 |
| 流程 | 问题 → 检索 → 回答 | 目标 → 分析 → 决策 → 行动 → 状态变化 → 持续优化 |
| 系统类型 | 检索增强生成 | Goal-Oriented Agent System |
| 状态管理 | 无状态 | 求职状态系统 + Agent 决策系统 |

---

## 2. 用户画像

### 2.1 主要用户

| 用户类型 | 核心痛点 | 典型行为 |
|---------|---------|---------|
| 应届生 | 不了解岗位要求，简历缺乏针对性 | 海投无回音，不知道如何优化 |
| 实习生 | 缺少经验，不知如何包装项目 | 盲目投递，回复率低 |
| 转行求职者 | 技能匹配度低，不知如何转型 | 不知道该学什么，投递方向迷茫 |
| AI 开发岗位求职者 | 岗位细分多，JD 要求模糊 | 难以判断岗位真实要求 |

### 2.2 典型用户旅程

```
用户打开 Boss直聘
  ↓
看到一个岗位：AI应用开发工程师
  ↓
Agent 自动：
  分析 JD → 提取技能要求 → 匹配用户简历
  → 计算匹配度 → 指出缺失能力 → 生成优化建议
  → 生成沟通话术
  ↓
用户决策：投递 / 跳过 / 优化简历后再投
  ↓
投递后 Agent 持续跟踪状态
  ↓
经验沉淀，优化下一次求职
```

---

## 3. 系统核心闭环

```
发现岗位 → 岗位分析 → 简历匹配 → 决策推荐 → 生成沟通话术
    ↑                                                    ↓
    ←←←←← 经验沉淀 ←←← 优化下一次求职 ←←← 跟踪状态 ←←← 投递记录
```

### 3.1 闭环阶段定义

| 阶段 | 输入 | 处理 | 输出 | 状态变更 |
|------|------|------|------|---------|
| 发现岗位 | 招聘网站页面 | DOM 解析、数据提取 | 结构化岗位数据 | `DISCOVERED` |
| 岗位分析 | 岗位 JD 文本 | NLP 解析、技能提取 | 技能列表、关键词、难度评级 | `ANALYZED` |
| 简历匹配 | 岗位要求 + 用户简历 | 语义匹配、差距分析 | 匹配分数、缺失技能 | `MATCHED` |
| 决策推荐 | 匹配结果 + 历史数据 | 策略计算 | 投递建议（投/不投/优化后投） | `RECOMMENDED` |
| 生成沟通话术 | 岗位信息 + 用户画像 | LLM 生成 | 打招呼内容、回复模板 | `COMMUNICATION_READY` |
| 投递记录 | 用户操作 | 状态记录 | 投递记录 | `APPLIED` |
| 跟踪状态 | 时间线 | 状态检测 | 状态更新通知 | `IN_PROGRESS` / `REJECTED` / `INTERVIEW` |
| 经验沉淀 | 历史投递数据 | 统计分析 | 策略优化建议 | `REFLECTED` |

---

## 4. 系统架构

### 4.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                  Interaction Layer                       │
│              Chrome Extension (Vue 3 + TS)               │
│     页面感知 │ 结果展示 │ 操作入口 │ 消息通信              │
├─────────────────────────────────────────────────────────┤
│                    API Layer                             │
│                FastAPI (编排层)                           │
│     接口暴露 │ 请求编排 │ 会话管理 │ 认证鉴权              │
├─────────────────────────────────────────────────────────┤
│               Agent Runtime Layer                        │
│              LangGraph (核心层)                           │
│     任务规划 │ 工具调用 │ 决策执行 │ 反思优化              │
├─────────────────────────────────────────────────────────┤
│              Domain Service Layer                        │
│               业务服务层                                  │
│  Job Service │ Resume Service │ Application Service      │
│  Communication Service │ Strategy Service                │
├─────────────────────────────────────────────────────────┤
│                 State Layer                              │
│  PostgreSQL (持久化) │ Redis (缓存/会话/运行时)           │
├─────────────────────────────────────────────────────────┤
│              External Layer                              │
│  LLM │ 招聘平台 │ 邮件通知 │ 消息推送                     │
└─────────────────────────────────────────────────────────┘
```

### 4.2 各层职责与模块映射

#### 4.2.1 Interaction Layer（交互层）

| 模块 | 职责 | 技术实现 |
|------|------|---------|
| 页面感知 | 读取招聘网站 DOM，提取岗位信息 | Content Script + DOM Engine |
| 结果展示 | 展示分析结果、匹配分数、优化建议 | Vue 3 组件 + Tailwind CSS |
| 操作入口 | 提供 [分析岗位] [生成回复] [优化简历] 按钮 | Chrome Extension Popup/SidePanel |
| 消息通信 | 与后端 WebSocket/HTTP 通信 | Chrome Message API + WebSocket |
| 平台适配 | 适配不同招聘网站的 DOM 结构 | Adapter 模式（Boss/猎聘/智联/实习僧） |

#### 4.2.2 API Layer（编排层）

| 模块 | 职责 | 路由前缀 |
|------|------|---------|
| 认证鉴权 | 用户注册/登录、JWT Token 管理 | `/api/auth` |
| 岗位接口 | 岗位 CRUD、JD 分析触发 | `/api/jobs` |
| 简历接口 | 简历上传/管理、匹配触发 | `/api/resume` |
| 匹配接口 | 匹配计算、结果查询 | `/api/match` |
| Agent 接口 | Agent 会话管理、任务下发 | `/api/agent` |
| 工作流接口 | 工作流定义/执行/状态查询 | `/api/workflow` |
| 会话接口 | 用户会话管理 | `/api/session` |
| 任务接口 | 异步任务状态查询 | `/api/task` |
| 用户接口 | 用户信息管理 | `/api/user` |

**中间件**：

| 中间件 | 职责 |
|--------|------|
| auth | JWT 认证、权限校验 |
| cors | 跨域配置 |
| exception | 全局异常处理 |
| logging | 请求日志 |
| rate_limit | 接口限流 |
| request_id | 请求链路追踪 ID |

#### 4.2.3 Agent Runtime Layer（核心层）

| 子模块 | 职责 | 关键组件 |
|--------|------|---------|
| Planner | 任务规划与分解 | planner.py, task_graph.py, workflow.py |
| Executor | 任务执行与调度 | executor.py, dispatcher.py, retry.py, timeout.py |
| State | 状态管理 | agent_state.py, task_state.py, workflow_state.py |
| Memory | 记忆管理 | short_term.py, long_term.py, vector_memory.py |
| Workflow | 工作流引擎 | dag.py, node.py, edge.py, workflow_engine.py |
| Context | 上下文管理 | session_context.py, task_context.py |
| Checkpoint | 检查点与恢复 | manager.py, recovery.py |
| Event | 事件总线 | bus.py, publisher.py, subscriber.py |
| Scheduler | 调度管理 | concurrency.py, priority.py, queue_manager.py |
| Observer | 可观测性 | logger.py, metrics.py, tracing.py |

#### 4.2.4 Domain Service Layer（业务层）

| 服务 | 职责 | 核心方法 |
|------|------|---------|
| Job Service | 岗位管理、JD 解析、技能提取 | `parse_jd()`, `extract_skills()`, `analyze_difficulty()` |
| Resume Service | 简历管理、结构化解析、验证 | `parse_resume()`, `validate()`, `get_structure()` |
| Match Service | 匹配计算、评分、排序、策略 | `calculate_match()`, `rank_jobs()`, `generate_strategy()` |
| Communication Service | 沟通话术生成、合规检查、模板管理 | `generate_greeting()`, `check_compliance()`, `get_template()` |
| Session Service | 会话生命周期管理 | `create()`, `get()`, `update()` |
| User Service | 用户信息管理 | `register()`, `login()`, `update_profile()` |
| Workflow Service | 工作流定义与执行 | `define()`, `execute()`, `get_status()` |

#### 4.2.5 State Layer（状态层）

**PostgreSQL**：

| 实体 | 说明 |
|------|------|
| users | 用户信息 |
| jobs | 岗位信息 |
| resumes | 简历信息 |
| applications | 投递记录 |
| interviews | 面试记录 |
| agent_memories | Agent 长期记忆 |
| sessions | 会话记录 |
| tasks | 异步任务 |
| workflows | 工作流定义与执行记录 |

**Redis**：

| 用途 | 说明 |
|------|------|
| 缓存 | 岗位分析结果、匹配结果缓存 |
| 会话状态 | Agent 运行时会话数据 |
| 运行时数据 | 任务队列、调度状态、限流计数 |

#### 4.2.6 External Layer（外部能力层）

| 集成 | 说明 | 实现 |
|------|------|------|
| LLM | 大语言模型调用 | OpenAI / DeepSeek / LangChain |
| 招聘平台 | 页面数据抓取 | Boss直聘 / 猎聘 / 智联 / 实习僧适配器 |
| 邮件通知 | 投递状态变更通知 | SMTP |
| 消息推送 | 实时通知 | Webhook / 微信通知 |
| 浏览器自动化 | 页面操作 | Playwright |
| 向量检索 | 语义搜索 | RAG + Vector Search |

---

## 5. Agent 能力设计

### 5.1 Agent 总览

```
                    ┌─────────────────────┐
                    │   Agent Runtime     │
                    │   (LangGraph)       │
                    └──────────┬──────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                     │
┌─────────▼─────────┐ ┌───────▼────────┐ ┌────────▼────────┐
│  Job Analysis     │ │  Resume Agent  │ │  Communication  │
│  Agent            │ │                │ │  Agent          │
└───────────────────┘ └────────────────┘ └─────────────────┘
                                               │
                                    ┌──────────▼──────────┐
                                    │  Career Strategy    │
                                    │  Agent              │
                                    └─────────────────────┘
```

### 5.2 Job Analysis Agent（岗位分析 Agent）

**职责**：解析 JD、提取技能要求、提取关键词、分析岗位难度

**输入**：JD 原始文本

**输出**：

```json
{
  "skills": ["Python", "LangChain", "FastAPI", "PostgreSQL"],
  "keywords": ["AI应用开发", "RAG", "Agent", "大模型"],
  "seniority": "mid",
  "difficulty": "medium",
  "salary_range": {"min": 25, "max": 40, "unit": "K"},
  "company_info": {
    "industry": "互联网",
    "scale": "500-1000人",
    "stage": "B轮"
  },
  "hidden_requirements": ["可能需要oncall", "有竞业协议"]
}
```

**工具依赖**：LLM Extract Tool、Web Search Tool

**状态流转**：`IDLE` → `PARSING` → `EXTRACTING` → `ANALYZING` → `COMPLETED`

### 5.3 Resume Agent（简历 Agent）

**职责**：匹配简历、识别缺失技能、生成优化建议

**输入**：岗位分析结果 + 用户简历

**输出**：

```json
{
  "match_score": 85,
  "matched_skills": ["Python", "FastAPI", "PostgreSQL"],
  "missing_skills": ["LangChain", "RAG"],
  "strengths": ["后端开发经验丰富", "有AI项目经历"],
  "weaknesses": ["缺少RAG实战经验", "Agent开发经验不足"],
  "optimization_suggestions": [
    {
      "type": "add_project",
      "description": "补充一个RAG项目经历，强调向量检索和重排序",
      "priority": "high"
    },
    {
      "type": "reorder",
      "description": "将AI相关项目提前到第一页",
      "priority": "medium"
    }
  ]
}
```

**工具依赖**：Resume Parser Tool、LLM Classify Tool、RAG Tool

**状态流转**：`IDLE` → `PARSING_RESUME` → `MATCHING` → `ANALYZING_GAP` → `GENERATING_SUGGESTIONS` → `COMPLETED`

### 5.4 Communication Agent（沟通 Agent）

**职责**：生成打招呼内容、回复 HR、面试邀约回复

**输入**：岗位信息 + 用户画像 + 沟通上下文

**输出**：

```json
{
  "greeting": "您好，我对贵司的AI应用开发工程师岗位很感兴趣...",
  "greeting_type": "proactive",
  "replies": [
    {
      "scenario": "hr_ask_experience",
      "content": "关于AI开发经验，我在上家公司...",
      "tone": "professional"
    }
  ],
  "interview_reply": {
    "accept": "非常感谢您的面试邀请，我确认参加...",
    "reschedule": "感谢邀请，但该时间段我有冲突，是否可以..."
  }
}
```

**工具依赖**：LLM Rewrite Tool、PII Filter Tool、Content Checker Tool

**合规要求**：
- 所有生成内容必须经过 PII 过滤
- 不得生成虚假经历或夸大描述
- 必须经过合规检查（compliance_checker）

**状态流转**：`IDLE` → `GENERATING` → `COMPLIANCE_CHECK` → `FILTERING_PII` → `COMPLETED`

### 5.5 Career Strategy Agent（求职策略 Agent）

**职责**：分析求职方向、统计投递结果、优化求职策略

**输入**：用户历史投递数据 + 市场信息

**输出**：

```json
{
  "strategy_report": {
    "total_applications": 30,
    "response_rate": {
      "ai_jobs": 0.35,
      "backend_jobs": 0.12,
      "overall": 0.22
    },
    "recommendations": [
      {
        "type": "direction_adjust",
        "content": "AI岗位回复率显著高于后端岗位，建议增加AI岗位投递比例",
        "confidence": 0.85
      },
      {
        "type": "skill_gap",
        "content": "回复率高的岗位普遍要求RAG经验，建议优先补充",
        "confidence": 0.78
      }
    ],
    "next_actions": [
      "重点投递AI应用开发岗位",
      "补充LangChain/RAG项目经历",
      "优化简历中AI相关描述"
    ]
  }
}
```

**工具依赖**：LLM Summarize Tool、Web Search Tool、RAG Tool

**触发条件**：投递数 ≥ 10 时自动生成 / 用户手动触发

**状态流转**：`IDLE` → `COLLECTING_DATA` → `ANALYZING` → `GENERATING_STRATEGY` → `COMPLETED`

---

## 6. 求职状态系统（Job State System）

### 6.1 岗位状态机

```
DISCOVERED → ANALYZED → MATCHED → RECOMMENDED → COMMUNICATION_READY
                                                         │
                     ┌───────────────────────────────────┤
                     ↓                                   ↓
               APPLIED                              SKIPPED
                 │
     ┌───────────┼───────────┐
     ↓           ↓           ↓
  VIEWED     REJECTED    INTERVIEW
                             │
                    ┌────────┼────────┐
                    ↓        ↓        ↓
               SCHEDULED  PASSED   FAILED
                    │
                    ↓
               OFFERED
```

### 6.2 状态定义

| 状态 | 说明 | 触发条件 |
|------|------|---------|
| `DISCOVERED` | 已发现岗位 | Extension 检测到新岗位 |
| `ANALYZED` | 已完成 JD 分析 | Job Analysis Agent 完成 |
| `MATCHED` | 已完成简历匹配 | Resume Agent 完成 |
| `RECOMMENDED` | 已生成投递建议 | 匹配分数 + 策略计算完成 |
| `COMMUNICATION_READY` | 沟通话术已生成 | Communication Agent 完成 |
| `APPLIED` | 已投递 | 用户确认投递 |
| `SKIPPED` | 已跳过 | 用户选择跳过 |
| `VIEWED` | HR 已查看 | 平台状态检测 |
| `REJECTED` | 已被拒绝 | 平台状态检测 / 超时 |
| `INTERVIEW` | 进入面试 | HR 邀约面试 |
| `SCHEDULED` | 面试已安排 | 用户确认面试时间 |
| `PASSED` | 面试通过 | 用户手动更新 |
| `FAILED` | 面试未通过 | 用户手动更新 |
| `OFFERED` | 已发 Offer | 用户手动更新 |

### 6.3 Agent 决策系统（Agent Runtime）

**核心设计原则**：

1. **目标驱动**：每个 Agent 有明确的目标状态，而非简单的问答
2. **状态感知**：Agent 决策基于当前求职状态和历史数据
3. **工具调用**：Agent 通过工具与外部系统交互
4. **反思优化**：Agent 执行后进行结果评估和策略调整
5. **长期记忆**：跨会话保留用户偏好和求职历史

**决策流程**：

```
接收目标
  ↓
评估当前状态
  ↓
规划行动序列（Planner）
  ↓
执行行动（Executor + Tools）
  ↓
观察结果（Observer）
  ↓
状态变更（State Manager）
  ↓
反思与优化（Reflection）
  ↓
是否达成目标？
  ├── 是 → 完成
  └── 否 → 重新规划
```

---

## 7. Chrome Extension 设计

### 7.1 架构

```
┌─────────────────────────────────────────────────┐
│                  Chrome Extension                │
├──────────────┬──────────────┬───────────────────┤
│  Popup /     │  Content     │  Background       │
│  SidePanel   │  Script      │  Service Worker   │
│              │              │                   │
│  Vue 3 UI    │  DOM 解析    │  消息路由          │
│  结果展示     │  数据上报    │  任务监听          │
│  操作入口     │  事件监听    │  缓存管理          │
│              │              │  WebSocket 连接    │
├──────────────┴──────────────┴───────────────────┤
│              Platform Adapters                   │
│  Boss直聘 │ 猎聘 │ 智联招聘 │ 实习僧             │
│  selector │ parser │ actions │ adapter           │
└─────────────────────────────────────────────────┘
```

### 7.2 平台适配器设计

每个招聘平台适配器包含四个模块：

| 模块 | 职责 |
|------|------|
| `selector.ts` | CSS 选择器定义，定位页面元素 |
| `parser.ts` | DOM 解析逻辑，提取结构化数据 |
| `actions.ts` | 页面操作（点击、滚动、输入） |
| `adapter.ts` | 统一接口适配，屏蔽平台差异 |

### 7.3 核心交互流程

1. 用户浏览招聘网站 → Content Script 检测页面类型
2. 识别到岗位详情页 → 解析 DOM 提取岗位数据
3. 通过 Chrome Message 发送到 Background
4. Background 通过 WebSocket/HTTP 转发到后端
5. 后端触发 Agent 工作流
6. 结果通过 WebSocket 推送回 Extension
7. Extension 在页面侧边展示分析结果

---

## 8. 功能需求清单

### 8.1 P0 - 核心功能（MVP）

| 编号 | 功能 | 描述 | 验收标准 |
|------|------|------|---------|
| F-001 | 用户注册/登录 | 邮箱注册、JWT 认证 | 注册成功返回 Token，登录获取用户信息 |
| F-002 | 简历上传与解析 | 支持 PDF/DOCX 格式上传，自动解析为结构化数据 | 解析出教育经历、工作经历、技能列表 |
| F-003 | 岗位信息提取 | Extension 从招聘页面提取岗位数据 | 提取岗位名称、公司、薪资、JD 全文 |
| F-004 | JD 智能分析 | 解析 JD，提取技能要求、关键词、难度 | 输出结构化分析结果（skills, keywords, seniority） |
| F-005 | 简历-岗位匹配 | 计算简历与岗位的匹配度 | 输出匹配分数、匹配技能、缺失技能 |
| F-006 | 优化建议生成 | 基于匹配结果生成简历优化建议 | 输出具体可执行的优化建议列表 |
| F-007 | 沟通话术生成 | 生成打招呼内容和常见回复模板 | 生成自然、专业、个性化的沟通内容 |
| F-008 | 投递记录管理 | 记录用户投递状态和结果 | 投递记录可查询、状态可更新 |
| F-009 | Agent 会话管理 | 管理 Agent 与用户的交互会话 | 会话创建、恢复、销毁正常工作 |

### 8.2 P1 - 增强功能

| 编号 | 功能 | 描述 | 验收标准 |
|------|------|------|---------|
| F-010 | 求职策略分析 | 统计投递数据，生成策略优化建议 | 投递 ≥10 次后自动生成策略报告 |
| F-011 | 面试管理 | 面试安排、提醒、面经记录 | 面试时间提醒、面经可记录和检索 |
| F-012 | 多平台适配 | 支持 Boss直聘、猎聘、智联、实习僧 | 每个平台可正常提取岗位数据 |
| F-013 | 实时状态跟踪 | 跟踪投递后的状态变化 | 状态变更时推送通知 |
| F-014 | Agent 长期记忆 | 跨会话保留用户偏好和求职历史 | 新会话可引用历史交互内容 |
| F-015 | 工作流编排 | 支持自定义 Agent 工作流 | 可定义和执行多步骤工作流 |

### 8.3 P2 - 未来功能

| 编号 | 功能 | 描述 |
|------|------|------|
| F-016 | 简历自动优化 | 根据目标岗位自动修改简历内容 |
| F-017 | 市场趋势分析 | 分析招聘市场趋势和薪资水平 |
| F-018 | 模拟面试 | AI 驱动的模拟面试练习 |
| F-019 | 社区经验分享 | 用户间求职经验分享 |
| F-020 | 多语言支持 | 支持英文 JD 分析和简历优化 |

---

## 9. 非功能需求

### 9.1 性能要求

| 指标 | 目标值 |
|------|--------|
| JD 分析响应时间 | ≤ 5s（不含 LLM 调用） |
| 匹配计算响应时间 | ≤ 3s |
| 沟通话术生成时间 | ≤ 8s（含 LLM 调用） |
| Extension 页面注入时间 | ≤ 500ms |
| WebSocket 消息延迟 | ≤ 200ms |
| 并发用户支持 | ≥ 100 |

### 9.2 安全要求

| 要求 | 说明 |
|------|------|
| 认证 | JWT Token，Access Token 15min 过期，Refresh Token 7d 过期 |
| 授权 | 基于用户 ID 的资源隔离 |
| 数据加密 | 密码 bcrypt 加密，敏感字段 AES-256 加密 |
| PII 保护 | 生成内容经过 PII 过滤，不泄露用户隐私 |
| 通信安全 | HTTPS + WSS |
| 限流 | API 限流 60 req/min/user |

### 9.3 可靠性要求

| 要求 | 说明 |
|------|------|
| Agent 执行可靠性 | 支持检查点和恢复，失败自动重试（最多 3 次） |
| 数据持久化 | PostgreSQL 持久化，每日备份 |
| 状态一致性 | Agent 状态变更原子性保证 |
| 降级策略 | LLM 不可用时返回缓存结果或降级提示 |

### 9.4 可观测性要求

| 维度 | 实现 |
|------|------|
| 日志 | Loguru 结构化日志，请求链路追踪 |
| 指标 | Agent 执行耗时、成功率、匹配分数分布 |
| 追踪 | Request ID 贯穿全链路 |
| 告警 | Agent 执行失败率 > 10% 时告警 |

---

## 10. 数据模型

### 10.1 核心实体关系

```
User 1──N Resume
User 1──N Application
User 1──N AgentMemory
User 1──N Session

Job 1──N Application
Job 1──1 JobAnalysis

Application N──1 Interview
Application 1──N Communication

Session 1──N Task
Workflow 1──N WorkflowExecution
```

### 10.2 核心实体定义

#### User（用户）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| email | String | 邮箱，唯一 |
| password_hash | String | 密码哈希 |
| name | String | 姓名 |
| target_position | String | 目标岗位 |
| target_industry | String | 目标行业 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

#### Job（岗位）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| title | String | 岗位名称 |
| company | String | 公司名称 |
| salary_min | Integer | 最低薪资（K） |
| salary_max | Integer | 最高薪资（K） |
| jd_text | Text | JD 原文 |
| source | String | 来源平台 |
| source_url | String | 原始链接 |
| location | String | 工作地点 |
| skills | JSON | 提取的技能列表 |
| keywords | JSON | 提取的关键词 |
| seniority | String | 资历要求 |
| difficulty | String | 难度评级 |
| created_at | DateTime | 创建时间 |

#### Resume（简历）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户 |
| raw_text | Text | 简历原文 |
| structured_data | JSON | 结构化数据 |
| skills | JSON | 技能列表 |
| experience_years | Integer | 工作年限 |
| is_active | Boolean | 是否为当前活跃简历 |
| created_at | DateTime | 创建时间 |

#### Application（投递记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户 |
| job_id | UUID | 目标岗位 |
| status | String | 投递状态（见状态机） |
| match_score | Float | 匹配分数 |
| applied_at | DateTime | 投递时间 |
| status_updated_at | DateTime | 状态更新时间 |
| notes | Text | 备注 |

#### AgentMemory（Agent 记忆）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户 |
| session_id | UUID | 关联会话 |
| memory_type | String | 记忆类型（short_term / long_term / reflection） |
| content | JSON | 记忆内容 |
| embedding | Vector | 向量嵌入 |
| created_at | DateTime | 创建时间 |

---

## 11. 技术选型

### 11.1 后端技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.12+ | 开发语言 |
| FastAPI | 0.136+ | Web 框架 |
| LangChain | 1.3+ | LLM 编排 |
| LangGraph | 1.2+ | Agent 工作流 |
| SQLAlchemy | 2.0+ | ORM |
| asyncpg | 0.31+ | PostgreSQL 异步驱动 |
| Alembic | 1.18+ | 数据库迁移 |
| Pydantic | 2.0+ | 数据校验 |
| Loguru | 0.7+ | 日志 |
| Playwright | 1.60+ | 浏览器自动化 |
| httpx | 0.28+ | HTTP 客户端 |

### 11.2 前端技术栈（Chrome Extension）

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue 3 | 3.5+ | UI 框架 |
| TypeScript | 6.0+ | 开发语言 |
| Vite | 8.0+ | 构建工具 |
| @crxjs/vite-plugin | 2.6+ | Chrome Extension 开发插件 |
| Pinia | 3.0+ | 状态管理 |
| Tailwind CSS | 4.3+ | 样式 |
| Lucide Vue | 0.577+ | 图标 |
| Zod | 4.4+ | 运行时校验 |
| Axios | 1.17+ | HTTP 客户端 |
| LocalForage | 1.10+ | 本地存储 |

### 11.3 基础设施

| 技术 | 用途 |
|------|------|
| PostgreSQL | 主数据库 |
| Redis | 缓存 / 会话 / 队列 |
| OpenAI / DeepSeek | LLM 服务 |
| Docker | 容器化部署 |

---

## 12. 接口设计概要

### 12.1 核心 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/refresh` | 刷新 Token |
| GET | `/api/jobs` | 获取岗位列表 |
| POST | `/api/jobs/analyze` | 触发 JD 分析 |
| GET | `/api/jobs/{id}/analysis` | 获取分析结果 |
| POST | `/api/resume/upload` | 上传简历 |
| GET | `/api/resume` | 获取简历信息 |
| POST | `/api/match/calculate` | 计算匹配度 |
| GET | `/api/match/{job_id}` | 获取匹配结果 |
| POST | `/api/agent/chat` | Agent 对话 |
| POST | `/api/agent/task` | 下发 Agent 任务 |
| GET | `/api/agent/task/{id}` | 查询任务状态 |
| POST | `/api/communication/generate` | 生成沟通话术 |
| GET | `/api/applications` | 获取投递列表 |
| POST | `/api/applications` | 创建投递记录 |
| PATCH | `/api/applications/{id}` | 更新投递状态 |
| GET | `/api/strategy/report` | 获取求职策略报告 |
| WS | `/ws/agent/{session_id}` | Agent WebSocket 连接 |

---

## 13. 开发里程碑

### Phase 1 - MVP（核心闭环）

- 用户认证系统
- 简历上传与解析
- Chrome Extension 基础框架（Boss直聘适配）
- Job Analysis Agent
- Resume Agent（匹配 + 优化建议）
- Communication Agent（打招呼生成）
- 投递记录管理

### Phase 2 - 增强

- 多平台适配（猎聘、智联、实习僧）
- Career Strategy Agent
- 面试管理
- Agent 长期记忆
- 实时状态跟踪

### Phase 3 - 优化

- 工作流自定义编排
- 简历自动优化
- 市场趋势分析
- 性能优化与压测

---

## 14. 项目亮点总结

本项目的核心亮点不在于 Agent 调用了多少工具，而在于：

1. **求职状态系统（Job State System）**：定义了完整的岗位生命周期状态机，从发现到 Offer 的每个阶段都有明确的状态、触发条件和数据记录，使 Agent 的决策有状态依据。

2. **Agent 决策系统（Agent Runtime）**：基于 LangGraph 构建的目标驱动 Agent 运行时，支持任务规划、工具调用、状态感知、反思优化和长期记忆，实现了从"信息"到"行动"的闭环决策链路。

3. **两者结合**：求职状态系统提供"世界模型"，Agent 决策系统提供"行动能力"，二者结合后项目脱离了普通 RAG 和 ChatBot 的范畴，成为真正的 Goal-Oriented Agent System。
