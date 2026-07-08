/**
 * Boss 直聘职位数据接收与解析 - Content Script
 *
 * 此脚本运行在 Content Script 的 isolated world 中，
 * 负责接收主世界拦截器发送的数据，解析并转换为扩展内部格式。
 */

// ==================== 类型定义 ====================

/** 扩展内部职位数据结构 */
interface RawBossJob {
  /** 职位名称 */
  title: string;
  /** 公司名称 */
  company: string;
  /** 薪资描述（原始） */
  salary: string;
  /** 薪资下限（元） */
  salaryMin: number | null;
  /** 薪资上限（元） */
  salaryMax: number | null;
  /** 薪资月数 */
  salaryMonths: number;
  /** 完整位置 */
  location: string;
  /** 城市 */
  city: string;
  /** 区域 */
  district: string;
  /** 商圈 */
  area: string;
  /** 标签列表 */
  tags: string[];
  /** 技能要求 */
  skills: string[];
  /** 工作经验要求 */
  experience: string;
  /** 学历要求 */
  degree: string;
  /** 职位 ID（加密） */
  jobId: string;
  /** 公司 ID（加密） */
  companyId: string;
  /** 职位详情页 URL */
  detailUrl: string;
  /** 公司主页 URL */
  companyUrl: string;
  /** 公司 Logo */
  companyLogo: string;
  /** 融资阶段 */
  fundingStage: string;
  /** 行业 */
  industry: string;
  /** 公司规模 */
  companySize: string;
  /** 招聘者姓名 */
  recruiterName: string;
  /** 招聘者职位 */
  recruiterTitle: string;
  /** 招聘者是否在线 */
  recruiterOnline: boolean;
  /** 招聘者头像 */
  recruiterAvatar: string;
  /** 安全 ID（用于详情页访问） */
  securityId: string;
  /** 职位类型：0=全职, 5=实习 */
  jobType: number;
  /** 经度 */
  longitude: number | null;
  /** 纬度 */
  latitude: number | null;
  /** 福利列表 */
  benefits: string[];
  /** 职位状态 */
  status: number;
  /** 捕获时间戳 */
  capturedAt: number;
}

/** 捕获的 API 响应数据 */
interface CapturedApiData {
  url: string;
  method: string;
  status: number;
  data: any;
  headers: Record<string, string> | string;
}

// ==================== 解析函数 ====================

/**
 * 解析薪资字符串
 * @example "8-12K" => { min: 8000, max: 12000, months: 12 }
 * @example "11-16K·13薪" => { min: 11000, max: 16000, months: 13 }
 */
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

/**
 * 拼接完整位置
 * @example "广州" + "番禺区" + "南村" => "广州·番禺区·南村"
 */
function buildLocation(
  cityName: string,
  areaDistrict: string,
  businessDistrict: string
): string {
  return [cityName, areaDistrict, businessDistrict].filter(Boolean).join('·');
}

/**
 * 构建职位详情页 URL
 */
function buildJobDetailUrl(encryptJobId: string): string {
  return `https://www.zhipin.com/job_detail/${encryptJobId}.html`;
}

/**
 * 构建公司主页 URL
 */
function buildCompanyUrl(encryptBrandId: string): string {
  return `https://www.zhipin.com/gongsi/${encryptBrandId}.html`;
}

/**
 * 将 API 响应中的单个职位转换为扩展内部格式
 */
function convertToRawBossJob(jobItem: any): RawBossJob {
  const salary = parseSalary(jobItem.salaryDesc);

  return {
    title: jobItem.jobName || '',
    company: jobItem.brandName || '',
    salary: jobItem.salaryDesc || '',
    salaryMin: salary.min,
    salaryMax: salary.max,
    salaryMonths: salary.months,
    location: buildLocation(
      jobItem.cityName,
      jobItem.areaDistrict,
      jobItem.businessDistrict
    ),
    city: jobItem.cityName || '',
    district: jobItem.areaDistrict || '',
    area: jobItem.businessDistrict || '',
    tags: Array.isArray(jobItem.jobLabels) ? jobItem.jobLabels : [],
    skills: Array.isArray(jobItem.skills) ? jobItem.skills : [],
    experience: jobItem.jobExperience || '',
    degree: jobItem.jobDegree || '',
    jobId: jobItem.encryptJobId || '',
    companyId: jobItem.encryptBrandId || '',
    detailUrl: buildJobDetailUrl(jobItem.encryptJobId || ''),
    companyUrl: buildCompanyUrl(jobItem.encryptBrandId || ''),
    companyLogo: jobItem.brandLogo || '',
    fundingStage: jobItem.brandStageName || '',
    industry: jobItem.brandIndustry || '',
    companySize: jobItem.brandScaleName || '',
    recruiterName: jobItem.bossName || '',
    recruiterTitle: jobItem.bossTitle || '',
    recruiterOnline: jobItem.bossOnline || false,
    recruiterAvatar: jobItem.bossAvatar || '',
    securityId: jobItem.securityId || '',
    jobType: jobItem.jobType || 0,
    longitude: jobItem.gps?.longitude || null,
    latitude: jobItem.gps?.latitude || null,
    benefits: Array.isArray(jobItem.welfareList) ? jobItem.welfareList : [],
    status: jobItem.jobValidStatus || 0,
    capturedAt: Date.now(),
  };
}

/**
 * 解析 API 响应，提取职位列表
 */
function parseJobListResponse(responseData: any): {
  jobs: RawBossJob[];
  hasMore: boolean;
  total: number;
} {
  const jobs: RawBossJob[] = [];

  // 检查响应结构
  if (responseData.code !== 0) {
    console.warn('[BossParser] API returned non-zero code:', responseData.code);
    return { jobs, hasMore: false, total: 0 };
  }

  const jobList = responseData.zpData?.jobList;
  if (!Array.isArray(jobList)) {
    console.warn('[BossParser] No jobList found in response');
    return { jobs, hasMore: false, total: 0 };
  }

  for (const item of jobList) {
    try {
      jobs.push(convertToRawBossJob(item));
    } catch (error) {
      console.error('[BossParser] Error converting job item:', error, item);
    }
  }

  return {
    jobs,
    hasMore: responseData.zpData?.hasMore || false,
    total: jobs.length,
  };
}

// ==================== 消息监听 ====================

// 监听主世界拦截器发送的消息
window.addEventListener('message', (event: MessageEvent) => {
  // 只处理 Boss 职位数据消息
  if (event.data?.type !== 'BOSS_JOB_DATA_CAPTURED') {
    return;
  }

  // 确保消息来自同一窗口
  if (event.source !== window) {
    return;
  }

  const capturedData = event.data.payload as CapturedApiData;

  console.log('[BossParser] Captured job API response:', capturedData.url);

  try {
    const result = parseJobListResponse(capturedData.data);

    if (result.jobs.length > 0) {
      console.log(`[BossParser] Parsed ${result.jobs.length} jobs, hasMore: ${result.hasMore}`);

      // 发送给 Service Worker
      chrome.runtime.sendMessage({
        type: 'BOSS_JOBS_CAPTURED',
        payload: {
          jobs: result.jobs,
          hasMore: result.hasMore,
          total: result.total,
          sourceUrl: capturedData.url,
          capturedAt: Date.now(),
        },
      });
    }
  } catch (error) {
    console.error('[BossParser] Error parsing job data:', error);
  }
});

// 通知 Service Worker Content Script 已加载
chrome.runtime.sendMessage({
  type: 'BOSS_CONTENT_SCRIPT_READY',
  payload: {
    url: window.location.href,
    timestamp: Date.now(),
  },
});

console.log('[BossParser] Content Script loaded and ready');
