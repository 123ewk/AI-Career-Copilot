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
import { initSession } from './backend_client'

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

/**
 * 拦截器的期望注册配置
 *
 * 提取为模块级常量，doRegister 和 isInterceptorConfigMatch 共用。
 * 未来修改注册配置时只改这一处，避免比对逻辑和注册逻辑不一致。
 */
const DESIRED_INTERCEPTOR_CONFIG: chrome.scripting.RegisteredContentScript = {
  id: 'boss-interceptor',
  matches: ['https://www.zhipin.com/*'],
  js: ['interceptor.js'],
  runAt: 'document_start',
  world: 'MAIN',
  allFrames: false,
}

/**
 * 比对已注册脚本配置是否与期望配置完全匹配
 *
 * 为什么需要这个函数：
 * registerContentScripts 的注册结果持久化在 Chrome 中，跨 SW 生命周期存活。
 * SW 重启时如果脚本已注册且配置正确，完全不需要重新注册。
 * 直接跳过可消除「先 unregister 再 register」的窗口期——
 * 窗口期内拦截器被移除，此时触发的页面 reload 会导致拦截器不注入。
 */
function isInterceptorConfigMatch(
  script: chrome.scripting.RegisteredContentScript,
  desired: chrome.scripting.RegisteredContentScript,
): boolean {
  // matches / js 在 RegisteredContentScript 中是 optional，用 ?? [] 兜底
  const scriptMatches = script.matches ?? []
  const scriptJs = script.js ?? []
  const desiredMatches = desired.matches ?? []
  const desiredJs = desired.js ?? []
  const matchesOk =
    scriptMatches.length === desiredMatches.length &&
    scriptMatches.every((m) => desiredMatches.includes(m))
  const jsOk =
    scriptJs.length === desiredJs.length &&
    scriptJs.every((j) => desiredJs.includes(j))
  const runAtOk = script.runAt === desired.runAt
  const worldOk = script.world === desired.world
  const allFramesOk = script.allFrames === desired.allFrames
  return matchesOk && jsOk && runAtOk && worldOk && allFramesOk
}

async function doRegister(): Promise<void> {
  const SCRIPT_ID = DESIRED_INTERCEPTOR_CONFIG.id
  console.log('[SW] doRegister 开始执行，SCRIPT_ID:', SCRIPT_ID)
  try {
    const existing = await chrome.scripting.getRegisteredContentScripts({
      ids: [SCRIPT_ID],
    })
    console.log('[SW] 已注册的脚本数量:', existing.length)
    if (existing.length > 0) {
      if (isInterceptorConfigMatch(existing[0], DESIRED_INTERCEPTOR_CONFIG)) {
        console.log('[SW] ✅ interceptor 已注册且配置匹配，跳过重新注册（避免 race condition）')
        return
      }
      console.log('[SW] 发现已注册脚本但配置不匹配，重新注册...')
      await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] })
    }

    console.log('[SW] 正在注册 interceptor.js...')
    await chrome.scripting.registerContentScripts([DESIRED_INTERCEPTOR_CONFIG])
    console.log('[SW] ✅ boss-interceptor 注册成功 (MAIN world, document_start)')

    const verify = await chrome.scripting.getRegisteredContentScripts({
      ids: [SCRIPT_ID],
    })
    console.log('[SW] 验证注册结果:', JSON.stringify(verify, null, 2))
  } catch (err) {
    console.error('[SW] ❌ registerInterceptor 失败:', err)
    console.error('[SW] 错误详情:', JSON.stringify(err, Object.getOwnPropertyNames(err)))
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

console.log('[SW] service worker 模块加载，准备初始化消息路由...')

// 注册所有消息 handler（router 内部注册 AUTH_TOKEN_UPDATED + GET_SW_STATE + 6 个 stub）
initMessageRouter()

console.log('[SW] 消息路由初始化完成，准备注册拦截器...')

// SW 每次加载时确保拦截器已注册（兜底：onInstalled 可能未触发或注册失败）
void registerInterceptor()

// SW 启动时尝试从 storage 恢复 backendUrl，并用 refresh cookie 静默刷新 access token。
// 失败属于正常未登录状态，SidePanel/Popup 查询 GET_SW_STATE 时会得到最终结果。
void initSession()

console.log('[SW] service worker loaded |', new Date().toISOString())

export {}
