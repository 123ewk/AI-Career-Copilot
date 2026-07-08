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

// ==================== SW 生命周期 ====================

chrome.runtime.onInstalled.addListener((details) => {
  console.log(
    '[SW] onInstalled | reason=',
    details.reason,
    '| id=',
    chrome.runtime.id,
  )
})

chrome.runtime.onStartup.addListener(() => {
  console.log('[SW] onStartup | SW activated')
})

// ==================== 初始化消息路由 ====================

// 注册所有消息 handler（router 内部注册 AUTH_TOKEN_UPDATED + GET_SW_STATE + 6 个 stub）
initMessageRouter()

console.log('[SW] service worker loaded |', new Date().toISOString())

export {}
