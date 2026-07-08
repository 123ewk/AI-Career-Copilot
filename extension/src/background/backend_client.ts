/**
 * 后端 API 客户端（Service Worker 专用）
 *
 * 职责：
 * - 内存中保存 access_token（不持久化到 chrome.storage，安全要求）
 * - 统一封装 fetchBackend<T>(path, options) 函数
 * - 401 时自动调用 /api/auth/refresh 刷新 token 并重试请求
 * - 自动注入 Authorization / Content-Type / X-Request-Id 头
 *
 * 设计动机：
 * - MV3 Service Worker 生命周期短（30 秒空闲后被 Chrome 回收）
 *   → token 必须在内存中（变量），SW 重启时由 Popup 重新发送 AUTH_TOKEN_UPDATED
 * - 不使用 axios：fetch 在 SW 中原生支持，无需额外依赖，更符合最小化原则
 * - refresh token 由后端通过 HttpOnly Cookie 自动管理（withCredentials: 'include'）
 *   → 浏览器自动在 /api/auth/refresh 请求中带上 refresh_token Cookie
 *
 * 错误处理：
 * - 401：尝试 refresh，refresh 失败则抛 AuthExpiredError 让上层引导重新登录
 * - 网络错误：抛 NetworkError
 * - 4xx/5xx：抛 BackendError，包含 error_code 和 detail
 *
 * 并发安全：
 * - refresh 并发去重：多个请求同时 401 时，只发起一次 refresh，其他请求等待同一个 Promise
 * - 避免 refresh 风暴（同时多个 token 失效场景）
 */

/** 默认后端 base URL（用户可在 Popup 修改并通过 AUTH_TOKEN_UPDATED 同步） */
const DEFAULT_BACKEND_URL = 'http://localhost:8000'

/** Token 存储（模块级变量，SW 生命周期内有效） */
let accessToken: string | null = null
let backendUrl: string = DEFAULT_BACKEND_URL

/** 正在进行的 refresh 请求（用于并发去重） */
let refreshPromise: Promise<string> | null = null

/** 自定义错误类型 */
export class AuthExpiredError extends Error {
  constructor(message = '登录已过期，请重新登录') {
    super(message)
    this.name = 'AuthExpiredError'
  }
}

export class NetworkError extends Error {
  constructor(message = '无法连接后端服务') {
    super(message)
    this.name = 'NetworkError'
  }
}

export class BackendError extends Error {
  readonly statusCode: number
  readonly errorCode?: string
  readonly detail?: unknown

  constructor(
    message: string,
    statusCode: number,
    errorCode?: string,
    detail?: unknown,
  ) {
    super(message)
    this.name = 'BackendError'
    this.statusCode = statusCode
    this.errorCode = errorCode
    this.detail = detail
  }
}

/** 后端标准错误响应结构 */
interface BackendErrorBody {
  error_code?: string
  detail?: string
  request_id?: string
  debug?: unknown
}

/** 后端标准响应结构（部分接口） */
export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: {
    id: string
    email: string
    name: string
  }
}

/**
 * 设置 access_token（Popup 登录成功后通过 AUTH_TOKEN_UPDATED 调用）
 */
export function setAccessToken(token: string | null): void {
  accessToken = token
}

/**
 * 设置后端 base URL
 */
export function setBackendUrl(url: string): void {
  // 移除尾部斜杠，避免拼接时出现 //
  backendUrl = url.replace(/\/$/, '')
}

/**
 * 获取当前 access_token（供其他模块检查登录态）
 */
export function getAccessToken(): string | null {
  return accessToken
}

/**
 * 获取当前后端 base URL
 */
export function getBackendUrl(): string {
  return backendUrl
}

/**
 * 生成 request_id（用于日志追踪）
 * 与后端约定：request_id 贯穿前后端，便于排查问题
 */
function generateRequestId(): string {
  return `ext-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * 刷新 access_token（使用 HttpOnly Cookie 中的 refresh_token）
 *
 * 并发去重：多个请求同时 401 时，只发起一次 refresh
 *
 * @returns 新的 access_token
 * @throws AuthExpiredError refresh 失败（refresh_token 过期或无效）
 */
async function refreshAccessToken(): Promise<string> {
  // 并发去重：如果已有 refresh 在进行，复用同一个 Promise
  if (refreshPromise) {
    return refreshPromise
  }

  refreshPromise = (async () => {
    try {
      const resp = await fetch(`${backendUrl}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include', // 携带 HttpOnly Cookie 中的 refresh_token
        headers: {
          'Content-Type': 'application/json',
          'X-Request-Id': generateRequestId(),
        },
      })

      if (!resp.ok) {
        // refresh_token 过期或无效，需要重新登录
        accessToken = null
        throw new AuthExpiredError()
      }

      const data: TokenResponse = await resp.json()
      accessToken = data.access_token
      return data.access_token
    } catch (err) {
      // 网络错误也视为登录过期（保守策略，让用户重新登录）
      accessToken = null
      if (err instanceof AuthExpiredError) throw err
      throw new AuthExpiredError('刷新登录态失败，请重新登录')
    } finally {
      // 清理并发去重标记，下次 401 时可重新发起 refresh
      refreshPromise = null
    }
  })()

  return refreshPromise
}

/**
 * 统一后端请求函数
 *
 * @param path API 路径（以 / 开头，如 /api/jobs/）
 * @param options fetch 配置
 * @returns 响应体（已 JSON 解析）
 * @throws AuthExpiredError / NetworkError / BackendError
 */
export async function fetchBackend<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = path.startsWith('http') ? path : `${backendUrl}${path}`

  // 构造请求头
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Request-Id': generateRequestId(),
    ...(options.headers as Record<string, string> | undefined),
  }

  // 注入 access_token
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`
  }

  let resp: Response
  try {
    resp = await fetch(url, {
      ...options,
      headers,
      credentials: 'include', // 携带 Cookie（用于 refresh_token）
    })
  } catch (err) {
    throw new NetworkError(
      `网络请求失败：${err instanceof Error ? err.message : String(err)}`,
    )
  }

  // 401 自动刷新并重试一次
  if (resp.status === 401 && accessToken) {
    try {
      await refreshAccessToken()
    } catch {
      throw new AuthExpiredError()
    }

    // 用新 token 重试（只重试一次，避免死循环）
    if (accessToken) {
      headers.Authorization = `Bearer ${accessToken}`
      try {
        resp = await fetch(url, {
          ...options,
          headers,
          credentials: 'include',
        })
      } catch (err) {
        throw new NetworkError(
          `重试请求失败：${err instanceof Error ? err.message : String(err)}`,
        )
      }
    }
  }

  // 解析响应
  if (resp.status === 204) {
    return undefined as T
  }

  const bodyText = await resp.text()
  let body: unknown = undefined
  if (bodyText) {
    try {
      body = JSON.parse(bodyText)
    } catch {
      body = bodyText
    }
  }

  if (!resp.ok) {
    const errBody = body as BackendErrorBody | undefined
    throw new BackendError(
      errBody?.detail ?? `后端错误 ${resp.status}`,
      resp.status,
      errBody?.error_code,
      errBody,
    )
  }

  return body as T
}

/**
 * 检查后端健康状态（不需要 token）
 *
 * 用于 SidePanel 显示后端状态灯
 */
export async function checkBackendHealth(): Promise<boolean> {
  try {
    const resp = await fetch(`${backendUrl}/health`, {
      method: 'GET',
      headers: { 'X-Request-Id': generateRequestId() },
    })
    if (!resp.ok) return false
    const data = (await resp.json()) as { status?: string }
    return data.status === 'ok'
  } catch {
    return false
  }
}
