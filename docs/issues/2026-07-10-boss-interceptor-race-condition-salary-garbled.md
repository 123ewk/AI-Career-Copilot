# Boss 直聘 API 拦截器竞态条件与薪资乱码问题

> 发生时间:2026-07-10
> 影响范围:扩展 SidePanel 薪资显示、数据采集链路
> 涉及文件:content.ts、service_worker.ts、sidepanel.ts

---

## 一、问题现象

用户反馈扩展 UI 显示的薪资是乱码(字体反爬后的 Unicode 字符),但数据库里的薪资字段却是正常的。两个地方看同一份数据,结果完全相反。

### 对比说明

| 位置 | 数据来源 | 显示结果 |
|------|----------|----------|
| **扩展 SidePanel** | `raw.salaryRaw` 原始字符串 | 乱码(如 `` 等 Unicode) |
| **数据库 jobs 表** | `salary_min` / `salary_max` / `salary_unit` 解析后的数值字段 | 正常(如 8 / 12 / K) |

用户据此误判:"数据库正常 = API 拦截器在正常工作",实际上拦截器早已失效。

---

## 二、数据流追踪

要理解问题,先看正常情况下的数据流:

```
Boss 页面发起 API 请求 (/wapi/zpgeek/pc/recommend/job/list.json)
        ↓
主世界 interceptor.js 拦截 fetch/XHR 响应
        ↓ window.postMessage
Content Script (content.ts) 接收
        ↓ chrome.runtime.sendMessage
Service Worker (router.ts) 转发
        ↓ POST /api/jobs/
后端入库 (salary_min/max/unit,解析后的数值)
        ↓ JOBS_CREATED 广播
SidePanel 显示 (salaryRaw,原始字符串)
```

关键点:**数据库存的是解析后的数值字段,扩展 UI 显示的是原始字符串**。两者来自同一条 `RawBossJob` 的不同切片。

---

## 三、三个叠加的根因

### 根因 1:拦截器注入竞态

`content.ts` 在 `document_start` 时通过动态创建 `<script>` 标签注入 `interceptor.js`:

```typescript
const script = document.createElement('script')
script.src = chrome.runtime.getURL('interceptor.js')
script.onload = () => script.remove()
document.head.appendChild(script)
```

`<script>` 标签是**异步加载**(网络请求 + 解析 + 执行)。Boss 自己的 JS 可能在拦截器安装前就完成了首次 API 请求,导致拦截失败。

**结果**:API 拦截器时灵时不灵,取决于 Boss JS 和 `<script>` 标签加载的速度赛跑。

### 根因 2:DOM fallback 先到,API 数据被当重复丢弃

`content.ts` 在 `initListPage()` 中设置 600ms 后触发 DOM fallback:

```typescript
setTimeout(() => {
  runDomFallback(window.location.href)
}, 600)
```

当 API 拦截器因竞态失败时,DOM fallback 先触发,提取到字体反爬的乱码薪资,并通过 `SentJobTracker` 按 `detailUrl` 标记为"已发送"。

后续 API 拦截器命中时,`filterNewJobs` 按 `detailUrl` 去重,把干净的 API 数据当重复丢弃,SidePanel 永远显示乱码。

```
时间线(问题场景):
0ms    ── Content Script 启动,<script> 标签开始异步加载
~50ms  ── Boss JS 执行,发起首次 API 请求(拦截器还没装好,漏抓)
600ms  ── DOM fallback 触发,提取乱码薪资,标记 detailUrl 为"已发送"
~800ms ── <script> 标签加载完成,拦截器装上
~1s    ── Boss 发起后续 API 请求,拦截器命中
         → 但 filterNewJobs 发现 detailUrl 已在 tracker 中 → 丢弃
         → SidePanel 永远显示 600ms 时抓的乱码数据
```

### 根因 3:数据库"正常"只是历史遗产

后端 `POST /api/jobs/` 在 `source_url` 重复时**幂等返回已有记录**(不更新薪资字段):

```typescript
// router.ts
const resp = await fetchBackend('/api/jobs/', { method: 'POST', body: ... })
created.push({
  salaryRaw: raw.salaryRaw,  // ← 用本次的 raw(可能是乱码)
  // ...
})
```

- 早期某次 API 拦截成功 → DB 写入干净的 `salary_min/max`
- 后续乱码 DOM 数据 POST → 后端幂等返回旧记录,DB **不被覆盖**
- 但 SW 广播的 `salaryRaw` 用的是本次 `raw.salaryRaw`(乱码)→ SidePanel 显示乱码

**这就是"DB 正常、UI 乱码"同时发生的原因。**

---

## 四、解决方案

### Fix 1:双路径注入拦截器,主路径消除竞态

**主路径**:SW 通过 `chrome.scripting.registerContentScripts` 注册 `interceptor.js` 为 `world: 'MAIN'` + `runAt: 'document_start'` 的动态 content script。Chrome 在页面任何 JS 执行前直接注入,无竞态。

**兜底路径**:Content Script 保留 `<script>` 标签注入。如果主路径失败,兜底路径仍能装上拦截器(有竞态,但数据链路不断)。

`interceptor.js` 的 `__bossJobInterceptorInstalled` 标志防止重复安装,两条路径安全共存。

```typescript
// service_worker.ts — 主路径
async function doRegister(): Promise<void> {
  await chrome.scripting.registerContentScripts([{
    id: 'boss-interceptor',
    matches: ['https://www.zhipin.com/*'],
    js: ['interceptor.js'],
    runAt: 'document_start',
    world: 'MAIN',           // 关键:主世界注入
    allFrames: false,
  }])
}

// content.ts — 兜底路径
function injectBossApiInterceptor(): void {
  const script = document.createElement('script')
  script.src = chrome.runtime.getURL('interceptor.js')
  document.head.appendChild(script)
}
injectBossApiInterceptor()
```

### Fix 2:移除 DOM 列表 fallback

DOM 提取的薪资受字体反爬影响为乱码,是问题源头。只要 Fix 1 的主路径生效,API 拦截器会覆盖初始加载和滚动加载(走同一 API 端点)。

- **删除** `runDomFallback()` 函数
- **删除** `apiCapturedForCurrentPage` 变量及所有引用
- **简化** `initListPage()`:移除 600ms 的 `setTimeout(runDomFallback)`
- **简化** `REFRESH_JOBS` handler:改为返回 `{ ok: true }`
- **保留** `SentJobTracker`:仅用于 API 路径去重(滚动加载新岗位)
- **保留** 详情面板 JD 提取(不涉及薪资,无乱码问题)

### Fix 3:STORAGE_VERSION 升级 2 → 3

让旧的乱码持久化数据被 `loadFromStorage` 的版本校验丢弃,避免 SidePanel 重开后展示历史乱码缓存。

---

## 五、调试中遇到的坑

### 坑 1:移除 `<script>` 标签注入后数据完全断流

最初方案只保留 `registerContentScripts`,完全移除 `<script>` 标签注入。结果用户测试发现完全获取不到数据。

**原因**:`registerContentScripts` 只在 `onInstalled` 回调中调用,如果 `onInstalled` 没正确触发或注册失败(用户看不到 SW console 的错误),拦截器就完全没装上。

**修复**:恢复 `<script>` 标签注入作为兜底,并在 SW 顶层(不只 `onInstalled`)调用 `registerInterceptor()`。

### 坑 2:Duplicate script ID 'boss-interceptor'

SW 顶层调用和 `onInstalled` 回调**并发执行** `registerInterceptor()`。两者同时查 `getRegisteredContentScripts` 都得到"未注册",然后都尝试 `registerContentScripts`,第二个报错。

**修复**:用共享 Promise `registerPromise` 做去重,确保并发调用只执行一次注册:

```typescript
let registerPromise: Promise<void> | null = null

async function registerInterceptor(): Promise<void> {
  if (registerPromise) return registerPromise  // 已有进行中的注册,直接复用
  registerPromise = doRegister()
  try {
    await registerPromise
  } finally {
    registerPromise = null
  }
}
```

---

## 六、修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `service_worker.ts` | 新增 `registerInterceptor()` + `doRegister()`,在 `onInstalled` 和 SW 顶层调用;共享 Promise 防并发 |
| `content.ts` | 恢复 `injectBossApiInterceptor()` 兜底;删除 `runDomFallback`、`apiCapturedForCurrentPage`;简化 `initListPage` 和 `REFRESH_JOBS` |
| `sidepanel.ts` | `STORAGE_VERSION` 2 → 3,附带注释说明升级原因 |

**无需修改**:`interceptor.js`(已有防重复守卫)、`manifest.json`(`scripting` 权限已具备)、`router.ts`(广播逻辑已正确)

---

## 七、验证结果

修复后扩展正常工作:
- SidePanel 薪资显示为正常数字(如 "15-30K"),非乱码
- 滚动加载的新岗位薪资正常
- SPA 切换搜索条件后新列表薪资正常
- SW console 无 Duplicate script ID 错误
- 后端日志正常接收 `POST /api/jobs/` 请求

---

## 八、经验总结

1. **"数据库正常"不等于"当前链路正常"**:幂等 POST + 不更新字段的组合,会让 DB 成为历史数据的化石,掩盖当前链路已断裂的事实。

2. **双路径注入比单路径更稳健**:`registerContentScripts` 是更好的方案,但作为唯一路径时,失败会导致数据完全断流。保留 `<script>` 标签兜底,用 `__bossJobInterceptorInstalled` 防重复,两条路径安全共存。

3. **MV3 SW 的并发陷阱**:`onInstalled` 回调和模块顶层代码会并发执行,共享资源的并发访问需要用 Promise 去重,不能依赖"时序"假设。

4. **DOM 提取作为 fallback 在字体反爬场景下有害**:它不仅数据是乱码,还会通过去重机制阻止后续干净的 API 数据到达 UI。空列表比乱码列表对用户更友好。
