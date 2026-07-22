/**
 * Boss 直聘职位列表 API 拦截器 - 主世界注入脚本
 *
 * 职责：
 * - 注入到页面主世界（Main World），在 Boss 直聘 JS 之前安装
 * - 拦截 fetch / XMLHttpRequest，捕获职位列表 API 响应
 * - 通过 window.postMessage 将数据发送给 Content Script（isolated world）
 *
 * 注入方式：
 * - Content Script 动态创建 <script src="chrome-extension://<id>/interceptor.js">
 * - manifest.json 已将该文件声明为 web_accessible_resources
 */

(function () {
  'use strict'

  // ==================== 诊断日志 ====================
  // 此日志用于确认 interceptor.js 是否真正注入到 Main World
  // 应出现在 DevTools 控制台的「顶层」上下文（不是 Content Script 上下文）
  // 如果看不到此日志，说明 registerContentScripts 没有成功注入
  console.log('[Boss拦截器] 主世界脚本已执行，页面地址:', window.location.href)
  console.log('[Boss拦截器] 当前时间:', new Date().toISOString())
  console.log('[Boss拦截器] window.fetch 类型:', typeof window.fetch)
  console.log('[Boss拦截器] XMLHttpRequest 类型:', typeof XMLHttpRequest)

  // 常量声明必须在使用前完成，避免 TDZ（Temporal Dead Zone）导致 sendLog 静默失败
  // 职位列表 API（已存在）
  const JOB_API_PATTERNS = [
    '/wapi/zpgeek/pc/recommend/job/list.json',
    '/wapi/zpgeek/search/job/list.json',
    '/wapi/zpgeek/job/list.json',
  ]

  // 聊天 API（2026-07-21 逆向分析确认）
  // - geekFilterByLabel: GET 获取 HR 基础列表（friendList）
  // - getGeekFriendList.json: POST 获取 HR 详情（含 securityId/lastMessageInfo/unreadMsgCount）
  // 注：history/pull 返回 Protobuf+Base64，本次不拦截，消息历史走 DOM 兜底
  const CHAT_LIST_API_PATTERN = '/wapi/zprelation/friend/geekFilterByLabel'
  const CHAT_DETAIL_API_PATTERN = '/wapi/zprelation/friend/getGeekFriendList.json'

  /** 消息类型：职位数据（已存在） */
  const MESSAGE_TYPE_JOB = 'BOSS_JOB_DATA_CAPTURED'
  /** 消息类型：HR 列表基础信息（friendList） */
  const MESSAGE_TYPE_CHAT_LIST = 'BOSS_CHAT_LIST_CAPTURED'
  /** 消息类型：HR 详情（含 securityId/最后消息/未读数） */
  const MESSAGE_TYPE_CHAT_DETAIL = 'BOSS_CHAT_DETAIL_CAPTURED'
  const LOG_MESSAGE_TYPE = 'BOSS_INTERCEPTOR_LOG'

  /**
   * 根据 URL 判定数据类型
   *
   * 设计动机：单一入口分发，避免 fetch/XHR 拦截器中重复判定逻辑
   * 返回值用于决定 postMessage 的 type 字段，让 Content Script 走不同处理分支
   */
  function classifyUrl(url) {
    if (!url || typeof url !== 'string') return null
    if (JOB_API_PATTERNS.some((p) => url.includes(p))) return 'job'
    if (url.includes(CHAT_LIST_API_PATTERN)) return 'chat_list'
    if (url.includes(CHAT_DETAIL_API_PATTERN)) return 'chat_detail'
    return null
  }

  /**
   * 判断 URL 是否匹配目标 API（兼容旧调用方）
   *
   * 保留 isTargetApi 是为了不破坏既有调试日志的语义，
   * 内部直接复用 classifyUrl 并归一化为布尔
   */
  function isTargetApi(url) {
    return classifyUrl(url) !== null
  }

  // 调试开关：开启后会记录所有 fetch/XHR 请求 URL，用于定位拦截失败根因
  const DEBUG_ALL_REQUESTS = true

  /**
   * 发送日志给 Content Script，由其转发到后端终端
   * targetOrigin 使用 '*' 以确保跨世界消息投递不被 origin 检查阻断
   */
  function sendLog(level, message, context) {
    try {
      window.postMessage(
        {
          type: LOG_MESSAGE_TYPE,
          payload: { level, source: 'interceptor', message, context, timestamp: Date.now() },
        },
        '*',
      )
    } catch (error) {
      console.error('[BossInterceptor] sendLog failed:', error)
    }
  }

  // 防止重复注入
  if (window.__bossJobInterceptorInstalled) {
    sendLog('info', 'interceptor already installed, skipping')
    return
  }
  window.__bossJobInterceptorInstalled = true
  sendLog('info', 'Job & Chat API interceptor installed, waiting for target API calls')

  /**
   * 将捕获的数据发送给 Content Script
   *
   * 根据 URL 分类选择对应的消息类型：
   * - job → BOSS_JOB_DATA_CAPTURED（职位列表）
   * - chat_list → BOSS_CHAT_LIST_CAPTURED（HR 基础列表）
   * - chat_detail → BOSS_CHAT_DETAIL_CAPTURED（HR 详情+最后消息）
   *
   * 设计动机：Content Script 通过 type 字段分流，避免在同一个 handler 里
   * 用 URL 字符串匹配判定，降低耦合且便于扩展新的 API 拦截
   */
  function sendCapturedData(url, payload) {
    const kind = classifyUrl(url)
    if (!kind) return

    // 类型映射：kind → postMessage.type
    const typeMap = {
      job: MESSAGE_TYPE_JOB,
      chat_list: MESSAGE_TYPE_CHAT_LIST,
      chat_detail: MESSAGE_TYPE_CHAT_DETAIL,
    }
    const messageType = typeMap[kind]

    try {
      window.postMessage(
        {
          type: messageType,
          payload,
          timestamp: Date.now(),
        },
        '*',
      )
    } catch (error) {
      // 发送失败不应影响页面功能
      console.error('[BossInterceptor] postMessage failed:', error)
    }
  }

  /**
   * 安全解析 JSON，失败时返回原始文本包装
   */
  function safeJsonParse(text) {
    try {
      return JSON.parse(text)
    } catch {
      return { __rawText: String(text).slice(0, 5000) }
    }
  }

  // ==================== Fetch 拦截 ====================

  const originalFetch = window.fetch

  window.fetch = async function (input, init) {
    let url = ''
    try {
      url = typeof input === 'string' ? input : input.url || String(input)
    } catch {
      url = ''
    }

    // 调试：记录所有 fetch 请求，确认拦截函数是否被调用
    if (DEBUG_ALL_REQUESTS) {
      sendLog('debug', '[fetch] 拦截到请求', { url: url.slice(0, 200), method: (init && init.method) || 'GET' })
      console.log('[Boss拦截器] fetch 请求:', url.slice(0, 200))
    }

    const response = await originalFetch.apply(this, arguments)

    if (isTargetApi(url)) {
      sendLog('info', '捕获到目标 API (fetch)', { url: url.slice(0, 120) })
      console.log('[Boss拦截器] ✅ 捕获到目标 API (fetch):', url.slice(0, 120))
      try {
        const clonedResponse = response.clone()
        const text = await clonedResponse.text()

        sendCapturedData(url, {
          url,
          method: (init && init.method) || 'GET',
          status: response.status,
          data: safeJsonParse(text),
          headers: Object.fromEntries(response.headers.entries()),
        })
      } catch (error) {
        sendLog('error', 'Fetch capture error', { error: String(error) })
      }
    }

    return response
  }

  // ==================== XMLHttpRequest 拦截 ====================

  const originalXHROpen = XMLHttpRequest.prototype.open
  const originalXHRSend = XMLHttpRequest.prototype.send

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      this.__interceptorUrl = typeof url === 'string' ? url : String(url)
      this.__interceptorMethod = String(method).toUpperCase()
      // 调试：记录所有 XHR open 请求，确认拦截函数是否被调用
      if (DEBUG_ALL_REQUESTS) {
        sendLog('debug', '[xhr] 拦截到 open 请求', { url: this.__interceptorUrl.slice(0, 200), method: this.__interceptorMethod })
        console.log('[Boss拦截器] XHR open:', this.__interceptorMethod, this.__interceptorUrl.slice(0, 200))
      }
    } catch {
      // 忽略元数据记录错误
    }
    return originalXHROpen.apply(this, [method, url, ...rest])
  }

  XMLHttpRequest.prototype.send = function (body) {
    const xhr = this
    const url = xhr.__interceptorUrl || ''

    if (isTargetApi(url)) {
      sendLog('info', '捕获到目标 API (XHR)', { url: url.slice(0, 120) })
      console.log('[Boss拦截器] ✅ 捕获到目标 API (XHR):', url.slice(0, 120))
      xhr.addEventListener('load', function () {
        try {
          sendCapturedData(url, {
            url,
            method: xhr.__interceptorMethod || 'GET',
            status: xhr.status,
            data: safeJsonParse(xhr.responseText),
            headers: xhr.getAllResponseHeaders(),
          })
        } catch (error) {
          sendLog('error', 'XHR capture error', { error: String(error) })
        }
      })
    }

    return originalXHRSend.apply(this, [body])
  }

  sendLog('info', 'Job & Chat API interceptor installed')
  console.log('[Boss拦截器] ✅ 拦截器安装完成（Job + Chat），等待 Boss API 请求...')
})()
