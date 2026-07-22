/**
 * 消息路由分发器
 *
 * 职责：
 * - 注册 chrome.runtime.onMessage 监听器
 * - 根据消息类型（ChromeMessageType）分发到对应 handler
 * - 统一错误捕获并包装为 ChromeMessageResponse
 * - SW → SidePanel 单向广播（JOBS_CREATED / TASK_STATUS_UPDATED）
 *
 * 设计动机：
 * - 单一职责：service_worker.ts 负责 SW 生命周期，router.ts 负责消息分发
 * - handler 注册式：每个消息类型的处理函数独立注册，便于扩展和维护
 * - 类型安全：复用 chrome_message.ts 的类型定义
 *
 * 当前已实现的 handler（Step 4）：
 * - AUTH_TOKEN_UPDATED：更新内存中的 access_token 和 backendUrl
 * - JOBS_EXTRACTED：批量 POST /api/jobs/ + 写 source_url_map + 广播 JOBS_CREATED
 * - JOB_DETAIL_EXTRACTED：通过 source_url 反查 jobId → PATCH /api/jobs/{id}
 * - REQUEST_ANALYZE：POST /api/jobs/analyze → 启动 task_poller → 广播 TASK_STATUS_UPDATED
 * - REQUEST_MATCH：POST /api/match/compute（同步）→ 广播 TASK_STATUS_UPDATED
 * - REQUEST_COMMUNICATION：POST /api/communication/generate → 启动 task_poller → 广播
 * - RECORD_APPLICATION：POST /api/applications/（同步，不广播）
 */

import {
  ChromeMessageType,
  sendMessageToTab,
  type ChromeMessage,
  type ChromeMessageResponse,
  type ChromeMessagePayloadMap,
} from '../messaging/chrome_message'
import {
  setAccessToken,
  setBackendUrl,
  setCurrentUser,
  getAccessToken,
  getBackendUrl,
  getValidToken,
  fetchBackend,
  BackendError,
  AuthExpiredError,
  initSession,
  getSessionState,
  resetSessionPromises,
  clearAuthStateFromStorage,
  clearMemoryToken,
  type SessionState,
} from './backend_client'
import { bulkSet, getJobId, clear as clearSourceUrlMap } from './source_url_map'
import {
  parseChatListResponse,
  parseChatDetailResponse,
  mergeFriendsAndDetails,
  isChatListUrl,
  isChatDetailUrl,
  type CapturedChatApiPayload,
} from '../modules/boss/chat_api_parser'
import type {
  ApiChatFriend,
  ApiChatFriendDetail,
} from '../types/communication'

// ==================== JOBS_EXTRACTED 并发锁 ====================

/**
 * JOBS_EXTRACTED 串行队列锁
 *
 * 问题：Content Script 可能在短时间内多次发送 JOBS_EXTRACTED（DOM observer + 手动刷新），
 * 每个消息都会启动独立的 handleJobsExtracted，导致并发 POST 风暴触发后端限流。
 *
 * 解决：用 Promise 链串行化所有 JOBS_EXTRACTED 处理，同一时刻只有一个在执行。
 */
let jobsExtractQueue: Promise<void> = Promise.resolve()

// ==================== 聊天对话列表缓存 ====================

/** 内存缓存：Content Script 提取的左侧对话列表（SidePanel 关闭后重开时用） */
let cachedConversations: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED]['conversations'] = []

/**
 * Chat API 数据缓存（2026-07-21 新增）
 *
 * 设计动机：
 * - geekFilterByLabel 和 getGeekFriendList.json 是两个独立请求，时序不固定
 * - SW 需要等两个 API 都到达后才能合并出完整 ChatConversationItem[]
 * - 用两个独立缓存分别保存，每次任一 API 到达都尝试合并并广播
 *
 * 生命周期：
 * - 用户进入聊天页 → 两个 API 陆续到达 → 缓存填充 → 合并 → 广播
 * - 用户离开聊天页 → 缓存保留（避免来回切换时重新拉取）
 * - 用户登出 → 通过 RESET_EXTRACTION_STATE 清空
 */
let cachedChatFriends: ApiChatFriend[] = []
let cachedChatDetails: ApiChatFriendDetail[] = []

/**
 * 标记 Chat API 是否已成功捕获过
 *
 * 用于 chat_adapter 的降级决策：
 * - true → API 已生效，DOM 提取的对话列表可跳过（避免覆盖 API 数据）
 * - false → API 未捕获，DOM 提取的对话列表作为兜底数据源
 */
let chatApiDataCaptured = false

/** 请求间隔（ms），60 req/min = 1 req/sec，留 20% 余量 */
const REQUEST_INTERVAL_MS = 1200
import { startPolling, stopAllPolling } from './task_poller'
import { ensureBossContentScriptInjected } from './interceptor_injector'
import {
  toJobCreateRequest,
  cleanJdText,
  type RawBossJob,
  type JobCreateRequest,
} from '../modules/boss/parser'

/** handler 注册表：每个消息类型对应一个异步处理函数 */
type MessageHandler<T extends ChromeMessageType> = (
  payload: ChromeMessagePayloadMap[T],
  sender: chrome.runtime.MessageSender,
) => Promise<ChromeMessageResponse>

const handlers = new Map<ChromeMessageType, MessageHandler<ChromeMessageType>>()

/**
 * 注册消息 handler
 *
 * @param type 消息类型
 * @param handler 处理函数
 */
export function registerHandler<T extends ChromeMessageType>(
  type: T,
  handler: MessageHandler<T>,
): void {
  // 类型擦除：通过 Map 统一存储，运行时按 type 字符串匹配
  handlers.set(type, handler as MessageHandler<ChromeMessageType>)
}

// ==================== 后端响应类型（最小子集） ====================

/** POST /api/jobs/ 响应：JobResponse（仅取路由需要的字段） */
interface JobResponse {
  id: string
  title: string
  company: string
  source_url: string | null
}

/** POST /api/jobs/analyze 响应：JobAnalyzeResponse */
interface JobAnalyzeResponse {
  job_id: string
  task_id: string | null
  status: 'pending' | 'completed'
  analysis_result?: unknown
  cached: boolean
}

/** POST /api/communication/generate 响应：CommunicationGenerateResponse */
interface CommunicationGenerateResponse {
  task_id: string
  status: string
}

// ==================== 广播函数 ====================

/**
 * SW → SidePanel 单向广播
 *
 * 用 chrome.runtime.sendMessage 不带 tabId：
 * - SidePanel 注册的 onMessage 监听器会收到
 * - SidePanel 关闭时消息丢失（可接受，用户重新打开时通过 store 状态恢复）
 *
 * @param type 消息类型
 * @param payload 消息载荷
 */
function broadcastToSidePanel<T extends ChromeMessageType>(
  type: T,
  payload: ChromeMessagePayloadMap[T],
): void {
  const message = { type, payload }
  // chrome.runtime.sendMessage 在没有接收方时会 reject
  // 用 Promise 形式捕获错误，避免未处理的 rejection
  chrome.runtime.sendMessage(message).catch((err: unknown) => {
    console.warn(`[SW] 广播 ${type} 失败（SidePanel 可能未打开）:`, err)
  })
}

// ==================== Handler 实现 ====================

/**
 * AUTH_TOKEN_UPDATED handler
 *
 * Popup 登录成功后通过此消息同步 token 到 SW 内存
 */
async function handleAuthTokenUpdated(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.AUTH_TOKEN_UPDATED],
): Promise<ChromeMessageResponse> {
  setAccessToken(payload.accessToken, payload.expiresIn, payload.user ?? null)
  setBackendUrl(payload.backendUrl)
  setCurrentUser(payload.user ?? null)

  // 登出时同步清除持久化 backendUrl，避免下次 SW 启动时错误地尝试 refresh
  if (!payload.accessToken) {
    await clearAuthStateFromStorage()
  }

  console.log(
    '[SW] token updated | user=',
    payload.user?.email ?? '(unknown)',
    '| backend=',
    payload.backendUrl,
    '| expiresIn=',
    payload.expiresIn ?? '(not provided)',
  )
  return { ok: true }
}

/**
 * JOBS_EXTRACTED handler
 *
 * 流程：
 * 1. 接收 RawBossJob[]（payload.jobs 类型为 unknown[]，需断言）
 * 2. 对每个 RawBossJob 调用 toJobCreateRequest 转换为 JobCreateRequest
 * 3. 串行调用 POST /api/jobs/（避免并发 DB 写压力 + source_url 唯一约束冲突）
 * 4. 收集结果到 created / duplicated / failed
 * 5. 一次性 bulkSet 写入 source_url_map
 * 6. 广播 JOBS_CREATED 到 SidePanel
 * 7. 返回 ok 给 content script
 *
 * 重复 source_url 处理：
 * - 后端 POST /api/jobs/ 在 source_url 重复时返回已有记录（幂等，DTO 注释）
 * - 所有 2xx 响应都归入 created（无法区分新建 vs 幂等命中）
 * - 4xx/5xx 归入 failed
 *
 * 串行 vs 并发：
 * - 串行：单次海投 30 个岗位 × 200ms = 6s，可接受
 * - 并发：30 个同时请求会冲击 DB（连接池 / 唯一约束竞态）
 * - 选择串行，避免后端压力
 */
async function handleJobsExtracted(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_EXTRACTED],
): Promise<ChromeMessageResponse> {
  // 排队等待：确保同一时刻只有一个 JOBS_EXTRACTED handler 在执行
  // 避免多次触发导致并发 POST 风暴
  const result = new Promise<ChromeMessageResponse>((resolve) => {
    jobsExtractQueue = jobsExtractQueue.then(() =>
      doHandleJobsExtracted(payload).then(resolve),
    )
  })
  return result
}

/** 实际的 JOBS_EXTRACTED 处理逻辑（被队列锁保护） */
async function doHandleJobsExtracted(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_EXTRACTED],
): Promise<ChromeMessageResponse> {
  const { pageUrl, jobs } = payload
  const rawJobs = jobs as RawBossJob[]

  console.log(
    `[SW] JOBS_EXTRACTED | pageUrl=${pageUrl} | count=${rawJobs.length}`,
  )

  // 等待 SW 启动时的静默刷新完成，避免 SW 被 Chrome 回收后 token 丢失导致 401
  await initSession()

  // 检查登录态：未登录直接返回错误，避免后端 401 风暴
  if (!getAccessToken()) {
    return { ok: false, error: '未登录，请先在 Popup 登录' }
  }

  const created: ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_CREATED]['created'] = []
  const failed: ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_CREATED]['failed'] = []

  // 串行创建 + 请求间隔控制，避免触发后端 60 req/min 限流
  for (let i = 0; i < rawJobs.length; i++) {
    // 非首个请求前等待间隔（60 req/min ≈ 1 req/sec，留 20% 余量）
    if (i > 0) {
      await new Promise((r) => setTimeout(r, REQUEST_INTERVAL_MS))
    }

    const raw = rawJobs[i]
    const req: JobCreateRequest = toJobCreateRequest(raw)
    try {
      const resp = await fetchBackend<JobResponse>('/api/jobs/', {
        method: 'POST',
        body: JSON.stringify(req),
      })
      created.push({
        sourceUrl: req.source_url,
        jobId: resp.id,
        title: resp.title,
        company: resp.company,
        salaryRaw: raw.salaryRaw ?? '',
        location: raw.location ?? '',
        tags: raw.tags ?? [],
      })
    } catch (err) {
      const errorMsg =
        err instanceof BackendError
          ? `${err.statusCode}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err)
      failed.push({
        sourceUrl: req.source_url,
        title: raw.title,
        company: raw.company,
        salaryRaw: raw.salaryRaw ?? '',
        location: raw.location ?? '',
        tags: raw.tags ?? [],
        error: errorMsg,
      })
      console.warn(
        `[SW] 创建岗位失败 | title=${raw.title} | source_url=${req.source_url} | error=`,
        err,
      )
    }
  }

  // 一次性批量写入 source_url_map（仅成功的）
  const entries: Array<[string, string]> = created.map((c) => [c.sourceUrl, c.jobId])
  if (entries.length > 0) {
    try {
      await bulkSet(entries)
    } catch (err) {
      // 映射写入失败不影响主流程（已创建的 Job 仍在后端）
      console.error('[SW] source_url_map 批量写入失败:', err)
    }
  }

  console.log(
    `[SW] JOBS_EXTRACTED 完成 | created=${created.length} | failed=${failed.length}`,
  )

  // 广播 JOBS_CREATED 到 SidePanel
  broadcastToSidePanel(ChromeMessageType.JOBS_CREATED, {
    pageUrl,
    created,
    duplicated: [], // MVP 阶段不区分 duplicated，所有 2xx 归入 created
    failed,
  })

  return { ok: true, data: { created: created.length, failed: failed.length } }
}

/**
 * JOB_DETAIL_EXTRACTED handler
 *
 * 流程：
 * 1. 通过 payload.sourceUrl 从 source_url_map 查 jobId
 * 2. 调用 PATCH /api/jobs/{jobId}，body 仅传详情面板补充的字段
 * 3. 返回 ok 给 content script
 *
 * 字段映射：
 * - jd_text: payload.jdText（清洗后传）
 * - skills: payload.skills
 * - location: payload.address（详情面板的详细地址优先于列表页地点）
 * - recruiter* 字段后端无对应列，忽略
 */
async function handleJobDetailExtracted(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.JOB_DETAIL_EXTRACTED],
): Promise<ChromeMessageResponse> {
  const { sourceUrl, jdText, skills, address } = payload

  console.log(
    `[SW] JOB_DETAIL_EXTRACTED | sourceUrl=${sourceUrl} | jdText.length=${jdText.length} | skills=${skills.length}`,
  )

  // 通过 source_url 反查 jobId
  const jobId = await getJobId(sourceUrl)
  if (!jobId) {
    return {
      ok: false,
      error: `source_url 未找到对应 jobId，可能 JOBS_EXTRACTED 未成功创建：${sourceUrl}`,
    }
  }

  // 构造 PATCH body（仅传非空字段，遵循 PATCH 语义）
  const updateBody: Record<string, unknown> = {}
  if (jdText) {
    const cleaned = cleanJdText(jdText)
    if (cleaned) {
      updateBody.jd_text = cleaned
    }
  }
  if (skills && skills.length > 0) {
    updateBody.skills = skills
  }
  if (address) {
    updateBody.location = address
  }

  // 若无可更新字段，直接返回成功（避免空 body PATCH）
  if (Object.keys(updateBody).length === 0) {
    console.log('[SW] JOB_DETAIL_EXTRACTED 无可更新字段，跳过 PATCH')
    // 通知 SidePanel 详情已处理，但无有效 JD，避免 UI 无限等待
    broadcastToSidePanel(ChromeMessageType.JOB_DETAIL_PATCHED, {
      jobId,
      sourceUrl,
      hasJdText: false,
    })
    return { ok: true, data: { jobId, updated: false } }
  }

  try {
    await fetchBackend<JobResponse>(`/api/jobs/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify(updateBody),
    })
    console.log(`[SW] JOB_DETAIL_EXTRACTED PATCH 成功 | jobId=${jobId}`)

    // PATCH 成功后广播给 SidePanel，触发分析流水线
    const hasJdText = Boolean(updateBody.jd_text)
    broadcastToSidePanel(ChromeMessageType.JOB_DETAIL_PATCHED, {
      jobId,
      sourceUrl,
      hasJdText,
    })

    return { ok: true, data: { jobId, updated: true } }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `PATCH /api/jobs/${jobId} 失败：${errorMsg}` }
  }
}

/**
 * REQUEST_ANALYZE handler
 *
 * 流程：
 * 1. 调用 POST /api/jobs/analyze，body: { job_id, session_id }
 * 2. 后端返回 JobAnalyzeResponse { status, task_id?, analysis_result? }
 * 3. status=completed（缓存命中或同步降级）：
 *    → 直接广播 TASK_STATUS_UPDATED(completed, result=analysis_result)
 * 4. status=pending：
 *    → 启动 task_poller 轮询 GET /api/tasks/{task_id}
 *    → 完成时广播 TASK_STATUS_UPDATED
 *
 * 立即返回 ok 给 SidePanel（任务结果通过广播推送）
 */
async function handleRequestAnalyze(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.REQUEST_ANALYZE],
): Promise<ChromeMessageResponse> {
  const { jobId, sessionId } = payload

  console.log(
    `[SW] REQUEST_ANALYZE | jobId=${jobId} | sessionId=${sessionId}`,
  )

  try {
    const resp = await fetchBackend<JobAnalyzeResponse>(
      '/api/jobs/analyze',
      {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId, session_id: sessionId }),
      },
    )

    if (resp.status === 'completed') {
      // 缓存命中或同步降级：直接广播完成
      console.log(
        `[SW] ANALYZE 缓存命中 | jobId=${jobId} | cached=${resp.cached}`,
      )
      broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
        taskId: 'sync', // 同步完成无 task_id，用占位符
        taskType: 'analyze_jd',
        status: 'completed',
        jobId,
        result: resp.analysis_result,
      })
      return { ok: true, data: { status: 'completed', cached: resp.cached } }
    }

    // pending：启动轮询
    if (resp.task_id) {
      startPolling({
        taskId: resp.task_id,
        taskType: 'analyze_jd',
        jobId,
        onComplete: (result) => {
          broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
            taskId: resp.task_id!,
            taskType: 'analyze_jd',
            status: 'completed',
            jobId,
            result,
          })
        },
        onError: (errorMessage) => {
          broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
            taskId: resp.task_id!,
            taskType: 'analyze_jd',
            status: 'failed',
            jobId,
            errorMessage,
          })
        },
      })
      return { ok: true, data: { status: 'pending', taskId: resp.task_id } }
    }

    // 不应到达的分支：后端返回 pending 但无 task_id
    return {
      ok: false,
      error: '后端返回 pending 但未提供 task_id',
    }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `POST /api/jobs/analyze 失败：${errorMsg}` }
  }
}

/**
 * REQUEST_MATCH handler
 *
 * 流程：
 * 1. 调用 POST /api/match/compute（同步，200 + MatchResultResponse）
 * 2. 直接广播 TASK_STATUS_UPDATED(completed, result=MatchResultResponse)
 *
 * Match 接口是同步的：内部走 BM25 + 语义相似度计算，无 LLM 调用
 * → 不需要轮询
 */
async function handleRequestMatch(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.REQUEST_MATCH],
): Promise<ChromeMessageResponse> {
  const { jobId, resumeId } = payload

  console.log(
    `[SW] REQUEST_MATCH | jobId=${jobId} | resumeId=${resumeId ?? '(default)'}`,
  )

  // 构造请求 body（resume_id 可选）
  const body: Record<string, unknown> = { job_id: jobId }
  if (resumeId) {
    body.resume_id = resumeId
  }

  try {
    const resp = await fetchBackend<unknown>('/api/match/compute', {
      method: 'POST',
      body: JSON.stringify(body),
    })

    // 同步完成：直接广播
    broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
      taskId: 'sync',
      taskType: 'compute_match',
      status: 'completed',
      jobId,
      result: resp,
    })

    return { ok: true, data: resp }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)

    // 失败也广播，让 SidePanel 能感知
    broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
      taskId: 'sync',
      taskType: 'compute_match',
      status: 'failed',
      jobId,
      errorMessage: errorMsg,
    })

    return { ok: false, error: `POST /api/match/compute 失败：${errorMsg}` }
  }
}

/**
 * REQUEST_COMMUNICATION handler
 *
 * 流程：
 * 1. 调用 POST /api/communication/generate（202 + { task_id, status }）
 * 2. 启动 task_poller 轮询 GET /api/tasks/{task_id}
 * 3. 完成时广播 TASK_STATUS_UPDATED(completed/failed)
 */
async function handleRequestCommunication(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.REQUEST_COMMUNICATION],
): Promise<ChromeMessageResponse> {
  const { jobId, sessionId, resumeId, tone } = payload

  console.log(
    `[SW] REQUEST_COMMUNICATION | jobId=${jobId} | sessionId=${sessionId} | tone=${tone ?? 'natural'}`,
  )

  // 构造请求 body
  const body: Record<string, unknown> = {
    job_id: jobId,
    session_id: sessionId,
  }
  if (resumeId) {
    body.resume_id = resumeId
  }
  if (tone) {
    body.tone = tone
  }

  try {
    const resp = await fetchBackend<CommunicationGenerateResponse>(
      '/api/communication/generate',
      {
        method: 'POST',
        body: JSON.stringify(body),
      },
    )

    // 启动轮询
    startPolling({
      taskId: resp.task_id,
      taskType: 'generate_communication',
      jobId,
      onComplete: (result) => {
        broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
          taskId: resp.task_id,
          taskType: 'generate_communication',
          status: 'completed',
          jobId,
          result,
        })
      },
      onError: (errorMessage) => {
        broadcastToSidePanel(ChromeMessageType.TASK_STATUS_UPDATED, {
          taskId: resp.task_id,
          taskType: 'generate_communication',
          status: 'failed',
          jobId,
          errorMessage,
        })
      },
    })

    return { ok: true, data: { status: resp.status, taskId: resp.task_id } }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return {
      ok: false,
      error: `POST /api/communication/generate 失败：${errorMsg}`,
    }
  }
}

/**
 * RECORD_APPLICATION handler
 *
 * 流程：
 * 1. 调用 POST /api/applications/（201 + ApplicationResponse）
 * 2. 返回 ok 给 SidePanel（不需要广播，用户主动操作）
 *
 * 用户主动操作：SidePanel 通过 sendMessage 的 response 直接获取结果
 */
async function handleRecordApplication(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RECORD_APPLICATION],
): Promise<ChromeMessageResponse> {
  const { jobId, matchScore, notes } = payload

  console.log(
    `[SW] RECORD_APPLICATION | jobId=${jobId} | matchScore=${matchScore ?? 'N/A'}`,
  )

  // 构造请求 body
  const body: Record<string, unknown> = { job_id: jobId }
  if (matchScore !== undefined) {
    body.match_score = matchScore
  }
  if (notes) {
    body.notes = notes
  }

  try {
    const resp = await fetchBackend<unknown>('/api/applications/', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    console.log(`[SW] RECORD_APPLICATION 成功 | jobId=${jobId}`)
    return { ok: true, data: resp }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return {
      ok: false,
      error: `POST /api/applications/ 失败：${errorMsg}`,
    }
  }
}

/**
 * 判断 URL 是否为 Boss 职位列表页
 */
function isBossListPage(url: string | undefined): boolean {
  return !!url && url.includes('zhipin.com/web/geek/jobs')
}

/**
 * LOG handler
 *
 * 接收 Content Script / Service Worker / SidePanel 发送的运行时日志，
 * 批量 POST 到 /api/extension/logs，由后端统一输出到终端。
 */
async function handleLog(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.LOG],
): Promise<ChromeMessageResponse> {
  // 等待 SW 启动时的静默刷新完成，避免 token 尚未就绪就发日志导致 401
  await initSession()

  try {
    await fetchBackend('/api/extension/logs', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    return { ok: true }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    console.warn('[SW] LOG handler failed:', errorMsg)
    return { ok: false, error: errorMsg }
  }
}

/**
 * CLEAR_TOKEN_CACHE handler
 *
 * 登出时由 Popup 调用：清空 SW 内存中的 token 缓存。
 * Popup 负责先清 chrome.storage.local，再发此消息。
 */
async function handleClearTokenCache(): Promise<ChromeMessageResponse> {
  clearMemoryToken()
  return { ok: true }
}

/**
 * RESET_EXTRACTION_STATE handler
 *
 * SidePanel 重新打开 / 登出后重新登录时触发：
 * 1. 停止所有 task_poller 轮询
 * 2. 清空 source_url_map 内存与持久化映射
 * 3. 重置 backend_client 并发 promise 缓存
 * 4. 清空 Chat API 数据缓存（friends/details/cachedConversations/apiCaptured 标记）
 * 5. 如需清空 sidepanel_state（登出场景）
 * 6. 向当前激活 Tab 的 Content Script 转发重置消息，让 CS 清空 apiDataCaptured / sentJobTracker
 */
async function handleResetExtractionState(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RESET_EXTRACTION_STATE],
): Promise<ChromeMessageResponse> {
  // 1. 停止所有异步任务轮询（避免旧账号/旧会话的轮询继续）
  stopAllPolling()

  // 2. 清空 source_url → jobId 映射
  try {
    await clearSourceUrlMap()
  } catch (err) {
    console.warn('[SW] 清空 source_url_map 失败:', err)
  }

  // 3. 重置 initSession / refresh 的并发 promise 缓存
  resetSessionPromises()

  // 4. Chat 模块缓存清理策略
  //
  // keepChatCache=true：SidePanel 重开场景
  // - 用户在 BOSS 聊天页打开过 SidePanel,Content Script 已经把 5 个对话发给 SW
  // - 用户关闭 SidePanel 后再次打开,如果清空 cachedConversations,
  //   就再也拿不到这些数据(Content Script 不会再次主动广播)
  // - 因此 SidePanel 重开时只清 Job 提取状态,保留 Chat 缓存
  //
  // keepChatCache=false/undefined：登出/切换账号场景
  // - 必须清空,避免新账号看到旧账号的 HR 列表(隐私 + 数据正确性)
  // - 默认行为,向后兼容
  const keepChatCache = payload.keepChatCache === true
  if (!keepChatCache) {
    console.log('[SW] RESET_EXTRACTION_STATE | 彻底清空 Chat 缓存(登出场景)')
    cachedChatFriends = []
    cachedChatDetails = []
    cachedConversations = []
    chatApiDataCaptured = false
  } else {
    console.log(
      `[SW] RESET_EXTRACTION_STATE | 保留 Chat 缓存 | conversations=${cachedConversations.length} | friends=${cachedChatFriends.length} | details=${cachedChatDetails.length}`,
    )
  }

  // 5. 如需清空 SidePanel 持久化状态（登出场景）
  if (payload.clearSidePanelStorage) {
    try {
      await chrome.storage.local.remove('sidepanel_state')
    } catch (err) {
      console.warn('[SW] 清空 sidepanel_state 失败:', err)
    }
  }

  // 6. 转发给当前激活 Tab 的 Content Script
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0]
      if (!tab?.id) {
        resolve({ ok: true })
        return
      }

      console.log('[SW] RESET_EXTRACTION_STATE | tabId=', tab.id, '| url=', tab.url)

      void sendMessageToTab(tab.id, ChromeMessageType.RESET_EXTRACTION_STATE, {})
        .then((resp) => resolve(resp))
        .catch((err: unknown) => {
          console.warn('[SW] 转发 RESET_EXTRACTION_STATE 到 Content Script 失败:', err)
          resolve({ ok: false, error: err instanceof Error ? err.message : String(err) })
        })
    })
  })
}

/**
 * REFRESH_JOBS handler
 *
 * SidePanel 手动刷新时触发：
 * 1. 查询当前激活的 Tab
 * 2. 向该 Tab 的 Content Script 发送 REFRESH_JOBS 消息
 * 3. Content Script 收到后重新调用 extractJobs() 并发送 JOBS_EXTRACTED
 *
 * 注意：SidePanel 自己没有 tabId，因此需要 SW 查询当前窗口的 active tab 再转发
 */
async function handleRefreshJobs(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.REFRESH_JOBS],
): Promise<ChromeMessageResponse> {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0]
      if (!tab?.id) {
        resolve({ ok: false, error: '未找到当前激活的 Tab' })
        return
      }

      console.log(
        '[SW] REFRESH_JOBS | tabId=',
        tab.id,
        '| url=',
        tab.url,
      )

      if (!isBossListPage(tab.url)) {
        resolve({
          ok: false,
          error: '当前页面不是 Boss 职位列表页，无法刷新',
        })
        return
      }

      const sendRefresh = () => {
        return sendMessageToTab(tab.id!, ChromeMessageType.REFRESH_JOBS, {
          pageUrl: payload.pageUrl ?? tab.url ?? '',
        })
      }

      void sendRefresh().then(async (resp) => {
        // 若 Content Script 未响应，可能是扩展更新后未重新注入，尝试补偿注入后重试一次
        if (
          !resp.ok &&
          resp.error?.includes('Receiving end does not exist.')
        ) {
          console.warn(
            '[SW] Content script not responding, trying to inject | tabId=',
            tab.id,
          )
          const injected = await ensureBossContentScriptInjected(tab.id!)
          if (injected) {
            // 等待 Content Script 初始化完成
            await new Promise((r) => setTimeout(r, 300))
            resolve(await sendRefresh())
            return
          }
          resolve({
            ok: false,
            error: '当前页面未注入 Content Script，请刷新 Boss 列表页后重试',
          })
          return
        }

        resolve(resp)
      })
    })
  })
}

/**
 * LOAD_JOB_DETAIL handler
 *
 * SidePanel 选中岗位后，请求 Content Script 在 Boss 页面点击对应卡片，
 * 从而展开 Boss 详情面板并触发 JD 提取。
 *
 * 流程：
 * 1. 查询当前激活 Tab
 * 2. 校验当前页是否为 Boss 列表页
 * 3. 向该 Tab 的 Content Script 发送 LOAD_JOB_DETAIL 消息
 * 4. 把 Content Script 的响应原样返回给 SidePanel
 */
async function handleLoadJobDetail(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.LOAD_JOB_DETAIL],
): Promise<ChromeMessageResponse> {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0]
      if (!tab?.id) {
        resolve({ ok: false, error: '未找到当前激活的 Tab' })
        return
      }

      console.log(
        '[SW] LOAD_JOB_DETAIL | tabId=',
        tab.id,
        '| sourceUrl=',
        payload.sourceUrl,
      )

      if (!isBossListPage(tab.url)) {
        resolve({
          ok: false,
          error: '当前页面不是 Boss 职位列表页，无法加载详情',
        })
        return
      }

      void sendMessageToTab(tab.id, ChromeMessageType.LOAD_JOB_DETAIL, {
        sourceUrl: payload.sourceUrl,
      }).then(resolve)
    })
  })
}

// ==================== 简历 Handlers ====================

async function handleResumeUpload(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RESUME_UPLOAD],
): Promise<ChromeMessageResponse> {
  const { filename, mimeType, fileData } = payload

  // fileData 是 number[] 格式（避免 ArrayBuffer 在消息传递中被转为普通对象）
  // 重建为 Uint8Array 用于构建 Blob
  const uint8 = new Uint8Array(fileData)
  console.log(`[SW] RESUME_UPLOAD | filename=${filename} | mime=${mimeType} | size=${uint8.byteLength}`)

  await initSession()

  // 上传不能用 fetchBackend（它强制 Content-Type: application/json）
  // 手动构建 FormData + raw fetch
  const token = await getValidToken()
  if (!token) {
    return { ok: false, error: '未登录，请先登录' }
  }

  const blob = new Blob([uint8], { type: mimeType })
  const formData = new FormData()
  formData.append('file', blob, filename)

  const backendUrl = getBackendUrl()

  try {
    let resp = await fetch(`${backendUrl}/api/resumes/upload`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
      credentials: 'include',
    })

    // 401 重试一次（与 fetchBackend 逻辑一致）
    if (resp.status === 401) {
      console.warn('[SW] RESUME_UPLOAD 收到 401，尝试 refresh')
      await initSession()
      const newToken = await getValidToken()
      if (!newToken) {
        return { ok: false, error: '登录已过期，请重新登录' }
      }
      resp = await fetch(`${backendUrl}/api/resumes/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${newToken}` },
        body: formData,
        credentials: 'include',
      })
    }

    if (!resp.ok) {
      const errText = await resp.text()
      let detail = `上传失败 (${resp.status})`
      try {
        const errJson = JSON.parse(errText)
        detail = errJson.detail ?? detail
      } catch { /* 用默认消息 */ }
      return { ok: false, error: detail }
    }

    const data = await resp.json()
    console.log(`[SW] RESUME_UPLOAD 成功 | resumeId=${data.resume?.id}`)
    return { ok: true, data }
  } catch (err) {
    return { ok: false, error: `网络错误：${err instanceof Error ? err.message : String(err)}` }
  }
}

async function handleResumeList(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RESUME_LIST],
): Promise<ChromeMessageResponse> {
  const { limit, offset } = payload

  console.log(`[SW] RESUME_LIST | limit=${limit ?? 20} | offset=${offset ?? 0}`)

  try {
    const params = new URLSearchParams()
    if (limit !== undefined) params.set('limit', String(limit))
    if (offset !== undefined) params.set('offset', String(offset))
    const qs = params.toString()
    const path = `/api/resumes/${qs ? `?${qs}` : ''}`

    const data = await fetchBackend(path)
    return { ok: true, data }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `获取简历列表失败：${errorMsg}` }
  }
}

async function handleResumeGet(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RESUME_GET],
): Promise<ChromeMessageResponse> {
  const { resumeId } = payload

  console.log(`[SW] RESUME_GET | resumeId=${resumeId}`)

  try {
    const data = await fetchBackend(`/api/resumes/${resumeId}`)
    return { ok: true, data }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `获取简历详情失败：${errorMsg}` }
  }
}

async function handleResumeSetActive(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RESUME_SET_ACTIVE],
): Promise<ChromeMessageResponse> {
  const { resumeId } = payload

  console.log(`[SW] RESUME_SET_ACTIVE | resumeId=${resumeId}`)

  try {
    const data = await fetchBackend(`/api/resumes/${resumeId}/set-active`, {
      method: 'POST',
    })
    return { ok: true, data }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `切换活跃简历失败：${errorMsg}` }
  }
}

async function handleResumeDelete(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.RESUME_DELETE],
): Promise<ChromeMessageResponse> {
  const { resumeId } = payload

  console.log(`[SW] RESUME_DELETE | resumeId=${resumeId}`)

  try {
    await fetchBackend(`/api/resumes/${resumeId}`, { method: 'DELETE' })
    return { ok: true }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `删除简历失败：${errorMsg}` }
  }
}

// ==================== 沟通模块 Handler ====================

/**
 * CHAT_PAGE_DETECTED handler
 *
 * Content Script 检测到聊天页时发送，SW 记录日志
 */
async function handleChatPageDetected(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_PAGE_DETECTED],
): Promise<ChromeMessageResponse> {
  console.log(`[SW] CHAT_PAGE_DETECTED | url=${payload.pageUrl} | recruiter=${payload.recruiterName}`)
  return { ok: true }
}

/**
 * CHAT_DIAGNOSE handler
 *
 * Content Script 发送选择器诊断结果，SW 广播给 SidePanel
 */
async function handleChatDiagnose(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_DIAGNOSE],
): Promise<ChromeMessageResponse> {
  console.log('[SW] CHAT_DIAGNOSE')
  broadcastToSidePanel(ChromeMessageType.CHAT_DIAGNOSE, payload)
  return { ok: true }
}

/**
 * CHAT_CONVERSATIONS_EXTRACTED handler
 *
 * Content Script 从 BOSS 聊天页左侧 DOM 提取对话列表后发送
 * SW 缓存到内存 + 广播给 SidePanel
 *
 * 与 Chat API 数据的关系（2026-07-21 新增）：
 * - 若 chatApiDataCaptured=false（API 未拦截），DOM 提取的列表作为主数据源，直接覆盖缓存
 * - 若 chatApiDataCaptured=true（API 已拦截），DOM 列表不覆盖 cachedConversations
 *   但需要把 isActive 状态合并到 cachedConversations 中（API 不返回选中状态）
 *
 * 合并策略（API 已捕获场景）：
 * - 按 recruiterName 匹配 DOM 项与 cachedConversations 项
 * - 匹配成功的项：把 DOM 的 isActive 同步到 cachedConversations
 * - DOM 中存在但 cachedConversations 没有的项：忽略（API 是更完整的数据源）
 * - 广播合并后的 cachedConversations 给 SidePanel
 */
async function handleChatConversationsExtracted(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED],
): Promise<ChromeMessageResponse> {
  console.log(
    `[SW] CHAT_CONVERSATIONS_EXTRACTED | count=${payload.conversations.length} | apiCaptured=${chatApiDataCaptured}`,
  )

  if (!chatApiDataCaptured) {
    // API 未捕获：DOM 是主数据源，直接覆盖缓存
    cachedConversations = payload.conversations
    broadcastToSidePanel(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, payload)
    return { ok: true }
  }

  // API 已捕获：DOM 不覆盖缓存，但合并 isActive 状态
  // 用 DOM 的 isActive 更新 cachedConversations 中匹配项的选中状态
  // Map key 为 recruiterName（DOM 拿不到 friendId，只能用姓名匹配）
  const domActiveByName = new Map<string, boolean>()
  for (const dom of payload.conversations) {
    domActiveByName.set(dom.recruiterName, dom.isActive)
  }

  let activeChanged = false
  for (const cached of cachedConversations) {
    const newActive = domActiveByName.get(cached.recruiterName) ?? false
    if (cached.isActive !== newActive) {
      cached.isActive = newActive
      activeChanged = true
    }
  }

  // 只在 active 状态变化时才广播，避免高频无意义广播
  if (activeChanged) {
    console.log(
      `[SW] DOM isActive 已合并到 API 数据，广播更新 | activeCount=${cachedConversations.filter((c) => c.isActive).length}`,
    )
    broadcastToSidePanel(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, {
      conversations: cachedConversations,
      pageUrl: payload.pageUrl,
    })
  }

  return { ok: true }
}

/**
 * REQUEST_CONVERSATIONS_LIST handler
 *
 * SidePanel 打开时主动拉取缓存的对话列表（解决广播丢失问题）
 *
 * 返回数据优先级：
 * 1. 若 API 已捕获（cachedChatFriends + cachedChatDetails 合并），返回合并后的完整列表
 * 2. 否则返回 DOM 提取的 cachedConversations
 *
 * 字段完整性：
 * - API 数据优先时返回完整字段（jobTitle/jobId/unreadCount 等）
 * - DOM 兜底时只有基础字段（id/recruiterName/company/lastMessage/isActive）
 */
async function handleRequestConversationsList(): Promise<ChromeMessageResponse> {
  console.log(
    `[SW] REQUEST_CONVERSATIONS_LIST | apiCaptured=${chatApiDataCaptured} | domCached=${cachedConversations.length} | apiFriends=${cachedChatFriends.length} | apiDetails=${cachedChatDetails.length}`,
  )

  // API 数据优先：若 friends 非空，合并后返回
  if (chatApiDataCaptured && cachedChatFriends.length > 0) {
    const merged = mergeFriendsAndDetails(cachedChatFriends, cachedChatDetails)
    // 转换为 CHAT_CONVERSATIONS_EXTRACTED 的 payload 格式（保留完整 API 字段）
    const conversations = merged.map((c) => ({
      id: c.id,
      recruiterName: c.recruiterName,
      company: c.company ?? '',
      lastMessage: c.lastMessage,
      isActive: c.isActive ?? false, // API 不返回选中状态，由后续 CHAT_CONVERSATIONS_EXTRACTED 广播补全
      // 完整 API 字段
      recruiterJobTitle: c.recruiterJobTitle,
      jobTitle: c.jobTitle,
      jobId: c.jobId ?? null,
      lastMessageAt: c.lastMessageAt ?? null,
      unreadCount: c.unreadCount ?? 0,
      messageCount: c.messageCount,
    }))
    return { ok: true, data: { conversations } }
  }

  // 兜底：DOM 提取的对话列表
  return { ok: true, data: { conversations: cachedConversations } }
}

/**
 * CHAT_LIST_CAPTURED handler（2026-07-21 新增）
 *
 * 数据来源：主世界拦截器捕获 GET /wapi/zprelation/friend/geekFilterByLabel
 * Content Script 原样转发给 SW，SW 解析 zpData.friendList 为 ApiChatFriend[]
 *
 * 流程：
 * 1. 校验 URL 是 geekFilterByLabel
 * 2. 调用 parseChatListResponse 解析
 * 3. 缓存到 cachedChatFriends
 * 4. 标记 chatApiDataCaptured=true（API 已生效，后续 DOM 提取可降级）
 * 5. 若 cachedChatDetails 也已就绪，合并并广播 CHAT_CONVERSATIONS_EXTRACTED
 */
async function handleChatListCaptured(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_LIST_CAPTURED],
): Promise<ChromeMessageResponse> {
  if (!isChatListUrl(payload.url)) {
    console.warn(`[SW] CHAT_LIST_CAPTURED | URL mismatch: ${payload.url}`)
    return { ok: false, error: 'URL 不是 geekFilterByLabel' }
  }

  const friends = parseChatListResponse(payload as CapturedChatApiPayload)
  console.log(
    `[SW] CHAT_LIST_CAPTURED | parsed friends=${friends.length} | url=${payload.url.slice(0, 100)}`,
  )

  if (friends.length === 0) {
    return { ok: true, data: { count: 0 } }
  }

  cachedChatFriends = friends
  chatApiDataCaptured = true

  // 若详情也已缓存，合并并广播
  broadcastMergedConversationsIfReady()

  return { ok: true, data: { count: friends.length } }
}

/**
 * CHAT_DETAIL_CAPTURED handler（2026-07-21 新增）
 *
 * 数据来源：主世界拦截器捕获 POST /wapi/zprelation/friend/getGeekFriendList.json
 * Content Script 原样转发给 SW，SW 解析 zpData.result 为 ApiChatFriendDetail[]
 *
 * 流程：
 * 1. 校验 URL 是 getGeekFriendList.json
 * 2. 调用 parseChatDetailResponse 解析
 * 3. 缓存到 cachedChatDetails
 * 4. 若 cachedChatFriends 也已就绪，合并并广播 CHAT_CONVERSATIONS_EXTRACTED
 */
async function handleChatDetailCaptured(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_DETAIL_CAPTURED],
): Promise<ChromeMessageResponse> {
  if (!isChatDetailUrl(payload.url)) {
    console.warn(`[SW] CHAT_DETAIL_CAPTURED | URL mismatch: ${payload.url}`)
    return { ok: false, error: 'URL 不是 getGeekFriendList.json' }
  }

  const details = parseChatDetailResponse(payload as CapturedChatApiPayload)
  console.log(
    `[SW] CHAT_DETAIL_CAPTURED | parsed details=${details.length} | url=${payload.url.slice(0, 100)}`,
  )

  if (details.length === 0) {
    return { ok: true, data: { count: 0 } }
  }

  cachedChatDetails = details

  // 若 friends 也已缓存，合并并广播
  broadcastMergedConversationsIfReady()

  return { ok: true, data: { count: details.length } }
}

/**
 * 合并 cachedChatFriends + cachedChatDetails 并广播到 SidePanel
 *
 * 触发条件：friends 和 details 都非空时才合并
 * 设计动机：两个 API 时序不固定，每次任一到达都尝试合并，
 * 保证最后一个 API 到达时 SidePanel 能拿到完整数据
 *
 * 字段完整性：
 * - 直接使用 mergeFriendsAndDetails 输出的完整 ChatConversationItem[]
 * - 包含 jobTitle/jobId/recruiterJobTitle/lastMessageAt/unreadCount 等 API 字段
 * - isActive 默认 false（API 不返回选中状态）
 * - 后续由 handleChatConversationsExtracted 合并 DOM 的 isActive 状态
 */
function broadcastMergedConversationsIfReady(): void {
  if (cachedChatFriends.length === 0 || cachedChatDetails.length === 0) {
    return
  }

  const conversations = mergeFriendsAndDetails(cachedChatFriends, cachedChatDetails)

  // 更新 cachedConversations，让后续 REQUEST_CONVERSATIONS_LIST 也能返回 API 数据
  // 注意：conversations 是 ChatConversationItem[]，与 payload 类型兼容（多余字段不影响类型校验）
  cachedConversations = conversations.map((c) => ({
    id: c.id,
    recruiterName: c.recruiterName,
    company: c.company ?? '',
    lastMessage: c.lastMessage,
    isActive: c.isActive ?? false,
    // API 合并的完整字段，广播给 SidePanel 用于渲染详情
    recruiterJobTitle: c.recruiterJobTitle,
    jobTitle: c.jobTitle,
    jobId: c.jobId ?? null,
    lastMessageAt: c.lastMessageAt ?? null,
    unreadCount: c.unreadCount ?? 0,
    messageCount: c.messageCount,
  }))

  console.log(
    `[SW] Chat API 合并完成 | merged=${conversations.length} | 广播到 SidePanel`,
  )

  // 广播到 SidePanel（复用 CHAT_CONVERSATIONS_EXTRACTED 消息类型，避免新增广播类型）
  broadcastToSidePanel(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, {
    conversations: cachedConversations,
    pageUrl: 'api-merged', // 标识来源为 API 合并（区别于 DOM 提取）
  })
}

/**
 * CHAT_MESSAGES_EXTRACTED handler
 *
 * Content Script 从聊天页提取消息后发送，SW 同步到后端并广播给 SidePanel
 */
async function handleChatMessagesExtracted(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_MESSAGES_EXTRACTED],
): Promise<ChromeMessageResponse> {
  const { conversationId, recruiterName, messages, pageUrl } = payload

  console.log(`[SW] CHAT_MESSAGES_EXTRACTED | recruiter=${recruiterName} | count=${messages.length}`)

  await initSession()
  if (!getAccessToken()) {
    return { ok: false, error: '未登录，请先在 Popup 登录' }
  }

  // 同步到后端
  try {
    await fetchBackend('/api/conversations/sync', {
      method: 'POST',
      body: JSON.stringify({
        recruiter_name: recruiterName,
        messages,
      }),
    })
  } catch (err) {
    console.warn('[SW] 对话同步到后端失败:', err)
    // 同步失败不影响 SidePanel 更新（降级为本地模式）
  }

  // 广播到 SidePanel
  broadcastToSidePanel(ChromeMessageType.CHAT_MESSAGES_UPDATED, {
    conversationId,
    recruiterName,
    messages,
    pageUrl,
  })

  return { ok: true }
}

/**
 * CHAT_CONVERSATION_CHANGED handler
 *
 * 用户在 BOSS 左侧切换对话时，Content Script 发送此消息
 * SW 广播到 SidePanel 以切换上下文
 */
async function handleChatConversationChanged(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATION_CHANGED],
): Promise<ChromeMessageResponse> {
  const { recruiterName, conversationId } = payload

  console.log(`[SW] CHAT_CONVERSATION_CHANGED | recruiter=${recruiterName}`)

  // 广播到 SidePanel
  broadcastToSidePanel(ChromeMessageType.CHAT_CONVERSATION_SWITCHED, {
    conversationId,
    recruiterName,
  })

  return { ok: true }
}

/**
 * REQUEST_CHAT_REPLY handler
 *
 * SidePanel 请求 AI 生成对话回复（同步，~2s）
 * 调用 POST /api/communication/reply 直接返回 suggested_reply
 */
async function handleRequestChatReply(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.REQUEST_CHAT_REPLY],
): Promise<ChromeMessageResponse> {
  const { jobId, recruiterName, messages, resumeId, tone } = payload

  console.log(`[SW] REQUEST_CHAT_REPLY | recruiter=${recruiterName} | msgCount=${messages.length}`)

  await initSession()
  if (!getAccessToken()) {
    return { ok: false, error: '未登录，请先在 Popup 登录' }
  }

  try {
    const data = await fetchBackend<{ suggested_reply: string; conversation_id?: string }>(
      '/api/communication/reply',
      {
        method: 'POST',
        body: JSON.stringify({
          job_id: jobId ?? null,
          recruiter_name: recruiterName,
          messages,
          resume_id: resumeId ?? null,
          tone: tone ?? 'natural',
        }),
      },
    )
    return { ok: true, data }
  } catch (err) {
    const errorMsg =
      err instanceof BackendError
        ? `${err.statusCode}: ${err.message}`
        : err instanceof Error
          ? err.message
          : String(err)
    return { ok: false, error: `生成回复失败：${errorMsg}` }
  }
}

/**
 * INJECT_CHAT_TEXT_FROM_SIDEPANEL handler
 *
 * SidePanel 审核模式：请求将文本注入到 BOSS 聊天输入框
 * SW 找到活跃的 BOSS 聊天 Tab，转发 INJECT_CHAT_TEXT 消息
 */
async function handleInjectChatTextFromSidepanel(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.INJECT_CHAT_TEXT_FROM_SIDEPANEL],
): Promise<ChromeMessageResponse> {
  const { text } = payload

  console.log(`[SW] INJECT_CHAT_TEXT_FROM_SIDEPANEL | textLen=${text.length}`)

  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0]
      if (!tab?.id) {
        resolve({ ok: false, error: '未找到当前激活的 Tab' })
        return
      }
      // 验证是 BOSS 聊天页
      if (!tab.url?.includes('zhipin.com/web/geek/chat')) {
        resolve({ ok: false, error: '当前页面不是 BOSS 聊天页' })
        return
      }
      sendMessageToTab(tab.id, ChromeMessageType.INJECT_CHAT_TEXT, { text })
        .then(resolve)
        .catch((err: unknown) => {
          resolve({
            ok: false,
            error: `注入失败：${err instanceof Error ? err.message : String(err)}`,
          })
        })
    })
  })
}

/**
 * AUTO_SEND_REPLY handler
 *
 * SidePanel 自动模式：请求注入文本并自动点击发送
 * SW 找到活跃的 BOSS 聊天 Tab，转发 INJECT_AND_SEND_CHAT_TEXT 消息
 */
async function handleAutoSendReply(
  payload: ChromeMessagePayloadMap[typeof ChromeMessageType.AUTO_SEND_REPLY],
): Promise<ChromeMessageResponse> {
  const { text } = payload

  console.log(`[SW] AUTO_SEND_REPLY | textLen=${text.length}`)

  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs[0]
      if (!tab?.id) {
        resolve({ ok: false, error: '未找到当前激活的 Tab' })
        return
      }
      if (!tab.url?.includes('zhipin.com/web/geek/chat')) {
        resolve({ ok: false, error: '当前页面不是 BOSS 聊天页' })
        return
      }
      sendMessageToTab(tab.id, ChromeMessageType.INJECT_AND_SEND_CHAT_TEXT, { text })
        .then(resolve)
        .catch((err: unknown) => {
          resolve({
            ok: false,
            error: `自动发送失败：${err instanceof Error ? err.message : String(err)}`,
          })
        })
    })
  })
}

// ==================== 路由初始化 ====================

/**
 * 初始化消息路由（在 service_worker.ts 中调用一次）
 *
 * @returns 注销函数（用于测试或 SW 卸载时取消监听）
 */
export function initMessageRouter(): () => void {
  // 注册所有 handler
  registerHandler(
    ChromeMessageType.AUTH_TOKEN_UPDATED,
    handleAuthTokenUpdated,
  )
  registerHandler(ChromeMessageType.JOBS_EXTRACTED, handleJobsExtracted)
  registerHandler(
    ChromeMessageType.JOB_DETAIL_EXTRACTED,
    handleJobDetailExtracted,
  )
  registerHandler(ChromeMessageType.REQUEST_ANALYZE, handleRequestAnalyze)
  registerHandler(ChromeMessageType.REQUEST_MATCH, handleRequestMatch)
  registerHandler(
    ChromeMessageType.REQUEST_COMMUNICATION,
    handleRequestCommunication,
  )
  registerHandler(
    ChromeMessageType.RECORD_APPLICATION,
    handleRecordApplication,
  )
  registerHandler(ChromeMessageType.REFRESH_JOBS, handleRefreshJobs)
  registerHandler(ChromeMessageType.RESET_EXTRACTION_STATE, handleResetExtractionState)
  registerHandler(ChromeMessageType.CLEAR_TOKEN_CACHE, handleClearTokenCache)
  registerHandler(ChromeMessageType.LOAD_JOB_DETAIL, handleLoadJobDetail)
  registerHandler(ChromeMessageType.LOG, handleLog)
  registerHandler(ChromeMessageType.RESUME_UPLOAD, handleResumeUpload)
  registerHandler(ChromeMessageType.RESUME_LIST, handleResumeList)
  registerHandler(ChromeMessageType.RESUME_GET, handleResumeGet)
  registerHandler(ChromeMessageType.RESUME_SET_ACTIVE, handleResumeSetActive)
  registerHandler(ChromeMessageType.RESUME_DELETE, handleResumeDelete)
  registerHandler(ChromeMessageType.CHAT_PAGE_DETECTED, handleChatPageDetected)
  registerHandler(ChromeMessageType.CHAT_DIAGNOSE, handleChatDiagnose)
  registerHandler(ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED, handleChatConversationsExtracted)
  registerHandler(ChromeMessageType.REQUEST_CONVERSATIONS_LIST, handleRequestConversationsList)
  registerHandler(ChromeMessageType.CHAT_LIST_CAPTURED, handleChatListCaptured)
  registerHandler(ChromeMessageType.CHAT_DETAIL_CAPTURED, handleChatDetailCaptured)
  registerHandler(ChromeMessageType.CHAT_MESSAGES_EXTRACTED, handleChatMessagesExtracted)
  registerHandler(ChromeMessageType.CHAT_CONVERSATION_CHANGED, handleChatConversationChanged)
  registerHandler(ChromeMessageType.REQUEST_CHAT_REPLY, handleRequestChatReply)
  registerHandler(ChromeMessageType.INJECT_CHAT_TEXT_FROM_SIDEPANEL, handleInjectChatTextFromSidepanel)
  registerHandler(ChromeMessageType.AUTO_SEND_REPLY, handleAutoSendReply)

  // 注册 chrome.runtime.onMessage 监听
  const listener = (
    message: ChromeMessage | { type: string; payload?: unknown },
    sender: chrome.runtime.MessageSender,
    sendResponse: (response: ChromeMessageResponse) => void,
  ) => {
    // 内部查询消息：GET_SW_STATE（不走 ChromeMessageType，用于 SidePanel 启动时查询登录态）
    if (message.type === 'GET_SW_STATE') {
      // 先等待 SW 启动时的静默刷新完成，再返回最终状态；带 5 秒超时避免阻塞。
      getSwState()
        .then((state) => sendResponse({ ok: true, data: state }))
        .catch((err: unknown) =>
          sendResponse({
            ok: false,
            error: err instanceof Error ? err.message : String(err),
          }),
        )
      return true
    }

    const handler = handlers.get(message.type as ChromeMessageType)
    if (!handler) {
      console.warn(`[SW] 未知消息类型: ${message.type}`)
      sendResponse({ ok: false, error: `未知消息类型: ${message.type}` })
      return false // 不保持通道开启
    }

    // 异步执行 handler
    Promise.resolve(
      handler(message.payload as ChromeMessagePayloadMap[ChromeMessageType], sender),
    )
      .then(sendResponse)
      .catch((err: unknown) => {
        if (err instanceof AuthExpiredError) {
          // 登录过期：清除 token，让 SidePanel 引导重新登录
          setAccessToken(null)
          sendResponse({ ok: false, error: err.message })
        } else if (err instanceof Error) {
          sendResponse({ ok: false, error: err.message })
        } else {
          sendResponse({ ok: false, error: String(err) })
        }
      })

    return true // 保持消息通道开启直到 sendResponse 被调用
  }

  chrome.runtime.onMessage.addListener(listener)
  return () => chrome.runtime.onMessage.removeListener(listener)
}

/** SW 状态查询返回类型 */
export interface SwState {
  /** 是否已通过静默刷新或登录持有有效 access_token */
  hasToken: boolean
  /** 当前后端 base URL */
  backendUrl: string
  /** 当前登录用户信息（未登录时为 null） */
  user: SessionState['user']
}

/**
 * 获取当前 SW 状态（供 SidePanel/Popup 检查登录态）
 *
 * SidePanel 启动时调用此函数，判断是否需要提示用户登录。
 * 本函数会先等待 SW 启动时的 initSession() 完成，避免在静默刷新过程中
 * 返回「未登录」的临时状态。
 */
export async function getSwState(): Promise<SwState> {
  const SESSION_INIT_TIMEOUT_MS = 5000

  try {
    await Promise.race([
      initSession(),
      new Promise<never>((_, reject) =>
        setTimeout(
          () => reject(new Error('等待 SW 会话初始化超时')),
          SESSION_INIT_TIMEOUT_MS,
        ),
      ),
    ])
  } catch (err) {
    console.warn('[router] 等待会话初始化失败:', err)
    // 超时或初始化异常时返回当前已知状态，不让 UI 无限等待
  }

  const state = getSessionState()
  return {
    hasToken: state.isLoggedIn,
    backendUrl: state.backendUrl,
    user: state.user,
  }
}
