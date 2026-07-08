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
  getAccessToken,
  getBackendUrl,
  fetchBackend,
  BackendError,
  AuthExpiredError,
} from './backend_client'
import { bulkSet, getJobId } from './source_url_map'
import { startPolling } from './task_poller'
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
  setAccessToken(payload.accessToken)
  setBackendUrl(payload.backendUrl)
  console.log(
    '[SW] token updated | user=',
    payload.user?.email ?? '(unknown)',
    '| backend=',
    payload.backendUrl,
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
  const { pageUrl, jobs } = payload
  // payload.jobs 类型为 unknown[]，断言为 RawBossJob[]
  // Content Script 通过 sendMessageToBackground 发送，类型由协议保证
  const rawJobs = jobs as RawBossJob[]

  console.log(
    `[SW] JOBS_EXTRACTED | pageUrl=${pageUrl} | count=${rawJobs.length}`,
  )

  // 检查登录态：未登录直接返回错误，避免后端 401 风暴
  if (!getAccessToken()) {
    return { ok: false, error: '未登录，请先在 Popup 登录' }
  }

  const created: ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_CREATED]['created'] = []
  const failed: ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_CREATED]['failed'] = []

  // 串行创建：每个 job 转换 → POST → 收集结果
  for (const raw of rawJobs) {
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

  // 注册 chrome.runtime.onMessage 监听
  const listener = (
    message: ChromeMessage | { type: string; payload?: unknown },
    sender: chrome.runtime.MessageSender,
    sendResponse: (response: ChromeMessageResponse) => void,
  ) => {
    // 内部查询消息：GET_SW_STATE（不走 ChromeMessageType，用于 SidePanel 启动时查询登录态）
    if (message.type === 'GET_SW_STATE') {
      sendResponse({ ok: true, data: getSwState() })
      return false
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

/**
 * 获取当前 SW 状态（供 SidePanel 检查登录态）
 *
 * SidePanel 启动时调用此函数，判断是否需要提示用户登录
 */
export function getSwState() {
  return {
    hasToken: getAccessToken() !== null,
    backendUrl: getBackendUrl(),
  }
}
