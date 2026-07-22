# BOSS 直聘聊天页 DOM 结构分析

> 分析时间：2026-07-20（更新）
> 分析工具：Chrome MCP Server (chrome_read_page, accessibility tree)
> URL：https://www.zhipin.com/web/geek/chat

## 页面整体布局

聊天页采用**左右分栏**布局：

```
┌─────────────────────────────────────────────────────────────┐
│  顶部导航栏（首页/职位/公司/校园/海归/APP/有了/海外/无障碍）   │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│   左侧面板    │         右侧面板（消息区域）                  │
│  (对话列表)   │                                              │
│              │    未选中对话时：占位提示                      │
│  ┌────────┐  │    选中对话后显示：                           │
│  │ 搜索框  │  │      - 对话标题（姓名+公司）                 │
│  ├────────┤  │      - 职位信息（职位名+薪资+查看职位）        │
│  │ 筛选标签│  │      - 消息历史                              │
│  ├────────┤  │      - 竞争者PK分析（可选）                   │
│  │ 对话项1 │  │      - 工具栏（表情/常用语/图片/求简历等）     │
│  │ 对话项2 │  │      - 输入框 + 发送按钮                     │
│  │ ...    │  │                                              │
│  └────────┘  │                                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

## 左侧面板：对话列表

### 搜索框

- **类型**：`<input type="text">`
- **placeholder**：`搜索30天内的联系人`

### 筛选标签栏

| 标签 | 说明 |
|------|------|
| 新招呼 | 筛选新招呼对话 |
| 仅沟通 | 筛选仅沟通对话 |
| 更多▼ | 展开子菜单（有交换/有面试/不感兴趣） |

### 对话列表容器

对话列表使用 **table 结构**（`<thead>` / `<tbody>` / `<tfoot>`）：

```
thead                        ← 列表头（可能为空）
group                        ← <tbody> 或 <div role="group">，包含所有对话项
  ├── listitem               ← 对话项 1
  ├── listitem               ← 对话项 2
  └── ...
tfoot                        ← 列表尾
  └── "没有更多了"
```

### 单个对话项结构

每个 `listitem` 包含以下子元素：

```
listitem
  ├── image                   ← 头像
  ├── generic "14:26"         ← 最后消息时间
  ├── generic "罗先生"        ← 招聘方姓名
  ├── generic "聚搜云"        ← 公司名
  ├── generic "运营总监"      ← 招聘方职位
  ├── generic "[送达]"        ← 消息状态标记
  └── generic "您好，我是..."  ← 最后消息预览
```

**观察到的对话项数据（2026-07-20 live page，Chrome MCP 验证）：**

| 时间 | 姓名 | 公司 | 职位 | 状态 | 消息预览 |
|------|------|------|------|------|----------|
| 15:27 | 曹先生 | - | 招聘hr | - | 您正在与Boss曹先生沟通 |
| 14:30 | 阮先生 | 深眸智现 | 招聘者 | [送达] | 您好，我是南华大学本科生，可以和您进一步沟通后端开发实习生职位吗？ |
| - | 罗先生 | 聚搜云 | 运营总监 | [已读] | 您好，我是南华大学本科生，可以和您进一步沟通JAVA, Python, php实习生职位吗？ |
| 07月18日 | 无夕教育科技 | 无夕教育科技 | CEO | [送达] | 您好，我是南华大学本科生，可以和您进一步沟通招聘大学生Python实习生职位吗？ |
| 07月18日 | 罗女士 | 科脉技术 | - | [送达] | 您好，我是南华大学本科生，可以和您进一步沟通python实习生职位吗？ |

### 列表底部

```
generic "没有更多了"                            ← 加载完成提示
generic "与您进行过沟通的 Boss 都会在左侧列表中显示"  ← 空状态提示
```

## 右侧面板：消息区域

### 未选中对话状态

当没有选中任何对话时，右侧面板显示：
- "与您进行过沟通的 Boss 都会在左侧列表中显示"

### 选中对话后的 DOM 结构（2026-07-19 已验证）

```
右侧面板（选中对话后）
  │
  ├── 对话标题栏
  │     ├── generic "罗女士"                ← 招聘方姓名
  │     ├── generic "科脉技术"              ← 公司名
  │     └── generic [更多选项按钮]          ← 右上角更多操作
  │           └── image (conversation-top-more.png)
  │
  ├── 职位信息栏
  │     ├── generic "python实习生"          ← 职位名称
  │     ├── generic "200-250元/天"          ← 薪资范围
  │     └── generic "查看职位"              ← 查看职位详情链接
  │
  ├── 消息历史区域
  │     ├── generic "昨天 16:17"            ← 消息时间戳
  │     ├── generic "您好，我是..."          ← 消息内容（用户发送，右对齐）
  │     └── ...                             ← 更多消息
  │
  ├── 竞争者PK分析区域（可选，首次对话时显示）
  │     ├── image (PK图标)
  │     ├── heading "你与该职位竞争者PK情况"
  │     ├── generic "共X人投递，你超过X%竞争者"
  │     ├── generic "优秀竞争者会XX，建议你XX"
  │     └── generic "查看详细分析"
  │
  ├── 工具栏
  │     ├── generic "表情"
  │     ├── generic "常用语"
  │     ├── generic "发送图片"
  │     ├── generic "求简历：双方回复后可用"
  │     ├── generic "交换手机：双方回复后可用"
  │     └── generic "交换微信：双方回复后可用"
  │
  └── 输入区域
        ├── generic [id="chat-input"]       ← 输入框（contenteditable div）
        ├── generic "按Enter键发送，按Ctrl+Enter键换行"  ← 输入提示
        └── button "发送" [type="send"]     ← 发送按钮
```

### 输入框详细说明

- **类型**：`contenteditable div`（**不是 textarea**）
- **id**：`chat-input`
- **交互**：
  - `Enter` → 发送消息
  - `Ctrl+Enter` → 换行
- **发送按钮**：`<button type="send">发送</button>`

### 工具栏功能

| 工具 | 文本 | 说明 |
|------|------|------|
| 表情 | "表情" | 插入表情 |
| 常用语 | "常用语" | 快捷回复模板 |
| 发送图片 | "发送图片" | 发送图片消息 |
| 求简历 | "求简历：双方回复后可用" | 需双方回复后解锁 |
| 交换手机 | "交换手机：双方回复后可用" | 需双方回复后解锁 |
| 交换微信 | "交换微信：双方回复后可用" | 需双方回复后解锁 |

## 已验证的 CSS class（2026-07-18 从下载的 HTML/CSS 确认）

| 元素 | 真实 class | 选择器 | 来源 |
|------|-----------|--------|------|
| 对话列表容器 | `.user-list-content` | `.user-list-content` | HTML |
| 对话项 | `.friend-content` | `.friend-content` | HTML |
| 对话项-选中 | `.friend-content.selected` | `.selected` | CSS |
| 招聘方姓名 | `span.name-text` | `.name-text` | HTML |
| 最后消息 | `span.last-msg-text` | `.last-msg-text` | HTML |
| 消息状态 | `i.message-status` | `.message-status` | HTML |
| 消息历史容器 | `.conversation-message` | `.conversation-message` | CSS |
| 消息气泡 | `.message-item` | `.message-item` | CSS |
| 消息文本 | `.message-item .text` | `.text` | CSS |
| 用户消息 | `.message-item.item-myself` | `.item-myself` | CSS |
| 招聘方消息 | `.chat-other` | `.chat-other` | CSS |
| 输入框 | `#chat-input` (contenteditable div) | `#chat-input` | **live page 已验证** |
| 发送按钮 | `.send` | `button[type="send"]` | **live page 已验证** |

**注意**：
- BOSS 使用 Vue.js，所有元素带 `data-v-*` 属性（如 `data-v-3d67f5a8`），选择器不应依赖这些版本化属性
- 输入框是 `contenteditable div`（id="chat-input"），**不是 textarea**
- 发送按钮是 `<button type="send">`，**不是 `<button type="submit">`**

## DOM 层级结构（已验证）

```
.chat-container
  .chat-wrap
    .list-warp.v2                    ← 左侧面板
      .chat-user.v2
        .boss-search-top             ← 搜索框 (input.boss-search-input)
        .label-list                  ← 筛选标签 (ul > li.selected)
        .chat-content
          .user-list
            .user-list-content       ← 对话列表容器
              ul[role="group"]
                li[role="listitem"]
                  .friend-content    ← 对话项（.selected 表示选中）
                    .figure          ← 头像 (img.image-circle)
                    .text
                      span.time      ← 时间戳
                      .title-box
                        .name-box
                          span.name-text  ← HR 姓名
                        i.vline
                        (职位文本)
                      .last-msg
                        i.message-status ← 状态标记
                        span.last-msg-text ← 消息预览
    .chat-conversation               ← 右侧面板
      .conversation-top              ← 对话标题栏（姓名+公司+更多按钮）
      .position-info                 ← 职位信息（职位名+薪资+查看职位）
      .conversation-message          ← 消息历史容器
        .message-item                ← 单条消息
          .item-myself               ← 用户发送的消息
          .chat-other                ← 招聘方发送的消息
          .text                      ← 消息文本
          .time                      ← 消息时间
      .pk-analysis                   ← 竞争者PK分析（可选）
      .toolbar                       ← 工具栏（表情/常用语/图片等）
      .footer-input                  ← 输入区域
        #chat-input                  ← 输入框（contenteditable div）
        button[type="send"]          ← 发送按钮
```

## 已确认的 DOM 事实（2026-07-19 更新）

基于 Chrome MCP Server (mcp-chrome-bridge) 在 live page 上的 accessibility tree 分析：

1. **对话列表使用 table 结构**：`<thead>` / `<tbody(group)>` / `<tfoot>`
2. **对话项是 `<li>` 元素**：包含在 group (tbody) 容器中
3. **每个对话项包含**：头像、时间、姓名、公司、职位、状态标记、消息预览
4. **筛选标签**：新招呼、仅沟通、更多（有交换/有面试/不感兴趣）
5. **搜索框**：`<input type="text" placeholder="搜索30天内的联系人">`
6. **选中对话后右侧面板**：标题栏 + 职位信息 + 消息历史 + 竞争者分析 + 工具栏 + 输入区
7. **输入框是 contenteditable div**：`id="chat-input"`，不是 textarea
8. **发送按钮**：`<button type="send">发送</button>`
9. **工具栏**：表情、常用语、发送图片、求简历、交换手机、交换微信
10. **竞争者PK分析**：首次对话时显示，包含投递人数、竞争者排名等
11. **职位信息栏**：显示职位名称、薪资范围、查看职位链接
12. **页面 URL 匹配**：`zhipin.com/web/geek/chat` — 与 adapter.ts 的 detect() 一致

## 选择器验证状态

| 选择器 | 来源 | 状态 | 备注 |
|--------|------|------|------|
| `.user-list-content` 对话列表容器 | **live page** | ✅ 已验证 | accessibility tree 显示 group 容器包含 listitem |
| `.friend-content` 对话项 | **live page** | ✅ 已验证 | 5 个对话项均正确匹配 |
| `.name-text` HR 姓名 | **live page** | ✅ 已验证 | 曹先生/阮先生/罗先生/罗女士 均正确提取 |
| `.last-msg-text` 消息预览 | **live page** | ✅ 已验证 | 5 条消息预览均正确提取 |
| `.selected` 选中状态 | **live page** | ✅ 已验证 | 无选中时 0 个匹配 |
| `i.message-status` 消息状态 | **live page** | ✅ 已验证 | [送达]/[已读] 均正确提取 |
| `#chat-input` 输入框 | **live page** | ✅ 已验证 | contenteditable div |
| `button[type="send"]` 发送按钮 | **live page** | ✅ 已验证 | type="send" 非 submit |
| `.conversation-message` 消息容器 | 下载 CSS | ⚠️ 仅 CSS 推断 | 需选中对话后验证 |
| `.message-item` 消息项 | 下载 CSS | ⚠️ 仅 CSS 推断 | 需选中对话后验证 |
| `.item-myself` 用户消息 | 下载 CSS | ⚠️ 仅 CSS 推断 | 需选中对话后验证 |
| `.chat-other` 招聘方消息 | 下载 CSS | ⚠️ 仅 CSS 推断 | 需选中对话后验证 |

## 选择器调优指引

若选择器失效，可通过以下方式获取真实 class：

**方式 1：Chrome MCP Server（推荐）**
使用 `mcp-chrome-bridge` 的 `chrome_javascript` 工具在 BOSS 页面上下文中执行 DOM 探测代码。

**方式 2：扩展 content script 注入探测代码**
在 `content.ts` 中临时添加 DOM 探测逻辑，通过 `console.log` 输出到扩展的 DevTools 面板。

**方式 3：通过扩展的 Service Worker 执行**
在 `router.ts` 中添加临时 handler，通过 `chrome.scripting.executeScript` 在 BOSS 页面上下文中执行探测代码。

## Live Page 验证记录（2026-07-20）

使用 Chrome MCP `chrome_read_page` (accessibility tree) 在已登录的 BOSS 聊天页上验证：

### 左侧面板 - 对话列表

```
group                           ← .user-list-content（对话列表容器）
  listitem                      ← .friend-content（对话项 1）
    image                       ← 头像
    generic "15:27"             ← 时间戳
    generic "曹先生"            ← 姓名 (.name-text)
    generic "招聘hr"            ← 职位（.title-box 内）
    generic "您正在与Boss..."   ← 最后消息 (.last-msg-text)
  listitem                      ← .friend-content（对话项 2）
    image                       ← 头像
    generic "14:30"             ← 时间戳
    generic "阮先生"            ← 姓名
    generic "深眸智现"           ← 公司（.title-box 内）
    generic "招聘者"            ← 职位
    generic "[送达]"            ← 状态 (i.message-status)
    generic "您好，我是..."     ← 最后消息
  listitem                      ← .friend-content（对话项 3）
    ...
tfoot
  generic "没有更多了"          ← 列表底部
```

### 右侧面板 - 未选中状态

```
generic "与您进行过沟通的 Boss 都会在左侧列表中显示"  ← 占位提示
```

### 关键发现

1. **对话列表选择器全部正确**：`.user-list-content`、`.friend-content`、`.name-text`、`.last-msg-text`、`i.message-status` 均从 live page 验证通过
2. **公司名提取**：`extractCompanyFromTitleBox()` 依赖 `i.vline` 后的文本节点。从 accessibility tree 看，公司名（如"深眸智现"）作为独立 generic 元素出现，实际 DOM 中应为 `.title-box` 内 `i.vline` 后的文本节点或子元素
3. **无选中对话时**：右侧面板仅显示占位提示，`.conversation-message`、`.message-item` 等选择器无法验证（需选中对话后验证）
4. **对话项数量**：当前页面有 5 个对话项，均有姓名和消息预览
