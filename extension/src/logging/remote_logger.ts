/**
 * 远程日志模块
 *
 * 职责：
 * - 把 Content Script / Service Worker 中的关键日志批量发送到后端
 * - 通过 Chrome 消息通道转交给 Service Worker，由 Service Worker 统一 POST 到后端
 * - 提供 console + remote 双输出，保证浏览器 Console 也能看到
 *
 * 设计动机：
 * - MV3 扩展的日志分散在多个执行上下文，排查数据流时需要切换浏览器 Console 上下文
 * - 把关键路径日志汇总到后端终端，实现前后端日志一体化
 * - 批量缓冲 + 自动 flush，避免每条日志都触发一次 HTTP 请求
 *
 * 使用方式：
 *   import { remoteLog } from '../logging/remote_logger'
 *   remoteLog('info', 'API interceptor captured data', { jobCount: 16 })
 */

import {
  ChromeMessageType,
  sendMessageToBackground,
} from '../messaging/chrome_message'

/** 日志来源上下文 */
type LogSource = 'content' | 'service_worker' | 'interceptor' | 'sidepanel'

/** 日志级别 */
type LogLevel = 'debug' | 'info' | 'warn' | 'error'

/** 单条日志条目 */
interface LogEntry {
  level: LogLevel
  source: LogSource
  message: string
  timestamp: number
  context?: Record<string, unknown>
}

/** 当前上下文来源 */
const SOURCE: LogSource = 'content'

/** 缓冲队列 */
let buffer: LogEntry[] = []

/** 定时 flush 句柄 */
let flushTimer: ReturnType<typeof setTimeout> | null = null

/** 合并窗口时间（ms） */
const FLUSH_INTERVAL_MS = 500

/** 单批最大日志条数 */
const MAX_BATCH_SIZE = 50

/**
 * 发送缓冲日志到后端
 *
 * 通过 Service Worker 代理 POST /api/extension/logs。
 * 失败时只打印 console.error，不阻塞业务。
 */
async function flush(): Promise<void> {
  if (buffer.length === 0) return

  const batch = buffer.splice(0, MAX_BATCH_SIZE)
  if (flushTimer) {
    clearTimeout(flushTimer)
    flushTimer = null
  }

  try {
    await sendMessageToBackground(ChromeMessageType.LOG, { logs: batch })
  } catch (err) {
    // 远端日志发送失败不应影响主业务，仅降级到本地 console
    console.error('[remoteLogger] failed to send logs:', err, batch)
  }

  // 如果缓冲里还有剩余（超过 MAX_BATCH_SIZE），继续发送下一批
  if (buffer.length > 0) {
    scheduleFlush()
  }
}

/**
 * 安排定时 flush
 */
function scheduleFlush(): void {
  if (flushTimer) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    void flush()
  }, FLUSH_INTERVAL_MS)
}

/**
 * 记录远程日志
 *
 * 同时输出到浏览器 Console 和发送到后端终端。
 *
 * @param level 日志级别
 * @param message 日志内容
 * @param context 可选上下文数据
 * @param source 日志来源，默认当前上下文
 */
export function remoteLog(
  level: LogLevel,
  message: string,
  context?: Record<string, unknown>,
  source: LogSource = SOURCE,
): void {
  const entry: LogEntry = {
    level,
    source,
    message,
    timestamp: Date.now(),
    context,
  }

  // 同时输出到浏览器 Console（保持原有调试体验）
  const consoleFn = {
    debug: console.debug,
    info: console.log,
    warn: console.warn,
    error: console.error,
  }[level]
  consoleFn(`[AI Career Copilot] ${message}`, context ?? '')

  buffer.push(entry)
  scheduleFlush()
}

/**
 * 立即 flush 所有缓冲日志
 *
 * 用于页面卸载前或关键节点确保日志不丢失。
 */
export function flushRemoteLogs(): void {
  void flush()
}
