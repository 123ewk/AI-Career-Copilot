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
 * 监听主世界拦截器发送的消息
 *
 * 主世界拦截器（interceptor.js）通过 registerContentScripts 注入，
 * 在页面 JS 执行前 hook fetch/XHR，捕获到数据后通过 postMessage 发送。
 * Content Script 在 isolated world 中监听 message 事件接收数据。
 */
window.addEventListener('message', (event: MessageEvent) => {
  // 只处理 Boss 职位数据消息
  if (event.data?.type !== 'BOSS_JOB_DATA_CAPTURED') {
    return
  }

  // 确保消息来自同一窗口
  if (event.source !== window) {
    return
  }

  const capturedData = event.data.payload as CapturedApiPayload

  remoteLog('info', '[postMessage] received job data from main world', {
    url: capturedData.url?.slice(0, 120),
  })

  handleCapturedPayload(capturedData)
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
} else {
  // 非列表页：仅监听 URL 变化，跳转到列表页时启动
  bossAdapter.observe({
    onUrlChanged: (url, isListPage) => {
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
 */
function resetExtractionState(): void {
  clearApiTimeout()
  apiDataCaptured = false
  sentJobTracker.clear()
  currentSelectedDetailUrl = ''
  currentListPageUrl = window.location.href
  bossAdapter.disconnect()
  initListPage()

  remoteLog('info', '[content] RESET_EXTRACTION_STATE 完成，重新初始化列表页提取')
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
    onUrlChanged: (url, isListPage) => {
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
    default:
      return { ok: true }
  }
})

export {}
