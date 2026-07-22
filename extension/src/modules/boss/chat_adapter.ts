/**
 * BOSS 直聘聊天页适配器（DOM 提取、注入与监听）
 *
 * 职责：
 * - detect()：识别当前页面是否为聊天页
 * - extractConversations()：提取左侧对话列表
 * - extractMessages()：提取右侧消息历史
 * - injectText()：将文本填入聊天输入框
 * - clickSend()：点击发送按钮
 * - observe()：监听消息变化和对话切换
 * - disconnect()：清理 observer
 *
 * 设计动机：
 * - 与 BossAdapter（列表页/详情页）平行，专门处理聊天页
 * - 单例模式：一个页面只创建一个 ChatAdapter 实例
 * - MutationObserver 监听两类变化：新消息到达、用户切换对话
 *
 * 运行环境：
 * - Content Script（可访问 document/window）
 * - 消息发送由 content.ts 调用 adapter.observe 回调后处理
 */

import type {
  ChatAdapterCallbacks,
  ChatMessage,
  BossChatConversation,
  BossConversationDetail,
} from '../../types/communication'
import {
  CHAT_SELECTORS,
  queryChatElement,
} from './chat_selector'
import {
  parseConversations,
  parseMessages,
  parseConversationDetail,
  getActiveRecruiterName,
} from './chat_parser'

/** MutationObserver 防抖延迟（ms） */
const DEBOUNCE_MS = 500

/**
 * 聊天页适配器（单例）
 *
 * 使用方式：
 *   import { chatAdapter } from './chat_adapter'
 *   if (chatAdapter.detect() === 'chat') {
 *     const messages = chatAdapter.extractMessages()
 *     chatAdapter.observe({
 *       onMessagesChanged: (msgs) => { ... },
 *       onConversationSwitched: (name) => { ... },
 *     })
 *   }
 */
export class ChatAdapter {
  /** 消息列表 MutationObserver */
  private messageObserver: MutationObserver | null = null
  /** 对话列表 MutationObserver（检测用户切换对话 + 列表项变化） */
  private conversationObserver: MutationObserver | null = null
  /** 消息变化防抖定时器 */
  private messageDebounceTimer: ReturnType<typeof setTimeout> | null = null
  /** 对话切换防抖定时器 */
  private conversationDebounceTimer: ReturnType<typeof setTimeout> | null = null
  /** 上次检测到的活跃对话招聘方姓名（用于检测切换） */
  private lastActiveRecruiter: string = ''

  /**
   * 上次检测到的对话列表签名(用于检测列表项变化)
   *
   * 设计动机:
   * - 单纯靠 MutationObserver 触发会高频回调(任何子节点变化都触发)
   * - 用签名(id 列表 join)对比,只在列表项数量/顺序变化时才回调
   * - 避免 active class 切换时也误触发 onConversationsListChanged
   */
  private lastConversationListSignature: string = ''

  /**
   * 检测当前页面是否为聊天页
   *
   * @returns 'chat' 或 'unknown'
   */
  detect(): 'chat' | 'unknown' {
    const url = location.href
    if (url.includes('zhipin.com/web/geek/chat')) return 'chat'
    return 'unknown'
  }

  /**
   * 提取左侧对话列表
   *
   * @returns BossChatConversation 数组
   */
  extractConversations() {
    return parseConversations()
  }

  /**
   * 提取当前对话的消息列表
   *
   * @returns ChatMessage 数组
   */
  extractMessages(): ChatMessage[] {
    return parseMessages()
  }

  /**
   * 获取当前活跃对话的招聘方姓名
   */
  getActiveConversationName(): string {
    return getActiveRecruiterName()
  }

  /**
   * 获取右侧对话详情（标题栏+职位信息）
   *
   * @returns BossConversationDetail，无活跃对话时返回 null
   */
  getConversationDetail(): BossConversationDetail | null {
    return parseConversationDetail()
  }

  /**
   * 将文本填入聊天输入框（不点击发送）
   *
   * BOSS 直聘输入框是 contenteditable div（id="chat-input"），非 textarea。
   * 2026-07-19 live page 已验证。
   *
   * @param text 要注入的文本
   * @returns 是否成功注入
   */
  injectText(text: string): boolean {
    // 优先使用已验证的 #chat-input 选择器
    const inputEl =
      queryChatElement(document, CHAT_SELECTORS.chatInput.inputBox) ??
      document.querySelector('[contenteditable="true"]') ??
      document.querySelector('[contenteditable]')

    if (!inputEl) return false

    // contenteditable div（BOSS 的 #chat-input 就是这种）
    if (
      inputEl.getAttribute('contenteditable') !== null ||
      (inputEl as HTMLElement).isContentEditable
    ) {
      ;(inputEl as HTMLElement).focus()
      ;(inputEl as HTMLElement).innerText = text
      inputEl.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    }

    return false
  }

  /**
   * 点击发送按钮
   *
   * BOSS 直聘发送按钮：<button type="send">发送</button>。
   * 2026-07-19 live page 已验证。注意：type="send"，非 type="submit"。
   *
   * @returns 是否成功点击
   */
  clickSend(): boolean {
    const sendBtn =
      queryChatElement(document, CHAT_SELECTORS.chatInput.sendButton) ??
      Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent?.trim() === '发送',
      )

    if (!sendBtn) return false
    ;(sendBtn as HTMLElement).click()
    return true
  }

  /**
   * 启动页面监听
   *
   * 监听两类事件：
   * 1. 消息变化：MutationObserver 监听消息容器的子节点变化
   * 2. 对话切换：MutationObserver 监听对话列表的 active 状态变化
   *
   * @param callbacks 回调集合
   * @returns 注销函数
   */
  observe(callbacks: ChatAdapterCallbacks): () => void {
    this.disconnect()

    // 记录当前活跃对话
    this.lastActiveRecruiter = this.getActiveConversationName()

    // 提取初始对话列表,用于签名对比 + 立即回调
    //
    // 关键修复(2026-07-22):
    // - 原实现只把 initialConversations 用来记录签名,从未回调给上层
    // - 导致 SidePanel 重开时 Content Script 走 resetExtractionState → initChatPage → doInitChatPage
    //   → chatAdapter.observe() 时,即使列表已存在 5 个 HR,也不会触发 onConversationsListChanged
    // - 因为 MutationObserver 只监听"未来"变化,签名不变就永远不回调
    // - 现在改为:initialConversations 非空时立即回调一次,让上层(Content Script)能拿到"已存在"的数据
    const initialConversations = this.extractConversations()
    this.lastConversationListSignature = initialConversations
      .map((c) => c.id)
      .join('|')

    // 立即回调初始数据(如果有):
    // - 列表已渲染完成时,observe() 时就能拿到 5 个 HR,立即回调补发
    // - 列表未渲染完时,initialConversations 为空,跳过,依赖后续 MutationObserver 触发
    if (initialConversations.length > 0 && callbacks.onConversationsListChanged) {
      console.log(
        `[ChatAdapter] observe() 检测到初始对话列表 | count=${initialConversations.length} | 立即回调`,
      )
      callbacks.onConversationsListChanged(initialConversations)
    }

    // 1. 消息变化监听
    if (callbacks.onMessagesChanged) {
      this.setupMessageObserver(callbacks.onMessagesChanged)
    }

    // 2. 对话切换 + 列表变化监听(合并到同一个 observer,避免重复监听同一容器)
    if (callbacks.onConversationSwitched || callbacks.onConversationsListChanged) {
      this.setupConversationObserver(
        callbacks.onConversationSwitched ?? (() => {}),
        callbacks.onConversationsListChanged ?? null,
      )
    }

    return () => this.disconnect()
  }

  /**
   * 清理所有 observer 和定时器
   */
  disconnect(): void {
    this.messageObserver?.disconnect()
    this.messageObserver = null

    this.conversationObserver?.disconnect()
    this.conversationObserver = null

    if (this.messageDebounceTimer) {
      clearTimeout(this.messageDebounceTimer)
      this.messageDebounceTimer = null
    }

    if (this.conversationDebounceTimer) {
      clearTimeout(this.conversationDebounceTimer)
      this.conversationDebounceTimer = null
    }
  }

  // ==================== 私有方法 ====================

  /**
   * 设置消息列表 MutationObserver
   *
   * 监听消息容器的子节点变化（新消息到达、消息加载）
   */
  private setupMessageObserver(
    onMessagesChanged: (messages: ChatMessage[]) => void,
  ): void {
    const container = document.querySelector(CHAT_SELECTORS.messageHistory.container)
    if (!container) {
      // 消息容器未找到，1 秒后重试
      setTimeout(() => {
        const retryContainer = document.querySelector(CHAT_SELECTORS.messageHistory.container)
        if (retryContainer) {
          this.setupMessageObserver(onMessagesChanged)
        }
      }, 1000)
      return
    }

    this.messageObserver = new MutationObserver(() => {
      if (this.messageDebounceTimer) {
        clearTimeout(this.messageDebounceTimer)
      }
      this.messageDebounceTimer = setTimeout(() => {
        const messages = this.extractMessages()
        if (messages.length > 0) {
          onMessagesChanged(messages)
        }
        this.messageDebounceTimer = null
      }, DEBOUNCE_MS)
    })

    this.messageObserver.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    })
  }

  /**
   * 设置对话列表 MutationObserver
   *
   * 监听对话列表的 DOM 变化,合并两种检测:
   * 1. active class 切换 → onConversationSwitched
   *    用户在 BOSS 左侧点击不同对话时,active class 会从一个 item 移到另一个
   * 2. 列表项数量/顺序变化 → onConversationsListChanged
   *    Content Script 启动时 DOM 未渲染完,后续异步渲染完成时补发对话列表
   *
   * 设计动机(2026-07-21 修复):
   * - 原 observer 只检测 active 切换,无法感知列表项增多
   * - 现增加签名(id 列表 join)对比,只在数量/顺序变化时才回调
   * - 合并到同一个 observer,避免重复监听同一容器造成性能浪费
   *
   * @param onConversationSwitched active 切换回调
   * @param onConversationsListChanged 列表项变化回调(可选)
   */
  private setupConversationObserver(
    onConversationSwitched: (newRecruiterName: string) => void,
    onConversationsListChanged: ((conversations: BossChatConversation[]) => void) | null,
  ): void {
    const container = document.querySelector(CHAT_SELECTORS.conversationList.container)
    if (!container) {
      setTimeout(() => {
        const retryContainer = document.querySelector(CHAT_SELECTORS.conversationList.container)
        if (retryContainer) {
          this.setupConversationObserver(onConversationSwitched, onConversationsListChanged)
        }
      }, 1000)
      return
    }

    this.conversationObserver = new MutationObserver(() => {
      if (this.conversationDebounceTimer) {
        clearTimeout(this.conversationDebounceTimer)
      }
      this.conversationDebounceTimer = setTimeout(() => {
        // 1. 检测 active class 切换
        const currentActive = this.getActiveConversationName()
        if (currentActive && currentActive !== this.lastActiveRecruiter) {
          this.lastActiveRecruiter = currentActive
          onConversationSwitched(currentActive)
        }

        // 2. 检测列表项数量/顺序变化(签名对比)
        if (onConversationsListChanged) {
          const conversations = this.extractConversations()
          const currentSignature = conversations.map((c) => c.id).join('|')

          if (currentSignature !== this.lastConversationListSignature) {
            this.lastConversationListSignature = currentSignature
            // 仅在有对话时回调(避免空列表误触发)
            if (conversations.length > 0) {
              console.log(
                `[ChatAdapter] 对话列表变化 | count=${conversations.length} | signature=${currentSignature.slice(0, 80)}`,
              )
              onConversationsListChanged(conversations)
            }
          }
        }

        this.conversationDebounceTimer = null
      }, DEBOUNCE_MS)
    })

    // 监听属性变化（active class 切换是 class 属性变化）+ 子节点变化(列表项增减)
    this.conversationObserver.observe(container, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    })
  }
}

/**
 * ChatAdapter 单例
 */
export const chatAdapter = new ChatAdapter()
