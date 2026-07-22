# Communication 模块数据采集修复计划

**创建时间**: 2026-07-21
**状态**: 待实施
**优先级**: P0

---

## 1. 问题概述

### 1.1 现象

Communication 模块（HR 聊天模块）无法获取真实 HR 列表，导致：
- HR 聊天功能无法测试
- 后续 AI 自动回复流程无法验证

### 1.2 当前实现

- **数据源**: 纯 DOM Selector 方案
- **选择器**: `chat_selector.ts` 中定义的 CSS 选择器
- **提取逻辑**: `chat_parser.ts` 中的 `parseConversations()` 函数
- **监听机制**: `MutationObserver` 监听 DOM 变化

---

## 2. 根因分析

### 2.1 主因：架构方案错误

Boss 直聘是 Vue.js SPA 应用，聊天列表使用**虚拟滚动**技术：

```
┌─────────────────────────────────────────────────────────────┐
│                    虚拟列表工作原理                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  完整数据（100+ HR）                                         │
│       │                                                     │
│       ▼                                                     │
│  Vue Store（存储全量数据）                                    │
│       │                                                     │
│       ▼                                                     │
│  虚拟列表组件（只渲染可视区域 ± buffer）                       │
│       │                                                     │
│       ▼                                                     │
│  DOM（只有 10-20 个 HR 元素）                                │
│                                                             │
│  ⚠️ 当前方案只读取 DOM，只能获取可视区域的部分数据              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 次因：API 拦截缺失

`interceptor.js` 当前只拦截 Job 列表 API：

```javascript
const TARGET_API_PATTERNS = [
  '/wapi/zpgeek/pc/recommend/job/list.json',
  '/wapi/zpgeek/search/job/list.json',
  '/wapi/zpgeek/job/list.json',
]

// ❌ 没有 Chat 相关 API
```

### 2.3 辅因：没有降级机制

- 没有尝试访问 Vue Store
- 没有处理虚拟列表的滚动加载
- 没有数据完整性校验

---

## 3. 目标架构

### 3.1 三级数据获取策略

```
┌─────────────────────────────────────────────────────────────┐
│                   Communication 数据架构（目标）              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Level 1: API Interceptor（优先）                           │
│  ├─ 拦截 Boss 聊天列表 API                                  │
│  ├─ 拦截聊天消息 API                                        │
│  ├─ 解析 Response，提取完整 HR 列表                          │
│  └─ 通过 postMessage 发送给 Content Script                  │
│                                                             │
│  Level 2: Runtime Store Hook（备选）                         │
│  ├─ Main World 注入                                         │
│  ├─ 访问 Vue Store / Vuex                                   │
│  └─ 直接读取 Store 中的 conversations 数据                  │
│                                                             │
│  Level 3: DOM Fallback（兜底）                               │
│  ├─ 优化当前 CSS Selector 方案                              │
│  ├─ 处理虚拟列表滚动加载                                     │
│  └─ 用于 API/Store 都失败时的降级                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 方案对比

| 方案 | 实现复杂度 | 稳定性 | 维护成本 | 数据完整性 | 推荐度 |
|------|-----------|--------|---------|-----------|--------|
| **API Interceptor** | 中 | 高 | 低 | 100% | ⭐⭐⭐⭐⭐ |
| **Runtime Store Hook** | 高 | 低 | 高 | 100% | ⭐⭐⭐ |
| **DOM Fallback** | 低 | 低 | 中 | 部分 | ⭐⭐ |

---

## 4. 实施步骤

### Phase 1: API 逆向分析（手动）

**目标**: 找到 Boss 聊天列表 API 接口

**操作步骤**:

```
1. 打开 Chrome DevTools → Network 面板
2. 过滤 Fetch/XHR 请求
3. 访问 https://www.zhipin.com/web/geek/chat
4. 观察并记录聊天列表加载的 API 请求
5. 分析 Response JSON 结构
```

**需要记录的信息**:

| 信息项 | 示例 | 用途 |
|--------|------|------|
| 完整 URL | `https://www.zhipin.com/wapi/zpgeek/chat/list` | 添加到拦截器 |
| Request Method | GET / POST | 配置拦截逻辑 |
| Response 结构 | `{ data: { list: [...] } }` | 解析数据 |
| 分页参数 | `page=1&pageSize=20` | 处理全量加载 |
| 认证参数 | Cookie / Token | 确保拦截成功 |

**输出物**: `docs/design/boss-chat-api-analysis.md`

---

### Phase 2: 扩展 API Interceptor

**目标**: 在 `interceptor.js` 中添加聊天 API 拦截

**修改文件**: `extension/public/interceptor.js`

**改动点**:

```javascript
// 1. 添加聊天 API 模式
const TARGET_API_PATTERNS = [
  // Job 列表（已有）
  '/wapi/zpgeek/pc/recommend/job/list.json',
  '/wapi/zpgeek/search/job/list.json',
  '/wapi/zpgeek/job/list.json',
  // Chat 列表（新增）
  '/wapi/zpgeek/chat/conversation/list',  // 待确认实际 URL
  '/wapi/zpgeek/chat/list',               // 待确认实际 URL
]

// 2. 新增消息类型
const MESSAGE_TYPE_JOB = 'BOSS_JOB_DATA_CAPTURED'
const MESSAGE_TYPE_CHAT = 'BOSS_CHAT_DATA_CAPTURED'  // 新增

// 3. 添加聊天数据解析函数
function parseChatListResponse(responseData) {
  // 解析 HR 列表数据
  // 返回结构化的 conversations 数组
}

// 4. 根据 URL 类型发送不同消息
function sendCapturedData(url, payload) {
  if (url.includes('chat')) {
    window.postMessage({ type: MESSAGE_TYPE_CHAT, payload }, '*')
  } else {
    window.postMessage({ type: MESSAGE_TYPE_JOB, payload }, '*')
  }
}
```

**输出物**: 更新后的 `interceptor.js`

---

### Phase 3: 更新 Content Script

**目标**: 处理从 Main World 接收的聊天数据

**修改文件**: `extension/src/content/content.ts`

**改动点**:

```typescript
// 1. 监听聊天数据消息
window.addEventListener('message', (event: MessageEvent) => {
  if (event.data?.type !== 'BOSS_CHAT_DATA_CAPTURED') return
  if (event.source !== window) return

  handleCapturedChatData(event.data.payload)
})

// 2. 处理聊天数据
function handleCapturedChatData(payload: CapturedChatPayload): void {
  // 转换为 ChatConversationItem 格式
  const conversations = parseChatConversations(payload)

  // 发送到 Service Worker
  void sendMessageToBackground(
    ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED,
    {
      conversations,
      pageUrl: window.location.href,
    }
  )
}

// 3. 新增聊天数据解析器
function parseChatConversations(payload: any): ChatConversationItem[] {
  // 将 API Response 转换为标准格式
}
```

**输出物**: 更新后的 `content.ts`

---

### Phase 4: 优化 DOM Fallback

**目标**: 作为兜底方案，处理虚拟列表场景

**修改文件**:
- `extension/src/modules/boss/chat_selector.ts`
- `extension/src/modules/boss/chat_parser.ts`
- `extension/src/modules/boss/chat_adapter.ts`

**改动点**:

```typescript
// 1. chat_adapter.ts - 添加滚动监听
observeWithScroll(callbacks: ChatAdapterCallbacks): void {
  // 监听对话列表容器的滚动事件
  const container = document.querySelector(
    CHAT_SELECTORS.conversationList.container
  )

  container?.addEventListener('scroll', debounce(() => {
    // 滚动停止后重新提取对话列表
    const conversations = this.extractConversations()
    callbacks.onConversationsUpdated?.(conversations)
  }, 500))
}

// 2. chat_parser.ts - 添加数据完整性校验
function validateConversations(conversations: BossChatConversation[]): boolean {
  // 检查必要字段是否存在
  return conversations.every(c => c.recruiterName && c.id)
}

// 3. chat_selector.ts - 更新选择器（如果 DOM 结构变化）
// 需要根据实际页面调整
```

**输出物**: 更新后的 chat 模块文件

---

### Phase 5: 测试验证

**测试场景**:

| 场景 | 验证点 | 预期结果 |
|------|--------|---------|
| 首次加载聊天页 | API 拦截是否成功 | 获取完整 HR 列表 |
| 切换对话 | 消息历史是否正确 | 显示当前对话消息 |
| 滚动加载 | 新对话是否捕获 | 动态添加到列表 |
| API 失败降级 | DOM Fallback 是否生效 | 获取可视区域数据 |
| AI 回复生成 | 端到端流程 | 成功生成并注入回复 |

**测试命令**:

```bash
# 构建扩展
cd extension
npm run build

# 加载到 Chrome
# chrome://extensions → 开发者模式 → 加载已解压的扩展 → 选择 dist/
```

**输出物**: 测试报告

---

## 5. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| Boss 聊天 API 接口变更 | 高 | 中 | 监控 API 响应格式，异常时降级到 DOM |
| API 需要特殊认证参数 | 中 | 低 | 分析 Request Headers，确保携带必要 Token |
| 虚拟列表分页加载 | 中 | 高 | 监听滚动事件，触发更多 API 请求 |
| Vue 2/3 差异影响 Store 访问 | 低 | 低 | 优先使用 API 方案，Store 访问作为备选 |
| 扩展审核被拒 | 高 | 低 | 遵循 Chrome Extension 最佳实践 |

---

## 6. 时间估算

| Phase | 任务 | 估算时间 | 依赖 |
|-------|------|---------|------|
| Phase 1 | API 逆向分析 | 0.5 天 | 无 |
| Phase 2 | 扩展 API Interceptor | 1 天 | Phase 1 |
| Phase 3 | 更新 Content Script | 0.5 天 | Phase 2 |
| Phase 4 | 优化 DOM Fallback | 0.5 天 | 无 |
| Phase 5 | 测试验证 | 0.5 天 | Phase 2-4 |
| **总计** | | **3 天** | |

---

## 7. 成功标准

- [ ] 能够获取完整的 HR 列表（不限于可视区域）
- [ ] 能够正确解析 HR 姓名、公司、最后消息
- [ ] 能够切换对话并显示消息历史
- [ ] AI 自动回复流程端到端可测试
- [ ] API 失败时 DOM Fallback 正常工作

---

## 8. 后续优化

1. **消息历史持久化**: 将聊天记录存储到后端数据库
2. **AI 回复优化**: 基于历史对话训练个性化回复
3. **多平台支持**: 扩展到智联招聘、猎聘等平台
4. **实时同步**: WebSocket 推送新消息

---

## 9. 参考资料

- Chrome Extension Manifest V3 文档
- Boss 直聘页面结构分析（`docs/design/boss-chat-dom-analysis.md`）
- 现有 Job 模块 API 拦截实现（`extension/public/interceptor.js`）
- Communication 模块设计文档（`docs/design/communication-module-spec.md`）

---

## 10. 待确认事项

- [ ] Boss 聊天 API 的实际 URL Pattern
- [ ] Boss 聊天 API 的 Response JSON 结构
- [ ] 是否需要处理分页参数
- [ ] 认证参数（Cookie/Token）是否足够
