/**
 * Boss 直聘职位拦截 - 完整实现示例
 *
 * 本文件展示如何将拦截器集成到 AI Career Copilot 扩展中
 */

// ==================== manifest.json 配置示例 ====================

/*
{
  "manifest_version": 3,
  "name": "AI Career Copilot",
  "version": "1.0.0",
  "permissions": [
    "activeTab",
    "scripting",
    "storage"
  ],
  "host_permissions": [
    "*://www.zhipin.com/*"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": ["*://www.zhipin.com/web/geek/jobs*"],
      "js": ["content.js"],
      "world": "ISOLATED",
      "run_at": "document_start"
    }
  ],
  "web_accessible_resources": [
    {
      "resources": ["interceptor.js"],
      "matches": ["*://www.zhipin.com/*"]
    }
  ]
}
*/

// ==================== content.ts ====================

/**
 * Content Script - 运行在隔离世界
 * 负责：
 * 1. 注入主世界拦截器
 * 2. 接收拦截数据
 * 3. 发送给 Service Worker
 */

// 类型定义
interface RawBossJob {
  title: string;
  company: string;
  salary: string;
  salaryMin: number | null;
  salaryMax: number | null;
  salaryMonths: number;
  location: string;
  city: string;
  district: string;
  area: string;
  tags: string[];
  skills: string[];
  experience: string;
  degree: string;
  jobId: string;
  companyId: string;
  detailUrl: string;
  companyUrl: string;
  companyLogo: string;
  fundingStage: string;
  industry: string;
  companySize: string;
  recruiterName: string;
  recruiterTitle: string;
  recruiterOnline: boolean;
  recruiterAvatar: string;
  securityId: string;
  jobType: number;
  longitude: number | null;
  latitude: number | null;
  benefits: string[];
  status: number;
  capturedAt: number;
}

// 注入主世界拦截器
function injectInterceptor(): void {
  const script = document.createElement('script');
  script.src = chrome.runtime.getURL('interceptor.js');
  script.onload = () => script.remove();
  (document.head || document.documentElement).appendChild(script);
}

// 解析薪资
function parseSalary(salaryDesc: string): {
  min: number | null;
  max: number | null;
  months: number;
} {
  if (!salaryDesc || salaryDesc === '面议') {
    return { min: null, max: null, months: 12 };
  }

  const match = salaryDesc.match(/(\d+)-(\d+)K(?:·(\d+)薪)?/i);
  if (match) {
    return {
      min: parseInt(match[1]) * 1000,
      max: parseInt(match[2]) * 1000,
      months: match[3] ? parseInt(match[3]) : 12,
    };
  }

  return { min: null, max: null, months: 12 };
}

// 构建位置
function buildLocation(city: string, district: string, area: string): string {
  return [city, district, area].filter(Boolean).join('·');
}

// 转换单个职位
function convertJob(item: any): RawBossJob {
  const salary = parseSalary(item.salaryDesc);

  return {
    title: item.jobName || '',
    company: item.brandName || '',
    salary: item.salaryDesc || '',
    salaryMin: salary.min,
    salaryMax: salary.max,
    salaryMonths: salary.months,
    location: buildLocation(item.cityName, item.areaDistrict, item.businessDistrict),
    city: item.cityName || '',
    district: item.areaDistrict || '',
    area: item.businessDistrict || '',
    tags: Array.isArray(item.jobLabels) ? item.jobLabels : [],
    skills: Array.isArray(item.skills) ? item.skills : [],
    experience: item.jobExperience || '',
    degree: item.jobDegree || '',
    jobId: item.encryptJobId || '',
    companyId: item.encryptBrandId || '',
    detailUrl: `https://www.zhipin.com/job_detail/${item.encryptJobId}.html`,
    companyUrl: `https://www.zhipin.com/gongsi/${item.encryptBrandId}.html`,
    companyLogo: item.brandLogo || '',
    fundingStage: item.brandStageName || '',
    industry: item.brandIndustry || '',
    companySize: item.brandScaleName || '',
    recruiterName: item.bossName || '',
    recruiterTitle: item.bossTitle || '',
    recruiterOnline: item.bossOnline || false,
    recruiterAvatar: item.bossAvatar || '',
    securityId: item.securityId || '',
    jobType: item.jobType || 0,
    longitude: item.gps?.longitude || null,
    latitude: item.gps?.latitude || null,
    benefits: Array.isArray(item.welfareList) ? item.welfareList : [],
    status: item.jobValidStatus || 0,
    capturedAt: Date.now(),
  };
}

// 解析职位列表响应
function parseJobListResponse(data: any): RawBossJob[] {
  if (data.code !== 0 || !Array.isArray(data.zpData?.jobList)) {
    return [];
  }

  return data.zpData.jobList.map(convertJob).filter(Boolean);
}

// 监听主世界消息
window.addEventListener('message', (event) => {
  if (event.data?.type !== 'BOSS_JOB_DATA_CAPTURED' || event.source !== window) {
    return;
  }

  const { data } = event.data.payload;
  const jobs = parseJobListResponse(data);

  if (jobs.length > 0) {
    // 发送给 Service Worker
    chrome.runtime.sendMessage({
      type: 'BOSS_JOBS_CAPTURED',
      payload: {
        jobs,
        hasMore: data.zpData?.hasMore || false,
        total: jobs.length,
        capturedAt: Date.now(),
      },
    });
  }
});

// 初始化
injectInterceptor();
console.log('[BossCopilot] Content script initialized');

// ==================== interceptor.js ====================

/**
 * 主世界拦截器 - 注入到页面上下文
 * 负责拦截 fetch/XHR 请求并捕获职位数据
 */

/*
(function() {
  'use strict';

  if (window.__bossInterceptorInstalled) return;
  window.__bossInterceptorInstalled = true;

  const TARGET_PATTERNS = [
    '/wapi/zpgeek/pc/recommend/job/list.json',
    '/wapi/zpgeek/search/job/list.json',
  ];

  function isTarget(url) {
    return TARGET_PATTERNS.some(p => url.includes(p));
  }

  function send(data) {
    window.postMessage({
      type: 'BOSS_JOB_DATA_CAPTURED',
      payload: data,
      timestamp: Date.now()
    }, '*');
  }

  // Fetch 拦截
  const origFetch = window.fetch;
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : input.url;
    const resp = await origFetch.call(this, input, init);

    if (isTarget(url)) {
      try {
        const clone = resp.clone();
        const text = await clone.text();
        send({ url, method: 'GET', status: resp.status, data: JSON.parse(text) });
      } catch (e) {
        console.error('[Interceptor] Error:', e);
      }
    }

    return resp;
  };

  // XHR 拦截
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__url = url;
    this.__method = method;
    return origOpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function(body) {
    const xhr = this;
    if (isTarget(xhr.__url || '')) {
      xhr.addEventListener('load', function() {
        try {
          send({
            url: xhr.__url,
            method: xhr.__method,
            status: xhr.status,
            data: JSON.parse(xhr.responseText)
          });
        } catch (e) {}
      });
    }
    return origSend.apply(this, [body]);
  };

  console.log('[Interceptor] Installed');
})();
*/

// ==================== background.ts (Service Worker) ====================

/**
 * Service Worker - 处理拦截到的职位数据
 */

/*
// 监听 Content Script 消息
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'BOSS_JOBS_CAPTURED') {
    const { jobs, hasMore, total } = message.payload;

    console.log(`[Background] Captured ${total} jobs, hasMore: ${hasMore}`);

    // 存储到本地
    chrome.storage.local.set({
      bossJobs: jobs,
      bossJobsMetadata: {
        hasMore,
        total,
        capturedAt: Date.now(),
        tabId: sender.tab?.id
      }
    });

    // 可以在这里触发其他处理逻辑
    // 例如：匹配简历、发送通知等
  }
});

// 监听标签页更新，注入拦截器
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url?.includes('zhipin.com/web/geek/jobs')) {
    // Content Script 会自动注入（通过 manifest 配置）
    console.log('[Background] Boss page loaded:', tabId);
  }
});
*/
