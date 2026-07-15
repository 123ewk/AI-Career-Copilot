# API 拦截器修复：从 Isolated World 迁回 Main World

## 问题背景

API 拦截器从未成功捕获过 Boss 直聘的 API 数据，始终降级到 DOM 提取。

### 根因分析

1. **Isolated World 方案根本不可行**
   - Content Script 运行在 Isolated World，与 Main World 有独立的全局对象
   - 在 Isolated World 中 hook `window.fetch` 不会影响 Main World 的 fetch 调用
   - Boss 直聘的 API 请求在 Main World 中执行，Isolated World 的 hook 完全拦截不到

2. **5 秒超时在 1ms 内触发**
   - 因为 hook 没有拦截到任何数据，Promise 立即 reject
   - 导致 DOM fallback 立即触发，而不是等待 5 秒

## 解决方案

恢复原始的 Main World 拦截器架构：

### 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                         Main World                           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  interceptor.js (registerContentScripts 注入)        │    │
│  │  - hook window.fetch                                 │    │
│  │  - hook XMLHttpRequest.prototype.open/send           │    │
│  │  - 捕获 /wapi/zpgeek/.../job/list.json 响应          │    │
│  │  - 通过 window.postMessage 发送数据                  │    │
│  └─────────────────────────────────────────────────────┘    │
│                           │                                  │
│                     window.postMessage                       │
│                           ▼                                  │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Content Script (Isolated World)                     │    │
│  │  - 监听 window.addEventListener('message')           │    │
│  │  - 接收 BOSS_JOB_DATA_CAPTURED 消息                  │    │
│  │  - 解析数据为 RawBossJob                             │    │
│  │  - 通过 chrome.runtime.sendMessage 发送给 SW         │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 改动文件

1. **`extension/src/background/service_worker.ts`**
   - 恢复 `registerInterceptor()` 函数
   - 使用 `chrome.scripting.registerContentScripts` 注册 Main World 拦截器
   - 在 `onInstalled` 和 SW 顶层调用 `registerInterceptor()`

2. **`extension/src/content/content.ts`**
   - 删除无效的 Isolated World inline 拦截器（`setupApiInterceptor()`）
   - 恢复 `window.addEventListener('message')` 监听器
   - 监听 `BOSS_JOB_DATA_CAPTURED` 和 `BOSS_INTERCEPTOR_LOG` 消息

3. **`extension/manifest.json`**
   - 恢复 `web_accessible_resources` 配置
   - 添加 `interceptor.js` 使其可被注入到 Main World

4. **`extension/public/interceptor.js`**
   - 保持不变，作为 Main World 拦截器脚本

## 验证步骤

1. 重新构建扩展：`npm run build`
2. 在 Chrome 中重新加载扩展
3. 打开 Boss 直聘列表页
4. 检查 DevTools 控制台：
   - 应看到 `[BossInterceptor] script executing in MAIN world` 日志
   - 应看到 `[fetch] intercepted request` 或 `[xhr] open intercepted` 日志
5. 检查后端日志：
   - 应看到 `[source=API] interceptor captured data` 而非 DOM fallback
   - PATH_DECISION 应为 `api_interceptor`

## 技术要点

### 为什么 Isolated World 不可行

Chrome 扩展的 Content Script 运行在 Isolated World 中，与页面的 Main World 隔离：

- **独立全局对象**：Isolated World 的 `window` 和 Main World 的 `window` 是不同的对象
- **独立原型链**：修改 Isolated World 的 `XMLHttpRequest.prototype` 不影响 Main World
- **共享网络栈**：虽然网络请求共享，但 hook 必须在请求发起的世界中安装

### 为什么 Main World 可行

- `registerContentScripts` with `world: 'MAIN'` 将脚本注入到页面的 Main World
- 在 Main World 中 hook `fetch/XHR` 能拦截页面的所有网络请求
- 通过 `window.postMessage` 将数据发送给 Content Script（跨世界通信）

### 时序保证

```
document_start
    ↓
Chrome 注册 Main World 拦截器 (registerContentScripts)
    ↓
页面 JS 开始执行
    ↓
Boss 调用 fetch/XHR 请求 API
    ↓
拦截器捕获响应
    ↓
postMessage 发送给 Content Script
    ↓
Content Script 解析并发送给 Service Worker
```

## 风险与注意事项

1. **registerContentScripts 可能在某些环境不生效**
   - 如果用户环境有问题，可能需要调试 Chrome 版本和扩展权限
   - 备选方案：使用 `<script>` 标签注入（但有加载延迟）

2. **Main World 拦截器可能被 Boss 检测**
   - Boss 直聘可能检测 `window.fetch` 是否被修改
   - 当前方案使用 `originalFetch.apply(this, arguments)` 保持调用上下文

3. **postMessage 可能被其他脚本监听**
   - 使用特定的 message type（`BOSS_JOB_DATA_CAPTURED`）减少冲突
   - 验证 `event.source === window` 确保消息来自同一窗口
