# 沟通模块 Beta 阶段规格文档

> **版本**: v0.1
> **日期**: 2026-07-18
> **状态**: 设计草案
> **前置依赖**: MVP 沟通模块（`docs/design/communication-module-spec.md` v1.1）

---

## 1. Beta 目标

在 MVP「用户主动切换 + 可选自动发送」的基础上，升级为**AI 主动驱动的多对话并发管理**：

| MVP | Beta |
|-----|------|
| 用户手动切换对话 | AI 自动监测新消息，批量预生成回复队列 |
| 用户逐个点击"生成回复" | AI 批量预生成，用户逐个审核 |
| 简单的开/关自动发送 | 智能分流：简单问题自动发，复杂问题转人工 |
| 发送即确认 | 3s 撤回窗口 |
| 纯 LLM 生成 | 模板优先匹配 + LLM 兜底 |
| 无数据分析 | 对话转化率 + HR 意图识别 |

---

## 2. 核心功能

### 2.1 后台批量预生成

**问题：** MVP 中用户需逐个对话点击"生成回复"，等待 ~2s，海投场景下效率低。

**方案：** AI 自动监测所有活跃对话的新消息，批量预生成回复队列。

```
[新消息到达 HR-A]
    │
    ▼
Content Script MutationObserver 检测到新 recruiter 消息
    │
    ├─ NEW_RECRUITER_MESSAGE ──► Service Worker
    │                               │
    │                               ├─ 入队到 replyQueue
    │                               │
    │                               ▼
    │                           后台异步处理队列：
    │                           ├─ 检查模板匹配（优先）
    │                           ├─ 模板未匹配 → 调用 LLM 生成
    │                           ├─ 判断复杂度 → 自动/审核分流
    │                           │
    │                           ▼
    │                       REPLY_READY ──► SidePanel
    │                                         │
    │                                         ▼
    │                                     对话卡片显示待处理数量
    │                                     用户点击 → 展示预生成回复
    │
[同时 HR-B 也有新消息]
    │
    └─ 同样流程，并发入队处理
```

**队列设计：**

```typescript
// Service Worker 内存队列
interface ReplyQueueItem {
  conversationId: string
  recruiterName: string
  jobId?: string
  newMessage: ChatMessage       // 最新的 recruiter 消息
  status: 'pending' | 'generating' | 'ready' | 'auto_sent' | 'failed'
  suggestedReply?: string
  confidence?: number           // AI 置信度 0-1
  matchedTemplate?: string      // 匹配到的模板名称
  intent?: HRIntent             // HR 意图标签
  createdAt: number
}

// 队列消费：串行处理（prefetch=1 语义），避免 LLM 并发风暴
let replyQueue: ReplyQueueItem[] = []
let isProcessing = false

async function processQueue() {
  if (isProcessing || replyQueue.length === 0) return
  isProcessing = true
  const item = replyQueue.shift()!
  try {
    // 1. 模板匹配
    const templateMatch = matchTemplate(item.newMessage.text)
    if (templateMatch) {
      item.suggestedReply = templateMatch.text
      item.matchedTemplate = templateMatch.name
      item.confidence = 0.95
    } else {
      // 2. LLM 生成
      const result = await fetchBackend('/api/communication/reply', { ... })
      item.suggestedReply = result.suggested_reply
      item.confidence = result.confidence
    }
    // 3. 智能分流
    if (item.confidence >= autoSendThreshold && isSimpleMessage(item.newMessage.text)) {
      item.status = 'auto_sent'
      injectAndSend(item.conversationId, item.suggestedReply!)
    } else {
      item.status = 'ready'
    }
    broadcastToSidePanel(REPLY_READY, item)
  } catch (err) {
    item.status = 'failed'
  } finally {
    isProcessing = false
    processQueue() // 处理下一个
  }
}
```

**SidePanel UI 变化：**

```
ChatConversationList.vue
├── 对话卡片
│     ├── HR 名 + 最后消息
│     ├── 🔵 徽标：待处理回复数量
│     └── 🟢/🔴 指示灯：已自动发送 / 待审核
└── 顶部统计栏
      └── "3 个待审核 | 2 个已自动发送"
```

---

### 2.2 智能分流（自动/审核）

**问题：** MVP 的自动发送是全局开关，不区分消息复杂度。

**方案：** AI 判断消息类型和回复复杂度，自动决定分流路径。

**分流规则：**

| 消息类型 | 示例 | 分流 | 理由 |
|----------|------|------|------|
| 简单确认 | "明天下午2点面试可以吗？" | 自动发送 | 标准回复，低风险 |
| 时间地点 | "你在哪里？什么时候到岗？" | 自动发送 | 简历中有信息 |
| 技术问题 | "你会 React 吗？做过什么项目？" | 人工审核 | 需要个性化回答 |
| 薪资谈判 | "你的期望薪资是多少？" | 人工审核 | 高敏感 |
| 模糊/复杂 | "你觉得这个岗位怎么样？" | 人工审核 | 需要上下文理解 |

**实现：**

```python
# 后端 LLM 响应增加 confidence 和 intent 字段
class ConversationReplyResponse(BaseModel):
    suggested_reply: str
    conversation_id: UUID | None = None
    confidence: float = Field(ge=0, le=1, description="AI 置信度 0-1")
    intent: str = Field(description="HR 意图分类")
    complexity: Literal["simple", "moderate", "complex"] = "moderate"
```

```typescript
// Service Worker 分流逻辑
function shouldAutoSend(item: ReplyQueueItem): boolean {
  // 1. 用户未开启自动发送 → 始终审核
  if (!autoSendEnabled) return false
  // 2. 置信度低于阈值 → 审核
  if ((item.confidence ?? 0) < autoSendThreshold) return false
  // 3. 复杂度为 complex → 审核
  if (item.complexity === 'complex') return false
  // 4. 高敏感意图 → 审核
  if (['salary_negotiation', 'technical_question'].includes(item.intent ?? '')) return false
  return true
}
```

**用户可配置：**
- 自动发送开关（全局）
- 置信度阈值滑块（默认 0.8，范围 0.5-1.0）
- 哪些意图类型允许自动发送（checkbox 列表）

---

### 2.3 3 秒撤回窗口

**问题：** 自动发送后发现内容不恰当，无法撤回。

**方案：** 自动发送后显示 3s 倒计时 toast，用户可点击"撤回"取消。

```
┌─────────────────────────────────────────────┐
│  ✅ 已自动发送给 张女士                       │
│  "好的，明天下午2点见！"                      │
│                                             │
│  ████████░░░░  3s 内可撤回  [撤回]           │
│                                             │
│  3s 后自动消失，消息正式发送                  │
└─────────────────────────────────────────────┘
```

**实现策略：**

```
[AI 生成回复] → [注入输入框] → [显示倒计时 toast]
                                      │
                          ┌───────────┼───────────┐
                          │           │           │
                      用户点击撤回   3s 到期    用户无操作
                          │           │           │
                          ▼           ▼           ▼
                      清空输入框   clickSend()   clickSend()
                      降级为审核   正式发送      正式发送
                      标记为待审核
```

**技术细节：**

```typescript
// Content Script 侧
class ChatAdapter {
  private pendingSend: { timer: ReturnType<typeof setTimeout>, text: string } | null = null

  injectAndScheduleSend(text: string, delayMs = 3000): boolean {
    // 1. 注入文本
    if (!this.injectText(text)) return false

    // 2. 显示倒计时 toast（通过消息通知 SidePanel）
    sendMessageToBackground(AUTO_SEND_COUNTDOWN, {
      conversationId: this.activeConversationId,
      text,
      countdownMs: delayMs
    })

    // 3. 延迟发送
    const timer = setTimeout(() => {
      this.clickSend()
      this.pendingSend = null
      sendMessageToBackground(AUTO_SEND_CONFIRMED, { conversationId: this.activeConversationId })
    }, delayMs)

    this.pendingSend = { timer, text }
    return true
  }

  cancelPendingSend(): boolean {
    if (!this.pendingSend) return false
    clearTimeout(this.pendingSend.timer)
    this.clearInput()
    this.pendingSend = null
    return true
  }
}
```

**新增消息类型：**

```typescript
AUTO_SEND_COUNTDOWN: {
  conversationId: string
  text: string
  countdownMs: number
}
AUTO_SEND_CANCEL: {
  conversationId: string
}
AUTO_SEND_CONFIRMED: {
  conversationId: string
}
```

---

### 2.4 模板 + LLM 混合

**问题：** 所有回复都调用 LLM，简单回复浪费时间和 API 配额。

**方案：** 用户可创建自定义模板，AI 优先模板匹配，匹配不到再调 LLM。

**模板数据模型：**

```python
# 后端新增表
CREATE TABLE reply_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        VARCHAR(100) NOT NULL,
    content     TEXT NOT NULL,
    category    VARCHAR(50),           -- 'greeting' | 'confirm' | 'decline' | 'custom'
    keywords    JSONB DEFAULT '[]',    -- 匹配关键词
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**内置模板（系统预设）：**

| 名称 | 内容 | 触发关键词 |
|------|------|-----------|
| 确认面试 | "好的，{time}可以，我会准时到的。" | 面试、时间、几点 |
| 咨询详情 | "您好，我想了解一下这个岗位的具体工作内容和要求。" | 岗位详情、工作内容 |
| 表达兴趣 | "您好，我对这个岗位很感兴趣，希望能进一步了解。" | 初次、打招呼 |
| 婉拒 | "感谢您的邀请，但这个岗位与我的职业规划不太匹配，祝您找到合适的人选。" | 不合适、婉拒 |

**匹配算法：**

```python
def match_template(message_text: str, templates: list[ReplyTemplate]) -> ReplyTemplate | None:
    """关键词匹配 + 相似度排序"""
    candidates = []
    for tpl in templates:
        if not tpl.is_active:
            continue
        score = 0
        for kw in tpl.keywords:
            if kw in message_text:
                score += 1
        if score > 0:
            candidates.append((score, tpl))

    if not candidates:
        return None

    # 按匹配关键词数降序
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]

    # 阈值：至少匹配 1 个关键词
    return best[1] if best[0] >= 1 else None
```

**模板管理 UI（SidePanel 新增 TemplateTab 或嵌入 ChatTab）：**

```
模板管理面板
├── 模板列表
│     ├── 确认面试 [编辑] [删除]
│     ├── 咨询详情 [编辑] [删除]
│     └── + 新建模板
├── 内置模板（不可删除，可编辑）
└── 模板预览：点击展开查看完整内容
```

---

### 2.5 HR 意图识别

**问题：** 用户不知道哪些对话紧急，哪些可以稍后处理。

**方案：** AI 在生成回复时同步识别 HR 意图，在对话列表中标注意图标签。

**意图分类：**

| 意图 | 标签 | 优先级 | 颜色 |
|------|------|--------|------|
| 约面试 | `schedule_interview` | 高 | 🟠 橙色 |
| 谈薪资 | `salary_negotiation` | 高 | 🔴 红色 |
| 发 Offer | `offer` | 最高 | 🟢 绿色 |
| 继续了解 | `continue_screening` | 中 | 🔵 蓝色 |
| 拒绝 | `rejected` | 低 | ⚫ 灰色 |
| 闲聊/模糊 | `unclear` | 低 | ⚪ 白色 |

**实现：**

```python
# LLM prompt 增加意图识别要求
_REPLY_SYSTEM_PROMPT = """...existing prompt...

额外要求：分析招聘方最新消息的意图，输出 intent 字段：
- schedule_interview: 约面试时间
- salary_negotiation: 讨论薪资福利
- offer: 发出录用通知
- continue_screening: 继续了解候选人
- rejected: 婉拒候选人
- unclear: 意图不明确

输出格式：
{
  "reply": "你生成的回复文本",
  "intent": "schedule_interview",
  "confidence": 0.85,
  "complexity": "simple"
}
"""
```

**SidePanel 展示：**

```
ChatConversationList.vue
├── 对话卡片
│     ├── 🟠 张女士 — "明天下午2点面试可以吗？"
│     │   [约面试] 标签 + 🔵 1 条待处理
│     ├── 🔵 李先生 — "你的项目经历能详细说说吗？"
│     │   [继续了解] 标签
│     └── ⚫ 王女士 — "感谢关注，暂时不招了"
│         [已拒绝] 标签
└── 顶部筛选栏
      ├── [全部] [待审核] [已自动发送] [已拒绝]
      └── 排序：按优先级 / 按时间
```

---

### 2.6 对话转化分析

**问题：** 用户不知道自己的求职策略是否有效，哪些话术更成功。

**方案：** 统计对话转化漏斗，帮用户优化求职策略。

**数据模型：**

```python
# conversations 表新增字段
ALTER TABLE conversations ADD COLUMN stage VARCHAR(50) DEFAULT 'initial_contact';
-- stage: initial_contact → screening → interview_scheduled → offered → rejected

ALTER TABLE conversations ADD COLUMN auto_replied_count INTEGER DEFAULT 0;
ALTER TABLE conversations ADD COLUMN manual_replied_count INTEGER DEFAULT 0;
ALTER TABLE conversations ADD COLUMN first_response_at TIMESTAMPTZ;
```

**分析维度：**

| 指标 | 计算方式 | 意义 |
|------|----------|------|
| 回复率 | 有回复的对话 / 总对话 | 用户活跃度 |
| 平均响应时间 | 首次回复时间 - HR 首次消息时间 | 响应速度 |
| 转化率 | 进入面试阶段 / 总对话 | 求职效率 |
| 自动发送占比 | 自动回复数 / 总回复数 | AI 辅助程度 |
| 意图分布 | 各意图类型占比 | 了解 HR 关注点 |

**SidePanel 展示（新增 StatsTab 或嵌入 ChatTab）：**

```
对话转化分析
├── 漏斗图
│     总对话: 25
│     ├─ 初次沟通: 25 (100%)
│     ├─ 筛选阶段: 18 (72%)
│     ├─ 约面试: 8 (32%)
│     ├─ Offer: 2 (8%)
│     └─ 拒绝: 7 (28%)
├── 响应速度
│     平均首次回复: 4.2 分钟
│     最快: 12 秒（自动发送）
│     最慢: 2 小时
├── 话术效果
│     使用模板: 15 次 → 转化率 35%
│     使用 LLM: 10 次 → 转化率 25%
└── 时间分布
      上午 9-12: 最活跃
      下午 14-17: 次活跃
```

---

## 3. 后端变更

### 3.1 新增端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /api/communication/analysis` | GET | 对话转化分析数据 |
| `GET /api/templates/` | GET | 用户模板列表 |
| `POST /api/templates/` | POST | 创建模板 |
| `PUT /api/templates/{id}` | PUT | 更新模板 |
| `DELETE /api/templates/{id}` | DELETE | 删除模板 |

### 3.2 LLM 响应扩展

```python
class ConversationReplyResponse(BaseModel):
    suggested_reply: str
    conversation_id: UUID | None = None
    confidence: float = 0.0        # 新增：AI 置信度
    intent: str = "unclear"        # 新增：HR 意图
    complexity: str = "moderate"   # 新增：回复复杂度
    matched_template: str | None = None  # 新增：匹配的模板名
```

### 3.3 新增表

| 表 | 用途 |
|------|------|
| `reply_templates` | 用户自定义回复模板 |

---

## 4. 扩展端变更

### 4.1 新增消息类型

```typescript
NEW_RECRUITER_MESSAGE: { conversationId, message: ChatMessage, pageUrl }
REPLY_READY: { conversationId, suggestedReply, confidence, intent, matchedTemplate }
AUTO_SEND_COUNTDOWN: { conversationId, text, countdownMs }
AUTO_SEND_CANCEL: { conversationId }
AUTO_SEND_CONFIRMED: { conversationId }
BATCH_REPLY_STATUS: { pending: number, ready: number, autoSent: number }
```

### 4.2 新增/修改文件

| 文件 | 改动 |
|------|------|
| `extension/src/background/reply_queue.ts` | 新建：回复队列管理器 |
| `extension/src/background/template_matcher.ts` | 新建：模板匹配逻辑 |
| `extension/src/stores/communication.ts` | 扩展：队列状态、模板管理、分析数据 |
| `extension/src/components/sidepanel/ChatConversationList.vue` | 扩展：意图标签、待处理徽标、筛选排序 |
| `extension/src/components/sidepanel/ChatMessagePanel.vue` | 扩展：撤回 toast、模板选择 |
| `extension/src/components/sidepanel/TemplatePanel.vue` | 新建：模板管理面板 |
| `extension/src/components/sidepanel/StatsPanel.vue` | 新建：转化分析面板 |
| `extension/src/modules/boss/chat_adapter.ts` | 扩展：`injectAndScheduleSend()` + `cancelPendingSend()` |

---

## 5. 实现顺序

```
Beta-1: 模板系统（后端表 + CRUD + 模板匹配）
    │
    ▼
Beta-2: LLM 响应扩展（confidence + intent + complexity）
    │
    ▼
Beta-3: 后台回复队列（reply_queue.ts + 智能分流）
    │
    ▼
Beta-4: 3s 撤回窗口（chat_adapter 扩展 + toast UI）
    │
    ▼
Beta-5: HR 意图识别 UI（对话列表标签 + 筛选排序）
    │
    ▼
Beta-6: 对话转化分析（后端聚合 + StatsPanel）
    │
    ▼
Beta-7: 集成测试 + 用户验收
```

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 后台队列 LLM 并发调用 | API 限流 / 费用飙升 | 串行处理队列，debounce 500ms，用户可设并发上限 |
| 模板匹配误命中 | 用错模板回复 HR | 匹配结果展示给用户确认，不直接自动发送 |
| 意图识别不准 | 标签错误导致优先级误判 | 意图标签仅作参考，用户可手动修正 |
| 3s 撤回窗口网络延迟 | 撤回指令到达时已发送 | Content Script 侧先清空输入框，再通知 SW；极端情况仍可能误发 |
| 转化分析数据不足 | 统计无意义 | 需积累至少 20 条对话才展示分析 |

---

## 7. MVP → Beta 升级清单

| MVP 功能 | Beta 升级 |
|----------|-----------|
| 用户手动点击"生成回复" | 后台自动预生成，用户审核队列 |
| 全局自动发送开关 | 智能分流：简单自动发，复杂转人工 |
| 发送即确认 | 3s 撤回窗口 |
| 纯 LLM 生成 | 模板优先 + LLM 兜底 |
| 对话列表（HR 名 + 最后消息） | 意图标签 + 优先级 + 筛选排序 |
| 无统计 | 对话转化漏斗 + 响应速度 + 话术效果 |
