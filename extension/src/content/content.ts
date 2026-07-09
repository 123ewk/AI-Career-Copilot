/**
 * Content Script 入口
 *
 * 职责：
 * - 兜底注入主世界拦截器（主路径为 SW 的 registerContentScripts）
 * - 接收主世界拦截器通过 postMessage 发送的 API 响应数据
 * - 解析数据为 RawBossJob，通过现有消息链路发送给 Service Worker
 * - 监听详情面板变化，补充 JD / 技能等信息
 * - 监听卡片点击，记录选中岗位的 detailUrl
 * - 监听 SPA URL 变化，切换搜索条件时重置状态
 *
 * 消息流向：
 *   主世界 interceptor.js → window.postMessage → Content Script → chrome.runtime.sendMessage → Service Worker → 后端
 *
 * 拦截器注入策略（双路径）：
 * - 主路径：SW 通过 chrome.scripting.registerContentScripts 注册
 *   world: 'MAIN' + runAt: 'document_start'（无竞态,早于页面 JS）
 * - 兜底路径：Content Script 通过 <script> 标签注入
 *   （有竞态,但确保主路径失败时数据链路不断）
 * - interceptor.js 的 __bossJobInterceptorInstalled 标志防重复安装
 *
 * 设计动机：
 * - adapter.ts 仅保留详情面板 DOM 监听和 URL 轮询,不再作为列表数据 fallback
 *   (DOM 提取的薪资受字体反爬影响为乱码,已废弃)
 * - api_parser.ts 将 Boss 内部 API 响应转换为现有 RawBossJob 格式
 * - 不直接调用 chrome.storage / fetch（在 Content Script 中受限，由 SW 代理）
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

// ==================== 主世界拦截器注入（兜底） ====================

/**
 * 向页面主世界注入 API 拦截器脚本（兜底路径）
 *
 * 主路径：SW 通过 chrome.scripting.registerContentScripts 在 document_start
 * 注入 world: 'MAIN' 的 interceptor.js（无竞态，早于页面 JS）。
 *
 * 兜底路径：如果 SW 注册失败（onInstalled 未触发、Chrome 版本不支持
 * world: 'MAIN' 等），Content Script 通过 <script> 标签注入，确保拦截器
 * 至少能装上（有 race condition，但数据链路不断）。
 *
 * interceptor.js 的 __bossJobInterceptorInstalled 标志防止重复安装，
 * 两条路径安全共存。
 */
function injectBossApiInterceptor(): void {
  if (document.querySelector('script[data-boss-interceptor]')) {
    return
  }

  const script = document.createElement('script')
  script.src = chrome.runtime.getURL('interceptor.js')
  script.dataset.bossInterceptor = 'true'
  script.onload = () => script.remove()
  ;(document.head || document.documentElement).appendChild(script)
}

// 立即注入，确保在 Boss 页面发起职位列表请求前完成安装
injectBossApiInterceptor()

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

// ==================== 监听主世界消息（API 数据） ====================

/**
 * 监听主世界拦截器通过 postMessage 发送的职位列表数据
 *
 * 只处理来自同一窗口、类型为 BOSS_JOB_DATA_CAPTURED 的消息。
 */
window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window) return
  if (event.data?.type !== 'BOSS_JOB_DATA_CAPTURED') return

  const payload = event.data.payload as CapturedApiPayload
  if (!payload || !isJobListApiPayload(payload)) return

  console.log(
    '[AI Career Copilot] Captured Boss API response:',
    payload.url.slice(0, 120),
  )

  try {
    const result = parseBossApiResponse(payload, window.location.href)
    if (result.jobs.length === 0) return

    const newJobs = sentJobTracker.filterNewJobs(result.jobs)
    if (newJobs.length === 0) {
      console.log(
        `[AI Career Copilot] API returned ${result.jobs.length} jobs, all duplicates`,
      )
      return
    }

    console.log(
      `[AI Career Copilot] API parsed ${result.jobs.length} jobs, ${newJobs.length} new`,
    )
    void sendJobsExtracted(window.location.href, newJobs)
  } catch (error) {
    console.error('[AI Career Copilot] Error parsing captured API data:', error)
  }
})

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
 * 列表页初始化逻辑
 *
 * 职责：
 * 1. 监听详情面板变化，补充 JD / 技能
 * 2. 监听 URL 变化，切换搜索条件时重置去重器
 *
 * 注意：列表岗位数据由 SW 注册的 main-world 拦截器捕获 API 响应后
 * 通过 postMessage 传回（见上方 message listener），不再走 DOM 提取。
 * DOM 提取的薪资受字体反爬影响为乱码，已废弃。
 */
function initListPage(): void {
  bossAdapter.observe({
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
 * - REFRESH_JOBS：SidePanel 手动刷新（API 拦截模式下数据被动捕获,此 handler 仅返回 ok）
 * - LOAD_JOB_DETAIL：SidePanel 请求点击 Boss 页面对应岗位卡片，加载详情面板
 */
onMessage((message) => {
  switch (message.type) {
    case ChromeMessageType.REFRESH_JOBS: {
      // API 拦截模式下数据被动捕获,手动刷新无需主动提取。
      // 用户滚动列表或 Boss 页面自己发起 API 请求时,拦截器会自动命中。
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
