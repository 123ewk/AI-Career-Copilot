/**
 * Boss 直聘 API 响应解析器
 *
 * 职责：
 * - 将主世界拦截器捕获到的 Boss 职位列表 API JSON
 *   转换为扩展内部现有的 RawBossJob 格式
 * - 复用 parser.ts 的 parseSalary 进行薪资结构化
 *
 * 输入来源：
 * - /wapi/zpgeek/pc/recommend/job/list.json
 * - /wapi/zpgeek/search/job/list.json
 *
 * 输出：
 * - RawBossJob[]，可直接复用现有消息链路发送给 Service Worker
 */

import type { RawBossJob } from './parser'
import { parseSalary } from './parser'

/** 拦截器捕获到的 API 响应包装 */
export interface CapturedApiPayload {
  /** 请求 URL */
  url: string
  /** 请求方法 */
  method: string
  /** HTTP 状态码 */
  status: number
  /** 解析后的响应体 */
  data: unknown
  /** 响应头 */
  headers: Record<string, string> | string
}

/** Boss API 列表响应顶层结构 */
interface BossApiListResponse {
  code?: number
  message?: string
  zpData?: {
    hasMore?: boolean
    jobList?: BossApiJobItem[]
    totalCount?: number
    [key: string]: unknown
  }
}

/** Boss API 单个职位项（字段基于逆向分析，按实际响应选填） */
interface BossApiJobItem {
  jobName?: string
  brandName?: string
  salaryDesc?: string
  cityName?: string
  areaDistrict?: string
  businessDistrict?: string
  jobLabels?: string[]
  skills?: string[]
  encryptJobId?: string
  encryptBrandId?: string
  securityId?: string
  bossName?: string
  bossTitle?: string
  bossAvatar?: string
  bossOnline?: boolean
  jobExperience?: string
  jobDegree?: string
  jobType?: number
  welfareList?: string[]
  brandLogo?: string
  brandStageName?: string
  brandIndustry?: string
  brandScaleName?: string
  jobValidStatus?: number
  [key: string]: unknown
}

/** 解析结果 */
export interface ParsedJobListResult {
  jobs: RawBossJob[]
  hasMore: boolean
  total: number
}

/**
 * 安全地判断值是否为 Boss API 列表响应
 */
function isBossApiListResponse(value: unknown): value is BossApiListResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as Record<string, unknown>).code === 0
  )
}

/**
 * 构建地点字符串
 *
 * @example "广州" + "番禺区" + "南村" => "广州·番禺区·南村"
 */
function buildLocation(
  city?: string,
  district?: string,
  business?: string,
): string {
  return [city, district, business].filter(Boolean).join('·')
}

/**
 * 构建职位详情页 URL
 */
function buildDetailUrl(encryptJobId?: string): string {
  if (!encryptJobId) return ''
  return `https://www.zhipin.com/job_detail/${encryptJobId}.html`
}

/**
 * 将单个 Boss API 职位项转换为 RawBossJob
 *
 * 注意：保持与 parser.ts 中 RawBossJob 的字段语义一致
 */
function convertApiJobToRawBossJob(
  item: BossApiJobItem,
  sourceUrl: string,
): RawBossJob | null {
  const title = item.jobName?.trim()
  if (!title) return null

  const salaryRaw = item.salaryDesc?.trim() ?? ''
  // 复用现有 parseSalary 校验格式，但不依赖其返回值填充 RawBossJob
  parseSalary(salaryRaw)

  const location = buildLocation(
    item.cityName,
    item.areaDistrict,
    item.businessDistrict,
  )

  const detailUrl = buildDetailUrl(item.encryptJobId)

  return {
    title,
    company: item.brandName?.trim() || '(未知公司)',
    salaryRaw,
    location,
    tags: Array.isArray(item.jobLabels) ? item.jobLabels : [],
    skills: Array.isArray(item.skills) ? item.skills : undefined,
    source: 'boss',
    sourceUrl,
    detailUrl,
    seen: false,
    recruiterName: item.bossName,
    recruiterTitle: item.bossTitle,
  }
}

/**
 * 解析 Boss API 职位列表响应
 *
 * @param payload 拦截器捕获到的 API 响应
 * @param sourceUrl 当前页面 URL，用于填充 RawBossJob.sourceUrl
 * @returns 解析后的职位列表、是否有更多、总数
 */
export function parseBossApiResponse(
  payload: CapturedApiPayload,
  sourceUrl: string,
): ParsedJobListResult {
  const result: ParsedJobListResult = {
    jobs: [],
    hasMore: false,
    total: 0,
  }

  if (!isBossApiListResponse(payload.data)) {
    console.warn('[BossApiParser] Response is not a valid Boss API list response')
    return result
  }

  const zpData = payload.data.zpData
  if (!zpData || !Array.isArray(zpData.jobList)) {
    console.warn('[BossApiParser] No jobList in response')
    return result
  }

  for (const item of zpData.jobList) {
    try {
      const job = convertApiJobToRawBossJob(item, sourceUrl)
      if (job) {
        result.jobs.push(job)
      }
    } catch (error) {
      console.error('[BossApiParser] Error converting job item:', error, item)
    }
  }

  result.hasMore = zpData.hasMore === true
  result.total = result.jobs.length

  return result
}

/**
 * 检查 payload 是否来自目标职位列表 API
 */
export function isJobListApiPayload(payload: CapturedApiPayload): boolean {
  const patterns = [
    '/wapi/zpgeek/pc/recommend/job/list.json',
    '/wapi/zpgeek/search/job/list.json',
  ]
  return patterns.some((pattern) => payload.url.includes(pattern))
}
