/**
 * BOSS 直聘聊天页 DOM 选择器配置
 *
 * 职责：
 * - 集中管理聊天页的所有 CSS 选择器（对话列表、消息历史、输入框）
 * - 与 selector.ts（列表页/详情页）平行，独立维护聊天页选择器
 *
 * 设计动机：
 * - 聊天页 DOM 结构与列表页完全不同，需要独立选择器配置
 * - 选择器基于 2026-07-19 Chrome MCP Server accessibility tree + 下载 HTML/CSS 验证
 *
 * DOM 结构（2026-07-19 已验证）：
 * - 页面容器：.chat-container > .chat-wrap
 * - 左侧列表：.list-warp.v2 > .chat-user.v2 > .chat-content > .user-list > .user-list-content
 * - 对话项：li[role="listitem"] > .friend-content（含 .selected 表示选中）
 * - 对话项内部：.figure(头像) + .text(时间/姓名/职位/状态/预览)
 * - 消息区域：.conversation-message（消息滚动容器）
 * - 输入区域：#chat-input（contenteditable div，非 textarea）
 * - 发送按钮：button[type="send"]（非 type="submit"）
 *
 * 注意：BOSS 使用 Vue.js，所有元素带 data-v-* 属性（如 data-v-3d67f5a8），
 * 选择器应避免依赖这些版本化的属性。
 */

/**
 * 聊天页选择器配置接口
 *
 * 五大区域：
 * - conversationList：左侧对话列表（HR 列表 + 最后消息预览）
 * - conversationDetail：右侧对话详情（标题栏 + 职位信息）
 * - messageHistory：右侧消息历史（气泡布局）
 * - chatInput：底部输入框 + 发送按钮
 * - chatPage：页面级容器（用于 detect）
 */
export interface ChatSelectorConfig {
  /** 页面级容器（用于检测是否在聊天页） */
  chatPage: {
    /** 聊天页主容器 */
    container: string
  }
  /** 左侧对话列表 */
  conversationList: {
    /** 对话列表容器 */
    container: string
    /** 单个对话项 */
    item: string
    /** 招聘方姓名 */
    recruiterName: string
    /** 公司名称（在 .title-box 内，i.vline 之后的文本节点） */
    companyName: string
    /** 招聘方职位（在 .title-box 内） */
    recruiterJobTitle: string
    /** 最后一条消息预览 */
    lastMessage: string
    /** 当前选中项的 class（用于检测活跃对话） */
    activeClass: string
    /** 消息状态标记（如 [送达]、[已读]） */
    messageStatus: string
  }
  /** 右侧对话详情（选中对话后显示） */
  conversationDetail: {
    /** 对话标题栏容器 */
    topBar: string
    /** 职位信息栏容器 */
    positionInfo: string
    /** 职位名称 */
    jobTitle: string
    /** 薪资范围 */
    jobSalary: string
  }
  /** 右侧消息历史 */
  messageHistory: {
    /** 消息列表容器 */
    container: string
    /** 单条消息 */
    messageItem: string
    /** 消息文本内容 */
    messageText: string
    /** 消息时间戳 */
    messageTime: string
    /** 用户发送的消息标记（class 或 data 属性） */
    sentByUser: string
    /** 招聘方发送的消息标记 */
    receivedByRecruiter: string
  }
  /** 聊天输入区域 */
  chatInput: {
    /** 输入框（contenteditable div，id="chat-input"） */
    inputBox: string
    /** 发送按钮（button[type="send"]） */
    sendButton: string
  }
}

/**
 * 聊天页选择器常量
 *
 * 基于 2026-07-19 Chrome MCP Server accessibility tree + 下载 HTML/CSS 验证。
 * DOM 结构：左栏 .list-warp.v2 > .user-list-content，右栏 .conversation-message。
 * BOSS 使用 Vue.js，所有元素带 data-v-* 属性，选择器避免依赖这些版本化属性。
 */
export const CHAT_SELECTORS: ChatSelectorConfig = {
  chatPage: {
    // 聊天页主容器（.chat-container > .chat-wrap）
    container: '.chat-container .chat-wrap',
  },
  conversationList: {
    // 左侧对话列表滚动容器（.list-warp.v2 > .chat-user.v2 > .chat-content > .user-list > .user-list-content）
    container: '.user-list-content',
    // 单个对话项（li[role="listitem"] 内的 .friend-content）
    item: '.friend-content',
    // HR 名称文本（.name-box > span.name-text）— 2026-07-19 live page 已验证
    recruiterName: '.name-text',
    // 公司名称：在 .text > .title-box 内，通过 i.vline 分隔符后的文本节点提取
    // DOM: .title-box > .name-box(姓名) + i.vline + (公司/职位文本)
    companyName: '.title-box',
    // 招聘方职位：从 .title-box 内提取（i.vline 之后的文本）
    recruiterJobTitle: '.title-box',
    // 最后消息预览文本（.last-msg > span.last-msg-text）— 2026-07-19 live page 已验证
    lastMessage: '.last-msg-text',
    // 当前选中对话的 active class（.friend-content.selected）— 2026-07-19 live page 已验证
    activeClass: '.selected',
    // 消息状态标记（i.message-status，如 "[送达]"、"[已读]"）— 2026-07-19 live page 已验证
    messageStatus: 'i.message-status',
  },
  conversationDetail: {
    // 对话标题栏（.conversation-top，包含姓名+公司+更多按钮）
    topBar: '.conversation-top',
    // 职位信息栏（.position-info，包含职位名+薪资+查看职位）
    positionInfo: '.position-info',
    // 职位名称（.position-info 内第一个 generic/text 元素）
    jobTitle: '.position-info',
    // 薪资范围（.position-info 内第二个 generic/text 元素）
    jobSalary: '.position-info',
  },
  messageHistory: {
    // 右侧消息列表滚动容器（.conversation-message）— CSS 推断，需进一步验证
    container: '.conversation-message',
    // 单条消息气泡（.message-item）— CSS 推断
    messageItem: '.message-item',
    // 消息文本内容（.message-item .text）— CSS 推断
    messageText: '.text',
    // 消息时间（.message-item 内的 .time 元素）— CSS 推断
    messageTime: '.time',
    // 用户发送的消息（.message-item.item-myself）— CSS 推断
    sentByUser: '.item-myself',
    // 招聘方发送的消息（.chat-other）— CSS 推断
    receivedByRecruiter: '.chat-other',
  },
  chatInput: {
    // 聊天输入框（contenteditable div，id="chat-input"）— 2026-07-19 live page 已验证
    // 注意：这是 contenteditable div，不是 textarea/input
    inputBox: '#chat-input',
    // 发送按钮（button[type="send"]）— 2026-07-19 live page 已验证
    // 注意：type="send"，不是 type="submit"
    sendButton: 'button[type="send"]',
  },
}

/**
 * 在指定根节点内查找单个元素
 */
export function queryChatElement(
  root: ParentNode | null,
  selector: string,
): Element | null {
  if (!root) return null
  return root.querySelector(selector)
}

/**
 * 在指定根节点内查找所有匹配元素
 */
export function queryChatAll(
  root: ParentNode | null,
  selector: string,
): NodeListOf<Element> {
  if (!root) return document.querySelectorAll('.__nonexistent__')
  return root.querySelectorAll(selector)
}

/**
 * 提取元素的文本内容
 */
export function queryChatText(
  root: ParentNode | null,
  selector: string,
): string {
  const el = queryChatElement(root, selector)
  if (!el) return ''
  const text = el.textContent?.trim() ?? ''
  if (text) return text
  return (el as HTMLElement).innerText?.trim() ?? ''
}

/**
 * 从 .title-box 元素提取公司名称
 *
 * DOM 结构（2026-07-19 已验证）：
 * .title-box
 *   .name-box > span.name-text  ← 姓名
 *   i.vline                     ← 分隔符
 *   (文本节点)                   ← 公司名 + 职位
 *
 * 公司名在 i.vline 分隔符之后，职位在公司名之后。
 * 由于没有独立的 class，通过遍历子节点提取。
 *
 * @param titleBox .title-box 元素
 * @returns 公司名称（可能为空字符串）
 */
export function extractCompanyFromTitleBox(titleBox: Element): string {
  const vline = titleBox.querySelector('i.vline')
  if (!vline) return ''

  // i.vline 之后的文本节点包含 "公司名职位" 或 "公司名"
  // 遍历 vline 之后的兄弟节点
  let text = ''
  let node = vline.nextSibling
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent?.trim() ?? ''
    } else if ((node as Element).tagName === 'I') {
      // 可能有第二个 vline 或其他 i 元素
      break
    } else {
      text += (node as Element).textContent?.trim() ?? ''
    }
    node = node.nextSibling
  }

  // 文本可能是 "公司名职位" 连在一起，这里返回整段
  // 调用方可以通过额外逻辑分离公司和职位
  return text.trim()
}

/**
 * 从 .title-box 元素提取招聘方职位
 *
 * 职位通常紧跟在公司名之后，格式如 "聚搜云运营总监"。
 * 由于没有独立的 class，此函数尝试从 .title-box 完整文本中
 * 去除姓名部分后返回剩余文本（包含公司+职位）。
 *
 * @param titleBox .title-box 元素
 * @returns 包含公司和职位的文本（可能为空字符串）
 */
export function extractJobInfoFromTitleBox(titleBox: Element): string {
  const vline = titleBox.querySelector('i.vline')
  if (!vline) return ''

  let text = ''
  let node = vline.nextSibling
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent?.trim() ?? ''
    } else {
      text += (node as Element).textContent?.trim() ?? ''
    }
    node = node.nextSibling
  }

  return text.trim()
}

/**
 * 从右侧职位信息栏提取职位名称和薪资
 *
 * DOM 结构（2026-07-19 已验证）：
 * .position-info
 *   generic "python实习生"    ← 职位名称
 *   generic "200-250元/天"    ← 薪资范围
 *   generic "查看职位"        ← 查看职位链接
 *
 * @param positionInfo .position-info 元素
 * @returns { jobTitle, jobSalary }
 */
export function extractPositionInfo(positionInfo: Element): {
  jobTitle: string
  jobSalary: string
} {
  const children = positionInfo.children
  // 跳过 "查看职位" 链接，取前两个子元素的文本
  const texts: string[] = []
  for (let i = 0; i < children.length; i++) {
    const t = children[i].textContent?.trim() ?? ''
    if (t && t !== '查看职位') {
      texts.push(t)
    }
  }

  return {
    jobTitle: texts[0] ?? '',
    jobSalary: texts[1] ?? '',
  }
}

/** 单个选择器的诊断结果 */
export interface SelectorDiagnostic {
  selector: string
  found: boolean
  count: number
  sampleText: string
  sampleClass: string
  sampleTag: string
}

/** 完整诊断结果 */
export interface ChatDiagnosticResult {
  url: string
  timestamp: number
  chatPage: {
    container: SelectorDiagnostic
  }
  conversationList: {
    container: SelectorDiagnostic
    item: SelectorDiagnostic
    recruiterName: SelectorDiagnostic
    lastMessage: SelectorDiagnostic
    activeItem: SelectorDiagnostic
    messageStatus: SelectorDiagnostic
  }
  conversationDetail: {
    topBar: SelectorDiagnostic
    positionInfo: SelectorDiagnostic
  }
  messageHistory: {
    container: SelectorDiagnostic
    messageItem: SelectorDiagnostic
    sentByUser: SelectorDiagnostic
    receivedByRecruiter: SelectorDiagnostic
  }
  chatInput: {
    inputBox: SelectorDiagnostic
    sendButton: SelectorDiagnostic
  }
  allChatClasses: string[]
}

function diagnoseOne(selector: string, root: ParentNode = document): SelectorDiagnostic {
  const els = root.querySelectorAll(selector)
  const first = els[0]
  return {
    selector,
    found: els.length > 0,
    count: els.length,
    sampleText: first?.textContent?.trim()?.substring(0, 80) ?? '',
    sampleClass: first?.className ?? '',
    sampleTag: first?.tagName ?? '',
  }
}

/**
 * 诊断所有聊天页选择器的匹配情况
 *
 * 在 content script 中调用，结果通过消息回传给 SidePanel 显示。
 * 用于验证选择器是否与真实 DOM 匹配，无需打开 DevTools。
 */
export function diagnoseSelectors(): ChatDiagnosticResult {
  // 收集页面上所有 chat/message/input 相关的 class
  const allClasses = new Set<string>()
  document.querySelectorAll('*').forEach((el) => {
    if (typeof el.className === 'string') {
      el.className.split(/\s+/).forEach((cls) => {
        if (/chat|conv|msg|message|input|send|friend|talk|editor|footer|position|toolbar/i.test(cls)) {
          allClasses.add(cls)
        }
      })
    }
  })

  return {
    url: location.href,
    timestamp: Date.now(),
    chatPage: {
      container: diagnoseOne(CHAT_SELECTORS.chatPage.container),
    },
    conversationList: {
      container: diagnoseOne(CHAT_SELECTORS.conversationList.container),
      item: diagnoseOne(CHAT_SELECTORS.conversationList.item),
      recruiterName: diagnoseOne(CHAT_SELECTORS.conversationList.recruiterName),
      lastMessage: diagnoseOne(CHAT_SELECTORS.conversationList.lastMessage),
      activeItem: diagnoseOne(`${CHAT_SELECTORS.conversationList.item}${CHAT_SELECTORS.conversationList.activeClass}`),
      messageStatus: diagnoseOne(CHAT_SELECTORS.conversationList.messageStatus),
    },
    conversationDetail: {
      topBar: diagnoseOne(CHAT_SELECTORS.conversationDetail.topBar),
      positionInfo: diagnoseOne(CHAT_SELECTORS.conversationDetail.positionInfo),
    },
    messageHistory: {
      container: diagnoseOne(CHAT_SELECTORS.messageHistory.container),
      messageItem: diagnoseOne(CHAT_SELECTORS.messageHistory.messageItem),
      sentByUser: diagnoseOne(CHAT_SELECTORS.messageHistory.sentByUser),
      receivedByRecruiter: diagnoseOne(CHAT_SELECTORS.messageHistory.receivedByRecruiter),
    },
    chatInput: {
      inputBox: diagnoseOne(CHAT_SELECTORS.chatInput.inputBox),
      sendButton: diagnoseOne(CHAT_SELECTORS.chatInput.sendButton),
    },
    allChatClasses: [...allClasses].sort(),
  }
}
