/**
 * 沟通模块 TypeScript 类型定义
 *
 * 职责：
 * - 镜像后端 Communication DTO（ChatMessage / ConversationSyncRequest 等）
 * - 定义前端 UI 状态类型（SuggestedReply 等）
 * - 与 chrome_message.ts 的 payload 类型保持一致
 */

/** 单条聊天消息（镜像后端 ChatMessage） */
export interface ChatMessage {
  role: 'user' | 'recruiter'
  text: string
  timestamp?: string
}

/** 对话摘要（列表用，镜像后端 ConversationSummary） */
export interface ConversationSummary {
  id: string
  recruiter_name: string
  job_id?: string | null
  channel: string
  last_message?: string | null
  last_message_at?: string | null
  message_count: number
}

/** 对话详情（含完整消息历史，镜像后端 ConversationDetail） */
export interface ConversationDetail {
  id: string
  user_id: string
  job_id?: string | null
  recruiter_name: string
  channel: string
  messages: ChatMessage[]
  created_at: string
  updated_at: string
}

/** AI 建议回复状态（前端 UI 状态） */
export interface SuggestedReply {
  /** AI 建议的回复文本 */
  text: string
  /** 是否正在生成中 */
  isGenerating: boolean
  /** 错误信息（null 表示无错误） */
  error: string | null
  /** 本次回复是否自动发送（默认 false，审核模式） */
  autoSend: boolean
}

/** 对话列表项（前端 UI 展示用，扩展 ConversationSummary） */
export interface ChatConversationItem {
  /** 对话 ID */
  id: string
  /** 招聘方姓名 */
  recruiterName: string
  /** 公司名称 */
  company?: string
  /** 招聘方职位 */
  recruiterJobTitle?: string
  /** 关联岗位 ID */
  jobId?: string | null
  /** 当前职位名称 */
  jobTitle?: string
  /** 薪资范围 */
  jobSalary?: string
  /** 最后一条消息预览 */
  lastMessage: string
  /** 最后消息时间 */
  lastMessageAt?: string | null
  /** 消息数量 */
  messageCount: number
  /** 消息列表（完整历史） */
  messages: ChatMessage[]
  /** 渠道标识 */
  channel: string
}

/** BOSS 聊天页对话列表项（DOM 提取） */
export interface BossChatConversation {
  /** 对话 ID（DOM 中的标识或本地生成） */
  id: string
  /** 招聘方姓名 */
  recruiterName: string
  /** 公司名称 */
  company: string
  /** 招聘方职位 */
  recruiterJobTitle: string
  /** 最后一条消息预览 */
  lastMessage: string
  /** 消息状态（如 "[送达]"、"[已读]"） */
  messageStatus: string
  /** 是否当前选中 */
  isActive: boolean
  /** 未读消息数 */
  unreadCount: number
}

/** 右侧对话详情信息（DOM 提取） */
export interface BossConversationDetail {
  /** 招聘方姓名（从标题栏提取） */
  recruiterName: string
  /** 公司名称（从标题栏提取） */
  company: string
  /** 当前职位名称（从职位信息栏提取） */
  jobTitle: string
  /** 薪资范围（从职位信息栏提取） */
  jobSalary: string
}

/** ChatAdapter 回调接口 */
export interface ChatAdapterCallbacks {
  /** 消息列表变化（新消息到达或消息更新） */
  onMessagesChanged?: (messages: ChatMessage[]) => void
  /** 用户在 BOSS 左侧切换了对话 */
  onConversationSwitched?: (newRecruiterName: string) => void
}
