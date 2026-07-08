/**
 * 异步任务轮询器（Service Worker 专用）
 *
 * 职责：
 * - 启动后定时调用 GET /api/tasks/{task_id} 查询任务状态
 * - 任务进入终态（COMPLETED / FAILED / CANCELLED）时停止轮询并回调
 * - 超时（默认 30 次 × 2s = 60s）后视为失败
 * - 完成时通过 onComplete 回调让 router 触发 TASK_STATUS_UPDATED 广播
 *
 * 设计动机：
 * - 后端 Job Analysis / Communication 是异步任务（202 + task_id），
 *   前端必须轮询 GET /api/tasks/{task_id} 获取结果
 * - 轮询放在 SW 而非 SidePanel：SidePanel 关闭后仍能继续轮询，符合 MV3 后台语义
 * - 不用 chrome.alarms：alarms 最小间隔 1 分钟，无法满足 2s 轮询需求
 * - 用 setInterval 实现：用户活跃使用时 SW 不会回收（消息活动维持），
 *   SW 被回收时轮询停止，下次 SidePanel 重新触发任务时会重新启动
 *
 * 已知限制（MVP 接受）：
 * - SW 被回收时正在进行的轮询会丢失，task_id 永远不会被回调
 *   → 影响场景：用户关闭 SidePanel 且长时间无操作（>30s）
 *   → 后端任务仍会完成，结果落库；前端无法实时感知
 *   → 后续优化：用 chrome.storage.session 持久化正在轮询的 task_id，
 *      SW 重启时检查并恢复（或用 SidePanel 重新打开时主动查询）
 *
 * 并发安全：
 * - 单个 task_id 同时只会有一个轮询器（router 在启动前会检查 activePollers）
 * - 模块级 Map 持有所有活跃轮询器，SW 重启时清空
 */

import { fetchBackend, BackendError } from './backend_client'

/** 轮询参数 */
export interface PollOptions {
  /** 后端任务 ID（UUID） */
  taskId: string
  /** 任务类型（用于回调时区分） */
  taskType: 'analyze_jd' | 'compute_match' | 'generate_communication'
  /** 关联的 Job UUID（用于回调时广播） */
  jobId: string
  /** 轮询间隔（毫秒），默认 2000 */
  intervalMs?: number
  /** 最大尝试次数，默认 30（30 × 2s = 60s 超时） */
  maxAttempts?: number
  /** 任务完成（COMPLETED）回调，参数为后端返回的 result 字段 */
  onComplete: (result: unknown) => void
  /** 任务失败（FAILED / CANCELLED / 超时 / 网络错误）回调 */
  onError: (errorMessage: string) => void
}

/** 后端 TaskDTO 的最小子集（仅取轮询需要的字段） */
interface TaskDTO {
  id: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  result?: unknown
  error_message?: string | null
}

/**
 * 模块级 Map：跟踪所有活跃轮询器
 *
 * key: taskId, value: NodeJS.Timeout（setInterval 句柄）
 *
 * 用途：
 * - 防止同一 task_id 被重复启动轮询
 * - 提供 stopAll() 用于 SW 卸载时清理（虽然 SW 被回收时模块变量自然消失）
 */
const activePollers = new Map<string, ReturnType<typeof setInterval>>()

/**
 * 启动任务轮询
 *
 * 流程：
 * 1. 检查 task_id 是否已在轮询（防止重复）
 * 2. 立即发起第一次查询（不等 intervalMs）
 * 3. 启动 setInterval 定时查询
 * 4. 每次查询后检查 status：
 *    - COMPLETED → 调用 onComplete(result)，停止轮询
 *    - FAILED / CANCELLED → 调用 onError(error_message)，停止轮询
 *    - PENDING / RUNNING → 继续轮询
 * 5. 超过 maxAttempts 仍未完成 → 调用 onError('轮询超时')，停止轮询
 *
 * @param options 轮询参数
 */
export function startPolling(options: PollOptions): void {
  const {
    taskId,
    taskType,
    jobId,
    intervalMs = 2000,
    maxAttempts = 30,
    onComplete,
    onError,
  } = options

  // 防止重复轮询：同一 task_id 已在轮询则直接返回
  if (activePollers.has(taskId)) {
    console.warn('[task_poller] task_id 已在轮询，跳过重复启动:', taskId)
    return
  }

  let attempts = 0

  /**
   * 单次查询逻辑
   *
   * 错误处理策略：
   * - 网络错误 / 5xx：本次失败但继续轮询（瞬时故障，可能恢复）
   * - 404：任务不存在，立即终止并报错
   * - 401：token 过期，由 fetchBackend 内部尝试 refresh，refresh 失败抛 AuthExpiredError
   *   → 上层 router 捕获并清理
   */
  async function pollOnce(): Promise<void> {
    attempts++
    try {
      const task = await fetchBackend<TaskDTO>(`/api/tasks/${taskId}`)

      console.log(
        `[task_poller] task=${taskId} | type=${taskType} | job=${jobId} | attempt=${attempts}/${maxAttempts} | status=${task.status}`,
      )

      switch (task.status) {
        case 'COMPLETED':
          stopPolling(taskId)
          onComplete(task.result)
          return
        case 'FAILED':
          stopPolling(taskId)
          onError(task.error_message ?? '任务执行失败')
          return
        case 'CANCELLED':
          stopPolling(taskId)
          onError('任务已取消')
          return
        case 'PENDING':
        case 'RUNNING':
          // 继续轮询
          if (attempts >= maxAttempts) {
            stopPolling(taskId)
            onError(`轮询超时：${maxAttempts * intervalMs / 1000}s 内未完成`)
          }
          return
      }
    } catch (err) {
      // 网络错误 / 后端错误：记录日志，本次失败但继续轮询（直到 maxAttempts）
      console.warn(
        `[task_poller] 查询失败 task=${taskId} attempt=${attempts}:`,
        err,
      )

      if (attempts >= maxAttempts) {
        stopPolling(taskId)
        // 优先提取已知错误类型的 message，最后兜底 String(err)
        const msg =
          err instanceof BackendError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err)
        onError(`轮询超时（含 ${attempts} 次失败）：${msg}`)
      }
    }
  }

  // 立即发起第一次查询（不等 intervalMs），让 pending 任务尽快反馈
  void pollOnce()

  // 启动定时轮询
  const handle = setInterval(() => {
    void pollOnce()
  }, intervalMs)

  activePollers.set(taskId, handle)
  console.log(
    `[task_poller] 启动轮询 task=${taskId} | type=${taskType} | interval=${intervalMs}ms | maxAttempts=${maxAttempts}`,
  )
}

/**
 * 停止指定任务的轮询
 *
 * @param taskId 任务 ID
 */
export function stopPolling(taskId: string): void {
  const handle = activePollers.get(taskId)
  if (handle) {
    clearInterval(handle)
    activePollers.delete(taskId)
    console.log(`[task_poller] 停止轮询 task=${taskId}`)
  }
}

/**
 * 停止所有轮询（用于 SW 卸载 / 用户登出）
 */
export function stopAllPolling(): void {
  for (const [taskId, handle] of activePollers) {
    clearInterval(handle)
    console.log(`[task_poller] 停止轮询 task=${taskId}（stopAll）`)
  }
  activePollers.clear()
}

/**
 * 检查指定任务是否正在轮询
 *
 * @param taskId 任务 ID
 * @returns 是否活跃
 */
export function isPolling(taskId: string): boolean {
  return activePollers.has(taskId)
}
