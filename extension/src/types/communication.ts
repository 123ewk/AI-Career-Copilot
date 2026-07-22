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
  /** 对话 ID（DOM 提取为本地生成 UUID；API 合并为 `api-{friendId}`） */
  id: string
  /** 招聘方姓名 */
  recruiterName: string
  /** 公司名称 */
  company?: string
  /** 招聘方职位 */
  recruiterJobTitle?: string
  /** 关联岗位 ID（API 合并优先使用 encryptJobId） */
  jobId?: string | null
  /** 当前职位名称 */
  jobTitle?: string
  /** 薪资范围 */
  jobSalary?: string
  /** 最后一条消息预览 */
  lastMessage: string
  /** 最后消息时间（ISO 字符串） */
  lastMessageAt?: string | null
  /** 消息数量 */
  messageCount: number
  /** 未读消息数（API 合并来自 unreadMsgCount；DOM 不提供，默认 0） */
  unreadCount?: number
  /** 消息列表（完整历史，由 DOM 兜底填充） */
  messages: ChatMessage[]
  /** 渠道标识 */
  channel: string
  /**
   * 是否当前选中（来自 DOM 的 active class，API 不提供）
   *
   * 设计动机：API 数据无选中状态，由 SW 合并 DOM 的 isActive 后广播给 SidePanel
   * 默认 false，避免新到达的 API 数据立即被误标记为活跃
   */
  isActive?: boolean
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
  /**
   * 左侧对话列表发生变化（HR 列表新增/删除/顺序变化）
   *
   * 触发场景(2026-07-21 修复):
   * - Content Script 启动时 DOM 未渲染完,只拿到部分对话(或 0 个)
   * - 后续 BOSS SPA 异步渲染完成,列表项增多
   * - 用户翻页加载更多 HR
   * - 用户搜索/筛选切换了 HR 列表
   *
   * 解决的问题:
   * - 原 observe() 只监听 active class 切换(onConversationSwitched)
   * - 不监听列表项数量变化,导致 Content Script 启动时拿到的 0 个对话永远无法补发
   * - SidePanel 打开后即使 SW 缓存为空,也能通过此回调收到新数据
   *
   * @param conversations 重新提取后的完整对话列表
   */
  onConversationsListChanged?: (conversations: BossChatConversation[]) => void
}

// ==================== API 拦截数据结构（2026-07-21 新增） ====================
//
// 来源：docs/design/boss-chat-api-analysis.md
// - GET /wapi/zprelation/friend/geekFilterByLabel → ApiChatFriend（基础信息）
// - POST /wapi/zprelation/friend/getGeekFriendList.json → ApiChatFriendDetail（含最后消息）
//
// 与 BossChatConversation 的关系：
// - BossChatConversation 来自 DOM 提取，受虚拟滚动限制，但能拿到选中状态
// - ApiChatFriend / ApiChatFriendDetail 来自 API 拦截，完整且无重复
// - SW 将两者按 friendId/uid + recruiterName 合并到 ChatConversationItem

/** HR 基础信息（来自 geekFilterByLabel 的 friendList 数组项） */
export interface ApiChatFriend {
  /** 好友ID（数字，主键） */
  friendId: number
  /** 加密好友ID（用于部分 API 调用） */
  encryptFriendId: string
  /** HR 姓名 */
  name: string
  /** 公司名称 */
  brandName: string
  /** 职位名称（如 "python实习"） */
  jobName: string
  /** 职位类型描述（如 "实习"） */
  jobTypeDesc?: string
  /** 工作城市 */
  jobCity?: string
  /** Boss 头衔（如 "招聘hr"） */
  bossTitle?: string
  /** 更新时间（毫秒时间戳） */
  updateTime?: number
}

/** HR 详情（来自 getGeekFriendList.json 的 result 数组项） */
export interface ApiChatFriendDetail {
  /** 用户ID（与 friendId 对应，作为主键） */
  uid: number
  /** HR 姓名 */
  name: string
  /** 头像URL */
  avatar?: string
  /** 缩略头像URL */
  tinyUrl?: string
  /** 公司名称 */
  brandName?: string
  /** Boss 头衔（如 "招聘官"） */
  title?: string
  /** 安全ID（拉取消息历史用，本次不使用） */
  securityId?: string
  /** 加密 BossID */
  encryptBossId?: string
  /** 关联职位ID */
  jobId?: number
  /** 加密职位ID */
  encryptJobId?: string
  /** 未读消息数 */
  unreadMsgCount?: number
  /** 聊天状态 */
  chatStatus?: number
  /** 最后消息时间戳（毫秒） */
  lastTS?: number
  /** 最后消息相对时间显示（如 "昨天"） */
  lastTime?: string
  /** 最后消息详情 */
  lastMessageInfo?: {
    /** 消息ID */
    msgId?: number
    /** 加密消息ID */
    encryptMsgId?: string
    /** 显示文本 */
    showText?: string
    /** 发送者ID */
    fromId?: number
    /** 接收者ID */
    toId?: number
    /** 消息状态 */
    status?: number
    /** 消息时间戳（毫秒） */
    msgTime?: number
  }
}
