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

  // 防止重复注入
  if (window.__bossJobInterceptorInstalled) {
    return
  }
  window.__bossJobInterceptorInstalled = true

  // 目标 API 特征（按实际观察到的路径配置）
  const TARGET_API_PATTERNS = [
    '/wapi/zpgeek/pc/recommend/job/list.json',
    '/wapi/zpgeek/search/job/list.json',
    '/wapi/zpgeek/job/list.json',
  ]

  const MESSAGE_TYPE = 'BOSS_JOB_DATA_CAPTURED'

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
        window.location.origin,
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

    const response = await originalFetch.apply(this, arguments)

    if (isTargetApi(url)) {
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
        console.error('[BossInterceptor] Fetch capture error:', error)
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
    } catch {
      // 忽略元数据记录错误
    }
    return originalXHROpen.apply(this, [method, url, ...rest])
  }

  XMLHttpRequest.prototype.send = function (body) {
    const xhr = this
    const url = xhr.__interceptorUrl || ''

    if (isTargetApi(url)) {
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
          console.error('[BossInterceptor] XHR capture error:', error)
        }
      })
    }

    return originalXHRSend.apply(this, [body])
  }

  console.log('[BossInterceptor] Job API interceptor installed')
})()
