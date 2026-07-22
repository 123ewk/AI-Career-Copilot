/**
 * Content Script 入口
 *
 * 职责：
 * - 监听主世界拦截器（interceptor.js）通过 postMessage 发送的 API 数据
 * - 解析数据为 RawBossJob，通过现有消息链路发送给 Service Worker
 * - 监听详情面板变化，补充 JD / 技能等信息
 * - 监听卡片点击，记录选中岗位的 detailUrl
 * - 监听 SPA URL 变化，切换搜索条件时重置状态
 *
 * 拦截器策略：
 * - 主世界拦截器通过 registerContentScripts (world: 'MAIN') 注入
 * - 在页面 JS 执行前 hook fetch/XHR，捕获 API 响应
 * - 通过 postMessage 将数据发送给 Content Script（isolated world）
 * - Content Script 接收后解析并发送给 Service Worker
 *
 * 运行环境：
 * - Content Script 隔离世界，可访问 document/window，但不能访问页面 JS 变量
 * - 可用 chrome.runtime.sendMessage 与 Service Worker 通信
 * - run_at: document_start
 */

import { bossAdapter } from '../modules/boss/adapter'
import { chatAdapter } from '../modules/boss/chat_adapter'
import { diagnoseSelectors } from '../modules/boss/chat_selector'
import type { RawBossJob } from '../modules/boss/parser'
import {
  ChromeMessageType,
  sendMessageToBackground,
  onMessage,
} from '../messaging/chrome_message'
import { BOSS_SELECTORS, queryAttribute } from '../modules/boss/selector'
import {
  parseBossApiResponse,
  isJobListApiPayload,
  type CapturedApiPayload,
} from '../modules/boss/api_parser'
import { remoteLog, flushRemoteLogs } from '../logging/remote_logger'

// ==================== 主世界拦截器数据接收 ====================

/**
 * 主世界拦截器支持的消息类型集合
 *
 * 设计动机：
 * - 拦截器现在拦截 3 类 API（Job/ChatList/ChatDetail），通过 type 字段区分
 * - Content Script 用一个统一的 listener 分发，避免重复注册多个 message 监听器
 * - 这里列出所有受支持的 type，便于类型收窄
 */
const SUPPORTED_CAPTURE_TYPES = [
  'BOSS_JOB_DATA_CAPTURED',
  'BOSS_CHAT_LIST_CAPTURED',
  'BOSS_CHAT_DETAIL_CAPTURED',
] as const

type CapturedMessageType = (typeof SUPPORTED_CAPTURE_TYPES)[number]

/**
 * 监听主世界拦截器发送的消息
 *
 * 主世界拦截器（interceptor.js）通过 registerContentScripts 注入，
 * 在页面 JS 执行前 hook fetch/XHR，捕获到数据后通过 postMessage 发送。
 * Content Script 在 isolated world 中监听 message 事件接收数据。
 *
 * 分发逻辑：
 * - BOSS_JOB_DATA_CAPTURED → handleCapturedPayload（已有，职位列表解析）
 * - BOSS_CHAT_LIST_CAPTURED → forwardChatCaptured（新增，转发给 SW）
 * - BOSS_CHAT_DETAIL_CAPTURED → forwardChatCaptured（新增，转发给 SW）
 *
 * Chat 数据不在 Content Script 中解析，直接原样转发给 SW，
 * 由 SW 调用 chat_api_parser 解析。原因：
 * - Content Script 不持有 conversation store，无法做合并
 * - SW 是数据汇聚点，适合做 API 列表 + DOM 消息的合并
 */
window.addEventListener('message', (event: MessageEvent) => {
  const type = event.data?.type as string | undefined
  if (!type || !SUPPORTED_CAPTURE_TYPES.includes(type as CapturedMessageType)) {
    return
  }

  // 确保消息来自同一窗口
  if (event.source !== window) {
    return
  }

  const capturedData = event.data.payload as CapturedApiPayload

  if (type === 'BOSS_JOB_DATA_CAPTURED') {
    remoteLog('info', '[postMessage] received job data from main world', {
      url: capturedData.url?.slice(0, 120),
    })
    handleCapturedPayload(capturedData)
    return
  }

  // Chat 数据：转发给 SW，由 SW 解析
  if (type === 'BOSS_CHAT_LIST_CAPTURED' || type === 'BOSS_CHAT_DETAIL_CAPTURED') {
    forwardChatCaptured(type, capturedData)
  }
})

/**
 * 监听主世界拦截器发送的日志消息
 *
 * 主世界拦截器通过 postMessage 发送日志，Content Script 转发到后端终端。
 */
window.addEventListener('message', (event: MessageEvent) => {
  if (event.data?.type !== 'BOSS_INTERCEPTOR_LOG') {
    return
  }

  if (event.source !== window) {
    return
  }

  const { level, message, context } = event.data.payload
  remoteLog(level, `[main-world] ${message}`, context)
})

/**
 * 处理捕获到的 API 响应
 *
 * 从主世界拦截器接收数据后解析并发送给 Service Worker。
 */
function handleCapturedPayload(payload: CapturedApiPayload): void {
  if (!isJobListApiPayload(payload)) return

  try {
    const result = parseBossApiResponse(payload, window.location.href)
    if (result.jobs.length === 0) return

    const newJobs = sentJobTracker.filterNewJobs(result.jobs)
    if (newJobs.length === 0) {
      remoteLog('info', `[source=API] interceptor returned ${result.jobs.length} jobs, all duplicates`)
      return
    }

    clearApiTimeout()
    apiDataCaptured = true  // 标记 API 数据已捕获，阻止后续超时触发

    remoteLog('info', `[source=API] interceptor captured data: parsed ${result.jobs.length} jobs, ${newJobs.length} new (DOM fallback cancelled)`, {
      jobCount: result.jobs.length,
      newJobCount: newJobs.length,
    })
    remoteLog('info', 'PATH_DECISION: 用户获取首屏数据走的是 API 拦截器路径', {
      path: 'api_interceptor',
      jobCount: newJobs.length,
    })
    flushRemoteLogs()
    void sendJobsExtracted(window.location.href, newJobs)
  } catch (err) {
    remoteLog('error', 'Error parsing captured API data', { error: err instanceof Error ? err.message : String(err) })
  }
}

/**
 * 转发 Chat API 拦截数据到 Service Worker
 *
 * 设计动机：
 * - Content Script 不解析 Chat API 响应（解析责任在 SW）
 * - 直接把拦截器原始 payload 转发，由 SW 调用 chat_api_parser 解析
 * - 通过 ChromeMessageType 区分列表 vs 详情，SW 走不同 handler
 *
 * @param captureType 主世界拦截器的消息类型（BOSS_CHAT_LIST_CAPTURED / BOSS_CHAT_DETAIL_CAPTURED）
 * @param captured 拦截器捕获的原始 payload
 */
function forwardChatCaptured(
  captureType: 'BOSS_CHAT_LIST_CAPTURED' | 'BOSS_CHAT_DETAIL_CAPTURED',
  captured: CapturedApiPayload,
): void {
  const messageType =
    captureType === 'BOSS_CHAT_LIST_CAPTURED'
      ? ChromeMessageType.CHAT_LIST_CAPTURED
      : ChromeMessageType.CHAT_DETAIL_CAPTURED

  remoteLog('info', `[postMessage] received chat data from main world (${captureType})`, {
    url: captured.url?.slice(0, 120),
    status: captured.status,
  })

  void sendMessageToBackground(messageType, {
    url: captured.url,
    method: captured.method,
    status: captured.status,
    data: captured.data,
    headers: captured.headers,
    pageUrl: window.location.href,
  })
}

remoteLog('info', 'Content script started. Waiting for main world interceptor data via postMessage. Fallback: DOM extraction after timeout.')

// ==================== 已发送岗位去重 ====================

/**
 * 已发送岗位去重器
 *
 * API 拦截器可能对同一接口的多次响应(滚动加载、分页)返回重复岗位,
 * 通过 detailUrl 去重避免 SidePanel 重复渲染。
 * URL 变化（切换搜索条件/分页）时清空，避免旧数据影响新列表。
 */
class SentJobTracker {
  private sentUrls = new Set<string>()

  /**
   * 过滤出未发送过的岗位，并记录为已发送
   */
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

  /**
   * 清空已发送记录（URL 变化时调用）
   */
  clear(): void {
    this.sentUrls.clear()
  }

  /**
   * 获取已记录数量（用于调试）
   */
  size(): number {
    return this.sentUrls.size
  }
}

const sentJobTracker = new SentJobTracker()

// ==================== 选中岗位追踪（详情面板关联用） ====================

/**
 * 当前选中岗位的详情页 URL
 *
 * 用户点击列表卡片后更新，用于 JOB_DETAIL_EXTRACTED 消息关联已创建的 Job
 * 初始为空字符串，未点击任何卡片时为空
 */
let currentSelectedDetailUrl: string = ''

/**
 * 监听卡片点击，记录选中岗位的 detailUrl
 *
 * 使用 capture 阶段（第三参数 true）的原因：
 * - Boss 直聘可能在 click 事件中调用 stopPropagation
 * - capture 阶段在事件冒泡前触发，确保能捕获到点击
 * - closest() 查找最近的 .job-card-box 祖先，处理点击命中子元素的情况
 */
document.addEventListener(
  'click',
  (event) => {
    const target = event.target as Element | null
    if (!target) return
    // closest 查找最近的 .job-card-box 祖先（点击可能命中卡片内子元素）
    const card = target.closest(BOSS_SELECTORS.list.jobCard)
    if (!card) return
    // 读取 .job-name 的 href（详情页 URL）
    const href = queryAttribute(card, BOSS_SELECTORS.list.detailLink, 'href')
    if (href) {
      // 相对 URL 转绝对 URL，与 JOBS_EXTRACTED 中的 detailUrl 一致
      currentSelectedDetailUrl = new URL(href, window.location.href).href
    }
  },
  true, // capture 阶段，确保在 Boss 的事件处理前捕获
)

// ==================== 页面初始化 ====================

/**
 * 初始化：检测页面类型
 */
const pageInfo = bossAdapter.detect()

/**
 * 当前列表页 URL（用于检测 SPA 路由变化）
 *
 * Content Script 在 document_start 注入，pageInfo.url 只反映初始 URL。
 * 后续 URL 变化通过 adapter 的轮询回调更新本变量。
 */
let currentListPageUrl: string = pageInfo.url

console.log(
  '[AI Career Copilot] Content script loaded | type=',
  pageInfo.type,
  '| url=',
  pageInfo.url,
)

/**
 * 仅在列表页启动提取和监听
 *
 * 非列表页（如首页、详情页）不启动，避免无意义监听
 * SPA 跳转到列表页时由 onUrlChanged 回调处理（见下方）
 */
if (pageInfo.isListPage) {
  initListPage()
} else if (pageInfo.isChatPage) {
  initChatPage()
} else {
  // 非列表页：仅监听 URL 变化，跳转到列表页时启动
  bossAdapter.observe({
    onUrlChanged: (url, isListPage, isChatPage) => {
      void sendMessageToBackground(ChromeMessageType.PAGE_CHANGED, {
        url,
        isBossListPage: isListPage,
      })
      // SPA 跳转到列表页时，重新初始化
      if (isListPage) {
        currentListPageUrl = url
        bossAdapter.disconnect()
        sentJobTracker.clear()
        currentSelectedDetailUrl = ''
        initListPage()
      }
      // SPA 跳转到聊天页时，重新初始化
      if (isChatPage) {
        bossAdapter.disconnect()
        initChatPage()
      }
    },
  })
}

/**
 * 清空 DOM 提取岗位的薪资字段
 *
 * Boss 的字体反爬是永久的字符映射（PUA Unicode → 数字字形），
 * innerText 返回 PUA 字符而非数字，字体加载与否不影响。
 * 清空 salaryRaw 避免 DOM 乱码覆盖 API 拦截器的明文薪资。
 */
function stripDomSalary(jobs: RawBossJob[]): RawBossJob[] {
  return jobs.map((j) => ({ ...j, salaryRaw: '' }))
}

/**
 * 首屏 DOM 兜底提取
 *
 * 当 API 拦截器超时/失效时提供首屏数据来源（title/company/tags/location，不含薪资）。
 * 由 setupApiTimeout 在 API 超时后触发，不主动调用。
 */
function runDomFallbackForInitialPage(): void {
  // 临时诊断代码，验证后移除：记录 5s 超时时的 DOM 状态，区分「DOM 无 jobCard 元素」和「有元素但 extractJobs 没提取到」
  const diagTs = Date.now()
  const listContainerExists = !!document.querySelector(BOSS_SELECTORS.list.listContainer)
  const domJobCardCount = document.querySelectorAll(BOSS_SELECTORS.list.jobCard).length
  remoteLog(
    'info',
    '[diag] runDomFallback enter',
    { ts: diagTs, readyState: document.readyState, listContainerExists, domJobCardCount },
  )
  remoteLog(
    'info',
    '[source=DOM-fallback] API interceptor did not capture usable data; starting DOM fallback extraction',
  )
  const jobs = bossAdapter.extractJobs()
  if (jobs.length === 0) {
    remoteLog('info', '[source=DOM-fallback] no jobs found on page', {
      ts: diagTs,
      domJobCardCount,
      listContainerExists,
    })
    remoteLog(
      'warn',
      'PATH_DECISION: API 拦截器 5s 超时未捕获数据，DOM 兜底暂未发现已渲染卡片。' +
        'MutationObserver 仍在监听，页面渲染完成后将自动捕获',
      { path: 'waiting', reason: 'api_timeout_and_dom_not_ready', domJobCardCount },
    )
    return
  }
  const newJobs = sentJobTracker.filterNewJobs(jobs)
  if (newJobs.length === 0) {
    remoteLog(
      'info',
      `[source=DOM-fallback] ${jobs.length} jobs already captured by API (skipped)`,
    )
    return
  }
  remoteLog(
    'info',
    `[source=DOM-fallback] extracted ${newJobs.length} new jobs`,
    { jobCount: jobs.length, newJobCount: newJobs.length, ts: diagTs, domJobCardCount },
  )
  remoteLog(
    'warn',
    'PATH_DECISION: 用户获取首屏数据走的是 DOM 兜底路径（API 拦截器未捕获到数据）',
    { path: 'dom_fallback', jobCount: newJobs.length, domJobCardCount },
  )
  flushRemoteLogs()
  void sendJobsExtracted(window.location.href, stripDomSalary(newJobs))
}

// ==================== API 超时 → DOM 兜底 ====================

const API_TIMEOUT_MS = 5000
let apiTimeoutTimer: ReturnType<typeof setTimeout> | null = null
let apiDataCaptured = false  // 标记 API 数据是否已捕获，防止超时后重复触发

function clearApiTimeout(): void {
  if (apiTimeoutTimer) {
    clearTimeout(apiTimeoutTimer)
    apiTimeoutTimer = null
  }
}

/**
 * 重置 Content Script 全部提取状态
 *
 * SidePanel 重新打开 / 登出后重新登录时调用：
 * - 清空 API 捕获标记与超时定时器
 * - 清空已发送岗位去重器
 * - 清空当前选中岗位与列表页 URL
 * - 断开并重新挂载 adapter observer
 *
 * 页面类型分流：
 * - 之前无条件调用 initListPage()，导致在聊天页时错误走列表页流程
 * - 现在根据 chatAdapter.detect() 判断当前页面类型，调用对应的初始化函数
 * - 列表页 → initListPage()；聊天页 → initChatPage()
 */
function resetExtractionState(): void {
  clearApiTimeout()
  apiDataCaptured = false
  sentJobTracker.clear()
  currentSelectedDetailUrl = ''
  currentListPageUrl = window.location.href
  bossAdapter.disconnect()

  // 根据当前页面类型选择初始化函数
  // chatAdapter.detect() 返回 'chat' 表示当前在 BOSS 聊天页
  if (chatAdapter.detect() === 'chat') {
    initChatPage()
    remoteLog('info', '[content] RESET_EXTRACTION_STATE 完成，重新初始化聊天页提取')
  } else {
    initListPage()
    remoteLog('info', '[content] RESET_EXTRACTION_STATE 完成，重新初始化列表页提取')
  }
}

/**
 * 设置 API 超时定时器
 *
 * 设计说明：
 * - API 拦截器本身是常驻监听的（hook 了 fetch/XHR），不会因超时而停止
 * - 这个 5 秒超时是 Content Script 侧的"首屏降级"机制：
 *   如果 5 秒内拦截器没捕获到首屏 API 数据，就用 DOM 兜底提供基本数据
 * - 超时后拦截器仍在运行，后续如果捕获到 API 数据（如滚动加载），
 *   会通过 sentJobTracker 去重，不会重复发送
 * - 如果首屏 API 数据在 DOM 兜底之后到达，API 数据会被去重跳过
 *   （薪资信息会丢失，但这是可接受的降级代价）
 *
 * API 捕获到数据时调用 clearApiTimeout() 取消定时器。
 */
function setupApiTimeout(): void {
  clearApiTimeout()

  // 如果 API 数据已经被捕获，不再设置超时
  if (apiDataCaptured) {
    remoteLog('info', '[source=API] 数据已捕获，跳过超时设置')
    return
  }

  apiTimeoutTimer = setTimeout(() => {
    // 再次检查，防止在超时触发前 API 数据刚好到达
    if (apiDataCaptured) {
      remoteLog('info', '[source=API] 数据已在超时前捕获，取消 DOM 兜底')
      apiTimeoutTimer = null
      return
    }
    remoteLog(
      'warn',
      `[source=API] interceptor did not capture data within ${API_TIMEOUT_MS / 1000}s; will switch to [source=DOM-fallback]`,
    )
    runDomFallbackForInitialPage()
    apiTimeoutTimer = null
  }, API_TIMEOUT_MS)
}

// ==================== 启动刷新检测（修复 P3 根因） ====================

/**
 * sessionStorage key：标记当前 Tab 是否已执行过扩展启动刷新
 *
 * 仅用 sessionStorage 防循环，不使用 URL 参数：
 * - URL 参数需要 history.replaceState 清理，可能干扰 Boss 的 SPA 路由
 * - sessionStorage 在 Tab 生命周期内有效，刷新后仍存在，足以保证只刷新一次
 */
const RELOAD_FLAG_KEY = '__acc_extension_reloaded'

/**
 * 检测并执行启动刷新
 *
 * 场景：用户先打开 Boss 列表页，后启用/安装扩展。
 * 此时页面 API 已完成，拦截器来不及拦截。
 * 通过 location.reload() 刷新页面，让拦截器在 document_start 注入后
 * 能拦住刷新后的 API 调用。
 *
 * 防循环：仅用 sessionStorage 标记，同一 Tab 会话只刷新一次。
 * SPA 路由切换不会触发（URL 不变，不走 initListPage 的刷新分支）。
 * REFRESH_JOBS（手动刷新）不清除此标记，F3 与 F5 职责独立。
 *
 * @returns true 表示已触发刷新，调用方应停止后续初始化
 */
function maybeReloadOnStartup(): boolean {
  // 已经刷新过（sessionStorage 标记存在），跳过
  if (sessionStorage.getItem(RELOAD_FLAG_KEY)) {
    return false
  }

  // 页面仍在加载中，拦截器有机会拦住 API，不需要刷新
  if (document.readyState === 'loading') {
    return false
  }

  // 页面已加载完成（interactive / complete），API 已结束
  // 标记并刷新，让拦截器在下次 document_start 注入后生效
  sessionStorage.setItem(RELOAD_FLAG_KEY, '1')
  remoteLog(
    'warn',
    'PATH_DECISION: 扩展在已加载页面启动，主动刷新以确保 API 拦截器生效',
    { path: 'startup_reload', readyState: document.readyState },
  )
  flushRemoteLogs()
  location.reload()
  return true
}

/**
 * 列表页初始化逻辑
 *
 * 数据源优先级：
 * 1. API 拦截器（主路径）：捕获 Boss 的 /wapi/zpgeek/.../job/list.json 响应
 * 2. DOM observer（滚动补充）：监听 .rec-job-list 子节点变化，提取新卡片
 * 3. DOM 首屏兜底：API 5 秒超时后触发，提供首屏数据（薪资为空）
 *
 * 时序：
 * - 页面加载 → API 拦截器在 document_start 注入 → 捕获首屏 API 请求
 * - 如果 5 秒内 API 未捕获到数据 → 触发 DOM 兜底
 * - API 捕获到数据后取消超时，不再触发 DOM 兜底
 */
function initListPage(): void {
  // 临时诊断代码，验证后移除：记录 initListPage 进入时序和 maybeReloadOnStartup 决策
  const initTs = Date.now()
  remoteLog(
    'info',
    '[diag] initListPage enter',
    { ts: initTs, readyState: document.readyState },
  )
  // 启动刷新检测：如果页面已加载完成，刷新让拦截器生效
  if (maybeReloadOnStartup()) {
    remoteLog('info', '[diag] maybeReloadOnStartup result', { ts: initTs, reloaded: true })
    return
  }
  remoteLog('info', '[diag] maybeReloadOnStartup result', { ts: initTs, reloaded: false })

  // 重置 API 数据捕获标记（新页面加载时）
  apiDataCaptured = false
  setupApiTimeout()

  bossAdapter.observe({
    onJobsExtracted: (jobs) => {
      const newJobs = sentJobTracker.filterNewJobs(jobs)
      if (newJobs.length > 0) {
        remoteLog(
          'info',
          `[source=DOM-observer] ${newJobs.length} new jobs (scroll/append)`,
          { newJobCount: newJobs.length },
        )
        remoteLog(
          'info',
          'PATH_DECISION: 用户获取滚动加载数据走的是 DOM observer 路径',
          { path: 'dom_observer', jobCount: newJobs.length },
        )
        flushRemoteLogs()
        void sendJobsExtracted(
          window.location.href,
          stripDomSalary(newJobs),
        )
      }
    },
    onDetailExtracted: (detail) => {
      void sendDetailExtracted(detail)
    },
    onUrlChanged: (url, isListPage, isChatPage) => {
      void sendMessageToBackground(ChromeMessageType.PAGE_CHANGED, {
        url,
        isBossListPage: isListPage,
      })
      // 切换搜索条件/分页时清空去重器并重新挂载 observer
      if (isListPage && url !== currentListPageUrl) {
        currentListPageUrl = url
        bossAdapter.disconnect()
        clearApiTimeout()
        sentJobTracker.clear()
        currentSelectedDetailUrl = ''
        initListPage()
      }
      // 跳转到聊天页时，重新初始化
      if (isChatPage) {
        bossAdapter.disconnect()
        clearApiTimeout()
        initChatPage()
      }
    },
  })
}

// ==================== 聊天页初始化 ====================

/** 聊天页 DOM 就绪检测选择器（至少对话列表容器存在即可开始） */
const CHAT_READY_SELECTOR = '.user-list-content'

/** 聊天页 DOM 等待最大重试次数（每次 500ms，共 5s） */
const CHAT_READY_MAX_RETRY = 10
/** 聊天页 DOM 等待重试间隔（ms） */
const CHAT_READY_RETRY_INTERVAL = 500

/**
 * 等待聊天页 DOM 就绪
 *
 * BOSS 直聘是 Vue SPA，聊天页内容是异步渲染的。
 * content script 在 document_start 注入，但 .user-list-content 等元素
 * 可能在 Vue 组件挂载后才出现在 DOM 中。
 * 用轮询等待关键元素出现后再执行提取。
 *
 * @param callback DOM 就绪后的回调
 * @param retryCount 当前重试次数
 */
function waitForChatDomReady(
  callback: () => void,
  retryCount = 0,
): void {
  const container = document.querySelector(CHAT_READY_SELECTOR)
  if (container) {
    // DOM 就绪，执行回调
    console.log(`[AI Career Copilot] Chat DOM ready after ${retryCount} retries`)
    callback()
    return
  }

  if (retryCount >= CHAT_READY_MAX_RETRY) {
    // 超时：DOM 仍未就绪，尝试用 MutationObserver 监听
    console.warn(
      `[AI Career Copilot] Chat DOM not ready after ${CHAT_READY_MAX_RETRY} retries, using MutationObserver fallback`,
    )
    waitForChatDomViaObserver(callback)
    return
  }

  setTimeout(() => {
    waitForChatDomReady(callback, retryCount + 1)
  }, CHAT_READY_RETRY_INTERVAL)
}

/**
 * MutationObserver 兜底：监听 DOM 变化等待关键元素出现
 *
 * 当轮询超时后，用 MutationObserver 监听 document.body 的子节点变化，
 * 一旦 .user-list-content 出现就触发回调。
 */
function waitForChatDomViaObserver(callback: () => void): void {
  // 先再检查一次（可能刚好在轮询超时和 observer 挂载之间出现）
  const container = document.querySelector(CHAT_READY_SELECTOR)
  if (container) {
    callback()
    return
  }

  let resolved = false
  const observer = new MutationObserver(() => {
    if (resolved) return
    const el = document.querySelector(CHAT_READY_SELECTOR)
    if (el) {
      resolved = true
      observer.disconnect()
      console.log('[AI Career Copilot] Chat DOM ready via MutationObserver')
      callback()
    }
  })

  observer.observe(document.body, { childList: true, subtree: true })

  // 10 秒超时，避免 observer 永久挂载
  setTimeout(() => {
    if (!resolved) {
      resolved = true
      observer.disconnect()
      console.warn('[AI Career Copilot] Chat DOM observer timeout (10s), proceeding anyway')
      callback()
    }
  }, 10000)
}

/**
 * 聊天页初始化逻辑
 *
 * 检测到 BOSS 聊天页时：
 * 1. 等待 DOM 就绪（Vue SPA 异步渲染）
 * 2. 通知 SW 当前在聊天页
 * 3. 提取当前对话的消息 + 对话详情（公司、职位、薪资）
 * 4. 启动 MutationObserver 监听消息变化和对话切换
 * 5. 监听 SW 转发的注入指令
 */
function initChatPage(): void {
  // 通知 SW 当前页面状态（立即发送，不等 DOM）
  void sendMessageToBackground(ChromeMessageType.PAGE_CHANGED, {
    url: window.location.href,
    isBossListPage: false,
  })

  // 等待 DOM 就绪后再执行提取
  waitForChatDomReady(() => {
    doInitChatPage()
  })
}

/**
 * 对话列表提取最大重试次数
 *
 * 设计动机:Vue SPA 异步渲染,.user-list-content 容器已存在但
 * .friend-content 列表项可能还没渲染完。waitForChatDomReady 只等
 * 容器出现,不等列表项。所以 extractConversations 可能返回空数组。
 * 5 次 × 500ms = 2.5s,覆盖大多数 Vue 渲染延迟。
 */
const CONVERSATION_EXTRACT_MAX_RETRY = 5

/** 对话列表提取重试间隔(ms) */
const CONVERSATION_EXTRACT_RETRY_INTERVAL = 500

/**
 * 提取并发送对话列表(带重试)
 *
 * 场景:
 * - Vue SPA 异步渲染,doInitChatPage 时 .friend-content 列表项可能未渲染完
 * - extractConversations 返回空数组时不发送,避免 SW 缓存被空数据覆盖
 * - 空数组时延迟重试,最多 5 次
 * - 重试 5 次仍为空,放弃(依赖 chatAdapter.observe 的 MutationObserver 后续补发)
 *
 * 日志:每次重试都打印,便于调试 Vue 渲染时序问题
 *
 * @param retryCount 当前重试次数(内部递归用,外部调用不传)
 */
function extractAndSendConversations(retryCount = 0): void {
  const conversations = chatAdapter.extractConversations()

  if (conversations.length === 0) {
    if (retryCount < CONVERSATION_EXTRACT_MAX_RETRY) {
      console.log(
        `[Content] extractConversations 返回空(retry=${retryCount}/${CONVERSATION_EXTRACT_MAX_RETRY}),` +
          `延迟 ${CONVERSATION_EXTRACT_RETRY_INTERVAL}ms 重试(可能 .friend-content 列表项未渲染完)`,
      )
      setTimeout(
        () => extractAndSendConversations(retryCount + 1),
        CONVERSATION_EXTRACT_RETRY_INTERVAL,
      )
      return
    }
    console.warn(
      '[Content] extractConversations 重试 5 次仍为空,放弃(依赖 chatAdapter.observe 的 MutationObserver 后续补发)',
    )
    return
  }

  // 提取成功,发送给 SW 缓存 + 广播到 SidePanel
  void sendMessageToBackground(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, {
    conversations: conversations.map((c) => ({
      id: c.id,
      recruiterName: c.recruiterName,
      company: c.company,
      lastMessage: c.lastMessage,
      isActive: c.isActive,
    })),
    pageUrl: window.location.href,
  })
  console.log(
    `[Content] extractAndSendConversations 成功 | count=${conversations.length} | retry=${retryCount}`,
  )
}

/**
 * 聊天页实际初始化逻辑（DOM 已就绪）
 *
 * 注意:对话列表提取带重试机制(见 extractAndSendConversations),
 * 解决 Vue SPA 异步渲染导致 .friend-content 列表项延迟出现的问题。
 */
function doInitChatPage(): void {
  const recruiterName = chatAdapter.getActiveConversationName()
  const conversationId = `conv-${Date.now()}-${recruiterName}`
  const detail = chatAdapter.getConversationDetail()

  console.log(
    '[AI Career Copilot] Chat page detected | recruiter=',
    recruiterName,
    '| company=',
    detail?.company ?? '',
    '| job=',
    detail?.jobTitle ?? '',
  )

  // 通知 SW 当前在聊天页（含对话详情）
  void sendMessageToBackground(ChromeMessageType.CHAT_PAGE_DETECTED, {
    pageUrl: window.location.href,
    recruiterName,
    company: detail?.company ?? '',
    jobTitle: detail?.jobTitle ?? '',
    jobSalary: detail?.jobSalary ?? '',
  })

  // 提取并发送左侧对话列表（带重试,防止列表项异步渲染未完成）
  extractAndSendConversations()

  // 初始提取消息
  const messages = chatAdapter.extractMessages()
  if (messages.length > 0) {
    void sendMessageToBackground(ChromeMessageType.CHAT_MESSAGES_EXTRACTED, {
      conversationId,
      recruiterName,
      company: detail?.company ?? '',
      jobTitle: detail?.jobTitle ?? '',
      jobSalary: detail?.jobSalary ?? '',
      messages,
      pageUrl: window.location.href,
    })
  }

  // 发送选择器诊断结果（帮助调试选择器是否匹配）
  const diagnostics = diagnoseSelectors()
  console.log('[AI Career Copilot] Chat diagnostics:', diagnostics)
  void sendMessageToBackground(ChromeMessageType.CHAT_DIAGNOSE, {
    diagnostics,
  })

  // 启动监听
  chatAdapter.observe({
    onMessagesChanged: (msgs) => {
      const currentRecruiter = chatAdapter.getActiveConversationName()
      const currentDetail = chatAdapter.getConversationDetail()
      void sendMessageToBackground(ChromeMessageType.CHAT_MESSAGES_EXTRACTED, {
        conversationId,
        recruiterName: currentRecruiter || recruiterName,
        company: currentDetail?.company ?? detail?.company ?? '',
        jobTitle: currentDetail?.jobTitle ?? detail?.jobTitle ?? '',
        jobSalary: currentDetail?.jobSalary ?? detail?.jobSalary ?? '',
        messages: msgs,
        pageUrl: window.location.href,
      })
    },
    onConversationSwitched: (newRecruiterName) => {
      const newConvId = `conv-${Date.now()}-${newRecruiterName}`
      // 切换对话后，等待 DOM 更新再提取详情
      setTimeout(() => {
        const newDetail = chatAdapter.getConversationDetail()
        void sendMessageToBackground(ChromeMessageType.CHAT_CONVERSATION_CHANGED, {
          pageUrl: window.location.href,
          recruiterName: newRecruiterName,
          conversationId: newConvId,
          company: newDetail?.company ?? '',
          jobTitle: newDetail?.jobTitle ?? '',
          jobSalary: newDetail?.jobSalary ?? '',
        })
      }, 600)
    },
    /**
     * 对话列表变化回调(2026-07-21 修复)
     *
     * 触发场景:
     * - Content Script 启动时 DOM 未渲染完,只拿到部分对话(或 0 个)
     * - 后续 BOSS SPA 异步渲染完成,列表项增多
     * - 用户翻页/搜索/筛选切换 HR 列表
     *
     * 修复的问题:
     * - 原 observe() 不监听列表项变化,导致 Content Script 启动时拿到的 0 个对话永远无法补发
     * - 现通过此回调重新提取并发送,让 SW + SidePanel 都能拿到最新列表
     *
     * 注:chatAdapter 内部已有签名对比 + 防抖,这里无需额外防抖
     */
    onConversationsListChanged: (conversations) => {
      if (conversations.length === 0) return
      console.log(
        `[Content] onConversationsListChanged | count=${conversations.length} | 补发 CHAT_CONVERSATIONS_EXTRACTED`,
      )
      void sendMessageToBackground(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, {
        conversations: conversations.map((c) => ({
          id: c.id,
          recruiterName: c.recruiterName,
          company: c.company,
          lastMessage: c.lastMessage,
          isActive: c.isActive,
        })),
        pageUrl: window.location.href,
      })
    },
  })
}

// ==================== 消息发送辅助函数 ====================

/**
 * 发送 JOBS_EXTRACTED 消息到 Service Worker
 *
 * @param pageUrl 当前列表页 URL
 * @param jobs 提取到的岗位数据
 */
async function sendJobsExtracted(
  pageUrl: string,
  jobs: RawBossJob[],
): Promise<void> {
  await sendMessageToBackground(ChromeMessageType.JOBS_EXTRACTED, {
    pageUrl,
    jobs,
  })
}

/**
 * 发送 JOB_DETAIL_EXTRACTED 消息到 Service Worker
 *
 * 仅在以下条件满足时发送：
 * 1. detail.jdText 非空（spec §4.3：仅当 jd_text 非空时才调用 PATCH）
 * 2. currentSelectedDetailUrl 非空（需要关联到已创建的 Job）
 *
 * @param detail 详情面板提取的数据
 */
async function sendDetailExtracted(
  detail: Partial<RawBossJob>,
): Promise<void> {
  // jdText 为空说明详情面板未加载完成，跳过
  if (!detail.jdText) return
  // 未选中卡片，无法关联到已创建的 Job
  if (!currentSelectedDetailUrl) return

  await sendMessageToBackground(ChromeMessageType.JOB_DETAIL_EXTRACTED, {
    sourceUrl: currentSelectedDetailUrl,
    jdText: detail.jdText,
    skills: detail.skills ?? [],
    address: detail.address,
    recruiterName: detail.recruiterName,
    recruiterTitle: detail.recruiterTitle,
  })
}

// ==================== 监听 Service Worker 消息 ====================

/**
 * 在 Boss 列表页查找并点击对应岗位卡片
 *
 * 通过 sourceUrl（岗位详情页 URL）匹配 .job-card-box 内的 .job-name href，
 * 找到后滚动到可视区域并模拟点击，触发 Boss 详情面板加载。
 *
 * @param sourceUrl 岗位详情页 URL
 * @returns 是否找到并点击成功
 */
function clickBossJobCard(sourceUrl: string): { ok: boolean; error?: string } {
  if (!sourceUrl) {
    return { ok: false, error: 'sourceUrl 为空' }
  }

  const info = bossAdapter.detect()
  if (!info.isListPage) {
    return { ok: false, error: '当前页面不是 Boss 列表页' }
  }

  const cards = document.querySelectorAll(BOSS_SELECTORS.list.jobCard)
  for (const card of cards) {
    const href = queryAttribute(card, BOSS_SELECTORS.list.detailLink, 'href')
    if (!href) continue

    const absoluteHref = new URL(href, window.location.href).href
    if (absoluteHref === sourceUrl) {
      // 先滚动到可视区域，再点击，避免虚拟列表中卡片不可点击
      card.scrollIntoView({ behavior: 'smooth', block: 'center' })
      // 稍微延迟点击，让滚动动画先开始，Boss 页面事件绑定有足够时间响应
      setTimeout(() => {
        ;(card as HTMLElement).click()
      }, 150)
      return { ok: true }
    }
  }

  return { ok: false, error: '未找到对应岗位卡片，请检查列表是否已加载或已滚动到该岗位' }
}

/**
 * 监听 Service Worker 转发的消息
 *
 * 当前处理：
 * - REFRESH_JOBS：SidePanel 手动刷新，清空去重器 + 重新加载页面让 API 拦截器重新拦截
 * - LOAD_JOB_DETAIL：SidePanel 请求点击 Boss 页面对应岗位卡片，加载详情面板
 */
onMessage((message) => {
  switch (message.type) {
    case ChromeMessageType.REFRESH_JOBS: {
      // 用户手动刷新：清空去重器，让 API 拦截器重新拦截首屏 API
      //
      // 关键：不清除 sessionStorage 启动标记，F3 与 F5 职责独立
      // - F3（启动刷新）只在首次扩展启动时触发一次，避免循环
      // - F5（手动刷新）由用户/SidePanel 主动触发，应当能 reload
      //
      // readyState 防护：若页面仍在 loading（可能正被 F3 触发的刷新加载中），
      // 不再二次 reload，仅重置 API 超时，等待当前加载完成自然触发 API 拦截
      remoteLog(
        'info',
        'PATH_DECISION: 用户手动刷新，清空去重器',
        { path: 'manual_refresh', readyState: document.readyState },
      )
      sentJobTracker.clear()
      flushRemoteLogs()
      if (document.readyState === 'complete') {
        // reload 会中断当前消息通道，先返回 ok，再异步 reload
        setTimeout(() => location.reload(), 0)
      } else {
        // 页面仍在加载：reload 会与 F3 的刷新叠加导致双重刷新循环
        // 仅重置 API 超时，等当前页面加载完成
        setupApiTimeout()
      }
      return { ok: true }
    }
    case ChromeMessageType.RESET_EXTRACTION_STATE: {
      resetExtractionState()
      return { ok: true }
    }
    case ChromeMessageType.LOAD_JOB_DETAIL: {
      const payload = message.payload as {
        sourceUrl: string
      }
      // 点击前先记录选中 URL，确保详情面板加载后能正确关联
      currentSelectedDetailUrl = payload.sourceUrl
      const result = clickBossJobCard(payload.sourceUrl)
      return result
    }
    case ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED: {
      // SidePanel 请求对话列表：等待 DOM 就绪后提取 + 直接回复 + 发送到 SW 缓存
      return new Promise((resolve) => {
        waitForChatDomReady(() => {
          const conversations = chatAdapter.extractConversations()
          void sendMessageToBackground(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, {
            conversations: conversations.map((c) => ({
              id: c.id,
              recruiterName: c.recruiterName,
              company: c.company,
              lastMessage: c.lastMessage,
              isActive: c.isActive,
            })),
            pageUrl: window.location.href,
          })
          resolve({
            ok: true,
            data: {
              conversations: conversations.map((c) => ({
                id: c.id,
                recruiterName: c.recruiterName,
                company: c.company,
                lastMessage: c.lastMessage,
                isActive: c.isActive,
              })),
            },
          })
        })
      })
    }
    case ChromeMessageType.INJECT_CHAT_TEXT: {
      const payload = message.payload as { text: string }
      const ok = chatAdapter.injectText(payload.text)
      return { ok, error: ok ? undefined : '注入文本失败：未找到输入框' }
    }
    case ChromeMessageType.INJECT_AND_SEND_CHAT_TEXT: {
      const payload = message.payload as { text: string }
      const injected = chatAdapter.injectText(payload.text)
      if (!injected) {
        return { ok: false, error: '注入文本失败：未找到输入框' }
      }
      // 延迟 500ms 确保文本已渲染，再点击发送
      setTimeout(() => {
        chatAdapter.clickSend()
      }, 500)
      return { ok: true }
    }
    default:
      return { ok: true }
  }
})

export {}
