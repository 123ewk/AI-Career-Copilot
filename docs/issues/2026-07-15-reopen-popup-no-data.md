# 重新打开 Popup 后获取不到岗位数据

## 背景

Chrome 扩展在以下场景出现数据丢失：

1. 用户首次登录，正常使用，能获取到岗位数据
2. 关闭 Popup 后重新打开（不需要重新登录）
3. 进入 SidePanel 后获取不到数据，后端日志显示 `POST /api/extension/logs` 返回 204

## 症状

| 场景 | 结果 | 后端日志 |
|------|------|----------|
| 首次登录后使用 | 正常获取 15 个岗位 | `POST /api/jobs/` → 201 |
| 关闭 Popup 重新打开后 | 无法获取数据 | 无 `POST /api/jobs/` 请求 |

后端日志对比：

```
# 首次（成功）
readyState: 'complete' → listContainer found → 15 jobs → POST /api/jobs/ ✅

# 重新打开（失败）
readyState: 'loading' → listContainer not found → DOM fallback → 0 jobs ❌
readyState: 'interactive' → listContainer found → 15 jobs（已晚，无重新提取）
```

## 根因分析

### 直接原因：MutationObserver 不触发

`MutationObserver` 只在 DOM **变化**时触发回调。当 observer 挂载时，如果 jobCard 已经存在于 DOM 中，不会产生 mutation 事件，observer 永远不触发。

### 时序问题

```
① SidePanel 打开 → RESET_EXTRACTION_STATE → Content Script 重新初始化
② initListPage() → setupApiTimeout()（5 秒超时）→ bossAdapter.observe()
③ observer 找不到 listContainer → 每 500ms 重试，最多 10 次
④ 5 秒超时触发 → runDomFallbackForInitialPage() → 此时页面未就绪 → 0 个卡片 → 放弃
⑤ observer 最终找到 listContainer → 挂载时 15 个卡片已在 DOM → 无 mutation → 不触发提取
```

关键：步骤④和⑤之间存在时间差。DOM fallback 在页面未就绪时运行（失败），observer 在页面就绪后挂载（但卡片已存在，不触发 mutation）。

### 为什么首次成功

首次加载时，页面已完全就绪（`readyState: 'complete'`），DOM fallback 运行时卡片已在 DOM 中，能正常提取。重新打开时，Content Script 重新初始化的时机与页面加载状态不同步。

## 解决方案

在 `setupListObserver` 中，observer 挂载后立即提取一次已有卡片：

```typescript
// adapter.ts — setupListObserver()
this.listObserver.observe(listContainer, { childList: true, subtree: true })

// 修复：observer 挂载时如果已有 jobCard，立即提取一次
if (jobCardCount > 0) {
  const jobs = this.extractJobs()
  if (jobs.length > 0) {
    onJobsExtracted(jobs)
  }
}
```

### 覆盖场景

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| observer 挂载时卡片已存在 | 不触发 ❌ | 立即提取 ✅ |
| observer 挂载后卡片动态加载 | mutation 触发 ✅ | mutation 触发 ✅ |
| DOM fallback 页面就绪 | 正常提取 ✅ | 正常提取 ✅ |

## 涉及文件

| 文件 | 改动 |
|------|------|
| `extension/src/modules/boss/adapter.ts` | observer 挂载后立即提取已有卡片 |

## 验证

1. `npm run build` 编译通过
2. 手动测试：
   - 登录 → 正常获取岗位数据
   - 关闭 Popup → 重新打开 Popup → 进入 SidePanel → 应能获取数据
   - 等待 SW 被回收（~30s）→ 重新打开 → 应能获取数据
   - 滚动加载新岗位 → observer 应正常触发
