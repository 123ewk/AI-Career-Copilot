# Boss 直聘职位数据采集实现文档

## 1. 概述

本文档描述 AI Career Copilot 浏览器扩展如何从 Boss 直聘（zhipin.com）职位搜索列表页获取职位数据。

**核心方案**：通过 Chrome Extension Content Script 向页面主世界（Main World）注入拦截器，捕获 Boss 直聘内部 API 的职位列表响应，解析后通过现有消息链路发送到后端。

**一句话总结**：不再解析 HTML，而是"劫"住页面自己已经请求到的 JSON 数据。

---

## 2. 背景与问题

### 2.1 原始方案：DOM 抓取

早期实现使用 CSS 选择器从渲染后的 HTML 中提取职位卡片：

```
.rec-job-list → .job-card-box → .job-name / .job-salary / .tag-list ...
```

### 2.2 遇到的问题

在真实环境中测试 TC-02 时发现：

- 页面加载后 SidePanel 显示"正在提取岗位 / 已发现 0 个岗位"
- 原因是 `.rec-job-list` / `.job-card-box` 等选择器与当前 Boss 页面结构不匹配
- 即使选择器正确，异步渲染、字体反爬、SPA 无刷新切换也会使 DOM 抓取不稳定

### 2.3 为什么 DOM 抓取脆弱

| 问题 | 说明 |
|------|------|
| 页面结构变化 | Boss 直聘改版后 CSS 类名、层级关系可能改变 |
| 异步加载 | 列表数据通过 JS 异步请求获取，Content Script 注入时机难以把握 |
| 字体反爬 | 薪资等字段使用自定义字体映射，需要读取渲染后文本 |
| SPA 路由 | 搜索条件切换不刷新页面，DOM 容器可能复用或重建 |

---

## 3. 解决方案：API 拦截

### 3.1 核心思想

Boss 直聘的职位列表数据来自其内部 API（`/wapi/zpgeek/pc/recommend/job/list.json`），返回的是结构化 JSON。我们不再自己请求这个接口，而是拦截页面自己已经发出的请求，复用其响应。

**优势**：

- 结构化数据，字段稳定
- 无需处理登录 Cookie、签名、反爬
- 页面改版时 API 通常比 HTML 结构更稳定
- 数据在页面渲染前就已拿到，实时性更好

### 3.2 架构对比

#### 改造前：DOM 抓取

```
Boss 页面 JS 请求 API
  ↓
API 返回 JSON
  ↓
Boss 页面渲染 HTML
  ↓
Content Script 解析 HTML → RawBossJob[]
  ↓
发送给 Service Worker
```

#### 改造后：API 拦截

```
Boss 页面 JS 请求 API
  ↓
interceptor.js（主世界）拦截响应 → postMessage
  ↓
content.ts（隔离世界）接收 JSON
  ↓
api_parser.ts 解析 JSON → RawBossJob[]
  ↓
发送给 Service Worker
```

---

## 4. 详细流程

### 4.1 整体时序

```text
Chrome 加载扩展
  │
  ▼
Content Script 在 document_start 注入（isolated world）
  │
  ├── 1. injectBossApiInterceptor()
  │      创建 <script src="chrome-extension://<id>/interceptor.js">
  │      注入到页面主世界
  │
  ▼
主世界 interceptor.js 执行
  │
  ├── 2. monkey-patch window.fetch
  ├── 3. monkey-patch XMLHttpRequest.prototype.open/send
  │
  ▼
Boss 页面 JS 发起职位列表请求
  │
  ▼
interceptor.js 捕获响应
  │
  ├── 4. response.clone().text()
  ├── 5. JSON.parse(text)
  ├── 6. window.postMessage({ type: 'BOSS_JOB_DATA_CAPTURED', payload }, origin)
  │
  ▼
Content Script 监听 message
  │
  ├── 7. 校验 event.source === window
  ├── 8. 校验 event.data.type === 'BOSS_JOB_DATA_CAPTURED'
  ├── 9. isJobListApiPayload(payload) 确认 URL 匹配目标 API
  │
  ▼
api_parser.ts 解析
  │
  ├── 10. 校验 code === 0
  ├── 11. 遍历 zpData.jobList[]
  ├── 12. 将每个 item 转换为 RawBossJob
  │
  ▼
SentJobTracker 去重
  │
  ├── 13. 按 detailUrl 过滤已发送岗位
  │
  ▼
chrome.runtime.sendMessage(JOBS_EXTRACTED)
  │
  ▼
Service Worker 接收并转发到后端
```

### 4.2 关键代码路径

| 步骤 | 文件 | 函数/代码 |
|------|------|----------|
| 注入拦截器 | `extension/src/content/content.ts` | `injectBossApiInterceptor()` |
| 拦截 fetch/XHR | `extension/public/interceptor.js` | `window.fetch` / `XMLHttpRequest.prototype.send` |
| 发送 postMessage | `extension/public/interceptor.js` | `sendCapturedData()` |
| 接收 postMessage | `extension/src/content/content.ts` | `window.addEventListener('message', ...)` |
| JSON 解析 | `extension/src/modules/boss/api_parser.ts` | `parseBossApiResponse()` |
| 去重 | `extension/src/content/content.ts` | `SentJobTracker.filterNewJobs()` |
| 发送后台 | `extension/src/content/content.ts` | `sendJobsExtracted()` |

---

## 5. 文件职责

### 5.1 `extension/manifest.json`

- 声明 `web_accessible_resources`，允许主世界加载 `interceptor.js`
- 将 Content Script 的 `run_at` 设为 `document_start`，确保拦截器尽早安装

```json
"web_accessible_resources": [
  {
    "resources": ["interceptor.js"],
    "matches": ["https://www.zhipin.com/*"]
  }
]
```

### 5.2 `extension/public/interceptor.js`

注入到页面主世界的纯 JavaScript 脚本，职责：

- 防止重复注入（`__bossInterceptorInstalled` 标记）
- 拦截 `fetch` 和 `XHR`
- 匹配 URL 包含目标 API 路径的请求
- 使用 `response.clone()` 避免破坏原请求
- 通过 `window.postMessage` 将数据发送给 Content Script

目标 API 路径：

```javascript
const TARGET_API_PATTERNS = [
  '/wapi/zpgeek/pc/recommend/job/list.json',
  '/wapi/zpgeek/search/job/list.json',
  '/wapi/zpgeek/job/list.json',
]
```

### 5.3 `extension/src/modules/boss/api_parser.ts`

将 Boss API JSON 转换为扩展内部 `RawBossJob` 格式：

- 校验响应结构（`code === 0`，存在 `zpData.jobList`）
- 字段映射：`jobName` → `title`、`brandName` → `company`、`salaryDesc` → `salaryRaw`
- 地点拼接：`cityName + areaDistrict + businessDistrict` → `location`
- 详情页 URL 构建：`https://www.zhipin.com/job_detail/${encryptJobId}.html`
- 复用 `parser.ts` 的 `parseSalary` 做格式校验

### 5.4 `extension/src/content/content.ts`

Content Script 入口，职责：

- 调用 `injectBossApiInterceptor()` 注入主世界脚本
- 监听 `window.message` 接收 API 数据
- 调用 `api_parser.ts` 解析数据
- 使用 `SentJobTracker` 对 API 和 DOM 数据去重
- 保留 DOM 提取作为 fallback
- 监听详情面板变化，触发 `JOB_DETAIL_EXTRACTED`
- 监听卡片点击，记录 `currentSelectedDetailUrl`

### 5.5 `extension/src/modules/boss/adapter.ts`

DOM 提取与监听适配器，现在作为 fallback：

- API 未命中时，DOM 提取兜底
- 监听详情面板变化（JD 补充）
- 监听 SPA URL 变化

---

## 6. 关键技术点

### 6.1 为什么要注入主世界

Content Script 运行在 **isolated world**：

- 可以访问 `document` 和 `window`，但访问的 `window` 是隔离副本
- 无法直接拦截页面主世界里的 `fetch` / `XHR`
- 无法读取页面 JS 变量

因此必须通过 `<script>` 标签将代码注入到主世界，才能 monkey-patch 真正的 `window.fetch`。

### 6.2 为什么选择拦截而不是自行请求

| 方案 | 优点 | 缺点 |
|------|------|------|
| 自行请求 API | 数据完整、可控 | 需要处理 Cookie、签名、反爬，维护成本高 |
| 拦截页面请求 | 复用页面认证，无需处理签名 | 依赖页面实际发起请求，需要尽早注入 |

Boss 直聘的 `securityId` 等字段不建议自行生成，拦截方案天然避开了这个问题。

### 6.3 去重机制

API 拦截和 DOM fallback 可能拿到同一批岗位。`SentJobTracker` 通过 `detailUrl` 去重：

```typescript
class SentJobTracker {
  private sentUrls = new Set<string>()

  filterNewJobs(jobs: RawBossJob[]): RawBossJob[] {
    const newJobs: RawBossJob[] = []
    for (const job of jobs) {
      if (!this.sentUrls.has(job.detailUrl)) {
        this.sentUrls.add(job.detailUrl)
        newJobs.push(job)
      }
    }
    return newJobs
  }
}
```

切换搜索条件或分页时清空去重器。

### 6.4 Fallback 机制

如果 API 拦截未命中（例如 Boss 改了接口路径）：

1. `initListPage()` 启动时会先尝试 DOM 提取
2. `bossAdapter.observe()` 监听 `.rec-job-list` 子节点变化
3. 滚动加载、筛选、分页触发 MutationObserver 时再次 DOM 提取
4. DOM 提取结果经 `SentJobTracker` 去重后发送

### 6.5 URL 变化处理

Boss 直聘是 Vue SPA，搜索条件切换不会刷新页面。处理逻辑：

- `adapter.ts` 使用 `setInterval` 轮询 `location.href`
- URL 变化时回调 `onUrlChanged`
- `content.ts` 检测到列表页 URL 变化后：
  - `bossAdapter.disconnect()`
  - `sentJobTracker.clear()`
  - 重新 `initListPage()`

这样可以确保 DOM observer 绑定到新的容器上。

---

## 7. 数据映射

### 7.1 Boss API 字段 → RawBossJob 字段

| Boss API 字段 | RawBossJob 字段 | 说明 |
|--------------|----------------|------|
| `jobName` | `title` | 职位名称 |
| `brandName` | `company` | 公司名称 |
| `salaryDesc` | `salaryRaw` | 薪资原始文本，如 "8-12K" |
| `cityName` + `areaDistrict` + `businessDistrict` | `location` | 工作地点，如 "广州·番禺区·南村" |
| `jobLabels` | `tags` | 经验、学历等标签 |
| `skills` | `skills` | 技能标签 |
| `encryptJobId` | `detailUrl` | 构建详情页 URL |
| `bossName` | `recruiterName` | 招聘者姓名 |
| `bossTitle` | `recruiterTitle` | 招聘者职位 |
| - | `source` | 固定为 `"boss"` |
| `window.location.href` | `sourceUrl` | 当前列表页 URL |

### 7.2 薪资解析

`api_parser.ts` 将 `salaryDesc` 原样存入 `salaryRaw`，后续由 `parser.ts` 的 `parseSalary` 解析：

| 输入 | 输出 |
|------|------|
| "8-12K" | `{ min: 8, max: 12, unit: "K" }` |
| "300-360元/天" | `{ min: 300, max: 360, unit: "元/天" }` |
| "薪资面议" | `{ isNegotiable: true }` |

---

## 8. 已确认 API 端点

### 8.1 职位列表 API

| 项目 | 值 |
|------|-----|
| URL | `https://www.zhipin.com/wapi/zpgeek/pc/recommend/job/list.json` |
| Method | GET |
| 认证 | Cookie（登录态） |
| 分页 | `page` / `pageSize` |

### 8.2 关键参数

| 参数 | 说明 |
|------|------|
| `page` | 页码，从 1 开始 |
| `pageSize` | 每页数量，默认 15 |
| `city` | 城市编码，如广州 `101280100` |
| `_` | 时间戳（毫秒） |

### 8.3 响应结构

```json
{
  "code": 0,
  "message": "Success",
  "zpData": {
    "hasMore": true,
    "jobList": [
      {
        "jobName": "Python 工程师",
        "brandName": "某某科技",
        "salaryDesc": "15-30K",
        "cityName": "广州",
        "areaDistrict": "天河区",
        "businessDistrict": "珠江新城",
        "jobLabels": ["3-5年", "本科"],
        "skills": ["Python", "Django"],
        "encryptJobId": "xxx",
        "securityId": "xxx",
        "bossName": "张女士",
        "bossTitle": "HR"
      }
    ]
  }
}
```

---

## 9. 安全与合规

### 9.1 使用边界

- 仅用于用户自己的浏览器扩展
- 不批量爬取、不对外提供服务
- 不伪造请求、不破解签名
- 复用页面已经通过的登录态和 Cookie

### 9.2 风险提示

| 风险 | 说明 | 应对 |
|------|------|------|
| API 路径变化 | Boss 改版可能更换接口路径 | 更新 `TARGET_API_PATTERNS` |
| 响应字段变化 | 字段名或结构可能调整 | 更新 `api_parser.ts` 映射 |
| 反爬升级 | 增加签名或加密 | 继续使用拦截方案，不自行构造请求 |
| 频率限制 | 大量请求可能触发风控 | 依赖用户正常浏览频率，避免自动翻页轰炸 |

---

## 10. 维护与扩展

### 10.1 新增 API 路径

如果 Boss 增加了新的职位列表接口（如搜索接口），在以下两处添加路径：

1. `extension/public/interceptor.js` 的 `TARGET_API_PATTERNS`
2. `extension/src/modules/boss/api_parser.ts` 的 `isJobListApiPayload`

### 10.2 字段映射调整

如果 API 响应字段变化，修改 `api_parser.ts` 中的 `BossApiJobItem` 接口和 `convertApiJobToRawBossJob` 函数。

### 10.3 调试方法

1. 打开 Boss 列表页，F12 → Console
2. 过滤 `[BossInterceptor]`，确认拦截器已安装
3. 过滤 `[AI Career Copilot]`，查看捕获和解析日志
4. Network 面板确认存在 `/wapi/zpgeek/pc/recommend/job/list.json` 请求

---

## 11. 总结

通过将数据源从 DOM 抓取切换到 API 拦截，AI Career Copilot 解决了 TC-02 中"提取到 0 个岗位"的问题。该方案：

- 利用页面自身请求，无需处理认证和签名
- 获取结构化 JSON，解析更稳定
- 保留 DOM 提取作为 fallback，提高容错性
- 通过去重机制避免 API 和 DOM 数据重复发送

当前实现已在 `extension/public/interceptor.js`、`extension/src/modules/boss/api_parser.ts` 和 `extension/src/content/content.ts` 中落地，构建验证通过。
