/**
 * Boss 直聘数据解析器
 *
 * 职责：
 * - 定义 RawBossJob 接口（Content Script 提取的原始数据）
 * - 实现 parseSalary：解析 Boss 薪资字符串为结构化数据
 * - 实现 cleanJdText：清洗 JD 文本（去除按钮文本、合并空白）
 * - 实现 toJobCreateRequest：将 RawBossJob 转换为后端 JobCreateRequest
 *
 * 设计动机：
 * - spec §2.2 要求 parser 独立于 selector/adapter，便于单测
 * - design doc §4.3 已给出薪资解析规则，本文件基于其增强
 * - 字体反爬已在 selector.ts 的 queryTextRendered 处理，parser 接收的 salaryRaw 应为正常数字
 */

/**
 * Boss 直聘原始岗位数据（Content Script 提取）
 *
 * 列表页提取后：基础信息字段填充，详情字段（jdText/skills/recruiter*）为 undefined
 * 详情面板补充后：所有字段填充
 *
 * 与 design doc §4.1 对齐
 */
export interface RawBossJob {
  /** 岗位名（如 "Python 实习生"） */
  title: string
  /** 公司名（如 "科脉技术"），猎头/代招可能为 "某大型 ICT 公司" */
  company: string
  /** 薪资原始文本（如 "300-360元/天"、"15-30K"、"薪资面议"） */
  salaryRaw: string
  /** 工作地点（如 "深圳·南山区·西丽"） */
  location: string
  /** 标签列表（如 ["5天/周", "6个月", "本科"]） */
  tags: string[]

  /** JD 正文（详情面板补充） */
  jdText?: string
  /** 技能标签（详情面板补充，如 ["Pandas", "MySQL", "Python"]） */
  skills?: string[]
  /** 招聘者姓名（如 "罗女士"） */
  recruiterName?: string
  /** 招聘者职位（如 "科脉技术 · HR"） */
  recruiterTitle?: string
  /** 详细工作地址（如 "深圳南山区南山智园 A4 栋 8 楼"） */
  address?: string

  /** 数据来源平台标识，固定为 "boss" */
  source: 'boss'
  /** 列表页 URL（用于区分不同搜索条件） */
  sourceUrl: string
  /** 岗位详情页 URL（用于唯一标识 Job） */
  detailUrl: string
  /** 是否已查看（.is-seen 类存在） */
  seen: boolean
  /** 特殊标签（"猎头"/"代招"/"普通"），来自 .job-tag-icon 的 alt 属性 */
  specialTag?: string
}

/**
 * 薪资解析结果
 */
export interface ParsedSalary {
  /** 最低薪资（数值部分） */
  min?: number
  /** 最高薪资（数值部分） */
  max?: number
  /** 单位（"K" / "元/天" / "元/时"） */
  unit?: string
  /** 附加信息（如 "14薪"），不影响 min/max */
  extra?: string
  /** 原始文本（未解析时也保留） */
  original: string
  /** 是否为面议 */
  isNegotiable: boolean
}

/**
 * 后端 JobCreateRequest 结构
 *
 * 与 backend/app/domain/job/models.py 的 JobCreateRequest 对齐
 * 海投模式：jd_text 可为空字符串（先创建后补充）
 */
export interface JobCreateRequest {
  /** 岗位名 */
  title: string
  /** 公司名 */
  company: string
  /** JD 正文，海投模式下可先为空字符串 */
  jd_text: string
  /** 数据来源，固定 "boss" */
  source: 'boss'
  /** 岗位详情页 URL（唯一约束，重复提交返回已有记录） */
  source_url: string
  /** 工作地点 */
  location?: string
  /** 薪资下限（数值） */
  salary_min?: number
  /** 薪资上限（数值） */
  salary_max?: number
  /** 薪资单位（"K" / "元/天" / "元/时"） */
  salary_unit?: string
  /** 技能标签 */
  skills?: string[]
  /** 关键词（从 tags 转换） */
  keywords?: string[]
  /** 资历级别（MVP 阶段不推断） */
  seniority?: string
  /** 难度（MVP 阶段不推断，需分析 JD） */
  difficulty?: string
}

/**
 * 后端 JobUpdateRequest 结构（PATCH 接口补充 JD 用）
 *
 * 与 backend/app/domain/job/models.py 的 JobUpdateRequest 对齐
 * 所有字段可选，仅更新传入字段
 */
export interface JobUpdateRequest {
  /** JD 正文 */
  jd_text?: string
  /** 技能标签 */
  skills?: string[]
  /** 关键词 */
  keywords?: string[]
  /** 工作地点 */
  location?: string
  /** 薪资下限 */
  salary_min?: number
  /** 薪资上限 */
  salary_max?: number
  /** 薪资单位 */
  salary_unit?: string
  /** 资历级别 */
  seniority?: string
  /** 难度 */
  difficulty?: string
}

/**
 * 解析 Boss 直聘薪资字符串
 *
 * 支持格式（design doc §4.3）：
 * - "15-30K" → { min: 15, max: 30, unit: "K" }
 * - "30-50K·14薪" → { min: 30, max: 50, unit: "K", extra: "14薪" }
 * - "300-360元/天" → { min: 300, max: 360, unit: "元/天" }
 * - "20元/时" → { min: 20, max: 20, unit: "元/时" }
 * - "薪资面议" → { isNegotiable: true }
 * - 无法解析 → { original: raw, isNegotiable: false }
 *
 * 注意：salaryRaw 应为浏览器渲染后的正常数字（由 selector.queryTextRendered 处理字体反爬）
 *
 * @param salaryRaw 薪资原始文本
 * @returns 解析结果
 */
export function parseSalary(salaryRaw: string): ParsedSalary {
  const original = salaryRaw.trim()

  // 面议：不解析数值
  if (original.includes('面议')) {
    return { original, isNegotiable: true }
  }

  // 提取附加信息（如 "·14薪"）
  const extraMatch = original.match(/·(\d+薪)/)
  const extra = extraMatch?.[1]

  // 1. K/月：如 "15-30K"、"30-50K·14薪"
  // \d+(?:\.\d+)? 支持小数（如 "15.5-30K"）
  const monthly = original.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*K/i)
  if (monthly) {
    return {
      min: Math.round(parseFloat(monthly[1])),
      max: Math.round(parseFloat(monthly[2])),
      unit: 'K',
      extra,
      original,
      isNegotiable: false,
    }
  }

  // 2. 元/天：如 "300-360元/天"
  const daily = original.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元\/天/)
  if (daily) {
    return {
      min: Math.round(parseFloat(daily[1])),
      max: Math.round(parseFloat(daily[2])),
      unit: '元/天',
      extra,
      original,
      isNegotiable: false,
    }
  }

  // 3. 元/时：如 "20-30元/时"
  const hourly = original.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*元\/时/)
  if (hourly) {
    return {
      min: Math.round(parseFloat(hourly[1])),
      max: Math.round(parseFloat(hourly[2])),
      unit: '元/时',
      extra,
      original,
      isNegotiable: false,
    }
  }

  // 4. 单个数字 + 单位：如 "20K"、"300元/天"、"20元/时"
  const single = original.match(/(\d+(?:\.\d+)?)\s*(K|元\/天|元\/时)/i)
  if (single) {
    const v = Math.round(parseFloat(single[1]))
    // 单值薪资：min = max
    return {
      min: v,
      max: v,
      unit: single[2],
      extra,
      original,
      isNegotiable: false,
    }
  }

  // 5. 无法解析：保留原始文本，不报错（不影响核心流程）
  return { original, isNegotiable: false }
}

/**
 * 清洗 JD 文本
 *
 * 处理：
 * 1. 去除 "展开"/"收起" 按钮文本（Boss JD 区域底部）
 * 2. 合并多个空白字符（空格、换行、制表符）为单个空格
 * 3. 去除首尾空白
 *
 * @param raw 原始 JD 文本
 * @returns 清洗后的 JD 文本
 */
export function cleanJdText(raw: string): string {
  if (!raw) return ''

  return raw
    // 去除 "展开"/"收起" 按钮文本（可能单独出现或在末尾）
    .replace(/\s*(展开|收起)\s*/g, ' ')
    // 合并多个空白字符（包括 \n \r \t 和多个空格）为单个空格
    .replace(/\s+/g, ' ')
    // 去除首尾空白
    .trim()
}

/**
 * 将 RawBossJob 转换为后端 JobCreateRequest
 *
 * 转换规则：
 * - title/company/source 直接映射
 * - jd_text：详情未补充时为空字符串（海投模式允许）
 * - source_url：使用 detailUrl（唯一约束，避免同一岗位重复创建）
 * - location：使用 location 字段（如 "深圳·南山区·西丽"）
 * - 薪资：通过 parseSalary 解析为 min/max/unit
 * - skills：详情补充前的空数组（后端接受 []）
 * - keywords：从 tags 转换（如 ["5天/周", "6个月", "本科"]）
 * - seniority/difficulty：MVP 阶段不推断
 *
 * @param raw 原始 Boss 岗位数据
 * @returns 后端 JobCreateRequest
 */
export function toJobCreateRequest(raw: RawBossJob): JobCreateRequest {
  const salary = parseSalary(raw.salaryRaw)

  return {
    title: raw.title,
    company: raw.company,
    // 海投模式：jd_text 可先为空，详情补充后通过 PATCH 更新
    jd_text: raw.jdText ?? '',
    source: 'boss',
    // source_url 用 detailUrl：Boss 详情页 URL 是全局唯一的
    source_url: raw.detailUrl || raw.sourceUrl,
    location: raw.location || undefined,
    salary_min: salary.min,
    salary_max: salary.max,
    salary_unit: salary.unit,
    // skills 未补充时传空数组（后端会存为 []）
    skills: raw.skills ?? [],
    // tags 转为 keywords（实习周期、学历等非技能标签）
    keywords: raw.tags,
    // MVP 阶段不推断资历和难度，需分析 JD 后才能得到
  }
}

/**
 * 从 RawBossJob 提取需要 PATCH 补充的字段
 *
 * 当详情面板加载完成后，调用此函数生成 JobUpdateRequest
 * 仅包含详情面板补充的字段（jd_text/skills/location/address 等）
 *
 * @param raw 补充详情后的 RawBossJob
 * @returns JobUpdateRequest（仅包含需要更新的字段）
 */
export function toJobUpdateRequest(raw: RawBossJob): JobUpdateRequest {
  const update: JobUpdateRequest = {}

  // jd_text：清洗后非空才更新
  if (raw.jdText) {
    const cleaned = cleanJdText(raw.jdText)
    if (cleaned) {
      update.jd_text = cleaned
    }
  }

  // skills：详情面板的技能标签
  if (raw.skills && raw.skills.length > 0) {
    update.skills = raw.skills
  }

  // location：详情面板的详细地址优先于列表页的地点
  if (raw.address) {
    update.location = raw.address
  }

  // keywords：详情面板的标签列表（如含城市、学历等）
  if (raw.tags.length > 0) {
    update.keywords = raw.tags
  }

  return update
}
