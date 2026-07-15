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
  const TARGET_API_PATTERNS = [
    '/wapi/zpgeek/pc/recommend/job/list.json',
    '/wapi/zpgeek/search/job/list.json',
    '/wapi/zpgeek/job/list.json',
  ]

  const MESSAGE_TYPE = 'BOSS_JOB_DATA_CAPTURED'
  const LOG_MESSAGE_TYPE = 'BOSS_INTERCEPTOR_LOG'

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
  sendLog('info', 'Job API interceptor installed, waiting for target API calls')

  /**
   * 判断 URL 是否匹配目标 API
   */
  function isTargetApi(url) {
    if (!url || typeof url !== 'string') return false
    return TARGET_API_PATTERNS.some((pattern) => url.includes(pattern))
  }

  /**
   * 将捕获的数据发送给 Content Script
   */
  function sendCapturedData(payload) {
    try {
      window.postMessage(
        {
          type: MESSAGE_TYPE,
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

        sendCapturedData({
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
          sendCapturedData({
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

  sendLog('info', 'Job API interceptor installed')
  console.log('[Boss拦截器] ✅ 拦截器安装完成，等待 Boss API 请求...')
})()
