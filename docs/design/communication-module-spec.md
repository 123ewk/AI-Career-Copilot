# 沟通模块（Communication Module）技术规格文档

> **版本**: v1.0
> **日期**: 2026-07-17
> **状态**: 设计完成，待实现
> **作者**: AI Career Copilot Team

---

## 1. 概述

### 1.1 业务目标

实现 AI 与 BOSS 直聘面试官的**半自动沟通**能力：
- 自动读取聊天页面的对话历史
- AI 基于岗位 JD + 用户简历 + 对话上下文生成建议回复
- 用户审核编辑后，一键注入到聊天输入框
- 用户手动点击发送（安全第一，不自动发送）

### 1.2 核心价值

| 痛点 | 解决方案 |
|------|----------|
| 海投时回复大量 HR 消息耗时 | AI 自动生成上下文相关回复 |
| 不知道如何回复专业问题 | AI 结合 JD 和简历给出针对性话术 |
| 担心回复不恰当 | 半自动模式，用户始终有最终控制权 |

### 1.3 设计原则

1. **安全第一**：永远不自动发送消息，用户必须手动确认
2. **上下文感知**：AI 回复基于完整对话历史 + 岗位信息 + 用户简历
3. **即时响应**：回复生成走同步端点（~2s），不走异步 MQ
4. **DOM 选择器集中管理**：所有 BOSS 直聘页面选择器集中在一个文件，便于维护

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Chrome Extension                         │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │  Content      │    │  Service      │    │  SidePanel       │  │
│  │  Script       │◄──►│  Worker       │◄──►│  (Vue 3 UI)      │  │
│  │              │    │              │    │                  │  │
│  │ chat_adapter │    │ router.ts    │    │ ChatTab.vue      │  │
│  │ chat_parser  │    │              │    │ ChatMessagePanel │  │
│  │ chat_selector│    │              │    │ ChatConvList     │  │
│  └──────┬───────┘    └──────┬───────┘    └──────────────────┘  │
│         │                   │                                   │
└─────────┼───────────────────┼───────────────────────────────────┘
          │ DOM 操作           │ HTTP API
          ▼                   ▼
┌─────────────────┐    ┌──────────────────────────────────────────┐
│  BOSS 直聘       │    │  Backend (FastAPI)                       │
│  聊天页面        │    │                                          │
│  /web/geek/chat  │    │  POST /api/communication/reply  (同步)   │
│                  │    │  POST /api/conversations/sync   (同步)   │
│                  │    │  GET  /api/conversations/       (列表)   │
│                  │    │                                          │
│                  │    │  CommunicationService.generate_reply()   │
│                  │    │  → LLM (多轮对话 prompt)                 │
│                  │    │                                          │
│                  │    │  conversations 表 (PostgreSQL)           │
└─────────────────┘    └──────────────────────────────────────────┘
```

### 2.2 数据流

```
[用户打开 BOSS 聊天页]
    │
    ▼
Content Script 检测到 /web/geek/chat
    │
    ├─ chatAdapter.extractMessages()  ─── 提取当前对话消息
    │       │
    │       ▼
    │   CHAT_MESSAGES_EXTRACTED ──────► Service Worker
    │                                       │
    │                                       ├─ POST /api/conversations/sync (持久化)
    │                                       │
    │                                       ▼
    │                                   CHAT_MESSAGES_UPDATED ──► SidePanel
    │                                                               │
    │                                                               ▼
    │                                                          展示对话列表 + 消息历史
    │                                                               │
    │                                          [用户点击 "生成回复"]
    │                                                               │
    │                                                               ▼
    │                                           REQUEST_CHAT_REPLY ──► Service Worker
    │                                                                       │
    │                                                                       ▼
    │                                                               POST /api/communication/reply
    │                                                                       │
    │                                                                       ▼
    │                                                               LLM 生成建议回复 (~2s)
    │                                                                       │
    │                                                                       ▼
    │                                                               返回 suggested_reply
    │                                                                       │
    │                                                                       ▼
    │                                                               SidePanel 展示建议
    │                                                                       │
    │                                              [用户编辑并点击 "使用此回复"]
    │                                                                       │
    │                                                                       ▼
    │                                               INJECT_CHAT_TEXT ──► Content Script
    │                                                                       │
    │                                                                       ▼
    │                                                               chatAdapter.injectText()
    │                                                                       │
    │                                                                       ▼
    │                                                               文本填入聊天输入框
    │                                                                       │
    │                                                               [用户手动点击发送]
```

---

## 3. 数据模型

### 3.1 数据库表 — `conversations`

```sql
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id          UUID REFERENCES jobs(id) ON DELETE SET NULL,
    recruiter_name  VARCHAR(100) NOT NULL,
    recruiter_id    VARCHAR(200),           -- BOSS 平台用户 ID（可选）
    channel         VARCHAR(50) NOT NULL DEFAULT 'boss',
    messages        JSONB NOT NULL DEFAULT '[]',
    last_message_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX ix_conversations_user_id ON conversations(user_id);
CREATE INDEX ix_conversations_user_job ON conversations(user_id, job_id);
CREATE INDEX ix_conversations_last_message_at ON conversations(last_message_at);
```

### 3.2 messages JSONB 结构

```json
[
  {
    "role": "user",
    "text": "您好，我对这个岗位很感兴趣",
    "timestamp": "2026-07-17T10:30:00Z"
  },
  {
    "role": "recruiter",
    "text": "你好，请问你什么时候方便面试？",
    "timestamp": "2026-07-17T11:00:00Z"
  }
]
```

### 3.3 Pydantic DTO

```python
# 消息
class ChatMessage(BaseModel):
    role: Literal["user", "recruiter"]
    text: str
    timestamp: str | None = None

# 对话上下文请求（发给 LLM 生成回复）
class ConversationContextRequest(BaseModel):
    job_id: UUID | None = None
    recruiter_name: str
    messages: list[ChatMessage]
    resume_id: UUID | None = None
    tone: Literal["natural", "formal", "enthusiastic"] = "natural"

# AI 回复响应
class ConversationReplyResponse(BaseModel):
    suggested_reply: str
    conversation_id: UUID | None = None

# 消息同步请求
class ConversationSyncRequest(BaseModel):
    job_id: UUID | None = None
    recruiter_name: str
    messages: list[ChatMessage]

# 对话摘要（列表用）
class ConversationSummary(BaseModel):
    id: UUID
    recruiter_name: str
    job_id: UUID | None
    channel: str
    last_message: str | None
    last_message_at: datetime | None
    message_count: int

# 对话详情
class ConversationDetail(BaseModel):
    id: UUID
    user_id: UUID
    job_id: UUID | None
    recruiter_name: str
    channel: str
    messages: list[ChatMessage]
    created_at: datetime
    updated_at: datetime
```

---

## 4. API 端点

### 4.1 `POST /api/communication/reply` — 生成对话回复（同步）

**设计理由：** 用户在聊天中主动等待，需即时响应。LLM 调用约 2 秒，同步可接受。

```
请求:
POST /api/communication/reply
Content-Type: application/json
Authorization: Bearer <token>

{
  "job_id": "uuid-optional",
  "recruiter_name": "张女士",
  "messages": [
    {"role": "user", "text": "您好，我对这个岗位很感兴趣"},
    {"role": "recruiter", "text": "你好，请问你什么时候方便面试？"}
  ],
  "resume_id": "uuid-optional",
  "tone": "natural"
}

响应: 200 OK
{
  "suggested_reply": "您好张女士，我这周三下午和周五全天都有空，您看哪个时间方便？",
  "conversation_id": "uuid"
}
```

| 项目 | 说明 |
|------|------|
| 方法 | POST |
| 路径 | `/api/communication/reply` |
| 认证 | JWT Bearer Token |
| 状态码 | 200（同步返回）/ 400（参数错误）/ 401（未认证）/ 502（LLM 调用失败） |
| 限流 | 与 `/api/jobs/` 共享 60 req/min/user |

### 4.2 `POST /api/conversations/sync` — 同步对话消息

```
请求:
POST /api/conversations/sync
Content-Type: application/json

{
  "job_id": "uuid-optional",
  "recruiter_name": "张女士",
  "messages": [
    {"role": "user", "text": "...", "timestamp": "..."},
    {"role": "recruiter", "text": "...", "timestamp": "..."}
  ]
}

响应: 200 OK
{
  "conversation_id": "uuid",
  "message_count": 2
}
```

**幂等性：** 按 `(user_id, job_id, recruiter_name)` 查找或创建，messages 全量覆盖（DOM 快照）。

### 4.3 `GET /api/conversations/` — 对话列表

```
请求:
GET /api/conversations/?limit=20&offset=0

响应: 200 OK
{
  "items": [
    {
      "id": "uuid",
      "recruiter_name": "张女士",
      "job_id": "uuid",
      "channel": "boss",
      "last_message": "你好，请问你什么时候方便面试？",
      "last_message_at": "2026-07-17T11:00:00Z",
      "message_count": 4
    }
  ],
  "total": 15,
  "limit": 20,
  "offset": 0
}
```

### 4.4 `GET /api/conversations/{id}` — 对话详情

```
请求:
GET /api/conversations/{conversation_id}

响应: 200 OK
{
  "id": "uuid",
  "user_id": "uuid",
  "job_id": "uuid",
  "recruiter_name": "张女士",
  "channel": "boss",
  "messages": [
    {"role": "user", "text": "...", "timestamp": "..."},
    {"role": "recruiter", "text": "...", "timestamp": "..."}
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 5. LLM Prompt 设计

### 5.1 回复生成 Prompt

```python
_REPLY_SYSTEM_PROMPT = """你是一个求职助手，正在帮用户在 BOSS 直聘上与招聘方沟通。

你的角色：
- 以求职者的身份回复招聘方的消息
- 语气自然、礼貌、专业
- 回复简洁（一般不超过 100 字）
- 不要过度热情或卑微
- 如果招聘方问了具体问题（如到岗时间、期望薪资），根据简历信息如实回答
- 如果不确定某个信息，建议用户确认后再回复

输出格式（JSON）：
{
  "reply": "你生成的回复文本"
}
"""

_REPLY_USER_PROMPT = """
{job_context}
{resume_context}

=== 对话历史 ===
{conversation_history}

=== 任务 ===
请根据以上对话历史和背景信息，生成对招聘方最新消息的回复。

招聘方最新消息：{last_recruiter_message}

语气风格：{tone_description}
"""
```

### 5.2 tone 参数映射

| tone | 中文描述 |
|------|----------|
| `natural` | 自然随意，像正常聊天 |
| `formal` | 正式商务，适合大公司 |
| `enthusiastic` | 积极热情，表达强烈兴趣 |

---

## 6. Chrome 扩展设计

### 6.1 BOSS 直聘聊天页 DOM 结构（预期）

> **注意：** 以下选择器为预期值，需在真实页面上验证并调优。

```
/web/geek/chat
├── .chat-conversation          # 左侧对话列表容器
│   └── .conversation-item      # 单个对话
│       ├── .conv-name          # HR 名称
│       ├── .conv-last-msg      # 最后消息预览
│       ├── .conv-unread        # 未读标记
│       └── .conv-active        # 选中态 class
│
├── .chat-message               # 右侧消息历史容器
│   └── .message-item           # 单条消息
│       ├── .message-text       # 消息文本
│       ├── .message-time       # 时间
│       ├── .message-sent       # 用户发送（class）
│       └── .message-received   # HR 发送（class）
│
└── .chat-input                 # 底部输入区
    ├── .input-box              # 输入框（contenteditable 或 textarea）
    └── .send-button            # 发送按钮
```

### 6.2 消息类型定义

```typescript
// Content Script → SW
CHAT_MESSAGES_EXTRACTED: {
  conversationId: string
  recruiterName: string
  messages: ChatMessage[]
  pageUrl: string
}

CHAT_PAGE_DETECTED: {
  pageUrl: string
  recruiterName: string
}

// SW → Content Script
INJECT_CHAT_TEXT: {
  text: string
}

// SidePanel → SW
REQUEST_CHAT_REPLY: {
  jobId?: string
  recruiterName: string
  messages: ChatMessage[]
  resumeId?: string
  tone?: 'natural' | 'formal' | 'enthusiastic'
}

INJECT_CHAT_TEXT_FROM_SIDEPANEL: {
  text: string
}

// SW → SidePanel（广播）
CHAT_MESSAGES_UPDATED: {
  conversationId: string
  recruiterName: string
  messages: ChatMessage[]
  pageUrl: string
}
```

### 6.3 Content Script 聊天页处理流程

```typescript
// content.ts 中新增聊天页分支
if (pageInfo.type === 'chat') {
  // 1. 通知 SW 检测到聊天页
  sendMessageToBackground(CHAT_PAGE_DETECTED, {
    pageUrl: currentUrl,
    recruiterName: chatAdapter.extractRecruiterName()
  })

  // 2. 初始提取消息
  const messages = chatAdapter.extractMessages()
  if (messages.length > 0) {
    sendMessageToBackground(CHAT_MESSAGES_EXTRACTED, {
      conversationId: generateId(),
      recruiterName: chatAdapter.extractRecruiterName(),
      messages,
      pageUrl: currentUrl
    })
  }

  // 3. MutationObserver 监听新消息
  chatAdapter.observe({
    onMessagesChanged: (msgs) => {
      sendMessageToBackground(CHAT_MESSAGES_EXTRACTED, {
        conversationId: generateId(),
        recruiterName: chatAdapter.extractRecruiterName(),
        messages: msgs,
        pageUrl: currentUrl
      })
    }
  })

  // 4. 监听注入指令
  onMessage(INJECT_CHAT_TEXT, ({ text }) => {
    chatAdapter.injectText(text)
  })
}
```

### 6.4 ChatAdapter 注入策略

```typescript
injectText(text: string): boolean {
  const inputBox = document.querySelector(CHAT_SELECTORS.chatInput.inputBox)
  if (!inputBox) return false

  // 方案 A: contenteditable div
  if (inputBox.getAttribute('contenteditable') === 'true') {
    inputBox.textContent = text
    inputBox.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  }

  // 方案 B: textarea
  if (inputBox.tagName === 'TEXTAREA') {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, 'value'
    )?.set
    nativeInputValueSetter?.call(inputBox, text)
    inputBox.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  }

  return false
}
```

### 6.5 组件层级

```
App.vue
  └── ChatTab (v-else-if="status === 'ready' && activeTab === 'chat'")
        ├── ChatConversationList.vue (width: 40%)
        │     ├── 对话卡片[]（HR 名 + 最后消息 + 未读标记）
        │     └── 空状态："请打开 BOSS 直聘聊天页"
        └── ChatMessagePanel.vue (width: 60%)
              ├── 头部：HR 名 + 岗位信息
              ├── 消息气泡[]（区分 user/recruiter 样式）
              ├── AI 回复区：
              │     ├── 可编辑 textarea（默认展示 AI 建议）
              │     ├── "生成回复" 按钮（调 REQUEST_CHAT_REPLY）
              │     └── "使用此回复" 按钮（调 INJECT_CHAT_TEXT）
              └── 状态指示：生成中 spinner / 错误 + 重试 / 空提示
```

### 6.6 Store 设计

```typescript
// stores/communication.ts
useCommunicationStore = defineStore('communication', () => {
  // === State ===
  conversations = ref<ChatConversation[]>([])
  activeConversationId = ref<string | null>(null)
  suggestedReply = ref<{ text: string, isGenerating: boolean, error: string | null }>({
    text: '', isGenerating: false, error: null
  })
  isOnChatPage = ref(false)

  // === Computed ===
  activeConversation = computed(() => conversations.find(c => c.id === activeConversationId))
  activeMessages = computed(() => activeConversation.value?.messages ?? [])

  // === Actions ===
  setOnChatPage(isOn: boolean)            // PAGE_CHANGED 时更新
  updateFromExtracted(data)               // CHAT_MESSAGES_EXTRACTED 时更新
  setActiveConversation(id | null)        // 用户点击对话列表
  requestReply(conversationId)            // → sendMessageToBackground(REQUEST_CHAT_REPLY)
  updateSuggestedReply(text)              // 用户编辑 textarea
  injectReply()                           // → sendMessageToBackground(INJECT_CHAT_TEXT_FROM_SIDEPANEL)
  clearSuggestedReply()                   // 注入后重置
  loadFromStorage()                       // 启动时恢复
  saveToStorage()                         // 变更时持久化
})
```

---

## 7. 文件清单

### 7.1 后端新建

| 文件 | 职责 |
|------|------|
| `backend/app/infra/database/models/conversation.py` | Conversation ORM 模型 |
| `backend/app/domain/repositories/conversation.py` | Repository Protocol 接口 |
| `backend/app/infra/repositories/conversation_repo.py` | Repository SQLAlchemy 实现 |
| `backend/app/api/routers/conversation.py` | 对话 CRUD 端点（list / get / sync） |
| `backend/migrations/versions/xxx_add_conversations.py` | Alembic 迁移脚本 |

### 7.2 后端修改

| 文件 | 改动 |
|------|------|
| `backend/app/domain/communication/models.py` | 新增 ChatMessage / ConversationContextRequest / ConversationReplyResponse / ConversationSyncRequest / ConversationSummary / ConversationDetail |
| `backend/app/domain/communication/service.py` | 新增 `generate_reply()` + `sync_messages()` + `_REPLY_SYSTEM_PROMPT` + `_REPLY_USER_PROMPT` |
| `backend/app/api/routers/communication.py` | 新增 `POST /reply` 同步端点 |
| `backend/main.py` | 注册 conversation router |

### 7.3 扩展端新建

| 文件 | 职责 |
|------|------|
| `extension/src/modules/boss/chat_selector.ts` | 聊天页 CSS 选择器注册表 |
| `extension/src/modules/boss/chat_parser.ts` | 聊天数据解析（对话列表 + 消息历史） |
| `extension/src/modules/boss/chat_adapter.ts` | 聊天页适配器（提取 + 监听 + 注入） |
| `extension/src/types/communication.ts` | TS 类型定义 |
| `extension/src/stores/communication.ts` | Pinia store |
| `extension/src/components/sidepanel/ChatTab.vue` | 聊天 Tab 容器 |
| `extension/src/components/sidepanel/ChatConversationList.vue` | 左栏：对话列表 |
| `extension/src/components/sidepanel/ChatMessagePanel.vue` | 右栏：消息 + AI 回复 |

### 7.4 扩展端修改

| 文件 | 改动 |
|------|------|
| `extension/src/modules/boss/selector.ts` | BossPageType 加 `'chat'` |
| `extension/src/modules/boss/adapter.ts` | detect() 识别 `/web/geek/chat` |
| `extension/src/messaging/chrome_message.ts` | 新增 6 个消息类型 + payload |
| `extension/src/background/router.ts` | 新增 3 个 handler |
| `extension/src/content/content.ts` | 聊天页分支：检测 + 提取 + 注入 |
| `extension/src/App.vue` | 渲染 ChatTab + 注册消息监听 |
| `extension/src/components/sidepanel/TabNav.vue` | chat tab enabled: true |
| `extension/src/stores/sidepanel.ts` | PersistedState 版本 bump |

---

## 8. 实现顺序

```
WP1: DB 迁移（conversations 表）
  │
  ▼
WP2: 后端 API（conversation repo + service + endpoints）    WP3: DOM 选择器 + 适配器
  │                                                                │ (并行)
  ▼                                                                ▼
WP4: Content Script 聊天页处理 ◄──────────────────────────────────┘
  │
  ▼
WP5: SidePanel UI（Store + 组件）
  │
  ▼
WP6: SW Handler（消息路由）
  │
  ▼
WP7: 集成测试 + DOM 选择器调优
```

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| BOSS 直聘 DOM 结构变更 | 选择器失效，无法提取消息 | 选择器集中管理 + 健康检查函数 + 降级提示 |
| 聊天页输入框类型不确定 | 注入失败 | 同时支持 contenteditable 和 textarea，注入失败时提示用户手动粘贴 |
| LLM 回复质量不稳定 | 建议回复不恰当 | 用户可编辑 + 可重新生成 + tone 参数微调 |
| 限流 60 req/min | 高频聊天时被限流 | 回复生成走同步端点（单次请求），消息同步可做 debounce |
| Content Script 未注入 | 聊天页功能不可用 | 复用 `ensureBossContentScriptInjected` 兜底 |

---

## 10. MVP 范围边界

### 包含

- [x] 读取 BOSS 直聘聊天页 DOM 消息
- [x] 基于对话历史 + 岗位 + 简历生成上下文 AI 回复
- [x] 注入建议文本到聊天输入框供用户审核
- [x] 用户手动点击发送（安全第一）
- [x] 对话历史持久化到后端
- [x] 对话列表和消息历史展示

### 不包含（后续迭代）

- [ ] 自动发送消息
- [ ] 多标签页并发聊天管理
- [ ] 对话分析 / 情感分析 / 意图识别
- [ ] template_manager / compliance_checker 实现
- [ ] Email / WeChat / webhook 工具
- [ ] 浏览器自动化工具（click/input/extract/scroll）
- [ ] WebSocket 实时同步
- [ ] 聊天页 API 拦截（DOM 提取足够 MVP）
- [ ] 多平台支持（智联/猎聘/实习僧）

---

## 11. 验收标准

1. `npm run build` 编译通过，无 TypeScript 错误
2. `alembic upgrade head` 迁移成功，`conversations` 表创建
3. `POST /api/communication/reply` 返回 AI 生成的回复
4. `POST /api/conversations/sync` 正确持久化对话消息
5. 打开 BOSS 聊天页 → SidePanel 沟通 Tab 显示对话列表
6. 点击"生成回复" → 2 秒内显示 AI 建议
7. 点击"使用此回复" → 聊天输入框被填入文本
8. 输入框文本可编辑，用户可修改后手动发送
