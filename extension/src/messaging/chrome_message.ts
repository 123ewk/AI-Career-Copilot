/**
 * Chrome 扩展统一消息协议
 *
 * 职责：
 * - 定义 Content Script ↔ Service Worker ↔ SidePanel 之间的消息类型
 * - 提供类型安全的消息发送 / 接收辅助函数
 * - 屏蔽 chrome.runtime.sendMessage / chrome.tabs.sendMessage 的底层细节
 *
 * 设计动机：
 * - 项目规则「优先使用成熟第三方库」：webextension-polyfill 已被 @types/chrome 覆盖
 * - MV3 Service Worker 是事件驱动的，所有通信走 chrome.runtime.onMessage
 * - 统一协议避免 Content Script / SidePanel 各自定义消息结构导致类型不一致
 *
 * 消息流向：
 *   Content Script → Service Worker → 后端 → Service Worker → SidePanel
 *   - Content Script 发送 JOBS_EXTRACTED / JOB_DETAIL_EXTRACTED
 *   - Service Worker 路由到后端 API（POST /api/jobs / PATCH /api/jobs/{id} 等）
 *   - Service Worker 通过 TASK_STATUS_UPDATED 通知 SidePanel 更新 UI
 *   - SidePanel 发送 REQUEST_ANALYZE / REQUEST_MATCH / REQUEST_COMMUNICATION 触发任务
 *
 * 类型安全：
 * - ChromeMessage<T> 泛型约束 payload 类型
 * - sendMessage 与 onMessage 泛型化，编译期发现类型不匹配
 * - requestId 用于异步请求/响应匹配（可选，MVP 阶段用 fire-and-forget 即可）
 */

/**
 * 消息类型常量（覆盖 MVP 全部流程）
 *
 * 使用 const object + 字面量联合类型替代 enum：
 * - 符合 TypeScript 的 erasableSyntaxOnly 约束（不产生运行时代码）
 * - 树摇友好，未被引用的类型可在打包时移除
 * - Chrome 消息通道只传字符串，与 enum 行为一致
 */
export const ChromeMessageType = {
  /** Content Script 提取到岗位列表 → 通知 Service Worker 批量创建 */
  JOBS_EXTRACTED: "JOBS_EXTRACTED",

  /** Content Script 提取到详情面板 → 通知 Service Worker PATCH 补充 JD */
  JOB_DETAIL_EXTRACTED: "JOB_DETAIL_EXTRACTED",

  /** SidePanel 请求触发岗位分析（POST /api/jobs/analyze） */
  REQUEST_ANALYZE: "REQUEST_ANALYZE",

  /** SidePanel 请求触发匹配计算（POST /api/match/compute） */
  REQUEST_MATCH: "REQUEST_MATCH",

  /** SidePanel 请求触发话术生成（POST /api/communication/generate） */
  REQUEST_COMMUNICATION: "REQUEST_COMMUNICATION",

  /** SidePanel 请求记录投递（POST /api/applications） */
  RECORD_APPLICATION: "RECORD_APPLICATION",

  /**
   * Service Worker 完成 PATCH /api/jobs/{id} 后 → 通知 SidePanel 详情已补充
   *
   * 设计动机：
   * - Content Script 发送 JOB_DETAIL_EXTRACTED 后，SW 调用 PATCH
   * - PATCH 成功后需要让 SidePanel 知道 JD 已补充，才能自动触发分析流水线
   * - 解耦 PATCH 与后续编排：SW 只负责 HTTP 代理，SidePanel 负责业务编排
   */
  JOB_DETAIL_PATCHED: "JOB_DETAIL_PATCHED",

  /** Popup 登录成功 → 通知 Service Worker 保存 access_token */
  AUTH_TOKEN_UPDATED: "AUTH_TOKEN_UPDATED",

  /** Service Worker 批量创建岗位完成 → 通知 SidePanel 更新 jobs 状态（含成功/失败/已存在） */
  JOBS_CREATED: "JOBS_CREATED",

  /** Service Worker 轮询任务完成 → 通知 SidePanel 更新 UI */
  TASK_STATUS_UPDATED: "TASK_STATUS_UPDATED",

  /** BossAdapter 检测到页面变化（URL 跳转 / 列表刷新）→ 通知 SidePanel 重置状态 */
  PAGE_CHANGED: "PAGE_CHANGED",

  /**
   * SidePanel 请求 Content Script 重新提取岗位
   *
   * 用于手动刷新按钮：用户滚动加载新岗位或切换筛选后，主动触发一次提取
   */
  REFRESH_JOBS: "REFRESH_JOBS",

  /**
   * SidePanel 请求 Content Script 在 Boss 页面点击对应岗位卡片
   *
   * 用于解决：用户在 SidePanel 点击卡片后，Boss 页面详情面板未自动展开，
   * 导致 JD 无法补充、AI 分析无法触发的问题。
   */
  LOAD_JOB_DETAIL: "LOAD_JOB_DETAIL",

  /**
   * Content Script / Service Worker 发送运行时日志到后端
   *
   * 用于把浏览器扩展关键路径日志汇总到后端终端，便于前后端日志一体化调试。
   */
  LOG: "LOG",

  /**
   * SidePanel / Popup 请求重置全部提取状态
   *
   * 用于：SidePanel 重新打开、用户登出后重新登录、手动重置。
   * Content Script 收到后清空 apiDataCaptured / sentJobTracker / observer，
   * Service Worker 收到后清空 source_url_map / task_poller。
   */
  RESET_EXTRACTION_STATE: "RESET_EXTRACTION_STATE",

  /**
   * 登出时通知 Service Worker 清空内存中的 token 缓存
   *
   * 流程：Popup 先清 chrome.storage.local → 再发此消息让 SW 清内存
   */
  CLEAR_TOKEN_CACHE: "CLEAR_TOKEN_CACHE",

  /** SidePanel 上传简历文件（multipart/form-data） */
  RESUME_UPLOAD: "RESUME_UPLOAD",

  /** SidePanel 请求简历列表（分页） */
  RESUME_LIST: "RESUME_LIST",

  /** SidePanel 请求简历详情 */
  RESUME_GET: "RESUME_GET",

  /** SidePanel 请求切换活跃简历 */
  RESUME_SET_ACTIVE: "RESUME_SET_ACTIVE",

  /** SidePanel 请求删除简历 */
  RESUME_DELETE: "RESUME_DELETE",

  // ==================== 沟通模块消息 ====================

  /** Content Script 提取到聊天消息 → 通知 Service Worker 同步到后端 */
  CHAT_MESSAGES_EXTRACTED: "CHAT_MESSAGES_EXTRACTED",

  /** Content Script 检测到聊天页 → 通知 Service Worker */
  CHAT_PAGE_DETECTED: "CHAT_PAGE_DETECTED",

  /** Content Script 检测到用户在 BOSS 左侧切换了对话 → 通知 Service Worker */
  CHAT_CONVERSATION_CHANGED: "CHAT_CONVERSATION_CHANGED",

  /** Service Worker 请求 Content Script 注入文本到聊天输入框（审核模式） */
  INJECT_CHAT_TEXT: "INJECT_CHAT_TEXT",

  /** Service Worker 请求 Content Script 注入文本并自动点击发送（自动模式） */
  INJECT_AND_SEND_CHAT_TEXT: "INJECT_AND_SEND_CHAT_TEXT",

  /** SidePanel 请求 AI 生成对话回复 */
  REQUEST_CHAT_REPLY: "REQUEST_CHAT_REPLY",

  /** SidePanel 请求注入文本到聊天输入框（审核模式，经由 SW 转发） */
  INJECT_CHAT_TEXT_FROM_SIDEPANEL: "INJECT_CHAT_TEXT_FROM_SIDEPANEL",

  /** SidePanel 请求自动发送回复（注入 + 点击发送） */
  AUTO_SEND_REPLY: "AUTO_SEND_REPLY",

  /** Service Worker 广播消息更新到 SidePanel */
  CHAT_MESSAGES_UPDATED: "CHAT_MESSAGES_UPDATED",

  /** Service Worker 广播对话切换到 SidePanel */
  CHAT_CONVERSATION_SWITCHED: "CHAT_CONVERSATION_SWITCHED",

  /** Content Script 发送聊天页选择器诊断结果到 SidePanel */
  CHAT_DIAGNOSE: "CHAT_DIAGNOSE",
} as const

/** 消息类型字面量联合（用于泛型约束） */
export type ChromeMessageType = (typeof ChromeMessageType)[keyof typeof ChromeMessageType]

/** 消息载荷类型映射：每个消息类型对应的 payload 结构 */
export interface ChromeMessagePayloadMap {
  [ChromeMessageType.JOBS_EXTRACTED]: {
    /** 当前页面 URL（用于 SidePanel 区分不同搜索条件） */
    pageUrl: string
    /** 提取到的原始岗位数据 */
    jobs: unknown[]
  }

  [ChromeMessageType.JOB_DETAIL_EXTRACTED]: {
    /** 当前选中岗位的 source_url（用于关联已创建的 Job） */
    sourceUrl: string
    /** 详情面板补充的 JD 文本 */
    jdText: string
    /** 技能标签 */
    skills: string[]
    /** 工作地址 */
    address?: string
    /** 招聘者姓名 */
    recruiterName?: string
    /** 招聘者职位 */
    recruiterTitle?: string
  }

  [ChromeMessageType.REQUEST_ANALYZE]: {
    /** 后端 Job UUID */
    jobId: string
    /** 会话 ID（本地生成的 UUID，后端 TaskService 会自动创建不存在的 session） */
    sessionId: string
  }

  [ChromeMessageType.REQUEST_MATCH]: {
    jobId: string
    /** 简历 ID（可选，未传则用用户默认激活简历） */
    resumeId?: string
  }

  [ChromeMessageType.REQUEST_COMMUNICATION]: {
    jobId: string
    sessionId: string
    resumeId?: string
    /** 话术语调，默认 "natural" */
    tone?: "natural" | "formal" | "enthusiastic"
  }

  [ChromeMessageType.RECORD_APPLICATION]: {
    jobId: string
    /** 匹配分（可选） */
    matchScore?: number
    /** 备注（可选） */
    notes?: string
  }

  [ChromeMessageType.JOB_DETAIL_PATCHED]: {
    /** 后端 Job UUID */
    jobId: string
    /** 原始 source_url，用于 SidePanel 定位 DisplayJob */
    sourceUrl: string
    /** PATCH 是否携带了非空 jd_text */
    hasJdText: boolean
  }

  [ChromeMessageType.AUTH_TOKEN_UPDATED]: {
    /** 新的 access_token（null 表示登出） */
    accessToken: string | null
    /** 后端 base URL（用户在 Popup 配置） */
    backendUrl: string
    /** 登录用户信息 */
    user?: {
      id: string
      email: string
      name: string
    }
    /** token 有效期（秒），从登录接口的 expires_in 字段获取，用于持久化过期时间 */
    expiresIn?: number
  }

  [ChromeMessageType.TASK_STATUS_UPDATED]: {
    /** 任务 ID（后端返回的 task_id） */
    taskId: string
    /** 任务类型（用于 SidePanel 区分分析/匹配/话术） */
    taskType: "analyze_jd" | "compute_match" | "generate_communication"
    /**
     * 任务状态（小写归一化）
     *
     * 后端 TaskDTO.status 使用大写枚举（PENDING/RUNNING/COMPLETED/FAILED/CANCELLED），
     * SW 在 task_poller 中映射为小写；CANCELLED 视为 failed。
     */
    status: "pending" | "running" | "completed" | "failed"
    /** 关联的 Job UUID */
    jobId: string
    /** 任务结果（status=completed 时有值） */
    result?: unknown
    /** 失败原因（status=failed 时有值） */
    errorMessage?: string
  }

  /**
   * JOBS_CREATED 载荷：SW 批量创建岗位后广播给 SidePanel
   *
   * 设计动机：
   * - 解耦 SW 与 SidePanel：SW 完成创建后单向广播，SidePanel 按结果更新 store
   * - 区分三类结果：created（新创建）/ duplicated（幂等命中已存在）/ failed（异常）
   * - 携带完整展示字段（title/company/salaryRaw/location/tags）便于 SidePanel 在未拉详情时也能渲染列表
   */
  [ChromeMessageType.JOBS_CREATED]: {
    /** 触发本次批量创建的列表页 URL */
    pageUrl: string
    /** 新创建成功的岗位（含 jobId 供后续 PATCH/analyze 使用） */
    created: Array<{
      sourceUrl: string
      jobId: string
      title: string
      company: string
      salaryRaw: string
      location: string
      tags: string[]
    }>
    /** 幂等命中（后端返回已有记录）的岗位 */
    duplicated: Array<{
      sourceUrl: string
      jobId: string
      title: string
      company: string
      salaryRaw: string
      location: string
      tags: string[]
    }>
    /** 创建失败的岗位（含失败原因供 UI 展示） */
    failed: Array<{
      sourceUrl: string
      title: string
      company: string
      salaryRaw: string
      location: string
      tags: string[]
      error: string
    }>
  }

  [ChromeMessageType.REFRESH_JOBS]: {
    /** 可选：指定要刷新的页面 URL；为空时使用当前页面 */
    pageUrl?: string
  }

  [ChromeMessageType.LOAD_JOB_DETAIL]: {
    /** 岗位详情页 URL（DisplayJob.sourceUrl），用于在 Boss 列表页定位对应卡片 */
    sourceUrl: string
  }

  [ChromeMessageType.PAGE_CHANGED]: {
    /** 新的页面 URL */
    url: string
    /** 是否为 Boss 列表页 */
    isBossListPage: boolean
  }

  [ChromeMessageType.LOG]: {
    /** 日志条目列表 */
    logs: Array<{
      level: 'debug' | 'info' | 'warn' | 'error'
      source: 'content' | 'service_worker' | 'interceptor' | 'sidepanel'
      message: string
      timestamp?: number
      context?: Record<string, unknown>
    }>
  }

  /**
   * RESET_EXTRACTION_STATE 载荷：请求重置提取状态
   *
   * - clearSidePanelStorage 为 true 时，同时清除持久化的 sidepanel_state（登出场景）
   */
  [ChromeMessageType.RESET_EXTRACTION_STATE]: {
    /** 是否同时清空持久化的 sidepanel_state（登出时用） */
    clearSidePanelStorage?: boolean
  }

  /**
   * CLEAR_TOKEN_CACHE 载荷：登出时通知 Service Worker 清空内存中的 token 缓存
   *
   * 设计动机：
   * - 登出流程：Popup 先清 chrome.storage.local → 再发此消息让 SW 清内存
   * - 分离 storage 清除和内存清除，避免 async 间隙中 SW 用旧内存 token 发请求
   * - 比 AUTH_TOKEN_UPDATED(null) 更语义化：专门用于清缓存，不涉及 backendUrl 等其他状态
   */
  [ChromeMessageType.CLEAR_TOKEN_CACHE]: Record<string, never>

  [ChromeMessageType.RESUME_UPLOAD]: {
    /** 原始文件名 */
    filename: string
    /** MIME 类型 */
    mimeType: string
    /** 文件字节（number[] 格式，避免 ArrayBuffer 在消息传递中被转为普通对象） */
    fileData: number[]
  }

  [ChromeMessageType.RESUME_LIST]: {
    /** 每页大小（1-100，默认 20） */
    limit?: number
    /** 偏移量（默认 0） */
    offset?: number
  }

  [ChromeMessageType.RESUME_GET]: {
    /** 简历 UUID */
    resumeId: string
  }

  [ChromeMessageType.RESUME_SET_ACTIVE]: {
    /** 简历 UUID */
    resumeId: string
  }

  [ChromeMessageType.RESUME_DELETE]: {
    /** 简历 UUID */
    resumeId: string
  }

  // ==================== 沟通模块消息载荷 ====================

  [ChromeMessageType.CHAT_MESSAGES_EXTRACTED]: {
    /** 对话 ID（本地生成的 UUID） */
    conversationId: string
    /** 招聘方姓名 */
    recruiterName: string
    /** 公司名称（从对话详情提取） */
    company?: string
    /** 当前职位名称 */
    jobTitle?: string
    /** 薪资范围 */
    jobSalary?: string
    /** 提取到的消息列表 */
    messages: Array<{ role: 'user' | 'recruiter'; text: string; timestamp?: string }>
    /** 当前页面 URL */
    pageUrl: string
  }

  [ChromeMessageType.CHAT_PAGE_DETECTED]: {
    /** 当前页面 URL */
    pageUrl: string
    /** 招聘方姓名 */
    recruiterName: string
    /** 公司名称 */
    company?: string
    /** 当前职位名称 */
    jobTitle?: string
    /** 薪资范围 */
    jobSalary?: string
  }

  [ChromeMessageType.CHAT_CONVERSATION_CHANGED]: {
    /** 当前页面 URL */
    pageUrl: string
    /** 新的招聘方姓名 */
    recruiterName: string
    /** 新的对话 ID */
    conversationId: string
    /** 公司名称 */
    company?: string
    /** 当前职位名称 */
    jobTitle?: string
    /** 薪资范围 */
    jobSalary?: string
  }

  [ChromeMessageType.INJECT_CHAT_TEXT]: {
    /** 要注入的文本 */
    text: string
  }

  [ChromeMessageType.INJECT_AND_SEND_CHAT_TEXT]: {
    /** 要注入并发送的文本 */
    text: string
  }

  [ChromeMessageType.REQUEST_CHAT_REPLY]: {
    /** 对话 ID */
    conversationId: string
    /** 关联岗位 ID（可选） */
    jobId?: string
    /** 招聘方姓名 */
    recruiterName: string
    /** 对话消息列表 */
    messages: Array<{ role: 'user' | 'recruiter'; text: string; timestamp?: string }>
    /** 简历 ID（可选） */
    resumeId?: string
    /** 回复风格 */
    tone?: 'natural' | 'formal' | 'enthusiastic'
  }

  [ChromeMessageType.INJECT_CHAT_TEXT_FROM_SIDEPANEL]: {
    /** 要注入的文本 */
    text: string
  }

  [ChromeMessageType.AUTO_SEND_REPLY]: {
    /** 对话 ID */
    conversationId: string
    /** 要发送的文本 */
    text: string
  }

  [ChromeMessageType.CHAT_MESSAGES_UPDATED]: {
    /** 对话 ID */
    conversationId: string
    /** 招聘方姓名 */
    recruiterName: string
    /** 消息列表 */
    messages: Array<{ role: 'user' | 'recruiter'; text: string; timestamp?: string }>
    /** 页面 URL */
    pageUrl: string
  }

  [ChromeMessageType.CHAT_CONVERSATION_SWITCHED]: {
    /** 对话 ID */
    conversationId: string
    /** 招聘方姓名 */
    recruiterName: string
  }

  [ChromeMessageType.CHAT_DIAGNOSE]: {
    /** 诊断结果（ChatDiagnosticResult 结构） */
    diagnostics: unknown
  }
}

/** 通用消息结构 */
export interface ChromeMessage<T extends ChromeMessageType = ChromeMessageType> {
  type: T
  payload: ChromeMessagePayloadMap[T]
  /** 请求 ID（可选，用于异步请求/响应匹配） */
  requestId?: string
}

/** 消息响应结构（onMessage 回调返回值） */
export interface ChromeMessageResponse<T = unknown> {
  ok: boolean
  data?: T
  error?: string
}

/** 消息响应超时时间（ms） */
const MESSAGE_TIMEOUT_MS = 8000

/** 允许自动重试一次的错误关键词 */
const RETRYABLE_ERRORS = [
  'Could not establish connection. Receiving end does not exist.',
  'The message port closed before a response was received.',
]

/**
 * 检查扩展上下文是否仍然有效
 *
 * MV3 中扩展重载/更新后，已打开的 SidePanel/Popup 页面会进入失效上下文，
 * 访问 chrome.runtime.* 会抛出 "Extension context invalidated."。
 * 通过探测 chrome.runtime.id 可在不触发未处理异常的前提下判断状态。
 *
 * @returns 上下文是否有效
 */
export function isExtensionContextValid(): boolean {
  try {
    return Boolean(chrome.runtime.id)
  } catch {
    return false
  }
}

/**
 * 单次发送消息到 Service Worker（带超时保护）
 *
 * @param type 消息类型
 * @param payload 消息载荷
 * @returns Service Worker 返回的响应
 */
async function sendMessageOnce<T extends ChromeMessageType>(
  type: T,
  payload: ChromeMessagePayloadMap[T],
): Promise<ChromeMessageResponse> {
  const message: ChromeMessage<T> = { type, payload }
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      resolve({
        ok: false,
        error: `消息发送超时：${type} 在 ${MESSAGE_TIMEOUT_MS}ms 内未收到响应`,
      })
    }, MESSAGE_TIMEOUT_MS)

    chrome.runtime.sendMessage(message, (response: ChromeMessageResponse) => {
      clearTimeout(timer)
      // chrome.runtime.lastError 在接收端未注册监听或上下文失效时会出现
      // 不视为异常，包装为 ok:false 让上层处理
      if (chrome.runtime.lastError) {
        resolve({
          ok: false,
          error: chrome.runtime.lastError.message,
        })
        return
      }
      resolve(response ?? { ok: true })
    })
  })
}

/**
 * 发送消息到 Service Worker（Content Script / SidePanel / Popup 通用）
 *
 * 对 "Receiving end does not exist" 类错误自动重试一次，缓解 MV3 SW 被回收后
 * 首次消息唤醒失败的瞬态问题。
 *
 * @param type 消息类型
 * @param payload 消息载荷（类型由 type 决定）
 * @returns Service Worker 返回的响应
 */
export async function sendMessageToBackground<T extends ChromeMessageType>(
  type: T,
  payload: ChromeMessagePayloadMap[T],
): Promise<ChromeMessageResponse> {
  if (!isExtensionContextValid()) {
    return {
      ok: false,
      error: '扩展上下文已失效，请关闭并重新打开 SidePanel',
    }
  }

  const first = await sendMessageOnce(type, payload)
  if (first.ok) return first

  const shouldRetry = RETRYABLE_ERRORS.some((msg) => first.error?.includes(msg))
  if (!shouldRetry) return first

  // 短暂等待后重试，给 Chrome 留出唤醒 SW 的时间
  await new Promise((resolve) => setTimeout(resolve, 250))

  if (!isExtensionContextValid()) {
    return {
      ok: false,
      error: '扩展上下文已失效，请关闭并重新打开 SidePanel',
    }
  }
  return sendMessageOnce(type, payload)
}

/**
 * 向指定 Tab 发送消息（Service Worker → Content Script）
 *
 * @param tabId 目标 Tab ID
 * @param type 消息类型
 * @param payload 消息载荷
 */
export async function sendMessageToTab<T extends ChromeMessageType>(
  tabId: number,
  type: T,
  payload: ChromeMessagePayloadMap[T],
): Promise<ChromeMessageResponse> {
  const message: ChromeMessage<T> = { type, payload }
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, message, (response: ChromeMessageResponse) => {
      if (chrome.runtime.lastError) {
        resolve({
          ok: false,
          error: chrome.runtime.lastError.message,
        })
        return
      }
      resolve(response ?? { ok: true })
    })
  })
}

/**
 * 注册消息处理器（Service Worker / SidePanel 通用）
 *
 * 类型安全：handler 的 payload 类型由 type 推导
 *
 * @param handler 收到消息时的回调，返回值作为响应
 * @returns 注销函数（取消监听）
 */
export function onMessage(
  handler: <T extends ChromeMessageType>(
    message: ChromeMessage<T>,
    sender: chrome.runtime.MessageSender,
  ) => ChromeMessageResponse | Promise<ChromeMessageResponse>,
): () => void {
  const listener = (
    message: ChromeMessage,
    sender: chrome.runtime.MessageSender,
    sendResponse: (response: ChromeMessageResponse) => void,
  ) => {
    // 异步处理：handler 返回 Promise 时需返回 true 保持消息通道开启
    Promise.resolve(handler(message, sender))
      .then(sendResponse)
      .catch((err: unknown) => {
        sendResponse({
          ok: false,
          error: err instanceof Error ? err.message : String(err),
        })
      })
    return true // 保持消息通道开启直到 sendResponse 被调用
  }

  chrome.runtime.onMessage.addListener(listener)
  return () => chrome.runtime.onMessage.removeListener(listener)
}

/**
 * 生成请求 ID（用于异步请求/响应匹配，MVP 阶段可选）
 */
export function generateRequestId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}
