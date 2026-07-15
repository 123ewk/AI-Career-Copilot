/**
 * Boss 直聘页面适配器（DOM 提取与监听）
 *
 * 职责：
 * - detect()：识别当前页面类型（列表页 / 详情页 / 未知）
 * - extractJobs()：从列表页批量提取 RawBossJob[]
 * - extractDetail()：从详情面板提取补充信息
 * - observe(callbacks)：监听列表加载、详情面板变化、URL 变化
 * - disconnect()：清理所有 observer 和定时器，避免内存泄漏
 *
 * 架构变化：
 * - content.ts 现在优先通过主世界拦截器捕获 Boss 内部 API 获取职位列表（更稳定）
 * - adapter.ts 退居 fallback 角色：
 *   1. API 未命中或页面结构特殊时，DOM 提取兜底
 *   2. 用户点击卡片后，详情面板 JD 补充仍依赖 DOM 监听
 *   3. SPA URL 变化检测仍由 adapter 提供
 *
 * 设计动机：
 * - spec §2.3 要求 adapter 封装 DOM 提取 + 监听逻辑，content.ts 仅负责消息发送
 * - design doc §6.2 数据流：Observer 监听 → extractJobs → parser 归一化 → 发送
 * - 单例模式：一个页面只创建一个 BossAdapter 实例，避免重复监听
 *
 * 运行环境：
 * - Content Script（可访问 document/window，但不能用 chrome.runtime API）
 * - 消息发送由 content.ts 调用 adapter.observe 回调后处理
 *
 * 性能考量：
 * - MutationObserver 用 debounce（500ms）避免频繁提取
 * - URL 变化用 setInterval 轮询（500ms），不劫持 history.pushState
 * - extractJobs 仅扫描 .job-card-box，不做全页查询
 */

import {
  BOSS_SELECTORS,
  queryText,
  queryTextRendered,
  queryTextList,
  queryElement,
  queryAttribute,
} from './selector'
import type { RawBossJob } from './parser'
import { cleanJdText } from './parser'
// 临时诊断代码，验证后移除：用于 setupListObserver 时序诊断
import { remoteLog } from '../../logging/remote_logger'

/**
 * Boss 页面类型
 * - list：职位搜索列表页（zhipin.com/web/geek/jobs）
 * - detail：单个岗位详情页（zhipin.com/job_detail/*）
 * - unknown：非 Boss 页面
 */
export type BossPageType = 'list' | 'detail' | 'unknown'

/**
 * 页面检测结果
 */
export interface BossPageInfo {
  /** 页面类型 */
  type: BossPageType
  /** 当前 URL */
  url: string
  /** 是否为列表页（type === 'list'） */
  isListPage: boolean
}

/**
 * BossAdapter 回调接口
 *
 * 所有回调可选，未注册的回调不会被调用
 */
export interface BossAdapterCallbacks {
  /** 列表页岗位提取完成（含滚动加载追加） */
  onJobsExtracted?: (jobs: RawBossJob[]) => void
  /** 详情面板加载完成（用户点击卡片后触发） */
  onDetailExtracted?: (detail: Partial<RawBossJob>) => void
  /** URL 变化（SPA 路由切换、搜索条件变化） */
  onUrlChanged?: (url: string, isListPage: boolean) => void
}

/** MutationObserver 防抖延迟（ms），避免页面渲染过程中频繁触发 */
const DEBOUNCE_MS = 500

/** URL 轮询间隔（ms），用 setInterval 检测 SPA 路由变化 */
const URL_POLL_INTERVAL_MS = 500

/**
 * Boss 直聘页面适配器（单例）
 *
 * 使用方式：
 *   import { bossAdapter } from './adapter'
 *   const info = bossAdapter.detect()
 *   if (info.isListPage) {
 *     const jobs = bossAdapter.extractJobs()
 *     bossAdapter.observe({
 *       onJobsExtracted: (jobs) => { /* 发送消息 *\/ },
 *       onDetailExtracted: (detail) => { /* 发送消息 *\/ },
 *     })
 *   }
 */
export class BossAdapter {
  /** 列表容器查找最大重试次数 */
  private static readonly LIST_CONTAINER_MAX_RETRY = 10
  /** 列表容器重试间隔（ms） */
  private static readonly LIST_CONTAINER_RETRY_INTERVAL = 500

  /** 列表页 MutationObserver（监听 .rec-job-list 子节点变化） */
  private listObserver: MutationObserver | null = null
  /** 详情面板 MutationObserver（监听 .job-detail-box 内容变化） */
  private detailObserver: MutationObserver | null = null
  /** URL 轮询定时器 */
  private urlPollTimer: ReturnType<typeof setInterval> | null = null
  /** 上次检测到的 URL（用于检测变化） */
  private lastUrl: string = ''
  /** 防抖定时器（列表提取） */
  private listDebounceTimer: ReturnType<typeof setTimeout> | null = null
  /** 防抖定时器（详情提取） */
  private detailDebounceTimer: ReturnType<typeof setTimeout> | null = null
  /** 列表容器查找重试定时器集合（避免 reload 后旧重试回调再挂载 observer） */
  private listRetryTimers: ReturnType<typeof setTimeout>[] = []

  /**
   * 检测当前页面类型
   *
   * 识别规则：
   * - zhipin.com/web/geek/jobs → list（列表页）
   * - zhipin.com/job_detail/ → detail（详情页）
   * - 其他 → unknown
   *
   * @returns 页面信息
   */
  detect(): BossPageInfo {
    const url = location.href
    // 列表页：包含 /web/geek/jobs 路径
    const isListPage = url.includes('zhipin.com/web/geek/jobs')
    // 详情页：包含 /job_detail/ 路径
    const isDetailPage = url.includes('zhipin.com/job_detail/')

    let type: BossPageType = 'unknown'
    if (isListPage) type = 'list'
    else if (isDetailPage) type = 'detail'

    return { type, url, isListPage }
  }

  /**
   * 从列表页批量提取所有可见岗位卡片
   *
   * 流程：
   * 1. 查找所有 .job-card-box 元素
   * 2. 对每个卡片调用 extractJobFromCard 提取字段
   * 3. 过滤解析失败的卡片（title 为空的跳过）
   *
   * @returns RawBossJob 数组（可能为空，不抛异常）
   */
  extractJobs(): RawBossJob[] {
    // 先查找列表容器，再在其内查找卡片，避免误匹配详情面板的元素
    const listContainer = document.querySelector(BOSS_SELECTORS.list.listContainer)
    const cards = listContainer
      ? listContainer.querySelectorAll(BOSS_SELECTORS.list.jobCard)
      : document.querySelectorAll(BOSS_SELECTORS.list.jobCard)

    const jobs: RawBossJob[] = []
    cards.forEach((card) => {
      const job = this.extractJobFromCard(card)
      // 防御性编程：title 为空说明卡片未渲染完成或选择器失效，跳过
      if (job && job.title) {
        jobs.push(job)
      }
    })

    return jobs
  }

  /**
   * 从详情面板提取补充信息
   *
   * 用户点击列表卡片后，右侧 .job-detail-box 加载完整 JD
   * 本方法提取 jdText/skills/recruiterName/recruiterTitle/address 等字段
   *
   * @returns 详情数据（Partial），未找到详情面板返回 null
   */
  extractDetail(): Partial<RawBossJob> | null {
    const container = document.querySelector(BOSS_SELECTORS.detail.container)
    if (!container) return null

    // JD 正文：用 queryText 读取，后续由 cleanJdText 清洗
    const jdText = queryText(container, BOSS_SELECTORS.detail.jd)
    // 技能标签列表
    const skills = queryTextList(container, BOSS_SELECTORS.detail.skillItem)
    // 招聘者信息
    const recruiterName = queryText(container, BOSS_SELECTORS.detail.bossName)
    const recruiterTitle = queryText(container, BOSS_SELECTORS.detail.bossTitle)
    // 详细工作地址
    const address = queryText(container, BOSS_SELECTORS.detail.address)
    // 详情面板的岗位名和薪资（用于与列表卡片关联）
    const title = queryText(container, BOSS_SELECTORS.detail.jobName)
    const salaryRaw = queryTextRendered(container, BOSS_SELECTORS.detail.jobSalary)
    // 详情面板的标签列表
    const tags = queryTextList(container, BOSS_SELECTORS.detail.tagItem)

    return {
      title,
      salaryRaw,
      tags,
      // jdText 清洗：去除 "展开/收起" 按钮文本和多余空白
      jdText: jdText ? cleanJdText(jdText) : undefined,
      skills: skills.length > 0 ? skills : undefined,
      recruiterName: recruiterName || undefined,
      recruiterTitle: recruiterTitle || undefined,
      address: address || undefined,
    }
  }

  /**
   * 启动页面监听
   *
   * 监听三类事件：
   * 1. 列表加载/滚动追加：MutationObserver 监听 .rec-job-list 子节点变化
   * 2. 详情面板切换：MutationObserver 监听 .job-detail-box 内容变化
   * 3. URL 变化：setInterval 轮询 location.href（SPA 路由切换）
   *
   * @param callbacks 回调集合（所有回调可选）
   * @returns 注销函数（调用后停止所有监听）
   */
  observe(callbacks: BossAdapterCallbacks): () => void {
    // 防御性编程：重复调用 observe 时先清理旧 observer
    this.disconnect()

    this.lastUrl = location.href

    // 1. 列表页监听
    if (callbacks.onJobsExtracted) {
      this.setupListObserver(callbacks.onJobsExtracted)
    }

    // 2. 详情面板监听
    if (callbacks.onDetailExtracted) {
      this.setupDetailObserver(callbacks.onDetailExtracted)
    }

    // 3. URL 变化监听
    if (callbacks.onUrlChanged) {
      this.setupUrlPolling(callbacks.onUrlChanged)
    }

    // 返回注销函数
    return () => this.disconnect()
  }

  /**
   * 清理所有 observer 和定时器
   *
   * 必须在页面卸载或重新 observe 前调用，避免内存泄漏
   */
  disconnect(): void {
    this.listObserver?.disconnect()
    this.listObserver = null

    this.detailObserver?.disconnect()
    this.detailObserver = null

    if (this.urlPollTimer) {
      clearInterval(this.urlPollTimer)
      this.urlPollTimer = null
    }

    if (this.listDebounceTimer) {
      clearTimeout(this.listDebounceTimer)
      this.listDebounceTimer = null
    }

    if (this.detailDebounceTimer) {
      clearTimeout(this.detailDebounceTimer)
      this.detailDebounceTimer = null
    }

    // 关键修复：清理所有列表容器重试定时器，避免 reload 后旧回调再挂载 observer
    for (const timer of this.listRetryTimers) {
      clearTimeout(timer)
    }
    this.listRetryTimers = []
  }

  // ==================== 私有方法 ====================

  /**
   * 从单个岗位卡片提取 RawBossJob
   *
   * 注意：变量名避免使用 `location`，会覆盖全局 Location 对象
   *
   * @param card .job-card-box 元素
   * @returns RawBossJob 或 null（卡片未渲染完成时返回 null）
   */
  private extractJobFromCard(card: Element): RawBossJob | null {
    // 岗位名：用 queryText 读取（无字体反爬）
    const title = queryText(card, BOSS_SELECTORS.list.jobName)
    if (!title) return null

    // 薪资：必须用 queryTextRendered 读取（字体反爬字段）
    const salaryRaw = queryTextRendered(card, BOSS_SELECTORS.list.jobSalary)
    // 公司名 / 招聘者
    const company = queryText(card, BOSS_SELECTORS.list.bossName)
    // 工作地点（变量名用 locationText 避免覆盖全局 location 对象）
    const locationText = queryText(card, BOSS_SELECTORS.list.companyLocation)
    // 标签列表
    const tags = queryTextList(card, BOSS_SELECTORS.list.tagItem)
    // 详情链接：.job-name 是 <a> 标签，读取 href
    const detailHref = queryAttribute(card, BOSS_SELECTORS.list.detailLink, 'href')
    // 将相对 URL 转为绝对 URL：base 用 window.location.href（当前页 URL）
    const detailUrl = detailHref
      ? new URL(detailHref, window.location.href).href
      : ''
    // 已读状态：.is-seen 类存在则已读
    const seen = queryElement(card, BOSS_SELECTORS.list.seenClass) !== null
    // 特殊标签：.job-tag-icon 的 alt 属性
    const specialTag = queryAttribute(card, BOSS_SELECTORS.list.specialTag, 'alt')

    return {
      title,
      company: company || '(未知公司)',
      salaryRaw,
      location: locationText,
      tags,
      source: 'boss',
      // sourceUrl：列表页 URL（当前页）
      sourceUrl: window.location.href,
      detailUrl,
      seen,
      specialTag: specialTag || undefined,
    }
  }

  /**
 * 设置列表页 MutationObserver
 *
 * 监听 .rec-job-list 的子节点变化（滚动加载、筛选、分页都会触发）
 * 用 debounce 避免渲染过程中频繁提取
 *
 * 持续重试：content.ts 在 document_start 注入，此时 DOM 未渲染。
 * 如果页面网络慢、SSR 延迟，.rec-job-list 可能在数秒后才出现。
 * 每 500ms 重试一次，最多 10 次（共 5s），确保容器出现后能挂载 observer。
 */
private setupListObserver(
    onJobsExtracted: (jobs: RawBossJob[]) => void,
    retryCount = 0,
  ): void {
    // 临时诊断代码，验证后移除：记录 setupListObserver 时序和 jobCard 渲染时机
    const diagTs = Date.now()
    const listContainer = document.querySelector(BOSS_SELECTORS.list.listContainer)
    if (!listContainer) {
      if (retryCount >= BossAdapter.LIST_CONTAINER_MAX_RETRY) {
        remoteLog('info', '[diag] listContainer give up', { ts: diagTs, retryCount })
        console.warn(
          `[BossAdapter] .rec-job-list 未找到，已重试 ${retryCount} 次，放弃监听列表变化`,
        )
        return
      }
      remoteLog('info', '[diag] listContainer not found, retry', {
        ts: diagTs,
        retryCount,
        maxRetry: BossAdapter.LIST_CONTAINER_MAX_RETRY,
      })
      // 持续重试，直到找到容器或达到上限
      const timer = setTimeout(() => {
        // 定时器触发后从集合中移除（已经执行）
        this.listRetryTimers = this.listRetryTimers.filter((t) => t !== timer)
        this.setupListObserver(onJobsExtracted, retryCount + 1)
      }, BossAdapter.LIST_CONTAINER_RETRY_INTERVAL)
      this.listRetryTimers.push(timer)
      return
    }

    // 临时诊断代码：记录 listContainer 找到时的 jobCard 数量（关键指标）
    // jobCardCount=0 说明 jobCard 还没渲染；>0 说明已渲染，observer 挂载时岗位已在 DOM 里
    const jobCardCount = listContainer.querySelectorAll(BOSS_SELECTORS.list.jobCard).length
    remoteLog('info', '[diag] listContainer found', { ts: diagTs, jobCardCount })

    // 临时诊断代码：记录 MutationObserver 首次触发时间（只在首次触发时打日志，避免刷屏）
    let mutationFired = false
    this.listObserver = new MutationObserver(() => {
      if (!mutationFired) {
        mutationFired = true
        const currentCount = listContainer.querySelectorAll(BOSS_SELECTORS.list.jobCard).length
        remoteLog('info', '[diag] mutation fired', { ts: Date.now(), jobCardCount: currentCount })
      }
      // 防抖：页面渲染过程中可能触发多次 mutation，500ms 内只提取一次
      if (this.listDebounceTimer) {
        clearTimeout(this.listDebounceTimer)
      }
      this.listDebounceTimer = setTimeout(() => {
        const jobs = this.extractJobs()
        if (jobs.length > 0) {
          onJobsExtracted(jobs)
        }
        this.listDebounceTimer = null
      }, DEBOUNCE_MS)
    })

    // 监听子节点增删（滚动加载新卡片）
    // subtree: true 因为卡片是 listContainer 的后代（不是直接子节点），
    // 滚动加载往深层容器追加卡片，subtree: false 监听不到
    this.listObserver.observe(listContainer, {
      childList: true,
      subtree: true,
    })
    // 临时诊断代码：记录 observer 挂载完成
    remoteLog('info', '[diag] observer mounted', { ts: Date.now(), jobCardCount })

    // 关键修复：observer 挂载时如果已有 jobCard，立即提取一次
    // 场景：DOM fallback 在页面未就绪时运行（0 个卡片），之后 observer 挂载时
    // 卡片已在 DOM 中，但 MutationObserver 只在变化时触发，已存在的卡片不会触发 mutation
    if (jobCardCount > 0) {
      remoteLog('info', `[diag] observer mounted with ${jobCardCount} existing cards, extracting immediately`)
      const jobs = this.extractJobs()
      if (jobs.length > 0) {
        onJobsExtracted(jobs)
      }
    }
  }

  /**
   * 设置详情面板 MutationObserver
   *
   * 监听 .job-detail-box 的内容变化（用户点击不同卡片时触发）
   * 用 debounce 避免渲染过程中频繁提取
   */
  private setupDetailObserver(
    onDetailExtracted: (detail: Partial<RawBossJob>) => void,
  ): void {
    const detailContainer = document.querySelector(BOSS_SELECTORS.detail.container)
    if (!detailContainer) {
      // 详情面板未找到：用户可能未点击任何卡片，1 秒后重试
      setTimeout(() => {
        const retryContainer = document.querySelector(BOSS_SELECTORS.detail.container)
        if (retryContainer) {
          this.setupDetailObserver(onDetailExtracted)
        }
      }, 1000)
      return
    }

    this.detailObserver = new MutationObserver(() => {
      if (this.detailDebounceTimer) {
        clearTimeout(this.detailDebounceTimer)
      }
      this.detailDebounceTimer = setTimeout(() => {
        const detail = this.extractDetail()
        if (detail) {
          onDetailExtracted(detail)
        }
        this.detailDebounceTimer = null
      }, DEBOUNCE_MS)
    })

    // 监听子节点 + 子树文本变化（JD 加载会改变文本内容）
    this.detailObserver.observe(detailContainer, {
      childList: true,
      subtree: true,
      characterData: true,
    })
  }

  /**
   * 设置 URL 变化轮询
   *
   * Boss 直聘是 Vue SPA，搜索条件变化、分页切换都会改变 URL 但不刷新页面
   * 用 setInterval 轮询 location.href 检测变化
   *
   * 不用 history.pushState 劫持的原因：
   * - 侵入性强，可能影响页面原有逻辑
   * - Boss 直聘可能已覆写 pushState，劫持会破坏其功能
   */
  private setupUrlPolling(onUrlChanged: (url: string, isListPage: boolean) => void): void {
    this.urlPollTimer = setInterval(() => {
      const currentUrl = location.href
      if (currentUrl !== this.lastUrl) {
        this.lastUrl = currentUrl
        const info = this.detect()
        onUrlChanged(info.url, info.isListPage)
      }
    }, URL_POLL_INTERVAL_MS)
  }
}

/**
 * BossAdapter 单例
 *
 * 一个页面只创建一个实例，避免重复监听导致内存泄漏
 */
export const bossAdapter = new BossAdapter()
