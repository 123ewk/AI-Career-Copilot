/**
 * Boss 直聘聊天 API 响应解析器
 *
 * 职责：
 * - 将主世界拦截器捕获的 Boss 聊天 API JSON 转换为 ApiChatFriend / ApiChatFriendDetail
 * - 提供"基础列表 + 详情"合并为 ChatConversationItem[] 的工具函数
 *
 * 数据来源（2026-07-21 逆向确认，详见 docs/design/boss-chat-api-analysis.md）：
 * - GET /wapi/zprelation/friend/geekFilterByLabel → zpData.friendList（基础信息）
 * - POST /wapi/zprelation/friend/getGeekFriendList.json → zpData.result（详情+最后消息）
 *
 * 设计动机：
 * - 与 api_parser.ts（职位列表）平行，专门处理聊天 API
 * - 解析逻辑与拦截器/Content Script 解耦：parser 只接收 data，返回结构化数据
 * - 合并函数：基础列表提供 friendId/name/brandName/jobName，
 *   详情提供 lastMessageInfo/unreadMsgCount/securityId，按 friendId===uid 合并
 */

import type {
  ApiChatFriend,
  ApiChatFriendDetail,
  ChatConversationItem,
} from '../../types/communication'

/** 拦截器捕获到的 API 响应包装（与 api_parser.ts 的 CapturedApiPayload 对齐） */
export interface CapturedChatApiPayload {
  url: string
  method: string
  status: number
  data: unknown
  headers: Record<string, string> | string
}

/** geekFilterByLabel 响应顶层结构 */
interface ChatListApiResponse {
  code?: number
  message?: string
  zpData?: {
    foldText?: string
    friendList?: unknown[]
    filterEncryptIdList?: unknown[]
    filterBossIdList?: unknown[]
  }
}

/** getGeekFriendList.json 响应顶层结构 */
interface ChatDetailApiResponse {
  code?: number
  message?: string
  zpData?: {
    result?: unknown[]
  }
}

/**
 * 类型守卫：是否为 geekFilterByLabel 响应
 *
 * 关键字段：zpData.friendList 必须是数组
 */
function isChatListResponse(value: unknown): value is ChatListApiResponse {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  if (v.code !== 0) return false
  const zpData = v.zpData
  if (typeof zpData !== 'object' || zpData === null) return false
  return Array.isArray((zpData as Record<string, unknown>).friendList)
}

/**
 * 类型守卫：是否为 getGeekFriendList.json 响应
 *
 * 关键字段：zpData.result 必须是数组
 */
function isChatDetailResponse(value: unknown): value is ChatDetailApiResponse {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  if (v.code !== 0) return false
  const zpData = v.zpData
  if (typeof zpData !== 'object' || zpData === null) return false
  return Array.isArray((zpData as Record<string, unknown>).result)
}

/**
 * 安全读取数字字段
 *
 * BOSS API 中 ID 字段有时返回 number，有时返回字符串（如 encryptId），
 * 数字字段用此函数统一转换并校验
 */
function toNumber(value: unknown): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && /^\d+$/.test(value)) {
    const n = Number(value)
    if (Number.isFinite(n)) return n
  }
  return undefined
}

/**
 * 安全读取字符串字段
 */
function toStr(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return ''
}

/**
 * 将 friendList 项转换为 ApiChatFriend
 *
 * 跳过缺少 friendId 或 name 的项（无法构成有效对话）
 */
function convertFriendItem(raw: unknown): ApiChatFriend | null {
  if (typeof raw !== 'object' || raw === null) return null
  const item = raw as Record<string, unknown>

  const friendId = toNumber(item.friendId)
  const name = toStr(item.name).trim()
  if (friendId === undefined || !name) return null

  return {
    friendId,
    encryptFriendId: toStr(item.encryptFriendId),
    name,
    brandName: toStr(item.brandName),
    jobName: toStr(item.jobName),
    jobTypeDesc: typeof item.jobTypeDesc === 'string' ? item.jobTypeDesc : undefined,
    jobCity: typeof item.jobCity === 'string' ? item.jobCity : undefined,
    bossTitle: typeof item.bossTitle === 'string' ? item.bossTitle : undefined,
    updateTime: toNumber(item.updateTime),
  }
}

/**
 * 将 result 项转换为 ApiChatFriendDetail
 *
 * 跳过缺少 uid 或 name 的项
 */
function convertDetailItem(raw: unknown): ApiChatFriendDetail | null {
  if (typeof raw !== 'object' || raw === null) return null
  const item = raw as Record<string, unknown>

  const uid = toNumber(item.uid)
  const name = toStr(item.name).trim()
  if (uid === undefined || !name) return null

  // 解析 lastMessageInfo 子对象
  let lastMessageInfo: ApiChatFriendDetail['lastMessageInfo']
  if (typeof item.lastMessageInfo === 'object' && item.lastMessageInfo !== null) {
    const msg = item.lastMessageInfo as Record<string, unknown>
    lastMessageInfo = {
      msgId: toNumber(msg.msgId),
      encryptMsgId: typeof msg.encryptMsgId === 'string' ? msg.encryptMsgId : undefined,
      showText: typeof msg.showText === 'string' ? msg.showText : undefined,
      fromId: toNumber(msg.fromId),
      toId: toNumber(msg.toId),
      status: toNumber(msg.status),
      msgTime: toNumber(msg.msgTime),
    }
  }

  return {
    uid,
    name,
    avatar: typeof item.avatar === 'string' ? item.avatar : undefined,
    tinyUrl: typeof item.tinyUrl === 'string' ? item.tinyUrl : undefined,
    brandName: typeof item.brandName === 'string' ? item.brandName : undefined,
    title: typeof item.title === 'string' ? item.title : undefined,
    securityId: typeof item.securityId === 'string' ? item.securityId : undefined,
    encryptBossId: typeof item.encryptBossId === 'string' ? item.encryptBossId : undefined,
    jobId: toNumber(item.jobId),
    encryptJobId: typeof item.encryptJobId === 'string' ? item.encryptJobId : undefined,
    unreadMsgCount: toNumber(item.unreadMsgCount),
    chatStatus: toNumber(item.chatStatus),
    lastTS: toNumber(item.lastTS),
    lastTime: typeof item.lastTime === 'string' ? item.lastTime : undefined,
    lastMessageInfo,
  }
}

/**
 * 解析 geekFilterByLabel 响应
 *
 * @param payload 拦截器捕获的原始 payload
 * @returns ApiChatFriend 数组（无效项已过滤）
 */
export function parseChatListResponse(
  payload: CapturedChatApiPayload,
): ApiChatFriend[] {
  if (!isChatListResponse(payload.data)) {
    console.warn('[ChatApiParser] Response is not a valid chat list response')
    return []
  }

  const friendList = payload.data.zpData!.friendList!
  const friends: ApiChatFriend[] = []
  for (const raw of friendList) {
    const f = convertFriendItem(raw)
    if (f) friends.push(f)
  }
  return friends
}

/**
 * 解析 getGeekFriendList.json 响应
 *
 * @param payload 拦截器捕获的原始 payload
 * @returns ApiChatFriendDetail 数组（无效项已过滤）
 */
export function parseChatDetailResponse(
  payload: CapturedChatApiPayload,
): ApiChatFriendDetail[] {
  if (!isChatDetailResponse(payload.data)) {
    console.warn('[ChatApiParser] Response is not a valid chat detail response')
    return []
  }

  const result = payload.data.zpData!.result!
  const details: ApiChatFriendDetail[] = []
  for (const raw of result) {
    const d = convertDetailItem(raw)
    if (d) details.push(d)
  }
  return details
}

/**
 * 将毫秒时间戳格式化为 ISO 字符串
 *
 * 用于填充 ChatConversationItem.lastMessageAt
 * 无效时间戳返回 undefined
 */
function formatTimestamp(ms: number | undefined): string | null {
  if (ms === undefined || !Number.isFinite(ms) || ms <= 0) return null
  try {
    return new Date(ms).toISOString()
  } catch {
    return null
  }
}

/**
 * 合并基础列表与详情为 ChatConversationItem[]
 *
 * 合并规则：
 * - 以 friends（基础列表）为主，遍历每项
 * - 在 details 中按 uid === friendId 查找对应详情
 * - 详情缺失时，仅用基础信息构造 ChatConversationItem（lastMessage/unreadCount 为空）
 * - 详情存在时，补全 lastMessage / lastMessageAt / unreadCount / jobId
 *
 * 设计动机：
 * - friends 来自列表 API，提供完整 HR 列表（不受虚拟滚动限制）
 * - details 来自详情 API，提供最后消息和未读数
 * - 两者通过 friendId/uid 关联，合并后即得到完整的对话列表
 * - isActive 默认 false，由 SW 后续合并 DOM 的 active 状态（API 无选中状态）
 *
 * @param friends 基础列表（geekFilterByLabel 解析结果）
 * @param details 详情列表（getGeekFriendList.json 解析结果）
 * @returns ChatConversationItem 数组，可直接用于 SidePanel 渲染
 */
export function mergeFriendsAndDetails(
  friends: ApiChatFriend[],
  details: ApiChatFriendDetail[],
): ChatConversationItem[] {
  // 以 uid 为键建立详情索引，O(1) 查找
  const detailMap = new Map<number, ApiChatFriendDetail>()
  for (const d of details) {
    detailMap.set(d.uid, d)
  }

  const items: ChatConversationItem[] = []
  for (const f of friends) {
    const d = detailMap.get(f.friendId)

    // lastMessage 优先取详情的 showText，无详情时为空
    const lastMessage = d?.lastMessageInfo?.showText ?? ''
    const lastMessageAt = formatTimestamp(d?.lastTS ?? d?.lastMessageInfo?.msgTime)
    const unreadCount = d?.unreadMsgCount ?? 0
    // jobId 用加密ID（与列表页 source_url 体系一致），无则用数字ID
    const jobId = d?.encryptJobId ?? (d?.jobId !== undefined ? String(d.jobId) : null)

    items.push({
      // 用 friendId 作为稳定 ID（比 recruiterName 更可靠，避免重名冲突）
      id: `api-${f.friendId}`,
      recruiterName: f.name,
      company: f.brandName || d?.brandName,
      recruiterJobTitle: f.bossTitle || d?.title,
      jobTitle: f.jobName,
      jobId,
      lastMessage,
      lastMessageAt,
      // unreadMsgCount 来自详情 API，无详情时默认 0
      unreadCount: unreadCount,
      messageCount: 0, // API 不返回消息总数，由 DOM 提取的消息列表填充
      messages: [], // 消息历史由 DOM 兜底填充
      channel: 'boss',
      // API 不返回选中状态，默认 false
      // 后续由 SW 合并 DOM 的 isActive 状态后广播给 SidePanel
      isActive: false,
      // activeRecruiterName 由 DOM 提供，用于在 API 数据上保留选中状态
      // 重名风险：BOSS 中姓名可能重复，但聊天场景重名概率低，可接受
      // 通过 recruiterName 匹配是因为 DOM 无法拿到 friendId
    })
  }

  return items
}

/**
 * 判断 URL 是否为 geekFilterByLabel
 */
export function isChatListUrl(url: string): boolean {
  return url.includes('/wapi/zprelation/friend/geekFilterByLabel')
}

/**
 * 判断 URL 是否为 getGeekFriendList.json
 */
export function isChatDetailUrl(url: string): boolean {
  return url.includes('/wapi/zprelation/friend/getGeekFriendList.json')
}
