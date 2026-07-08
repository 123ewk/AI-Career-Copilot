/**
 * 前端岗位相关 DTO 类型
 *
 * 职责：
 * - 对齐后端 Pydantic 响应结构，为 SidePanel UI 提供类型安全
 * - 替代 store.taskResults 中的 unknown，避免 Any 泛滥
 *
 * 包含：
 * - JobAnalysisResult：JD 分析结果
 * - MatchResultResponse：简历匹配结果
 * - CommunicationScriptResponse：沟通话术结果
 */

/** 薪资区间 */
export interface SalaryRange {
  /** 最低薪资 */
  min: number
  /** 最高薪资 */
  max: number
  /** 单位（K / 元/天 / 元/时） */
  unit: string
}

/** 公司信息 */
export interface CompanyInfo {
  /** 行业 */
  industry?: string | null
  /** 规模 */
  scale?: string | null
  /** 融资阶段 */
  stage?: string | null
}

/** JD 分析结果 */
export interface JobAnalysisResult {
  /** 提取的技能列表 */
  skills: string[]
  /** 提取的关键词 */
  keywords: string[]
  /** 资历要求（intern/entry/junior/mid/senior/lead/principal） */
  seniority?: string | null
  /** 难度评级（easy/medium/hard/expert） */
  difficulty?: string | null
  /** 薪资区间 */
  salary_range?: SalaryRange | null
  /** 公司信息 */
  company_info?: CompanyInfo | null
  /** 隐藏要求（如 oncall/竞业） */
  hidden_requirements: string[]
}

/** 匹配分数明细 */
export interface MatchScoreDetail {
  /** 岗位 ID */
  job_id: string
  /** 简历 ID */
  resume_id: string
  /** BM25 关键词匹配分（0-100） */
  bm25_score: number
  /** 语义相似度分（0-100） */
  semantic_score: number
  /** 加权融合综合分（0-100） */
  combined_score: number
  /** BM25 权重 */
  weight_bm25: number
  /** 语义权重 */
  weight_semantic: number
  /** 打分时间（ISO 8601） */
  scored_at: string
}

/** 匹配计算 API 响应 */
export interface MatchResultResponse {
  /** 岗位 ID */
  job_id: string
  /** 简历 ID */
  resume_id: string
  /** 分数明细 */
  score_detail: MatchScoreDetail
  /** 命中技能 */
  matched_skills: string[]
  /** 缺失技能 */
  missing_skills: string[]
  /** 投递建议 */
  suggestions: string[]
}

/** 沟通话术内容 */
export interface CommunicationScriptResponse {
  /** 岗位 ID */
  job_id: string
  /** 简历 ID */
  resume_id?: string | null
  /** 初次打招呼话术 */
  greeting: string
  /** 跟进/回复话术 */
  follow_up: string
  /** 完整对话参考 */
  full_script: string
}
