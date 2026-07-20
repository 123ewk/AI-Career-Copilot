/**
 * 沟通模块 Store
 *
 * 职责：
 * - 管理多对话状态：对话列表、活跃对话、消息历史
 * - 管理 AI 建议回复：每对话独立的 SuggestedReply
 * - 与 Service Worker 通信：请求 AI 回复、注入文本、自动发送
 * - 持久化对话列表到 chrome.storage.local
 *
 * 设计动机：
 * - 多对话并发模型：用户在 BOSS 左侧切换对话，SidePanel 跟随切换
 * - suggestedReplies 用 Map 存储，切换对话时不会丢失之前的建议
 * - autoSend 默认 false（审核模式），SW 重启后重置为 false（安全第一）
 */

import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import {
  ChromeMessageType,
  sendMessageToBackground,
  isExtensionContextValid,
} from '../messaging/chrome_message'
import type {
  ChatMessage,
  ChatConversationItem,
  SuggestedReply,
} from '../types/communication'

/** chrome.storage.local 中用于持久化沟通状态的 key */
const STORAGE_KEY = 'communication_state'

/** 持久化状态版本号（用于未来迁移） */
const STORAGE_VERSION = 1

/** 持久化状态结构 */
interface CommunicationStorageState {
  version: number
  conversations: ChatConversationItem[]
  activeConversationId: string | null
}

export const useCommunicationStore = defineStore('communication', () => {
  // ==================== State ====================

  /** 对话列表（持久化到 storage） */
  const conversations = ref<ChatConversationItem[]>([])

  /** 当前活跃对话 ID */
  const activeConversationId = ref<string | null>(null)

  /** 每个对话独立的 AI 建议回复（不持久化，SW 重启后清空） */
  const suggestedReplies = ref<Map<string, SuggestedReply>>(new Map())

  /** 是否在 BOSS 聊天页 */
  const isOnChatPage = ref(false)

  /** 全局自动发送开关（默认 false，SW 重启后重置） */
  const autoSendEnabled = ref(false)

  /** 聊天页选择器诊断结果（用于调试选择器匹配情况） */
  const diagnostics = ref<unknown>(null)

  // ==================== Computed ====================

  /** 当前选中的对话 */
  const activeConversation = computed(() =>
    conversations.value.find((c) => c.id === activeConversationId.value) ?? null,
  )

  /** 当前对话的消息列表 */
  const activeMessages = computed(() =>
    activeConversation.value?.messages ?? [],
  )

  /** 当前对话的 AI 建议 */
  const activeSuggestedReply = computed(() =>
    suggestedReplies.value.get(activeConversationId.value ?? '') ?? null,
  )

  /** 对话列表（按最后消息时间排序，活跃对话置顶） */
  const sortedConversations = computed(() => {
    const sorted = [...conversations.value]
    // 活跃对话置顶
    if (activeConversationId.value) {
      const idx = sorted.findIndex((c) => c.id === activeConversationId.value)
      if (idx > 0) {
        const [active] = sorted.splice(idx, 1)
        sorted.unshift(active)
      }
    }
    return sorted
  })

  // ==================== Actions ====================

  /** 设置是否在聊天页 */
  function setOnChatPage(isOn: boolean) {
    isOnChatPage.value = isOn
    if (!isOn) {
      // 离开聊天页时清空活跃对话
      activeConversationId.value = null
    }
  }

  /**
   * 从 Content Script 提取的数据更新对话
   *
   * CHAT_MESSAGES_EXTRACTED 消息触发
   */
  function updateFromExtracted(data: {
    conversationId: string
    recruiterName: string
    company?: string
    jobTitle?: string
    jobSalary?: string
    messages: ChatMessage[]
    pageUrl: string
  }) {
    const existing = conversations.value.find(
      (c) => c.recruiterName === data.recruiterName,
    )

    if (existing) {
      // 更新已有对话的消息和详情
      existing.messages = data.messages
      existing.lastMessage = data.messages.length > 0
        ? data.messages[data.messages.length - 1].text
        : ''
      existing.lastMessageAt = new Date().toISOString()
      existing.messageCount = data.messages.length
      if (data.company) existing.company = data.company
      if (data.jobTitle) existing.jobTitle = data.jobTitle
      if (data.jobSalary) existing.jobSalary = data.jobSalary
    } else {
      // 创建新对话
      const newConversation: ChatConversationItem = {
        id: data.conversationId,
        recruiterName: data.recruiterName,
        company: data.company,
        jobTitle: data.jobTitle,
        jobSalary: data.jobSalary,
        lastMessage: data.messages.length > 0
          ? data.messages[data.messages.length - 1].text
          : '',
        lastMessageAt: new Date().toISOString(),
        messageCount: data.messages.length,
        messages: data.messages,
        channel: 'boss',
      }
      conversations.value.push(newConversation)
    }

    // 如果没有活跃对话，自动选中第一个
    if (!activeConversationId.value && conversations.value.length > 0) {
      activeConversationId.value = conversations.value[0].id
    }

    saveToStorage()
  }

  /**
   * 切换对话（Content Script 检测到用户在 BOSS 左侧切换）
   *
   * CHAT_CONVERSATION_SWITCHED 消息触发
   */
  function switchConversation(data: {
    conversationId: string
    recruiterName: string
    company?: string
    jobTitle?: string
    jobSalary?: string
  }) {
    // 按 recruiterName 匹配已有对话
    const existing = conversations.value.find(
      (c) => c.recruiterName === data.recruiterName,
    )
    if (existing) {
      activeConversationId.value = existing.id
      // 更新详情（如果有）
      if (data.company) existing.company = data.company
      if (data.jobTitle) existing.jobTitle = data.jobTitle
      if (data.jobSalary) existing.jobSalary = data.jobSalary
    } else {
      // 新对话（消息稍后由 CHAT_MESSAGES_EXTRACTED 填充）
      const newConversation: ChatConversationItem = {
        id: data.conversationId,
        recruiterName: data.recruiterName,
        company: data.company,
        jobTitle: data.jobTitle,
        jobSalary: data.jobSalary,
        lastMessage: '',
        lastMessageAt: null,
        messageCount: 0,
        messages: [],
        channel: 'boss',
      }
      conversations.value.push(newConversation)
      activeConversationId.value = data.conversationId
    }
  }

  /** 用户在 SidePanel 点击对话列表项 */
  function setActiveConversation(id: string | null) {
    activeConversationId.value = id
  }

  /**
   * 请求 AI 生成回复
   *
   * 发送 REQUEST_CHAT_REPLY 到 SW，SW 调用 POST /api/communication/reply（同步）
   */
  async function requestReply(conversationId: string, jobId?: string, resumeId?: string) {
    const conversation = conversations.value.find((c) => c.id === conversationId)
    if (!conversation) return

    // 设置生成中状态
    suggestedReplies.value.set(conversationId, {
      text: '',
      isGenerating: true,
      error: null,
      autoSend: false,
    })

    try {
      const response = await sendMessageToBackground(
        ChromeMessageType.REQUEST_CHAT_REPLY,
        {
          conversationId,
          jobId: jobId ?? conversation.jobId ?? undefined,
          recruiterName: conversation.recruiterName,
          messages: conversation.messages,
          resumeId,
          tone: 'natural',
        },
      )

      if (response.ok && response.data) {
        const data = response.data as { suggested_reply: string }
        suggestedReplies.value.set(conversationId, {
          text: data.suggested_reply,
          isGenerating: false,
          error: null,
          autoSend: false,
        })
      } else {
        suggestedReplies.value.set(conversationId, {
          text: '',
          isGenerating: false,
          error: response.error ?? '生成回复失败',
          autoSend: false,
        })
      }
    } catch (err) {
      suggestedReplies.value.set(conversationId, {
        text: '',
        isGenerating: false,
        error: err instanceof Error ? err.message : String(err),
        autoSend: false,
      })
    }
  }

  /** 用户编辑建议文本 */
  function updateSuggestedReply(conversationId: string, text: string) {
    const existing = suggestedReplies.value.get(conversationId)
    if (existing) {
      existing.text = text
    }
  }

  /** 切换单个对话的自动发送模式 */
  function setAutoSend(conversationId: string, enabled: boolean) {
    const existing = suggestedReplies.value.get(conversationId)
    if (existing) {
      existing.autoSend = enabled
    }
  }

  /** 设置选择器诊断结果 */
  function setDiagnostics(data: unknown) {
    diagnostics.value = data
  }

  /**
   * 审核模式：注入建议文本到聊天输入框
   *
   * 发送 INJECT_CHAT_TEXT_FROM_SIDEPANEL 到 SW，SW 转发给 Content Script
   */
  async function injectReply(conversationId: string): Promise<boolean> {
    const reply = suggestedReplies.value.get(conversationId)
    if (!reply?.text) return false

    const response = await sendMessageToBackground(
      ChromeMessageType.INJECT_CHAT_TEXT_FROM_SIDEPANEL,
      { text: reply.text },
    )
    return response.ok
  }

  /**
   * 自动模式：注入文本并自动点击发送
   *
   * 发送 AUTO_SEND_REPLY 到 SW，SW 转发 INJECT_AND_SEND_CHAT_TEXT 给 Content Script
   */
  async function autoSendReply(conversationId: string): Promise<boolean> {
    const reply = suggestedReplies.value.get(conversationId)
    if (!reply?.text) return false

    const response = await sendMessageToBackground(
      ChromeMessageType.AUTO_SEND_REPLY,
      {
        conversationId,
        text: reply.text,
      },
    )
    return response.ok
  }

  /** 清空指定对话的 AI 建议 */
  function clearSuggestedReply(conversationId: string) {
    suggestedReplies.value.delete(conversationId)
  }

  // ==================== Storage 持久化 ====================

  /** 从 chrome.storage.local 恢复状态 */
  function loadFromStorage() {
    if (!isExtensionContextValid()) return

    chrome.storage.local.get([STORAGE_KEY], (result) => {
      const stored = result[STORAGE_KEY] as CommunicationStorageState | undefined
      if (!stored || stored.version !== STORAGE_VERSION) return

      conversations.value = stored.conversations ?? []
      activeConversationId.value = stored.activeConversationId ?? null
    })
  }

  /** 将当前状态持久化到 chrome.storage.local */
  function saveToStorage() {
    if (!isExtensionContextValid()) return

    const state: CommunicationStorageState = {
      version: STORAGE_VERSION,
      conversations: conversations.value,
      activeConversationId: activeConversationId.value,
    }

    chrome.storage.local.set({ [STORAGE_KEY]: state })
  }

  /** 监听状态变化，自动持久化 */
  watch(
    [conversations, activeConversationId],
    () => saveToStorage(),
    { deep: true },
  )

  // ==================== 导出 ====================

  return {
    // State
    conversations,
    activeConversationId,
    suggestedReplies,
    isOnChatPage,
    autoSendEnabled,
    diagnostics,
    // Computed
    activeConversation,
    activeMessages,
    activeSuggestedReply,
    sortedConversations,
    // Actions
    setOnChatPage,
    updateFromExtracted,
    switchConversation,
    setActiveConversation,
    requestReply,
    updateSuggestedReply,
    setAutoSend,
    setDiagnostics,
    injectReply,
    autoSendReply,
    clearSuggestedReply,
    loadFromStorage,
    saveToStorage,
  }
})
