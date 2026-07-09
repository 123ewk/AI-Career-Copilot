/**
 * Background Service Worker 入口
 *
 * 职责：
 * - SW 生命周期管理（onInstalled / onStartup）
 * - 初始化消息路由（router.ts）
 *
 * 设计动机：
 * - MV3 要求后台逻辑在 Service Worker 中执行
 * - SW 是事件驱动的，空闲 30 秒后被 Chrome 回收
 *   → 模块级变量（access_token）在 SW 重启时丢失
 *   → 由 Popup 在用户激活时通过 AUTH_TOKEN_UPDATED 重新发送 token
 * - 不在 SW 中放业务逻辑，业务逻辑在 router + handler 中
 *
 * 模块依赖关系：
 *   service_worker.ts (entry)
 *     └── router.ts (message dispatcher)
 *           └── backend_client.ts (HTTP client + token store)
 *                 └── chrome_message.ts (message protocol types)
 *
 * 注意：所有消息处理统一走 router.ts 注册
 * 不在 service_worker.ts 中单独 add onMessage 监听器，避免多监听器响应顺序冲突
 */

import { initMessageRouter } from './router'

// ==================== 主世界拦截器注册 ====================

/**
 * 向 Chrome 注册 main-world 动态 content script
 *
 * 用 chrome.scripting.registerContentScripts 把 interceptor.js 注册为
 * world: 'MAIN' + runAt: 'document_start' 的动态脚本。Chrome 会在页面
 * 任何 JS 执行前直接注入,无 SW 唤醒延迟,无 <script> 标签异步加载。
 * 注册结果持久化在 Chrome 中,跨 SW 生命周期存活。
 *
 * 替代原 content.ts 的 <script> 标签注入方案(存在 race condition):
 * - <script> 标签异步加载,Boss JS 可能在拦截器安装前完成首次 API 请求
 * - registerContentScripts 由 Chrome 在 document_start 同步注入,无竞态
 */
// 共享 Promise 防止并发调用（onInstalled 和顶层调用可能同时触发）
let registerPromise: Promise<void> | null = null

async function registerInterceptor(): Promise<void> {
  if (registerPromise) return registerPromise
  registerPromise = doRegister()
  try {
    await registerPromise
  } finally {
    registerPromise = null
  }
}

async function doRegister(): Promise<void> {
  const SCRIPT_ID = 'boss-interceptor'
  try {
    const existing = await chrome.scripting.getRegisteredContentScripts({
      ids: [SCRIPT_ID],
    })
    if (existing.length > 0) {
      await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] })
    }
    await chrome.scripting.registerContentScripts([
      {
        id: SCRIPT_ID,
        matches: ['https://www.zhipin.com/*'],
        js: ['interceptor.js'],
        runAt: 'document_start',
        world: 'MAIN',
        allFrames: false,
      },
    ])
    console.log(
      '[SW] boss-interceptor registered (MAIN world, document_start)',
    )
  } catch (err) {
    console.error('[SW] registerInterceptor failed:', err)
  }
}

// ==================== SW 生命周期 ====================

chrome.runtime.onInstalled.addListener(async (details) => {
  console.log(
    '[SW] onInstalled | reason=',
    details.reason,
    '| id=',
    chrome.runtime.id,
  )
  await registerInterceptor()
})

chrome.runtime.onStartup.addListener(() => {
  console.log('[SW] onStartup | SW activated')
})

// ==================== 初始化消息路由 ====================

// 注册所有消息 handler（router 内部注册 AUTH_TOKEN_UPDATED + GET_SW_STATE + 6 个 stub）
initMessageRouter()

// SW 每次加载时确保拦截器已注册（兜底：onInstalled 可能未触发或注册失败）
void registerInterceptor()

console.log('[SW] service worker loaded |', new Date().toISOString())

export {}
