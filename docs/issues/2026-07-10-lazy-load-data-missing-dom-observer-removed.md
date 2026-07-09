# Boss 直聘懒加载新数据无法获取问题

> 发生时间:2026-07-10
> 影响范围:滚动加载、分页追加的新岗位数据采集
> 涉及文件:content.ts、adapter.ts

---

## 一、问题现象

修复薪资乱码问题(详见 [2026-07-10-boss-interceptor-race-condition-salary-garbled.md](./2026-07-10-boss-interceptor-race-condition-salary-garbled.md))后,首屏数据正常显示,但用户向下滚动加载更多岗位时,新岗位无法出现在 SidePanel 中。

### 对比说明

| 场景 | 首屏加载 | 滚动加载 |
|------|----------|----------|
| **数据来源** | API 拦截器 | API 拦截器(理论上) |
| **实际结果** | 正常 | 无数据 |
| **原因** | `registerContentScripts` 在 `document_start` 注入,无竞态 | DOM observer 被移除,API 拦截器可能未捕获到后续请求 |

---

## 二、根因分析

### 数据流回顾

完整的岗位数据获取依赖两条路径:

1. **API 拦截器(主路径)**:`interceptor.js` patch 了 `window.fetch` 和 `XMLHttpRequest`,捕获 Boss 的 `/wapi/zpgeek/.../job/list.json` 响应
2. **DOM observer(补充路径)**:`adapter.ts` 的 `setupListObserver` 通过 MutationObserver 监听 `.rec-job-list` 的 `childList` 变化,提取新卡片

### 修复薪资乱码时的误伤

在修复薪资乱码问题时,我们移除了 DOM 列表 fallback(`runDomFallback` 函数和 `onJobsExtracted` 回调),理由是:
- DOM 提取的薪资受字体反爬影响为乱码
- API 拦截器理论上能覆盖首屏 + 滚动加载的所有请求

**但这个假设有漏洞**:API 拦截器虽然理论上能捕获后续请求(fetch/XHR patch 在 SPA 导航后存活),但 Boss 的滚动加载可能因为以下原因没被捕获:
- Boss 在某个时刻重新赋值了 `window.fetch`,覆盖了我们的 patch
- Boss 的滚动加载使用了不同的请求方式(如非标准 HTTP 客户端)
- Boss 在首次请求后缓存了原始 fetch 引用,后续用缓存的引用发请求

无论原因是什么,移除 DOM observer 后,API 拦截器成了唯一数据源,一旦它没捕获到后续请求,就没有补充了。

### adapter.ts 中已有但未生效的代码

`adapter.ts` 的 `setupListObserver`(第 324 行)本来就是为懒加载设计的:

```typescript
private setupListObserver(onJobsExtracted: (jobs: RawBossJob[]) => void): void {
  const listContainer = document.querySelector(BOSS_SELECTORS.list.jobList)
  if (!listContainer) {
    setTimeout(() => this.setupListObserver(onJobsExtracted), 1000)
    return
  }

  let debounceTimer: number | null = null
  this.listObserver = new MutationObserver(() => {
    if (debounceTimer) clearTimeout(debounceTimer)
    debounceTimer = setTimeout(() => {
      const jobs = this.extractJobs()
      onJobsExtracted(jobs)
    }, 500) as unknown as number
  })

  this.listObserver.observe(listContainer, {
    childList: true,   // 监听子节点变化(新卡片添加)
    subtree: false,    // 只监听直接子节点
  })
}
```

但 `setupListObserver` 只在 `bossAdapter.observe()` 传入 `onJobsExtracted` 回调时才会被调用(adapter.ts:222-225):

```typescript
if (callbacks.onJobsExtracted) {
  this.setupListObserver(callbacks.onJobsExtracted)
}
```

修复薪资乱码时移除了 `onJobsExtracted` 回调,导致 `setupListObserver` 完全不运行。

---

## 三、解决方案

在 `initListPage()` 中恢复 `onJobsExtracted` 回调,让 DOM observer 作为 API 拦截器的**补充数据源**(而非 fallback)。

### 核心改动(content.ts)

```typescript
function initListPage(): void {
  bossAdapter.observe({
    onJobsExtracted: (jobs) => {
      // DOM observer 作为 API 拦截器的补充：
      // sentJobTracker 按 detailUrl 去重，API 已捕获的会被跳过
      const newJobs = sentJobTracker.filterNewJobs(jobs)
      if (newJobs.length > 0) {
        console.log(
          `[AI Career Copilot] DOM observer: ${newJobs.length} new jobs (scroll/append)`,
        )
        void sendJobsExtracted(window.location.href, newJobs)
      }
    },
    onDetailExtracted: (detail) => {
      void sendDetailExtracted(detail)
    },
    onUrlChanged: (url, isListPage) => { ... },
  })
}
```

### 双数据源协调机制

`SentJobTracker` 按 `detailUrl` 去重,自动协调两个数据源:

```
场景 A: API 拦截器先捕获
  API 响应 → sentJobTracker 记录 detailUrl="job-001"
  滚动触发 DOM observer → 提取到 detailUrl="job-001"
  → filterNewJobs 发现已记录 → 跳过(不重复发送)

场景 B: API 拦截器未捕获
  滚动触发 DOM observer → 提取到 detailUrl="job-002"
  → filterNewJobs 发现未记录 → 发送给 SW
  → sentJobTracker 记录 detailUrl="job-002"
```

两个数据源自动协调,谁先到谁的数据被采用,后到的被跳过。

---

## 四、为什么这次不会有乱码问题

之前的乱码根因是**首屏加载时字体未加载完**,`innerText` 返回乱码。具体链路:

```
首屏: <script> 异步加载(慢) + Boss JS 发起首次请求(快)
  → 拦截器没装上 → 600ms DOM fallback 触发
  → 字体还没加载完 → innerText 返回乱码 → 薪资乱码
```

恢复 DOM observer 后不会有这个问题,因为:

| 场景 | 首屏加载 | 滚动加载 |
|------|----------|----------|
| **数据来源** | API 拦截器(主) | API 拦截器 + DOM observer(双保险) |
| **字体状态** | 可能未加载 | **已加载**(页面已渲染完成) |
| **innerText 结果** | N/A(不走 DOM) | **正常字符** |
| **竞态风险** | 无(`registerContentScripts` 在 `document_start` 注入) | 无(字体已加载,DOM 提取安全) |

关键区别:
- **首屏**:由 API 拦截器提供数据(`registerContentScripts` 在 `document_start` 注入,无竞态),DOM observer 不参与
- **滚动加载**:DOM observer 在此时触发,自定义字体早已加载完,`innerText` 返回正常字符
- **`sentJobTracker` 兜底**:即使 DOM observer 提取到了数据,如果 API 拦截器也捕获到了同一岗位(detailUrl 相同),后到的会被跳过

---

## 五、修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `content.ts` | `initListPage()` 中恢复 `onJobsExtracted` 回调,触发 `setupListObserver` 运行 |

**无需修改**:
- `adapter.ts`(`setupListObserver` 实现已完整,只是之前没被调用)
- `interceptor.js`(主世界拦截器逻辑不变)
- `service_worker.ts`(注入逻辑不变)

---

## 六、验证方法

1. 在 `chrome://extensions` 点击刷新按钮重新加载扩展
2. 刷新 Boss 列表页,确认首屏薪资正常(API 拦截器)
3. **向下滚动加载更多岗位**,确认新岗位出现在 SidePanel
4. DevTools Console 应看到:
   - 首屏:`[AI Career Copilot] Captured Boss API response:`(API 拦截器)
   - 滚动:`[AI Career Copilot] DOM observer: N new jobs (scroll/append)`(DOM 补充触发时)
5. 切换搜索条件(如换城市),确认新列表首屏 + 滚动都正常
6. 持续滚动多页,确认 SidePanel 岗位数量持续增长,无重复

---

## 七、经验总结

1. **移除 fallback 时要确认主路径的覆盖率**:移除 DOM fallback 的前提是"API 拦截器能覆盖所有场景",但滚动加载的请求可能因各种原因没被捕获。保留 DOM observer 作为补充,通过去重机制自动协调,比"纯 API"更稳健。

2. **`SentJobTracker` 是双数据源协调的关键**:它按 `detailUrl` 去重,让 API 和 DOM 两个数据源自动协调——谁先到谁的数据被采用,后到的被跳过。这种设计允许我们同时启用两个数据源而不担心重复。

3. **字体反爬问题有时效性**:首屏加载时字体可能未加载完,`innerText` 返回乱码;但滚动加载时页面已渲染完成,字体已加载,`innerText` 返回正常字符。同一个 DOM 提取逻辑在不同时机表现不同,不能一刀切地废弃。

4. **修复一个问题时检查副作用**:移除 `onJobsExtracted` 是为了解决薪资乱码,但意外破坏了滚动加载的数据采集。修复后应验证所有相关功能路径,不只是出问题的那个。
