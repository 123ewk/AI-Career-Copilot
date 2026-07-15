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

const store = useSidePanelStore()
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
  await sendMessageToBackground(ChromeMessageType.RESET_EXTRACTION_STATE, {})

  // 优先从 chrome.storage.local 恢复上次状态（岗位列表、分析结果、投递记录等）
  await loadFromStorage()

  // 设置当前页 URL
  const currentTab = await getCurrentTab()
  if (currentTab?.url) {
    const isBoss = currentTab.url.includes('zhipin.com/web/geek/jobs')
    store.setPageInfo(currentTab.url, isBoss)
  }

  // 并行检查后端 + 登录态
  await Promise.all([checkBackendHealth(), checkLoginState()])

  // 注册消息监听
  registerMessageListeners()

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

    <!-- 等待 Boss 列表页 -->
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

    <!-- 提取中 -->
    <div v-else-if="status === 'extracting'" class="status-view">
      <div class="spinner" />
      <h2 class="status-title">正在提取岗位</h2>
      <p class="status-desc">已发现 {{ jobs.length }} 个岗位</p>
    </div>

    <!-- 已就绪：岗位 Tab 内容 -->
    <div v-else-if="status === 'ready' && activeTab === 'jobs'" class="job-tab-content">
      <JobListPanel />
      <JobDetailPanel v-if="selectedJob" :key="selectedJob.sourceUrl" :job="selectedJob" />
      <div v-else class="detail-empty">
        <div class="empty-icon">👈</div>
        <p class="empty-title">选择左侧岗位查看详情</p>
        <p class="empty-desc">选中后点击「加载 Boss 详情并分析」即可触发 AI 分析</p>
      </div>
    </div>

    <!-- 其他 Tab 占位（Step 5 未实现） -->
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
