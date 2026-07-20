/**
 * BOSS 直聘聊天页数据解析器
 *
 * 职责：
 * - 将 DOM 元素解析为结构化数据（BossChatConversation / ChatMessage）
 * - 与 parser.ts（列表页岗位数据解析）平行
 *
 * 设计动机：
 * - 解析逻辑与选择器解耦：parser 接收 Element，内部用 chat_selector 读取字段
 * - 返回类型与 communication.ts 中的类型定义对齐
 */

import type {
  BossChatConversation,
  BossConversationDetail,
  ChatMessage,
} from '../../types/communication'
import {
  CHAT_SELECTORS,
  queryChatAll,
  queryChatText,
  queryChatElement,
  extractCompanyFromTitleBox,
  extractPositionInfo,
} from './chat_selector'

/**
 * 从对话列表容器解析所有对话项
 *
 * @param container 对话列表容器元素（可选，默认从 document 查找）
 * @returns BossChatConversation 数组
 */
export function parseConversations(
  container?: Element | null,
): BossChatConversation[] {
  const root = container ?? document.querySelector(CHAT_SELECTORS.conversationList.container)
  if (!root) return []

  const items = queryChatAll(root, CHAT_SELECTORS.conversationList.item)
  const conversations: BossChatConversation[] = []

  items.forEach((item, index) => {
    const recruiterName = queryChatText(item, CHAT_SELECTORS.conversationList.recruiterName)
    if (!recruiterName) return

    const lastMessage = queryChatText(item, CHAT_SELECTORS.conversationList.lastMessage)
    const isActive = item.matches(CHAT_SELECTORS.conversationList.activeClass.replace('.', ''))

    // 提取公司名称（从 .title-box 内 i.vline 后的文本节点）
    const titleBox = queryChatElement(item, CHAT_SELECTORS.conversationList.companyName)
    const companyAndJob = titleBox ? extractCompanyFromTitleBox(titleBox) : ''

    // 提取消息状态（如 "[送达]"、"[已读]"）
    const messageStatus = queryChatText(item, CHAT_SELECTORS.conversationList.messageStatus)

    conversations.push({
      // 用 index 作为 DOM 标识，切换时通过 recruiterName 匹配后端 conversation
      id: `dom-${index}-${recruiterName}`,
      recruiterName,
      company: companyAndJob,
      recruiterJobTitle: '',
      lastMessage,
      messageStatus,
      isActive,
      unreadCount: 0,
    })
  })

  return conversations
}

/**
 * 解析右侧对话详情信息（选中对话后显示）
 *
 * 包含：标题栏（姓名+公司）、职位信息栏（职位名+薪资）
 *
 * @returns BossConversationDetail，无活跃对话时返回 null
 */
export function parseConversationDetail(): BossConversationDetail | null {
  const topBar = document.querySelector(CHAT_SELECTORS.conversationDetail.topBar)
  if (!topBar) return null

  // 从标题栏提取姓名和公司
  const nameText = topBar.querySelector('.name-text')?.textContent?.trim() ?? ''
  // 公司名在标题栏的第二个子元素或通过特定 class
  const companyText = topBar.querySelectorAll('span, div')
  let company = ''
  for (const el of companyText) {
    const t = el.textContent?.trim() ?? ''
    // 跳过姓名和更多按钮
    if (t && t !== nameText && !t.includes('更多') && t.length < 30) {
      company = t
      break
    }
  }

  // 从职位信息栏提取职位和薪资
  const positionEl = document.querySelector(CHAT_SELECTORS.conversationDetail.positionInfo)
  const { jobTitle, jobSalary } = positionEl
    ? extractPositionInfo(positionEl)
    : { jobTitle: '', jobSalary: '' }

  return {
    recruiterName: nameText,
    company,
    jobTitle,
    jobSalary,
  }
}

/**
 * 从消息历史容器解析所有消息
 *
 * @param container 消息列表容器元素（可选，默认从 document 查找）
 * @returns ChatMessage 数组
 */
export function parseMessages(
  container?: Element | null,
): ChatMessage[] {
  const root = container ?? document.querySelector(CHAT_SELECTORS.messageHistory.container)
  if (!root) return []

  const items = queryChatAll(root, CHAT_SELECTORS.messageHistory.messageItem)
  const messages: ChatMessage[] = []

  items.forEach((item) => {
    const text = queryChatText(item, CHAT_SELECTORS.messageHistory.messageText)
    if (!text) return

    // 判断消息方向：通过 class 区分用户/招聘方
    const isUser = item.matches(
      CHAT_SELECTORS.messageHistory.sentByUser.replace('.', ''),
    )
    const role: 'user' | 'recruiter' = isUser ? 'user' : 'recruiter'

    const timestamp = queryChatText(item, CHAT_SELECTORS.messageHistory.messageTime)

    messages.push({
      role,
      text,
      timestamp: timestamp || undefined,
    })
  })

  return messages
}

/**
 * 解析当前活跃对话的完整信息
 *
 * @returns 包含对话信息、详情和消息列表的对象，无活跃对话时返回 null
 */
export function parseCurrentConversation(): {
  conversation: BossChatConversation
  detail: BossConversationDetail | null
  messages: ChatMessage[]
} | null {
  const conversations = parseConversations()
  const active = conversations.find((c) => c.isActive)
  if (!active) return null

  const detail = parseConversationDetail()
  const messages = parseMessages()

  // 用右侧详情补充对话信息（更准确的姓名和公司）
  if (detail) {
    if (detail.recruiterName) active.recruiterName = detail.recruiterName
    if (detail.company) active.company = detail.company
    if (detail.jobTitle) active.recruiterJobTitle = detail.jobTitle
  }

  return { conversation: active, detail, messages }
}

/**
 * 获取当前活跃对话的招聘方姓名
 *
 * @returns 招聘方姓名，无活跃对话时返回空字符串
 */
export function getActiveRecruiterName(): string {
  // 使用更具体的选择器避免匹配到筛选标签的 .selected
  const activeItem = document.querySelector(
    `${CHAT_SELECTORS.conversationList.item}${CHAT_SELECTORS.conversationList.activeClass}`,
  )
  if (!activeItem) return ''
  return queryChatText(activeItem, CHAT_SELECTORS.conversationList.recruiterName)
}
