<script setup lang="ts">
/**
 * SidePanel 根组件（App.vue）
 *
 * 职责：
 * - 渲染 Header：产品名 + 后端状态灯 + 当前页岗位数
 * - 渲染 Tab 导航：岗位 / 沟通 / 简历 / 设置（Step 5 仅岗位 Tab 有内容）
 * - 根据 Pinia store 的 status 渲染不同 UI 状态：
 *   loading / not_logged_in / idle / extracting / ready / error
 * - 监听 Service Worker 推送的消息（JOBS_CREATED / TASK_STATUS_UPDATED / PAGE_CHANGED / JOB_DETAIL_PATCHED）
 * - 启动时查询 SW 登录态 + 后端健康状态
 *
 * 设计动机：
 * - SidePanel 是用户主工作区，需持久展示状态
 * - 状态机驱动 UI：每个状态对应独立的视图，避免条件渲染混乱
 * - 岗位 Tab 采用左右分栏（列表 40% + 详情 60%），与 ui-plugin-design.md 对齐
 */
import { onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useSidePanelStore } from './stores/sidepanel'
import { useResumeStore } from './stores/resume'
import { useCommunicationStore } from './stores/communication'
import {
  onMessage,
  ChromeMessageType,
  sendMessageToBackground,
  isExtensionContextValid,
  type ChromeMessagePayloadMap,
} from './messaging/chrome_message'
import TabNav from './components/sidepanel/TabNav.vue'
import JobListPanel from './components/sidepanel/JobListPanel.vue'
import JobDetailPanel from './components/sidepanel/JobDetailPanel.vue'
import LoginPanel from './components/LoginPanel.vue'
import ResumeTab from './components/sidepanel/ResumeTab.vue'
import ChatTab from './components/sidepanel/ChatTab.vue'

const store = useSidePanelStore()
const resumeStore = useResumeStore()
const commStore = useCommunicationStore()
const { status, backendHealth, isBossListPage, jobs, userInfo, errorMessage, activeTab, selectedJob } =
  storeToRefs(store)
const { loadFromStorage } = store

// ==================== 初始化 ====================

/** 检查后端健康状态 */
async function checkBackendHealth() {
  try {
    const resp = await fetch(`${store.backendUrl}/health`)
    if (resp.ok) {
      const data = (await resp.json()) as { status?: string }
      store.setBackendHealth(data.status === 'ok' ? 'ok' : 'fail')
    } else {
      store.setBackendHealth('fail')
    }
  } catch {
    store.setBackendHealth('fail')
  }
}

/** 登录成功回调 */
function handleLoggedIn(payload: { user: { id: string; email: string; name: string }; backendUrl: string }) {
  store.setBackendUrl(payload.backendUrl)
  store.setUserInfo(payload.user)

  // 登录成功后若处于 Boss 列表页，立即触发一次手动刷新
  //（之前未登录时 Content Script 发送的 JOBS_EXTRACTED 可能被 SW 拒绝）
  if (store.isBossListPage) {
    void handleRefresh()
  }
}

/** SW 状态查询响应类型 */
interface SwState {
  hasToken: boolean
  backendUrl: string
  user: { id: string; email: string; name: string } | null
}

/**
 * 查询登录态（SW 优先，storage 兜底）
 *
 * 流程：
 * 1. 通过 GET_SW_STATE 查询 SW（SW 会从 chrome.storage.local 读 token 并尝试 refresh）
 * 2. 若 SW 有 token 但无 user 信息，从 storage 补充 user
 * 3. 若 SW 无有效 token，直接读 chrome.storage.local 作为兜底
 * 4. 全部无 token → 显示登录页
 */
async function checkLoginState() {
  // 方式 1：通过 SW 查询登录态
  const swResult = await new Promise<boolean>((resolve) => {
    if (!isExtensionContextValid()) {
      resolve(false)
      return
    }
    try {
      chrome.runtime.sendMessage(
        { type: 'GET_SW_STATE' },
        (response: { ok: boolean; data?: SwState; error?: string }) => {
          if (chrome.runtime.lastError || !response?.ok || !response.data) {
            console.warn('[App] 查询 SW 状态失败:', response?.error ?? chrome.runtime.lastError?.message)
            resolve(false)
            return
          }
          const state = response.data
          store.setBackendUrl(state.backendUrl)
          if (state.user) {
            store.setUserInfo(state.user)
          }
          console.log('[App] SW 登录态 | hasToken=', state.hasToken, '| hasUser=', !!state.user)
          resolve(state.hasToken ?? false)
        },
      )
    } catch {
      resolve(false)
    }
  })

  if (swResult) {
    // SW 有 token 但可能没有 user 信息（SW 重启后从 storage 恢复 token 时 user=null）
    // 从 storage 补充 user 信息
    if (!store.isLoggedIn) {
      await restoreUserFromStorage()
    }
    return
  }

  // 方式 2：SW 无有效 token，直接读 storage 兜底
  const storageResult = await new Promise<boolean>((resolve) => {
    try {
      chrome.storage.local.get(['auth_state'], (data) => {
        if (chrome.runtime.lastError) {
          resolve(false)
          return
        }
        const state = data.auth_state as {
          accessToken?: string
          expiresAt?: number
          backendUrl?: string
          user?: { id: string; email: string; name: string } | null
        } | undefined
        if (!state?.accessToken) {
          console.log('[App] storage 中无 auth_state，显示登录页')
          resolve(false)
          return
        }
        if (state.expiresAt && Date.now() >= state.expiresAt - 60000) {
          console.log('[App] storage 中 token 已过期 | expiresAt=', new Date(state.expiresAt).toISOString())
          resolve(false)
          return
        }
        if (state.backendUrl) {
          store.setBackendUrl(state.backendUrl)
        }
        // 恢复 user 信息（持久化在 auth_state.user 中）
        if (state.user) {
          store.setUserInfo(state.user)
        }
        console.log('[App] storage 中有有效 token | 剩余=', Math.round((state.expiresAt! - Date.now()) / 1000), 's | user=', state.user?.email ?? 'null')
        resolve(true)
      })
    } catch {
      resolve(false)
    }
  })

  if (!storageResult) {
    store.setUserInfo(null)
  }
}

/**
 * 从 chrome.storage.local 恢复 user 信息
 *
 * 用于：SW 有 token 但 user=null 的场景（SW 重启后从 storage 恢复了 token，
 * 但旧版本未持久化 user 信息；新版本已持久化，此函数作为兼容兜底）
 */
async function restoreUserFromStorage(): Promise<void> {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get(['auth_state'], (data) => {
        const state = data.auth_state as {
          user?: { id: string; email: string; name: string } | null
        } | undefined
        if (state?.user) {
          store.setUserInfo(state.user)
          console.log('[App] 从 storage 恢复 user 信息 | email=', state.user.email)
        }
        resolve()
      })
    } catch {
      resolve()
    }
  })
}

// ==================== 消息监听 ====================

let detachMessageListener: (() => void) | null = null

// ==================== Service Worker 保活 ====================

/** SidePanel 与 SW 之间的长连接端口，保持连接可防止 SW 被 Chrome 回收 */
let keepAlivePort: chrome.runtime.Port | null = null

/**
 * 建立与 Service Worker 的保活连接
 *
 * MV3 Service Worker 空闲 30 秒会被回收；长连接端口可让 Chrome 在 SidePanel
 * 打开期间保持 SW 活跃，避免首次消息触发 "Receiving end does not exist"。
 */
function connectKeepAlivePort(): void {
  if (!isExtensionContextValid()) return
  try {
    keepAlivePort = chrome.runtime.connect({ name: 'sidepanel_keepalive' })
    keepAlivePort.onDisconnect.addListener(() => {
      keepAlivePort = null
      if (!isExtensionContextValid()) {
        store.setError('扩展上下文已失效，请关闭并重新打开 SidePanel')
      }
    })
  } catch (err) {
    console.warn('[App] 建立 keep-alive 端口失败:', err)
  }
}

/**
 * 断开与 Service Worker 的保活连接
 */
function disconnectKeepAlivePort(): void {
  if (keepAlivePort) {
    try {
      keepAlivePort.disconnect()
    } catch {
      // 上下文失效时 disconnect 可能抛异常，忽略
    }
    keepAlivePort = null
  }
}

function registerMessageListeners() {
  detachMessageListener = onMessage((message) => {
    // 按消息类型分发到 store action
    // SW → SidePanel 的单向广播
    switch (message.type) {
      case ChromeMessageType.PAGE_CHANGED: {
        const payload = message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.PAGE_CHANGED]
        const pageChanged = store.currentUrl !== payload.url
        store.setPageInfo(payload.url, payload.isBossListPage)
        // 检测是否为聊天页
        const isChatPage = payload.url.includes('zhipin.com/web/geek/chat')
        commStore.setOnChatPage(isChatPage)
        // 设置活跃页面类型
        if (payload.isBossListPage) {
          store.setActivePage('list')
        } else if (isChatPage) {
          store.setActivePage('chat')
        } else {
          store.setActivePage('other')
        }
        // 页面 URL 发生变化时清空旧岗位列表，等待新的 JOBS_CREATED 替换
        if (pageChanged) {
          store.clearJobs()
        }
        if (payload.isBossListPage && store.isLoggedIn) {
          store.setStatus('extracting')
        }
        break
      }
      case ChromeMessageType.JOBS_CREATED: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.JOBS_CREATED]
        console.log(
          `[App] JOBS_CREATED | created=${payload.created.length} | duplicated=${payload.duplicated.length} | failed=${payload.failed.length}`,
        )
        store.applyJobsCreated(payload)
        break
      }
      case ChromeMessageType.TASK_STATUS_UPDATED: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.TASK_STATUS_UPDATED]
        store.applyTaskStatusUpdate(payload)
        break
      }
      case ChromeMessageType.JOB_DETAIL_PATCHED: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.JOB_DETAIL_PATCHED]
        console.log(`[App] JOB_DETAIL_PATCHED | jobId=${payload.jobId} | hasJdText=${payload.hasJdText}`)
        store.onJobDetailPatched(payload)
        break
      }
      case ChromeMessageType.CHAT_MESSAGES_UPDATED: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_MESSAGES_UPDATED]
        console.log(
          `[App] CHAT_MESSAGES_UPDATED | recruiter=${payload.recruiterName} | count=${payload.messages.length}`,
        )
        commStore.setOnChatPage(true)
        store.setActivePage('chat')
        commStore.updateFromExtracted(payload)
        break
      }
      case ChromeMessageType.CHAT_CONVERSATION_SWITCHED: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATION_SWITCHED]
        console.log(
          `[App] CHAT_CONVERSATION_SWITCHED | recruiter=${payload.recruiterName}`,
        )
        commStore.switchConversation(payload)
        break
      }
      case ChromeMessageType.CHAT_DIAGNOSE: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_DIAGNOSE]
        console.log('[App] CHAT_DIAGNOSE received')
        commStore.setDiagnostics(payload.diagnostics)
        break
      }
      case ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED: {
        const payload =
          message.payload as ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED]
        console.log(`[App] CHAT_CONVERSATIONS_EXTRACTED | count=${payload.conversations.length}`)
        commStore.updateConversationsList(payload)
        break
      }
      default:
        // 其他消息类型（REQUEST_* 等）由 SidePanel 主动发送，不在此处理
        break
    }
    return { ok: true }
  })
}

// ==================== 生命周期 ====================

onMounted(async () => {
  // 扩展重载/更新后，已打开的 SidePanel 上下文会失效
  // 先探测上下文，失效时直接提示用户重新打开，避免后续 chrome API 抛未处理异常
  if (!isExtensionContextValid()) {
    store.setError('扩展上下文已失效，请关闭并重新打开 SidePanel')
    return
  }

  // 建立保活端口，保持 SW 在 SidePanel 打开期间活跃
  connectKeepAlivePort()

  // 关键修复：每次打开 SidePanel，先强制重置 Content Script + SW 的提取状态
  // 避免关闭重开后复用 apiDataCaptured / sentJobTracker / source_url_map 等旧状态
  //
  // keepChatCache=true：保留 Chat 模块缓存
  // - Content Script 在 BOSS 聊天页加载时已经把对话列表发给 SW
  // - SidePanel 重开时不应清空这些缓存,否则会丢失数据且无法重新触发 Content Script 广播
  // - 登出场景会显式传 keepChatCache=false(或 undefined)彻底清空
  await sendMessageToBackground(ChromeMessageType.RESET_EXTRACTION_STATE, {
    keepChatCache: true,
  })

  // 优先从 chrome.storage.local 恢复上次状态（岗位列表、分析结果、投递记录等）
  await loadFromStorage()
  await resumeStore.loadFromStorage()

  // 设置当前页 URL
  const currentTab = await getCurrentTab()
  if (currentTab?.url) {
    const isBoss = currentTab.url.includes('zhipin.com/web/geek/jobs')
    const isChat = currentTab.url.includes('zhipin.com/web/geek/chat')
    store.setPageInfo(currentTab.url, isBoss)
    commStore.setOnChatPage(isChat)
    if (isBoss) store.setActivePage('list')
    else if (isChat) store.setActivePage('chat')
    else store.setActivePage('other')
  }

  // 并行检查后端 + 登录态
  await Promise.all([checkBackendHealth(), checkLoginState()])

  // 注册消息监听
  registerMessageListeners()

  // 如果在聊天页，主动向 Content Script 请求对话列表
  //
  // 三级 fallback 策略(2026-07-21 修复):
  // 1. 直接向 Content Script 请求(最新鲜的 DOM 数据)
  // 2. 失败/返回空 → 向 SW 请求缓存(可能包含 Content Script 之前发来的数据)
  // 3. 仍失败 → 等待 Content Script 通过 MutationObserver 触发的广播
  //
  // 原问题:catch 块完全吞掉错误,且注释错误地说"等待广播"
  // 实际 Content Script 的 doInitChatPage 只在页面加载时执行一次,
  // 不会再次主动广播,所以必须依赖 SW 缓存作为 fallback
  if (currentTab?.id && currentTab.url?.includes('zhipin.com/web/geek/chat')) {
    const tabId = currentTab.id
    const tabUrl = currentTab.url

    /**
     * 尝试从 Level 1(Content Script) + Level 2(SW 缓存)拉取对话列表
     *
     * @returns 是否成功拉取到非空对话列表
     */
    const tryLoadChatList = async (): Promise<boolean> => {
      // Level 1: 直接向 Content Script 请求(最新鲜的 DOM 数据)
      try {
        const resp = await chrome.tabs.sendMessage(tabId, {
          type: ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED,
          payload: {},
        } as never)
        if (resp?.ok && resp.data) {
          const data = resp.data as {
            conversations: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED]['conversations']
          }
          if (data.conversations?.length > 0) {
            commStore.updateConversationsList({
              conversations: data.conversations,
              pageUrl: tabUrl,
            })
            console.log(
              `[App] Level 1 拉取对话列表成功 | count=${data.conversations.length}`,
            )
            return true
          }
        }
      } catch (err) {
        // 不再静默吞错:Content Script 未注入/未就绪/异步超时都会到这里
        console.error('[App] Level 1 主动拉取对话列表失败:', err)
      }

      // Level 2: Level 1 失败/返回空 → 向 SW 请求缓存
      // SW 缓存可能包含 Content Script 之前广播过来的对话列表
      try {
        const swResp = await sendMessageToBackground(
          ChromeMessageType.REQUEST_CONVERSATIONS_LIST,
          {},
        )
        if (swResp?.ok && swResp.data) {
          const data = swResp.data as {
            conversations: ChromeMessagePayloadMap[typeof ChromeMessageType.CHAT_CONVERSATIONS_EXTRACTED]['conversations']
          }
          if (data.conversations?.length > 0) {
            commStore.updateConversationsList({
              conversations: data.conversations,
              pageUrl: 'sw-cache', // 标识数据来源为 SW 缓存(非 DOM 提取)
            })
            console.log(
              `[App] Level 2 从 SW 缓存拉取对话列表成功 | count=${data.conversations.length}`,
            )
            return true
          }
        }
      } catch (err) {
        console.error('[App] Level 2 SW 缓存拉取失败:', err)
      }

      return false
    }

    // 初次尝试(Level 1 + Level 2 顺序执行)
    let chatListLoaded = await tryLoadChatList()

    // Level 3: 主动重试(每 1s 一次,共 5 次)
    //
    // 设计动机(2026-07-22 修复):
    // - 之前 Level 3 只打 warn 不动作,等待 Content Script MutationObserver 广播
    // - 但 MutationObserver 只监听"未来"变化,不触发初始快照
    // - 场景 A:打开 BOSS 聊天页 → 等几秒 → 打开 SidePanel 时,
    //   列表早已渲染完成,observer 永不触发,SidePanel 永远收不到广播
    // - 修复策略:SidePanel 主动重试 Level 1/2
    //   - 等 1s 后 Content Script 可能已重新提取(doInitChatPage 5 次重试中)
    //   - 或 SW 缓存可能已通过其他途径获得数据
    //   - 总等待时间 5s,覆盖 Vue SPA 异步渲染 + Content Script 重试周期
    if (!chatListLoaded) {
      console.warn(
        '[App] Level 1+2 均失败,启动 Level 3 主动重试(每 1s 一次,共 5 次)',
      )
      for (let i = 1; i <= 5 && !chatListLoaded; i++) {
        await new Promise((resolve) => setTimeout(resolve, 1000))
        console.log(`[App] Level 3 重试 ${i}/5`)
        chatListLoaded = await tryLoadChatList()
      }
      if (!chatListLoaded) {
        console.warn(
          '[App] Level 3 重试 5 次仍失败,放弃,等待 Content Script MutationObserver 广播(若列表后续变化仍会被回调)',
        )
      }
    }
  }

  // 根据登录态 + 页面状态决定 UI 状态
  if (!store.isLoggedIn) {
    store.setStatus('not_logged_in')
  } else if (store.isBossListPage) {
    // 状态已重置，强制进入 extracting 并触发刷新，重新完整扫描页面数据
    store.setStatus('extracting')
    void handleRefresh()
  } else {
    store.setStatus('idle')
  }
})

onUnmounted(() => {
  detachMessageListener?.()
  disconnectKeepAlivePort()
})

/** 获取当前激活的 Tab */
async function getCurrentTab(): Promise<chrome.tabs.Tab | null> {
  if (!isExtensionContextValid()) return null
  return new Promise((resolve) => {
    try {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        resolve(tabs[0] ?? null)
      })
    } catch {
      resolve(null)
    }
  })
}

/** 手动刷新岗位列表 */
async function handleRefresh() {
  if (!isExtensionContextValid()) {
    store.setError('扩展上下文已失效，请关闭并重新打开 SidePanel')
    return
  }
  if (!store.isBossListPage) return

  store.setStatus('extracting')
  const resp = await sendMessageToBackground(ChromeMessageType.REFRESH_JOBS, {})
  if (!resp.ok) {
    console.warn('[App] 手动刷新失败:', resp.error)
    // 上下文失效或 SW 不可用：切换到错误状态并提示用户
    if (resp.error?.includes('Extension context invalidated')) {
      store.setError('扩展上下文已失效，请关闭并重新打开 SidePanel')
    }
  }
}
</script>

<template>
  <div class="sidepanel-root">
    <!-- ============ Header ============ -->
    <header class="sidepanel-header">
      <div class="header-left">
        <h1 class="header-title">AI Career Copilot</h1>
        <span class="header-subtitle">海投助手</span>
      </div>
      <div class="header-right">
        <div
          class="status-indicator"
          :class="backendHealth"
          :title="`后端: ${backendHealth === 'ok' ? '正常' : backendHealth === 'fail' ? '不可用' : '检测中'}`"
        />
        <span class="job-count">{{ jobs.length }} 岗位</span>
      </div>
    </header>

    <!-- ============ Tab 导航 ============ -->
    <TabNav :active-tab="activeTab" @update:active-tab="store.setActiveTab" />

    <!-- ============ 主体内容（按状态切换）============ -->

    <!-- 加载中 -->
    <div v-if="status === 'loading'" class="status-view">
      <div class="spinner" />
      <p>初始化中...</p>
    </div>

    <!-- 未登录 -->
    <div v-else-if="status === 'not_logged_in'" class="status-view login-view">
      <LoginPanel @logged-in="handleLoggedIn" />
    </div>

    <!-- 沟通 Tab：登录即可用，不依赖岗位状态 -->
    <div v-else-if="activeTab === 'chat'" class="chat-tab-wrapper">
      <ChatTab />
    </div>

    <!-- 简历 Tab：登录即可用，不依赖岗位状态 -->
    <div v-else-if="activeTab === 'resume'" class="resume-tab-wrapper">
      <ResumeTab />
    </div>

    <!-- 岗位 Tab + 等待列表页 -->
    <div v-else-if="status === 'idle'" class="status-view">
      <div class="status-icon">📋</div>
      <h2 class="status-title">等待 Boss 列表页</h2>
      <p class="status-desc">
        请打开 Boss 直聘职位搜索页<br />
        <code class="url-hint">zhipin.com/web/geek/jobs</code>
      </p>
      <div v-if="!isBossListPage" class="current-url">
        当前页：<code>{{ store.currentUrl || '(未知)' }}</code>
      </div>
    </div>

    <!-- 岗位 Tab + 提取中 -->
    <div v-else-if="status === 'extracting'" class="status-view">
      <div class="spinner" />
      <h2 class="status-title">正在提取岗位</h2>
      <p class="status-desc">已发现 {{ jobs.length }} 个岗位</p>
    </div>

    <!-- 岗位 Tab + 已就绪 -->
    <div v-else-if="status === 'ready' && activeTab === 'jobs'" class="job-tab-content">
      <JobListPanel />
      <JobDetailPanel v-if="selectedJob" :key="selectedJob.sourceUrl" :job="selectedJob" />
      <div v-else class="detail-empty">
        <div class="empty-icon">👈</div>
        <p class="empty-title">选择左侧岗位查看详情</p>
        <p class="empty-desc">选中后点击「加载 Boss 详情并分析」即可触发 AI 分析</p>
      </div>
    </div>

    <!-- 其他 Tab 占位（设置未实现） -->
    <div v-else-if="status === 'ready'" class="status-view placeholder-tab">
      <div class="status-icon">🚧</div>
      <h2 class="status-title">正在开发中</h2>
      <p class="status-desc">该 Tab 将在后续版本开放</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="status === 'error'" class="status-view">
      <div class="status-icon error">⚠️</div>
      <h2 class="status-title">发生错误</h2>
      <p class="status-desc">{{ errorMessage ?? '未知错误' }}</p>
      <button class="retry-btn" @click="handleRefresh">重试</button>
    </div>

    <!-- ============ Footer ============ -->
    <footer class="sidepanel-footer">
      <span>v0.0.1 · 本地开发版</span>
      <span v-if="userInfo">· {{ userInfo.name }}</span>
    </footer>
  </div>
</template>

<style scoped>
.sidepanel-root {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-base);
  overflow: hidden;
}

/* ==================== Header ==================== */

.sidepanel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.header-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.header-subtitle {
  font-size: 11px;
  color: var(--text-secondary);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-tertiary);
}

.status-indicator.ok {
  background: var(--success);
}

.status-indicator.fail {
  background: var(--error);
}

.job-count {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-base);
  padding: 2px 6px;
  border-radius: 4px;
}

/* ==================== 状态视图 ==================== */

.status-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;
  flex: 1;
  min-height: 0;
}

.status-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.status-icon.error {
  color: var(--error);
}

.status-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.status-desc {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
  margin-bottom: 16px;
}

.url-hint {
  background: var(--bg-base);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  color: var(--text-primary);
}

.backend-info {
  margin-top: 16px;
  padding: 8px 12px;
  background: var(--bg-card);
  border-radius: 6px;
  font-size: 11px;
}

.info-label {
  color: var(--text-secondary);
}

.info-value.ok {
  color: var(--success);
  font-weight: 500;
}

.info-value.fail {
  color: var(--error);
  font-weight: 500;
}

.current-url {
  margin-top: 12px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.current-url code {
  word-break: break-all;
  color: var(--text-secondary);
}

/* ==================== 登录视图 ==================== */

.login-view {
  align-items: stretch;
  justify-content: flex-start;
  padding: 16px;
  overflow-y: auto;
}

/* ==================== Spinner ==================== */

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 12px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* ==================== 岗位 Tab 左右分栏 ==================== */

.job-tab-content {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

/* ==================== 沟通 Tab 左右分栏 ==================== */

.chat-tab-wrapper {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

/* ==================== 简历 Tab 左右分栏 ==================== */

.resume-tab-wrapper {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.detail-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;
  color: var(--text-secondary);
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.empty-desc {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ==================== Footer ==================== */

.sidepanel-footer {
  padding: 8px 12px;
  background: var(--bg-card);
  border-top: 1px solid var(--border);
  font-size: 10px;
  color: var(--text-secondary);
  text-align: center;
  flex-shrink: 0;
}

.retry-btn {
  padding: 8px 16px;
  background: var(--accent);
  color: #ffffff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.retry-btn:hover {
  background: var(--accent-hover);
}
</style>
