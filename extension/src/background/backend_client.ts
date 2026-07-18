/**
 * 后端 API 客户端（Service Worker 专用）
 *
 * 职责：
 * - access_token 持久化到 chrome.storage.local（SW 重启后可恢复）
 * - 统一封装 fetchBackend<T>(path, options) 函数
 * - 每次请求前从 storage 读最新 token，禁止依赖内存全局变量
 * - 401 时自动调用 /api/auth/refresh 刷新 token 并重试请求
 * - SW 启动时通过 initSession() 恢复登录态（storage 优先，refresh 兜底）
 * - 自动注入 Authorization / Content-Type / X-Request-Id 头
 *
 * 设计动机：
 * - MV3 Service Worker 生命周期短（30 秒空闲后被 Chrome 回收）
 *   → 仅靠内存变量存 token 会导致 SW 重启后 token 丢失
 *   → token + expiresAt 持久化到 chrome.storage.local，SW 重启后可直接恢复
 *   → fetchBackend 每次请求前 await getValidToken()，确保用的是 storage 中最新值
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
 * - initSession 并发去重：重复调用返回同一个 Promise，避免 SW 启动时多次刷新
 * - 避免 refresh 风暴（同时多个 token 失效场景）
 */

/** 默认后端 base URL（用户可在 Popup 修改并通过 AUTH_TOKEN_UPDATED 同步） */
const DEFAULT_BACKEND_URL = 'http://localhost:8000'

/** chrome.storage.local 中用于持久化认证最小状态的 key */
const AUTH_STORAGE_KEY = 'auth_state'

/** 持久化的认证状态 schema */
interface StoredAuthState {
  /** 用户最后一次登录/使用的后端地址 */
  backendUrl: string
  /** 持久化的 access_token（SW 重启后恢复登录态） */
  accessToken: string | null
  /** token 绝对过期时间戳（ms, Date.now() + expires_in * 1000） */
  expiresAt: number | null
  /** 持久化的用户信息（SW 重启后恢复，避免 SidePanel 因 user=null 误判为未登录） */
  user: TokenResponse['user'] | null
}

/** Token 存储（模块级变量，SW 生命周期内有效） */
let accessToken: string | null = null
let tokenExpiresAt: number | null = null
let backendUrl: string = DEFAULT_BACKEND_URL
let currentUser: TokenResponse['user'] | null = null

/** token 过期预留 buffer（秒），过期前 60s 视为已过期，避免请求发出后才过期 */
const TOKEN_EXPIRY_BUFFER_MS = 60_000

/** 正在进行的 refresh 请求（用于并发去重） */
let refreshPromise: Promise<TokenResponse> | null = null

/** SW 启动时初始化会话的 Promise，避免 SidePanel/Popup 在刷新完成前查询状态 */
let sessionInitPromise: Promise<SessionState> | null = null

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

/** 当前会话状态（供 SidePanel/Popup 查询） */
export interface SessionState {
  /** 是否持有有效 access_token */
  isLoggedIn: boolean
  /** 当前登录用户信息（未登录时为 null） */
  user: TokenResponse['user'] | null
  /** 当前使用的后端 base URL */
  backendUrl: string
}

/**
 * 从 chrome.storage.local 读取持久化的认证状态
 *
 * 保存 backendUrl + accessToken + expiresAt，SW 重启后可直接恢复登录态。
 * chrome.storage.local 是扩展私有存储，其他网页/扩展无法读取。
 */
export async function loadAuthStateFromStorage(): Promise<StoredAuthState | null> {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get([AUTH_STORAGE_KEY], (result) => {
        const state = result[AUTH_STORAGE_KEY] as StoredAuthState | undefined
        resolve(state ?? null)
      })
    } catch {
      resolve(null)
    }
  })
}

/**
 * 将 backendUrl 持久化到 chrome.storage.local（保留已有的 token 字段）
 */
export async function saveBackendUrlToStorage(url: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      // 读取已有 state，只更新 backendUrl，不覆盖 token
      chrome.storage.local.get([AUTH_STORAGE_KEY], (result) => {
        const existing = result[AUTH_STORAGE_KEY] as StoredAuthState | undefined
        const state: StoredAuthState = {
          backendUrl: url,
          accessToken: existing?.accessToken ?? null,
          expiresAt: existing?.expiresAt ?? null,
          user: existing?.user ?? null,
        }
        chrome.storage.local.set({ [AUTH_STORAGE_KEY]: state }, () => {
          if (chrome.runtime.lastError) {
            console.warn('[backend_client] 持久化 backendUrl 失败:', chrome.runtime.lastError.message)
          }
          resolve()
        })
      })
    } catch {
      resolve()
    }
  })
}

/**
 * 将 access_token + 过期时间 + 用户信息持久化到 chrome.storage.local
 *
 * 登录成功或 refresh 成功后调用，SW 重启后可直接恢复。
 */
export async function saveTokenToStorage(
  token: string,
  expiresAt: number,
  user?: TokenResponse['user'] | null,
): Promise<void> {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get([AUTH_STORAGE_KEY], (result) => {
        const existing = result[AUTH_STORAGE_KEY] as StoredAuthState | undefined
        const state: StoredAuthState = {
          backendUrl: existing?.backendUrl ?? DEFAULT_BACKEND_URL,
          accessToken: token,
          expiresAt,
          user: user !== undefined ? user : (existing?.user ?? null),
        }
        chrome.storage.local.set({ [AUTH_STORAGE_KEY]: state }, () => {
          if (chrome.runtime.lastError) {
            console.warn('[backend_client] 持久化 token 失败:', chrome.runtime.lastError.message)
          } else {
            console.log(
              '[backend_client] token 已持久化 | expiresAt=',
              new Date(expiresAt).toISOString(),
              '| 剩余=',
              Math.round((expiresAt - Date.now()) / 1000),
              's',
              '| user=',
              state.user?.email ?? 'null',
            )
          }
          resolve()
        })
      })
    } catch {
      resolve()
    }
  })
}

/**
 * 清除持久化的认证状态（含历史遗留 key）
 *
 * 清除所有可能的登录相关 key，防止旧版本残留数据导致 SW 恢复旧 token。
 */
export async function clearAuthStateFromStorage(): Promise<void> {
  const ALL_AUTH_KEYS = [
    AUTH_STORAGE_KEY,     // 'auth_state' — 当前版本
    'access_token',       // 历史遗留
    'token_expire',       // 历史遗留
    'token_expires_at',   // 防御性清除
  ]
  return new Promise((resolve) => {
    try {
      chrome.storage.local.remove(ALL_AUTH_KEYS, () => {
        if (chrome.runtime.lastError) {
          console.warn('[backend_client] 清除 storage 失败:', chrome.runtime.lastError.message)
        } else {
          console.log('[backend_client] storage 登录字段已清除 | keys=', ALL_AUTH_KEYS)
        }
        resolve()
      })
    } catch {
      resolve()
    }
  })
}

/**
 * 检查 token 是否已过期或即将过期
 *
 * @param expiresAt 绝对过期时间戳（ms）
 * @returns true 表示已过期或即将过期（60s buffer）
 */
function isTokenExpired(expiresAt: number | null): boolean {
  if (!expiresAt) return true
  return Date.now() >= expiresAt - TOKEN_EXPIRY_BUFFER_MS
}

/**
 * 从 chrome.storage.local 读取有效 token（每次请求前调用）
 *
 * 设计动机：
 * - 禁止依赖内存中的 accessToken 全局变量（SW 重启后可能残留旧值）
 * - 每次发请求前必须 await 从 storage 读最新 token
 * - 若 storage 中无 token 或 token 已过期，返回 null
 *
 * @returns 有效的 access_token 字符串，或 null（未登录/已过期）
 */
export async function getValidToken(): Promise<string | null> {
  try {
    const stored = await loadAuthStateFromStorage()
    if (!stored?.accessToken) {
      console.log('[backend_client] getValidToken: storage 中无 token')
      return null
    }
    if (isTokenExpired(stored.expiresAt)) {
      console.log(
        '[backend_client] getValidToken: storage 中 token 已过期 | expiresAt=',
        stored.expiresAt ? new Date(stored.expiresAt).toISOString() : 'null',
      )
      return null
    }
    // 同步更新内存缓存（供 getSessionState 等同步读取场景使用）
    accessToken = stored.accessToken
    tokenExpiresAt = stored.expiresAt
    return stored.accessToken
  } catch (err) {
    console.warn('[backend_client] getValidToken: 读取 storage 失败:', err)
    return null
  }
}

/**
 * 设置 access_token（Popup 登录成功后通过 AUTH_TOKEN_UPDATED 调用）
 *
 * @param token access_token 字符串，null 表示登出
 * @param expiresIn token 有效期（秒），从登录接口的 expires_in 字段获取
 * @param user 登录用户信息（持久化到 storage，SW 重启后 SidePanel 可恢复）
 */
export function setAccessToken(
  token: string | null,
  expiresIn?: number,
  user?: TokenResponse['user'] | null,
): void {
  accessToken = token
  if (token && expiresIn) {
    const expiresAt = Date.now() + expiresIn * 1000
    tokenExpiresAt = expiresAt
    if (user !== undefined) {
      currentUser = user
    }
    void saveTokenToStorage(token, expiresAt, user)
  } else if (!token) {
    currentUser = null
    tokenExpiresAt = null
  }
}

/**
 * 设置后端 base URL
 *
 * 副作用：同时持久化到 chrome.storage.local，用于 SW 重启后的静默刷新。
 */
export function setBackendUrl(url: string): void {
  // 移除尾部斜杠，避免拼接时出现 //
  backendUrl = url.replace(/\/$/, '')
  void saveBackendUrlToStorage(backendUrl)
}

/**
 * 设置当前登录用户信息
 */
export function setCurrentUser(user: TokenResponse['user'] | null): void {
  currentUser = user
}

/**
 * 用户登出：清空内存 token、当前用户信息、并发 promise 缓存，并清除持久化 auth_state
 */
export async function logout(): Promise<void> {
  accessToken = null
  tokenExpiresAt = null
  currentUser = null
  refreshPromise = null
  sessionInitPromise = null
  await clearAuthStateFromStorage()
  console.log('[backend_client] 用户登出，内存与 storage 认证状态已清空')
}

/**
 * 仅清空内存中的 token 缓存（不清 storage，由调用方负责清 storage）
 *
 * 用于 CLEAR_TOKEN_CACHE 消息：Popup 先清 storage，再通知 SW 清内存。
 * 分离 storage 清除和内存清除，避免 async 间隙中 SW 用旧内存 token 发请求。
 */
export function clearMemoryToken(): void {
  const hadToken = !!accessToken
  accessToken = null
  tokenExpiresAt = null
  currentUser = null
  refreshPromise = null
  sessionInitPromise = null
  console.log('[backend_client] 内存 token 已清空 | 之前有 token:', hadToken)
}

/**
 * 仅重置 initSession / refresh 的并发 promise 缓存
 *
 * 用于 RESET_EXTRACTION_STATE：不清 token，只防止旧 promise 结果被复用。
 */
export function resetSessionPromises(): void {
  refreshPromise = null
  sessionInitPromise = null
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
 * @returns 新的 TokenResponse（含 access_token 与 user）
 * @throws AuthExpiredError refresh 失败（refresh_token 过期或无效）
 */
async function refreshAccessToken(): Promise<TokenResponse> {
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
        currentUser = null
        throw new AuthExpiredError()
      }

      const data: TokenResponse = await resp.json()
      accessToken = data.access_token
      currentUser = data.user
      // 持久化新 token + user 到 storage（refresh 后端默认签发与登录相同的 expires_in）
      const expiresAt = Date.now() + data.expires_in * 1000
      tokenExpiresAt = expiresAt
      void saveTokenToStorage(data.access_token, expiresAt, data.user)
      console.log(
        '[backend_client] refresh 成功 | user=',
        data.user.email,
        '| expiresIn=',
        data.expires_in,
        's',
      )
      return data
    } catch (err) {
      // 网络错误也视为登录过期（保守策略，让用户重新登录）
      accessToken = null
      currentUser = null
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
  const method = options.method ?? 'GET'

  // ===== 核心：每次请求前从 storage 读最新 token，禁止依赖内存全局变量 =====
  let token = await getValidToken()

  // storage 中无有效 token → 尝试 refresh（可能 Cookie 还有效）
  if (!token) {
    console.log(
      '[backend_client] 请求前无有效 token，尝试 refresh |',
      method,
      path,
    )
    try {
      const tokenData = await refreshAccessToken()
      token = tokenData.access_token
    } catch {
      // refresh 也失败，清除 storage 防止脏数据
      await clearAuthStateFromStorage()
      throw new AuthExpiredError()
    }
  }

  // 构造请求头
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Request-Id': generateRequestId(),
    ...(options.headers as Record<string, string> | undefined),
  }

  // 注入 access_token
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  // 调试日志：打印请求发起时的 token 状态（对比 storage vs 内存）
  const memToken = accessToken
  console.log('[backend_client] 请求发起 |', method, path, {
    storageToken: token ? `${token.slice(0, 12)}...` : null,
    memoryToken: memToken ? `${memToken.slice(0, 12)}...` : null,
    tokenMatch: token === memToken,
    expiresAt: tokenExpiresAt ? new Date(tokenExpiresAt).toISOString() : null,
  })

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

  // 调试日志：打印响应状态码
  console.log(
    '[backend_client] 响应 |',
    method,
    path,
    '| status=',
    resp.status,
    resp.status === 204 ? '(No Content — 可能是 token 无效导致后端无数据返回)' : '',
  )

  // 401 自动刷新并重试一次
  if (resp.status === 401 && token) {
    console.warn('[backend_client] 收到 401，尝试 refresh | path=', path)
    try {
      const tokenData = await refreshAccessToken()
      token = tokenData.access_token
    } catch {
      await clearAuthStateFromStorage()
      throw new AuthExpiredError()
    }

    // 用新 token 重试（只重试一次，避免死循环）
    if (token) {
      headers.Authorization = `Bearer ${token}`
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
    console.log(`[backend_client] ${path} 返回 204 No Content`)
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
 * 获取当前登录用户信息
 */
export function getCurrentUser(): TokenResponse['user'] | null {
  return currentUser
}

/**
 * 初始化会话（SW 启动时调用）
 *
 * 流程：
 * 1. 从 chrome.storage.local 恢复 backendUrl
 * 2. 尝试用 refresh cookie 静默换发 access_token
 * 3. 成功则恢复登录态；失败则保持未登录，等待用户重新登录
 *
 * 幂等：重复调用会返回同一个 Promise，避免并发初始化。
 */
export async function initSession(): Promise<SessionState> {
  if (sessionInitPromise) {
    return sessionInitPromise
  }

  sessionInitPromise = doInitSession()
  return sessionInitPromise
}

async function doInitSession(): Promise<SessionState> {
  let hasStoredState = false
  try {
    // ===== 启动时清理历史遗留 key（旧版本可能存储了独立的 access_token / token_expire）=====
    await new Promise<void>((resolve) => {
      chrome.storage.local.get(['access_token', 'token_expire', 'token_expires_at'], (legacy) => {
        const legacyKeys = Object.keys(legacy).filter((k) => legacy[k] !== undefined)
        if (legacyKeys.length > 0) {
          console.warn('[backend_client] 发现历史遗留 key，自动清理:', legacyKeys)
          chrome.storage.local.remove(legacyKeys, () => resolve())
        } else {
          resolve()
        }
      })
    })

    const stored = await loadAuthStateFromStorage()
    if (stored?.backendUrl) {
      // 不触发再次持久化，避免 SW 启动时写盘
      backendUrl = stored.backendUrl.replace(/\/$/, '')
      hasStoredState = true
    }

    // 尝试从 storage 恢复 access_token
    if (stored?.accessToken && stored.expiresAt) {
      if (!isTokenExpired(stored.expiresAt)) {
        // storage 中有有效 token，直接恢复到内存（含 user 信息）
        accessToken = stored.accessToken
        tokenExpiresAt = stored.expiresAt
        currentUser = stored.user ?? null
        const remainSec = Math.round((stored.expiresAt - Date.now()) / 1000)
        console.log(
          '[backend_client] 从 storage 恢复 token 成功 | 剩余有效期=',
          remainSec,
          's',
          '| user=',
          currentUser?.email ?? 'null',
        )
        return { isLoggedIn: true, user: currentUser, backendUrl }
      }
      // token 已过期，继续走 refresh 流程
      console.log(
        '[backend_client] storage 中 token 已过期，尝试 refresh | expiresAt=',
        new Date(stored.expiresAt).toISOString(),
      )
    }
  } catch (err) {
    console.warn('[backend_client] 从 storage 恢复认证状态失败:', err)
  }

  // 只有从未保存过 auth state 时才跳过 refresh（用户从未登录过）
  // 注意：不能通过 backendUrl === DEFAULT_BACKEND_URL 判断，因为用户可能
  // 使用的就是默认地址 http://localhost:8000，那样会导致 SW 回收后永远无法 refresh
  if (!hasStoredState) {
    return { isLoggedIn: false, user: null, backendUrl }
  }

  try {
    const data = await refreshAccessToken()
    console.log('[backend_client] 静默刷新成功 | user=', data.user.email)
    return { isLoggedIn: true, user: data.user, backendUrl }
  } catch (err) {
    // refresh 失败：cookie 不存在或已过期，属于正常未登录状态
    const message = err instanceof AuthExpiredError ? err.message : String(err)
    console.log('[backend_client] 静默刷新失败，保持未登录:', message)
    return { isLoggedIn: false, user: null, backendUrl }
  }
}

/**
 * 获取当前会话状态
 *
 * 注意：SidePanel/Popup 查询前应先 await initSession()，确保 SW 启动时的
 * 静默刷新已完成，否则可能拿到 SW 重启后的临时未登录状态。
 */
export function getSessionState(): SessionState {
  return {
    isLoggedIn: accessToken !== null,
    user: currentUser,
    backendUrl,
  }
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
