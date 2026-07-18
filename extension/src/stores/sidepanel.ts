/**
 * SidePanel 全局状态 Store
 *
 * 职责：
 * - 管理 SidePanel 的全局状态：登录态、后端状态、当前页岗位列表、选中岗位
 * - 监听 Service Worker 推送的消息（TASK_STATUS_UPDATED / PAGE_CHANGED）
 * - 提供计算属性给 UI 渲染
 *
 * 设计动机：
 * - SidePanel 是 Vue 3 + Pinia 架构，状态集中管理便于多组件共享
 * - jobs 列表 / selectedJobId / analysisMap 等状态需要在多个组件间同步
 * - 通过消息协议与 Service Worker 解耦，UI 只关心 store 状态
 *
 * 状态机（单岗位）：
 *   EXTRACTED → CREATED → DETAIL_EXTRACTED → ANALYZING → MATCHING → GENERATING → READY
 *     │          │             │                │            │              │
 *     ▼          ▼             ▼                ▼            ▼              ▼
 *   ERROR      ERROR          ERROR            ERROR        ERROR          ERROR
 *
 * MVP 阶段简化：只跟踪 jobs 列表 + selectedJobId + UI 状态
 * 后续 Step 5 实现详细的 analysisMap / matchMap / communicationMap
 */
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { isExtensionContextValid } from '../messaging/chrome_message'
import type {
  JobAnalysisResult,
  MatchResultResponse,
  CommunicationScriptResponse,
} from '../types/job'

/** SidePanel 全局 UI 状态 */
export type SidePanelStatus =
  | 'loading' // 初始化中
  | 'not_logged_in' // 未登录（提示用户去 Popup 登录）
  | 'idle' // 已登录，等待 Boss 列表页
  | 'extracting' // 正在提取岗位
  | 'ready' // 岗位列表已就绪
  | 'error' // 错误状态

/** 当前展示的岗位（Step 5 详细化） */
export interface DisplayJob {
  /** 后端 Job UUID（创建成功后填充） */
  id?: string
  /** 岗位标题 */
  title: string
  /** 公司名 */
  company: string
  /** 薪资原始字符串 */
  salaryRaw?: string
  /** 工作地点 */
  location?: string
  /** 标签列表 */
  tags: string[]
  /** source_url（用于关联详情补充） */
  sourceUrl: string
  /** 是否已读 */
  seen: boolean
  /** 创建状态 */
  createStatus: 'pending' | 'creating' | 'created' | 'failed'
  /** 创建失败原因 */
  createError?: string
  /** 详情面板 JD 是否已补充（PATCH 成功后为 true） */
  hasJdText?: boolean
}

/** 后端健康状态 */
type BackendHealth = 'unknown' | 'ok' | 'fail'

/** 单个任务的执行状态与结果 */
type TaskResult<T> =
  | { status: 'pending' | 'running' }
  | { status: 'completed'; result: T }
  | { status: 'failed'; errorMessage: string }

/** 单个岗位的任务结果集合 */
interface JobTaskResults {
  analyze?: TaskResult<JobAnalysisResult>
  match?: TaskResult<MatchResultResponse>
  communication?: TaskResult<CommunicationScriptResponse>
}

interface SwState {
  hasToken: boolean
  backendUrl: string
}

export const useSidePanelStore = defineStore('sidepanel', () => {
  // ==================== 状态 ====================

  /** UI 状态 */
  const status = ref<SidePanelStatus>('loading')

  /** 后端健康状态 */
  const backendHealth = ref<BackendHealth>('unknown')

  /** 当前页面 URL */
  const currentUrl = ref<string>('')

  /** 当前岗位列表对应的页面 URL（用于判断是否需要替换列表） */
  const currentPageUrl = ref<string>('')

  /** 是否为 Boss 列表页 */
  const isBossListPage = ref<boolean>(false)

  /** 提取到的岗位列表 */
  const jobs = ref<DisplayJob[]>([])

  /** 当前选中的岗位 sourceUrl */
  const selectedSourceUrl = ref<string | null>(null)

  /** 登录用户信息 */
  const userInfo = ref<{ id: string; email: string; name: string } | null>(null)

  /** 后端 base URL */
  const backendUrl = ref<string>('http://localhost:8000')

  /** 错误信息 */
  const errorMessage = ref<string | null>(null)

  /** 当前激活的 Tab（Step 5 仅岗位 Tab 有实际内容） */
  const activeTab = ref<'jobs' | 'chat' | 'resume' | 'settings'>('jobs')

  /** 已记录投递的岗位 ID 集合（防止重复投递） */
  const appliedJobIds = ref<Set<string>>(new Set())

  // ==================== 计算属性 ====================

  /** 当前选中岗位 */
  const selectedJob = computed(() =>
    jobs.value.find((j) => j.sourceUrl === selectedSourceUrl.value) ?? null,
  )

  /** 已创建的岗位数 */
  const createdJobCount = computed(
    () => jobs.value.filter((j) => j.createStatus === 'created').length,
  )

  /** 创建失败的岗位数 */
  const failedJobCount = computed(
    () => jobs.value.filter((j) => j.createStatus === 'failed').length,
  )

  /** 是否已登录 */
  const isLoggedIn = computed(() => userInfo.value !== null)

  /**
   * 判断指定岗位是否已记录投递
   *
   * 使用 computed 返回函数，避免在模板中直接访问 ref.value
   */
  const isApplied = computed(() => (jobId: string) => appliedJobIds.value.has(jobId))

  // ==================== Actions ====================

  /** 设置 UI 状态 */
  function setStatus(newStatus: SidePanelStatus) {
    status.value = newStatus
  }

  /** 设置后端健康状态 */
  function setBackendHealth(health: BackendHealth) {
    backendHealth.value = health
  }

  /** 设置当前页面信息 */
  function setPageInfo(url: string, isBoss: boolean) {
    currentUrl.value = url
    isBossListPage.value = isBoss
  }

  /** 替换岗位列表（JOBS_EXTRACTED 消息触发） */
  function setJobs(newJobs: DisplayJob[]) {
    jobs.value = newJobs
    if (newJobs.length > 0) {
      status.value = 'ready'
    }
  }

  /** 追加岗位（滚动加载场景） */
  function appendJobs(newJobs: DisplayJob[]) {
    // 按 sourceUrl 去重
    const existingUrls = new Set(jobs.value.map((j) => j.sourceUrl))
    const unique = newJobs.filter((j) => !existingUrls.has(j.sourceUrl))
    jobs.value.push(...unique)
    if (jobs.value.length > 0) {
      status.value = 'ready'
    }
  }

  /** 选中岗位 */
  function selectJob(sourceUrl: string | null) {
    selectedSourceUrl.value = sourceUrl
  }

  /** 更新单个岗位的创建状态 */
  function updateJobCreateStatus(
    sourceUrl: string,
    createStatus: DisplayJob['createStatus'],
    jobId?: string,
    error?: string,
  ) {
    const job = jobs.value.find((j) => j.sourceUrl === sourceUrl)
    if (job) {
      job.createStatus = createStatus
      if (jobId) job.id = jobId
      if (error) job.createError = error
    }
  }

  /** 设置登录用户信息 */
  function setUserInfo(user: { id: string; email: string; name: string } | null) {
    userInfo.value = user
    if (user) {
      // 已登录，根据当前页面状态决定 UI 状态
      status.value = isBossListPage.value ? 'extracting' : 'idle'
    } else {
      status.value = 'not_logged_in'
    }
  }

  /** 设置后端 URL */
  function setBackendUrl(url: string) {
    backendUrl.value = url
  }

  /** 切换当前 Tab */
  function setActiveTab(tab: 'jobs' | 'chat' | 'resume' | 'settings') {
    activeTab.value = tab
  }

  /** 标记岗位已投递 */
  function markApplied(jobId: string) {
    appliedJobIds.value.add(jobId)
  }

  /** 设置错误 */
  function setError(message: string | null) {
    errorMessage.value = message
    if (message) {
      status.value = 'error'
    }
  }

  /** 清空岗位列表 */
  function clearJobs() {
    jobs.value = []
    selectedSourceUrl.value = null
  }

  // ==================== Step 4: SW 广播消息处理 ====================

  /**
   * 应用 JOBS_CREATED 广播结果
   *
   * SW 批量创建岗位后广播，SidePanel 据此更新 jobs 列表的 createStatus 和 id
   *
   * 实现策略：
   * - 若 jobs 列表为空（首次收到），用 created 数组构造简化的 DisplayJob
   *   （MVP 阶段仅 title/company，Step 5 UI 实现时再扩展 payload 携带完整字段）
   * - 若 jobs 列表非空（已通过其他途径填充），按 sourceUrl 匹配更新 createStatus 和 id
   *
   * @param payload JOBS_CREATED 消息载荷
   */
  function applyJobsCreated(payload: {
    pageUrl: string
    created: Array<{
      sourceUrl: string
      jobId: string
      title: string
      company: string
      salaryRaw: string
      location: string
      tags: string[]
    }>
    duplicated: Array<{
      sourceUrl: string
      jobId: string
      title: string
      company: string
      salaryRaw: string
      location: string
      tags: string[]
    }>
    failed: Array<{
      sourceUrl: string
      title: string
      company: string
      salaryRaw: string
      location: string
      tags: string[]
      error: string
    }>
  }) {
    // 页面发生变化：清空旧列表，用新结果替换（搜索条件/分页切换场景）
    const shouldReplace = currentPageUrl.value && currentPageUrl.value !== payload.pageUrl
    if (shouldReplace) {
      jobs.value = []
      selectedSourceUrl.value = null
    }
    currentPageUrl.value = payload.pageUrl

    // 首次收到或已清空：用 created + duplicated + failed 构造 DisplayJob 列表
    if (jobs.value.length === 0) {
      const fromCreated = payload.created.map((c) => ({
        id: c.jobId,
        title: c.title,
        company: c.company,
        salaryRaw: c.salaryRaw,
        location: c.location,
        tags: c.tags,
        sourceUrl: c.sourceUrl,
        seen: false,
        createStatus: 'created' as const,
      }))
      const fromDuplicated = payload.duplicated.map((c) => ({
        id: c.jobId,
        title: c.title,
        company: c.company,
        salaryRaw: c.salaryRaw,
        location: c.location,
        tags: c.tags,
        sourceUrl: c.sourceUrl,
        seen: false,
        createStatus: 'created' as const,
      }))
      const fromFailed = payload.failed.map((c) => ({
        title: c.title,
        company: c.company,
        salaryRaw: c.salaryRaw,
        location: c.location,
        tags: c.tags,
        sourceUrl: c.sourceUrl,
        seen: false,
        createStatus: 'failed' as const,
        createError: c.error,
      }))
      jobs.value = [...fromCreated, ...fromDuplicated, ...fromFailed]
    } else {
      // 已有列表：按 sourceUrl 匹配更新（追加/更新已有记录）
      const map = new Map(jobs.value.map((j) => [j.sourceUrl, j]))
      for (const c of payload.created) {
        const job = map.get(c.sourceUrl)
        if (job) {
          job.id = c.jobId
          job.createStatus = 'created'
          if (c.salaryRaw) job.salaryRaw = c.salaryRaw
          job.location = c.location
          job.tags = c.tags
        } else {
          // 新增岗位（滚动加载追加）
          jobs.value.push({
            id: c.jobId,
            title: c.title,
            company: c.company,
            salaryRaw: c.salaryRaw,
            location: c.location,
            tags: c.tags,
            sourceUrl: c.sourceUrl,
            seen: false,
            createStatus: 'created',
          })
        }
      }
      for (const c of payload.duplicated) {
        const job = map.get(c.sourceUrl)
        if (job) {
          job.id = c.jobId
          job.createStatus = 'created'
          if (c.salaryRaw) job.salaryRaw = c.salaryRaw
          job.location = c.location
          job.tags = c.tags
        } else {
          jobs.value.push({
            id: c.jobId,
            title: c.title,
            company: c.company,
            salaryRaw: c.salaryRaw,
            location: c.location,
            tags: c.tags,
            sourceUrl: c.sourceUrl,
            seen: false,
            createStatus: 'created',
          })
        }
      }
      for (const c of payload.failed) {
        const job = map.get(c.sourceUrl)
        if (job) {
          job.createStatus = 'failed'
          job.createError = c.error
          if (c.salaryRaw) job.salaryRaw = c.salaryRaw
          job.location = c.location
          job.tags = c.tags
        } else {
          jobs.value.push({
            title: c.title,
            company: c.company,
            salaryRaw: c.salaryRaw,
            location: c.location,
            tags: c.tags,
            sourceUrl: c.sourceUrl,
            seen: false,
            createStatus: 'failed',
            createError: c.error,
          })
        }
      }
    }

    // 切换到 ready 状态（只要有岗位就展示）
    if (jobs.value.length > 0) {
      status.value = 'ready'
    }
  }

  /**
   * 任务结果缓存
   *
   * key: jobId, value: 该岗位的所有任务结果
   * 由 AnalysisCard / MatchCard / CommunicationCard 读取并渲染
   */
  const taskResults = ref<Record<string, JobTaskResults>>({})

  /**
   * 应用 TASK_STATUS_UPDATED 广播结果
   *
   * SW 轮询任务完成后广播，SidePanel 据此更新 taskResults 缓存
   * Step 5 UI 实现时由 AnalysisCard / MatchCard / CommunicationCard 读取并渲染
   *
   * @param payload TASK_STATUS_UPDATED 消息载荷
   */
  function applyTaskStatusUpdate(payload: {
    taskId: string
    taskType: 'analyze_jd' | 'compute_match' | 'generate_communication'
    status: 'pending' | 'running' | 'completed' | 'failed'
    jobId: string
    result?: unknown
    errorMessage?: string
  }) {
    const { jobId, taskType, status, result, errorMessage } = payload

    // 初始化该 job 的结果对象
    if (!taskResults.value[jobId]) {
      taskResults.value[jobId] = {}
    }

    // 映射 taskType 到 store 字段
    const fieldMap = {
      analyze_jd: 'analyze',
      compute_match: 'match',
      generate_communication: 'communication',
    } as const
    const field = fieldMap[taskType]

    // 根据状态构造强类型的 TaskResult
    if (status === 'completed') {
      // 按 taskType 做类型断言，避免 any 泛滥
      if (taskType === 'analyze_jd') {
        taskResults.value[jobId].analyze = { status, result: result as JobAnalysisResult }
      } else if (taskType === 'compute_match') {
        taskResults.value[jobId].match = { status, result: result as MatchResultResponse }
      } else {
        taskResults.value[jobId].communication = { status, result: result as CommunicationScriptResponse }
      }
    } else if (status === 'failed') {
      taskResults.value[jobId][field] = { status, errorMessage: errorMessage ?? '任务失败' }
    } else {
      taskResults.value[jobId][field] = { status }
    }

    console.log(
      `[store] TASK_STATUS_UPDATED | job=${jobId} | type=${taskType} | status=${status}`,
    )
  }

  /**
   * 应用 JOB_DETAIL_PATCHED 广播结果
   *
   * SW 完成 PATCH /api/jobs/{id} 后广播，SidePanel 据此更新岗位 hasJdText 状态
   * 并可在 App.vue 中触发分析流水线
   */
  function onJobDetailPatched(payload: {
    jobId: string
    sourceUrl: string
    hasJdText: boolean
  }) {
    const { sourceUrl, hasJdText } = payload
    const job = jobs.value.find((j) => j.sourceUrl === sourceUrl)
    if (job) {
      job.hasJdText = hasJdText
    }
    console.log(`[store] JOB_DETAIL_PATCHED | sourceUrl=${sourceUrl} | hasJdText=${hasJdText}`)
  }

  // ==================== SidePanel 状态持久化 ====================

  /** chrome.storage.local 中用于持久化 SidePanel 状态的 key */
  const STORAGE_KEY = 'sidepanel_state'

  /**
   * 持久化状态版本号
   *
   * 用途：当数据 schema 或提取方案升级时（如 DOM 抓取 → API 拦截），
   * 旧版本持久化的数据可能包含乱码/过期字段，通过版本号校验可强制丢弃旧数据，
   * 避免 SidePanel 重新打开时展示错误内容。
   *
   * v2 → v3:移除 DOM 列表 fallback,改为纯 API 拦截。v2 持久化的 salaryRaw
   * 可能为字体反爬乱码,强制丢弃。
   */
  const STORAGE_VERSION = 4

  /** 需要持久化的状态快照 */
  interface PersistedState {
    /** 数据版本号，用于恢复时校验 */
    version: number
    jobs: DisplayJob[]
    selectedSourceUrl: string | null
    taskResults: Record<string, JobTaskResults>
    appliedJobIds: string[]
    currentPageUrl: string
    activeTab: 'jobs' | 'chat' | 'resume' | 'settings'
  }

  /**
   * 将当前状态持久化到 chrome.storage.local
   *
   * 用途：SidePanel 关闭后重新打开时可恢复岗位列表、分析结果、投递记录等
   * 注意：不持久化 status/backendHealth/currentUrl/isBossListPage，这些在启动时重新检测
   */
  async function saveToStorage(): Promise<void> {
    if (!isExtensionContextValid()) {
      return
    }
    const state: PersistedState = {
      version: STORAGE_VERSION,
      jobs: jobs.value,
      selectedSourceUrl: selectedSourceUrl.value,
      taskResults: taskResults.value,
      appliedJobIds: Array.from(appliedJobIds.value),
      currentPageUrl: currentPageUrl.value,
      activeTab: activeTab.value,
    }
    return new Promise((resolve) => {
      try {
        chrome.storage.local.set({ [STORAGE_KEY]: state }, () => {
          if (chrome.runtime.lastError) {
            console.warn('[store] 持久化状态失败:', chrome.runtime.lastError.message)
          }
          resolve()
        })
      } catch {
        // 扩展上下文失效时忽略本次持久化
        resolve()
      }
    })
  }

  /**
   * 从 chrome.storage.local 恢复状态
   *
   * 在 App.vue onMounted 中调用，优先于 Content Script 的自动提取
   * 恢复后可立即展示上一次结果，避免 SidePanel 重新打开时空白
   */
  async function loadFromStorage(): Promise<void> {
    if (!isExtensionContextValid()) {
      return
    }
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get([STORAGE_KEY], (result) => {
          const state = result[STORAGE_KEY] as PersistedState | undefined
          if (!state) {
            resolve()
            return
          }

          // 版本号校验：旧版本（如 DOM 抓取时代）持久化的数据可能包含乱码/过期字段，
          // 直接丢弃，避免 SidePanel 展示错误内容。
          if (state.version !== STORAGE_VERSION) {
            console.warn(
              `[store] 持久化数据版本不匹配 | stored=${state.version ?? 'none'} | current=${STORAGE_VERSION}，清空旧数据`,
            )
            void clearStorage()
            resolve()
            return
          }

          if (state.jobs?.length > 0) {
            jobs.value = state.jobs
          }
          if (state.selectedSourceUrl) {
            selectedSourceUrl.value = state.selectedSourceUrl
          }
          if (state.taskResults) {
            taskResults.value = state.taskResults
          }
          if (state.appliedJobIds?.length > 0) {
            appliedJobIds.value = new Set(state.appliedJobIds)
          }
          if (state.currentPageUrl) {
            currentPageUrl.value = state.currentPageUrl
          }
          if (state.activeTab) {
            activeTab.value = state.activeTab
          }

          console.log(`[store] 已从 storage 恢复状态 | jobs=${jobs.value.length}`)
          resolve()
        })
      } catch {
        resolve()
      }
    })
  }

  /**
   * 清空持久化状态
   *
   * 用于用户登出或需要重置 SidePanel 时
   */
  async function clearStorage(): Promise<void> {
    if (!isExtensionContextValid()) {
      return
    }
    return new Promise((resolve) => {
      try {
        chrome.storage.local.remove(STORAGE_KEY, () => {
          resolve()
        })
      } catch {
        resolve()
      }
    })
  }

  /**
   * 自动持久化核心状态
   *
   * 监听 jobs / selectedSourceUrl / taskResults / appliedJobIds / currentPageUrl / activeTab
   * 任一变化后延迟写入 chrome.storage.local，避免频繁操作
   * 注意：status / backendHealth / currentUrl / isBossListPage 在启动时重新检测，不持久化
   */
  let autoSaveTimer: ReturnType<typeof setTimeout> | null = null
  watch(
    () => ({
      jobs: jobs.value,
      selectedSourceUrl: selectedSourceUrl.value,
      taskResults: taskResults.value,
      appliedJobIds: Array.from(appliedJobIds.value),
      currentPageUrl: currentPageUrl.value,
      activeTab: activeTab.value,
    }),
    () => {
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer)
      }
      autoSaveTimer = setTimeout(() => {
        void saveToStorage()
      }, 300)
    },
    { deep: true },
  )

  return {
    // 状态
    status,
    backendHealth,
    currentUrl,
    isBossListPage,
    jobs,
    selectedSourceUrl,
    userInfo,
    backendUrl,
    errorMessage,
    taskResults,
    activeTab,
    appliedJobIds,
    // 计算属性
    selectedJob,
    createdJobCount,
    failedJobCount,
    isLoggedIn,
    isApplied,
    // actions
    setStatus,
    setBackendHealth,
    setPageInfo,
    setJobs,
    appendJobs,
    selectJob,
    updateJobCreateStatus,
    setUserInfo,
    setBackendUrl,
    setActiveTab,
    markApplied,
    setError,
    clearJobs,
    applyJobsCreated,
    applyTaskStatusUpdate,
    onJobDetailPatched,
    // 持久化
    saveToStorage,
    loadFromStorage,
    clearStorage,
  }
})

/** SW 状态查询返回类型 */
export type { SwState }
