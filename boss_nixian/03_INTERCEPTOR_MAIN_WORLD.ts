/**
 * Boss 直聘职位列表 API 拦截器 - 主世界注入脚本
 *
 * 此脚本需要注入到页面的主世界（Main World）中，
 * 用于拦截 fetch 和 XMLHttpRequest 请求，捕获职位列表 API 响应。
 *
 * 注入方式: 通过 Content Script 使用 chrome.scripting.executeScript 注入
 */

(function () {
  'use strict';

  // 防止重复注入
  if ((window as any).__bossJobInterceptorInstalled) {
    return;
  }
  (window as any).__bossJobInterceptorInstalled = true;

  // 目标 API 特征
  const TARGET_API_PATTERNS = [
    '/wapi/zpgeek/pc/recommend/job/list.json',
    '/wapi/zpgeek/search/job/list.json',
    '/wapi/zpgeek/job/list.json',
  ];

  // 检查 URL 是否匹配目标 API
  function isTargetApi(url: string): boolean {
    return TARGET_API_PATTERNS.some((pattern) => url.includes(pattern));
  }

  // 发送捕获的数据给 Content Script
  function sendCapturedData(data: any) {
    window.postMessage(
      {
        type: 'BOSS_JOB_DATA_CAPTURED',
        payload: data,
        timestamp: Date.now(),
      },
      '*'
    );
  }

  // ==================== Fetch 拦截 ====================

  const originalFetch = window.fetch;

  window.fetch = async function (
    input: RequestInfo | URL,
    init?: RequestInit
  ): Promise<Response> {
    const url = typeof input === 'string' ? input : (input as Request).url || String(input);

    // 调用原始 fetch
    const response = await originalFetch.call(this, input, init);

    // 检查是否是目标 API
    if (isTargetApi(url)) {
      try {
        // 使用 clone 避免破坏原请求
        const clonedResponse = response.clone();
        const text = await clonedResponse.text();
        let data;

        try {
          data = JSON.parse(text);
        } catch {
          data = { rawText: text };
        }

        // 发送给 Content Script
        sendCapturedData({
          url: url,
          method: (init?.method || 'GET').toUpperCase(),
          status: response.status,
          data: data,
          headers: Object.fromEntries(response.headers.entries()),
        });
      } catch (error) {
        console.error('[BossInterceptor] Fetch capture error:', error);
      }
    }

    return response;
  };

  // ==================== XMLHttpRequest 拦截 ====================

  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (
    method: string,
    url: string | URL,
    ...rest: any[]
  ): void {
    // 存储请求信息
    (this as any).__interceptorUrl = typeof url === 'string' ? url : url.toString();
    (this as any).__interceptorMethod = method.toUpperCase();

    return originalXHROpen.apply(this, [method, url, ...rest] as any);
  };

  XMLHttpRequest.prototype.send = function (
    body?: Document | XMLHttpRequestBodyInit | null
  ): void {
    const xhr = this as any;
    const url = xhr.__interceptorUrl || '';

    // 检查是否是目标 API
    if (isTargetApi(url)) {
      xhr.addEventListener('load', function () {
        try {
          let data;

          try {
            data = JSON.parse(xhr.responseText);
          } catch {
            data = { rawText: xhr.responseText };
          }

          sendCapturedData({
            url: url,
            method: xhr.__interceptorMethod || 'GET',
            status: xhr.status,
            data: data,
            headers: xhr.getAllResponseHeaders(),
          });
        } catch (error) {
          console.error('[BossInterceptor] XHR capture error:', error);
        }
      });
    }

    return originalXHRSend.apply(this, [body] as any);
  };

  console.log('[BossInterceptor] Job API interceptor installed successfully');
})();
