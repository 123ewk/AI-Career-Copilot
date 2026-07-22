# BOSS 直聘聊天 API 逆向分析报告

**分析时间**: 2026-07-21
**分析方法**: Chrome MCP 网络捕获 (mcp-chrome-bridge CDP)
**分析页面**: `https://www.zhipin.com/web/geek/chat?ka=header-message`

---

## 1. API 清单总览

| # | API | Method | 用途 | 优先级 |
|---|-----|--------|------|--------|
| 1 | `/wapi/zprelation/friend/geekFilterByLabel` | GET | 获取好友/HR列表（基础信息） | ⭐⭐⭐⭐⭐ |
| 2 | `/wapi/zprelation/friend/getGeekFriendList.json` | POST | 获取好友详情（含最后消息） | ⭐⭐⭐⭐⭐ |
| 3 | `/wapi/zpmsg/history/pull` | GET | 拉取消息历史 | ⭐⭐⭐⭐ |
| 4 | `/wapi/zpchat/config/ws` | GET | 获取 WebSocket 地址 | ⭐⭐⭐ |
| 5 | `/wapi/zpchat/notify/setting/get` | GET | 通知设置 | ⭐⭐ |
| 6 | `/wapi/zpchat/config/get` | GET | 聊天配置 | ⭐⭐ |
| 7 | `/wapi/zpchat/gray/get` | GET | 灰度特性 | ⭐ |

---

## 2. 核心 API 详细分析

### 2.1 好友列表 API（基础信息）

```
GET /wapi/zprelation/friend/geekFilterByLabel?labelId=0&encryptSystemId={encryptSystemId}
```

**用途**: 获取当前用户的所有 HR 联系人列表（基础信息）

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `labelId` | int | 是 | 标签筛选，`0`=全部，`-1`=未分类 |
| `encryptSystemId` | string | 否 | 系统加密ID（可选） |

**Response 结构**:

```json
{
  "code": 0,
  "message": "Success",
  "zpData": {
    "foldText": "还有30个联系人",       // 折叠提示文本
    "filterEncryptIdList": [],          // 已过滤的加密ID列表
    "filterBossIdList": [],             // 已过滤的BossID列表
    "friendList": [                     // 好友列表
      {
        "friendId": 510594807,          // 好友ID（数字）
        "friendSource": 0,              // 来源类型
        "encryptFriendId": "a03de316eb0bbbb60nV63tS5GFJX",  // 加密好友ID
        "name": "张三",                  // HR 姓名
        "updateTime": 1784532474000,     // 更新时间（毫秒时间戳）
        "brandName": "华为",             // 公司名称
        "jobName": "python实习",         // 职位名称
        "jobTypeDesc": "实习",           // 职位类型描述
        "jobCity": "深圳",               // 工作城市
        "positionName": "Python",        // 职位英文名
        "bossTitle": "招聘hr",           // Boss 头衔
        "score": 0.0,                    // 匹配分数
        "waterLevel": 0                  // 活跃度等级
      }
    ]
  }
}
```

**字段映射（→ Communication 模块）**:

| API 字段 | ChatConversationItem 字段 | 说明 |
|----------|--------------------------|------|
| `friendId` | `id` | 唯一标识 |
| `name` | `recruiterName` | HR 姓名 |
| `brandName` | `companyName` | 公司名 |
| `jobName` | `jobTitle` | 职位名 |
| `bossTitle` | `recruiterTitle` | HR 头衔 |
| `encryptFriendId` | `encryptId` | 加密ID（用于后续API调用） |
| `jobCity` | `city` | 城市 |

---

### 2.2 好友详情 API（含最后消息）

```
POST /wapi/zprelation/friend/getGeekFriendList.json
Content-Type: application/x-www-form-urlencoded
```

**用途**: 根据好友ID列表获取详细信息，包括最后一条消息

**Request Body**:

```
friendIds=510594807,75718348,34927706,595101664,618413363
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `friendIds` | string | 是 | 逗号分隔的好友ID列表 |

**Response 结构**:

```json
{
  "code": 0,
  "message": "Success",
  "zpData": {
    "result": [
      {
        "friendSource": 0,                // 来源类型
        "securityId": "8l7nx1bYqd30b-...", // 安全ID（用于消息历史拉取）
        "name": "张三",                    // HR 姓名
        "avatar": "https://img.bosszhipin.com/boss/avatar/avatar_6.png",
        "isTop": 0,                        // 是否置顶
        "sourceTitle": "",                 // 来源标题
        "relationType": 2,                 // 关系类型
        "lastMsg": null,                   // 最后消息文本（可能为null）
        "lastMessageInfo": {               // 最后消息详情
          "msgId": 366140751463428,        // 消息ID
          "encryptMsgId": "04109039ccda7c9a1HJ82tm9F1dRxoq8VfiX",
          "showText": "你好，我在网上...",   // 显示文本
          "fromId": 742929338,             // 发送者ID
          "toId": 75718348,                // 接收者ID
          "status": 1,                     // 消息状态
          "msgTime": 1784529031882         // 消息时间戳
        },
        "lastTime": "昨天",                // 相对时间显示
        "lastTS": 1784529031882,           // 最后消息时间戳
        "sourceType": 0,                   // 来源类型
        "sourceExtend": null,              // 来源扩展
        "jobId": 544804993,                // 关联职位ID
        "jobSource": 0,                    // 职位来源
        "encryptJobId": "719d7d28742baa170nB-0925GVtT",
        "itemType": 0,                     // 项目类型
        "waterLevel": 0,                   // 活跃度
        "chatStatus": 0,                   // 聊天状态
        "title": "招聘官",                 // Boss 头衔
        "brandName": "某某科技",           // 公司名称
        "unreadMsgCount": 0,               // 未读消息数
        "filterReasonList": null,          // 过滤原因
        "note": null,                      // 备注
        "encryptBossId": "a113f716a35ee9860HF92tW-FFo~",
        "tinyUrl": "https://img.bosszhipin.com/boss/avatar/avatar_15.png",
        "filtered": false,                 // 是否被过滤
        "uid": 75718348,                   // 用户ID
        "encryptUid": "a113f716a35ee9860HF92tW-FFo~",
        "isFiltered": false                // 是否已过滤
      }
    ]
  }
}
```

**字段映射（→ ChatConversationItem 完整映射）**:

| API 字段 | ChatConversationItem 字段 | 说明 |
|----------|--------------------------|------|
| `uid` | `id` | 唯一标识 |
| `name` | `recruiterName` | HR 姓名 |
| `avatar` / `tinyUrl` | `avatar` | 头像URL |
| `brandName` | `companyName` | 公司名 |
| `title` | `recruiterTitle` | HR 头衔 |
| `lastMessageInfo.showText` | `lastMessage` | 最后消息内容 |
| `lastTS` | `lastMessageTime` | 最后消息时间 |
| `unreadMsgCount` | `unreadCount` | 未读消息数 |
| `securityId` | `securityId` | 安全ID（拉取消息用） |
| `encryptBossId` | `encryptId` | 加密BossID |
| `jobId` | `jobId` | 关联职位ID |
| `chatStatus` | `status` | 聊天状态 |

---

### 2.3 消息历史 API

```
GET /wapi/zpmsg/history/pull?type=0&lastId={lastId}&secretId={secretId}
```

**用途**: 分页拉取指定对话的消息历史

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | int | 是 | 消息类型，`0`=普通消息 |
| `lastId` | long | 是 | 上一页最后一条消息ID（分页游标） |
| `secretId` | string | 是 | 安全ID（从 getGeekFriendList 获取的 `securityId`） |

**Response 结构**:

```json
{
  "code": 0,
  "message": "Success",
  "zpData": {
    "hasMore": true,                      // 是否有更多消息
    "lastId": 366154852746245,            // 本页最后消息ID（下一页游标）
    "secretId": "3f36IoVNInPB_066...",    // 更新后的 secretId
    "type": 0,                            // 消息类型
    "stringList": [                       // 消息列表（Protobuf Base64 编码）
      "CAEahBAKYQiDBxIM57O757uf6YCa55+..."
    ]
  }
}
```

**关键发现**:
- 消息内容使用 **Protobuf 编码 + Base64** 序列化，不是纯 JSON
- 分页机制：`hasMore` + `lastId` 游标翻页
- `secretId` 每次请求后会更新，需保存用于下一页请求

---

### 2.4 WebSocket 配置 API

```
GET /wapi/zpchat/config/ws
```

**Response**:

```json
{
  "code": 0,
  "message": "Success",
  "zpData": {
    "result": [
      "ws6.zhipin.com",
      "ws.zhipin.com",
      "ws2.zhipin.com"
    ]
  }
}
```

**说明**: 聊天通过 WebSocket 实时推送，这些是 WebSocket 服务器地址。实时消息通过 WS 推送，非 HTTP 轮询。

---

## 3. 认证机制

### 3.1 请求头要求

```
Referer: https://www.zhipin.com/web/geek/chat?ka=header-message
Content-Type: application/x-www-form-urlencoded  (POST 请求)
x-requested-with: XMLHttpRequest
zp_token: V2Rt4lGOb02V1iVtRuxxgeKCu47DrQwiU~|Rt4lGOb02V1iVtRuxxgeKCu47DrRzCQ~
traceid: F-42y44uqvyu33wm5562z5bgjv
```

### 3.2 认证要素

| 要素 | 来源 | 说明 |
|------|------|------|
| `zp_token` | Cookie / Header | 主认证令牌，每次请求携带 |
| `wt2` | `/wapi/zppassport/get/wt` 获取 | WebSocket 认证令牌 |
| Session Cookie | 浏览器自动携带 | `__zp_stoken__` 等 |

### 3.3 Token 获取

```
GET /wapi/zppassport/get/wt
→ {"code":0,"message":"Success","zpData":{"wt2":"DskqaqjEy6-..."}}
```

---

## 4. 数据流架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    BOSS 聊天数据流                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 1: 加载页面                                               │
│  GET /web/geek/chat                                             │
│       │                                                         │
│       ▼                                                         │
│  Step 2: 获取 HR 列表                                           │
│  GET /wapi/zprelation/friend/geekFilterByLabel?labelId=0        │
│  → friendList: [{ friendId, name, brandName, ... }]             │
│       │                                                         │
│       ▼                                                         │
│  Step 3: 获取详情 + 最后消息                                     │
│  POST /wapi/zprelation/friend/getGeekFriendList.json            │
│  Body: friendIds=510594807,75718348,...                         │
│  → result: [{ securityId, lastMsg, unreadMsgCount, ... }]       │
│       │                                                         │
│       ▼                                                         │
│  Step 4: 点击对话 → 加载消息历史                                 │
│  GET /wapi/zpmsg/history/pull?type=0&lastId={}&secretId={}      │
│  → stringList: [protobuf_base64_messages]                       │
│       │                                                         │
│       ▼                                                         │
│  Step 5: 实时消息（WebSocket）                                   │
│  WS 连接到 ws6.zhipin.com / ws.zhipin.com / ws2.zhipin.com     │
│  Token: wt2 (from /wapi/zppassport/get/wt)                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. interceptor.js 扩展方案

### 5.1 需要拦截的 API Pattern

```javascript
const TARGET_API_PATTERNS = [
  // Job 列表（已有）
  '/wapi/zpgeek/pc/recommend/job/list.json',
  '/wapi/zpgeek/search/job/list.json',
  '/wapi/zpgeek/job/list.json',
  // Chat 联系人列表（新增）
  '/wapi/zprelation/friend/geekFilterByLabel',
  '/wapi/zprelation/friend/getGeekFriendList.json',
  // Chat 消息历史（新增）
  '/wapi/zpmsg/history/pull',
]
```

### 5.2 消息类型

```javascript
const MESSAGE_TYPE_JOB = 'BOSS_JOB_DATA_CAPTURED'
const MESSAGE_TYPE_CHAT_LIST = 'BOSS_CHAT_LIST_CAPTURED'      // 新增
const MESSAGE_TYPE_CHAT_DETAIL = 'BOSS_CHAT_DETAIL_CAPTURED'  // 新增
const MESSAGE_TYPE_CHAT_MSG = 'BOSS_CHAT_MSG_CAPTURED'        // 新增
```

### 5.3 数据流映射

| 拦截到的 URL | 消息类型 | 处理逻辑 |
|-------------|---------|---------|
| `geekFilterByLabel` | `BOSS_CHAT_LIST_CAPTURED` | 提取 friendList，记录 HR 基础信息 |
| `getGeekFriendList.json` | `BOSS_CHAT_DETAIL_CAPTURED` | 提取详细信息 + securityId |
| `history/pull` | `BOSS_CHAT_MSG_CAPTURED` | 解码 Protobuf 消息 |

---

## 6. 风险与注意事项

| 风险 | 说明 | 缓解 |
|------|------|------|
| Protobuf 编码 | 消息历史使用 Protobuf + Base64，需解码 | 需要逆向 Proto 定义或使用动态解码 |
| `secretId` 时效性 | 每次请求后 secretId 更新 | 每次响应后保存新 secretId |
| `zp_token` 过期 | Token 有有效期 | 拦截器已有 token 刷新机制 |
| 虚拟滚动 | 页面 UI 仍使用虚拟列表 | API 拦截绕过了 DOM 限制 |
| WebSocket | 实时消息通过 WS 推送，非 HTTP | 需要单独处理 WS 消息（当前不在拦截范围） |

---

## 7. 成功标准对照

| 标准 | 状态 | 说明 |
|------|------|------|
| 找到聊天列表 API | ✅ | `geekFilterByLabel` + `getGeekFriendList.json` |
| 确认 Request Method | ✅ | GET (列表) + POST (详情) |
| 分析 Response 结构 | ✅ | 完整字段映射已记录 |
| 分页参数 | ✅ | `hasMore` + `lastId` 游标（消息历史） |
| 认证参数 | ✅ | `zp_token` Header + Cookie |
| 输出物 | ✅ | 本文档 `docs/design/boss-chat-api-analysis.md` |
