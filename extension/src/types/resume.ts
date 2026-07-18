/**
 * 简历域 TypeScript 类型定义
 *
 * 镜像后端 Pydantic DTO（backend/app/domain/resume/models.py）
 * 用于 SidePanel ↔ Service Worker 消息传递和 UI 渲染
 */

/** 教育经历 */
export interface EducationItem {
  school: string
  degree?: string | null
  major?: string | null
  start_date?: string | null
  end_date?: string | null
  description?: string | null
}

/** 工作经历 */
export interface ExperienceItem {
  company: string
  position: string
  start_date?: string | null
  end_date?: string | null
  description?: string | null
}

/** 项目经历 */
export interface ProjectItem {
  name: string
  role?: string | null
  start_date?: string | null
  end_date?: string | null
  description?: string | null
  tech_stack: string[]
}

/** 结构化简历数据 */
export interface ResumeStructuredData {
  education: EducationItem[]
  experience: ExperienceItem[]
  projects: ProjectItem[]
}

/** 简历摘要（列表视图，不含 raw_text 和 structured_data） */
export interface ResumeSummary {
  id: string
  skills: string[]
  experience_years: number | null
  is_active: boolean
  created_at: string
}

/** 简历完整详情 */
export interface ResumeResponse extends ResumeSummary {
  user_id: string
  raw_text: string
  structured_data: ResumeStructuredData
}

/** 上传响应 */
export interface ResumeUploadResponse {
  resume: ResumeResponse
  parse_status: 'PARSED' | 'PARSING' | 'FAILED'
  message: string | null
}

/** 列表响应（分页） */
export interface ResumeListResponse {
  items: ResumeSummary[]
  total: number
  limit: number
  offset: number
}
