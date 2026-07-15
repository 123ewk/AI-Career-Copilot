<script setup lang="ts">
/**
 * Popup 根组件
 *
 * 职责：
 * - 显示登录面板（LoginPanel）
 * - 登录后显示用户信息 + 打开 SidePanel 按钮 + 登出按钮
 * - 显示后端连接状态
 *
 * 设计动机：
 * - Chrome MV3 Popup 生命周期短（用户点击扩展图标即出现，失焦即关闭）
 * - 不在 Popup 中放主 UI，主 UI 在 SidePanel 持久展示
 * - Popup 仅作为「入口 + 登录 + 设置」
 *
 * 状态：
 * - 检查 SW 内存中的 token（通过 GET_SW_STATE 消息）
 * - 已登录 → 显示用户面板
 * - 未登录 → 显示 LoginPanel
 */
import { onMounted, ref } from 'vue'
import LoginPanel from '../components/LoginPanel.vue'
import {
  ChromeMessageType,
  sendMessageToBackground,
} from '../messaging/chrome_message'

interface UserInfo {
  id: string
  email: string
  name: string
}

interface SwState {
  hasToken: boolean
  backendUrl: string
  user: UserInfo | null
}

const BACKEND_URL = 'http://localhost:8000'

const backendStatus = ref<'unknown' | 'ok' | 'fail'>('unknown')
const isLoggedIn = ref(false)
const userInfo = ref<UserInfo | null>(null)
const swBackendUrl = ref(BACKEND_URL)

/** 检查后端健康状态 */
async function checkBackend() {
  backendStatus.value = 'unknown'
  try {
    const resp = await fetch(`${BACKEND_URL}/health`)
    if (resp.ok) {
      const data = (await resp.json()) as { status?: string }
      backendStatus.value = data.status === 'ok' ? 'ok' : 'fail'
    } else {
      backendStatus.value = 'fail'
    }
  } catch {
    backendStatus.value = 'fail'
  }
}

/**
 * 查询 SW 登录态（Popup 初始化时调用）
 *
 * 流程：
 * 1. 优先通过 GET_SW_STATE 查询 SW（SW 会从 chrome.storage.local 读 token）
 * 2. 若 SW 无响应，直接读 chrome.storage.local 作为兜底
 * 3. 无有效 token → 显示登录页
 */
async function checkLoginState() {
  // 调试：直接读 storage 打印当前存储状态
  await new Promise<void>((resolve) => {
    chrome.storage.local.get(['auth_state', 'access_token', 'token_expire'], (data) => {
      const authState = data.auth_state as { accessToken?: string; expiresAt?: number } | undefined
      console.log('[popup] 初始化 storage 状态:', {
        auth_state: authState ? {
          hasToken: !!authState.accessToken,
          expiresAt: authState.expiresAt
            ? new Date(authState.expiresAt).toISOString()
            : null,
        } : '(不存在)',
        access_token: data.access_token ? '***已存在***' : '(不存在)',
        token_expire: data.token_expire ?? '(不存在)',
      })
      resolve()
    })
  })

  // 方式 1：通过 SW 查询登录态
  const swResult = await new Promise<boolean>((resolve) => {
    chrome.runtime.sendMessage(
      { type: 'GET_SW_STATE' },
      (response: { ok: boolean; data?: SwState; error?: string }) => {
        if (chrome.runtime.lastError || !response?.ok) {
          console.warn('[popup] SW 未响应，将直接检查 storage |', response?.error ?? chrome.runtime.lastError?.message)
          resolve(false)
          return
        }
        const state = response.data
        swBackendUrl.value = state?.backendUrl ?? BACKEND_URL
        userInfo.value = state?.user ?? null
        console.log('[popup] SW 登录态 | hasToken=', state?.hasToken, '| backend=', swBackendUrl.value)
        resolve(state?.hasToken ?? false)
      },
    )
  })

  if (swResult) {
    isLoggedIn.value = true
    return
  }

  // 方式 2：SW 无有效 token，直接读 storage 兜底
  const storageResult = await new Promise<boolean>((resolve) => {
    chrome.storage.local.get(['auth_state'], (data) => {
      const state = data.auth_state as { accessToken?: string; expiresAt?: number } | undefined
      if (!state?.accessToken) {
        console.log('[popup] storage 中无 auth_state，显示登录页')
        resolve(false)
        return
      }
      // 检查 token 是否过期
      if (state.expiresAt && Date.now() >= state.expiresAt - 60000) {
        console.log('[popup] storage 中 token 已过期 | expiresAt=', new Date(state.expiresAt).toISOString())
        resolve(false)
        return
      }
      console.log('[popup] storage 中有有效 token，但 SW 未恢复 | 剩余=', Math.round((state.expiresAt! - Date.now()) / 1000), 's')
      resolve(true)
    })
  })

  isLoggedIn.value = storageResult
  if (!isLoggedIn.value) {
    userInfo.value = null
  }
}

/** 登录成功回调 */
function handleLoggedIn(payload: { user: UserInfo; backendUrl: string }) {
  userInfo.value = payload.user
  swBackendUrl.value = payload.backendUrl
  isLoggedIn.value = true
  // 登录后验证 storage 写入
  setTimeout(() => {
    chrome.storage.local.get(['auth_state'], (data) => {
      const state = data.auth_state as { accessToken?: string; expiresAt?: number } | undefined
      console.log('[popup] 登录后 storage 验证:', {
        hasToken: !!state?.accessToken,
        tokenPreview: state?.accessToken ? `${state.accessToken.slice(0, 12)}...` : null,
        expiresAt: state?.expiresAt ? new Date(state.expiresAt).toISOString() : null,
      })
    })
  }, 500)
}

/** 打开 SidePanel（需要用户手势触发，Popup 点击符合 MV3 要求） */
async function openSidePanel() {
  await chrome.sidePanel.open({ windowId: chrome.windows.WINDOW_ID_CURRENT })
  // 关闭 Popup，让 SidePanel 获得焦点
  window.close()
}

/** 登出（清除 SW 内存 token、storage、source_url_map、轮询、Content Script 状态） */
async function handleLogout() {
  // 0. 登出前：打印当前 storage 中的登录字段（调试用）
  await new Promise<void>((resolve) => {
    chrome.storage.local.get(['auth_state', 'access_token', 'token_expire'], (before) => {
      console.log('[popup] 登出前 storage 状态:', {
        auth_state: before.auth_state ?? '(不存在)',
        access_token: before.access_token ? '***已存在***' : '(不存在)',
        token_expire: before.token_expire ?? '(不存在)',
      })
      resolve()
    })
  })

  // 1. 批量清除 chrome.storage.local 中所有登录相关 key（含当前 + 历史遗留）
  const AUTH_KEYS_TO_CLEAR = [
    'auth_state',       // 当前版本：{ backendUrl, accessToken, expiresAt }
    'access_token',     // 历史遗留：独立 token 字段
    'token_expire',     // 历史遗留：独立过期时间字段
    'token_expires_at', // 防御：其他可能的命名变体
  ]
  await new Promise<void>((resolve) => {
    chrome.storage.local.remove(AUTH_KEYS_TO_CLEAR, () => {
      if (chrome.runtime.lastError) {
        console.warn('[popup] 清除 storage 失败:', chrome.runtime.lastError.message)
      } else {
        console.log('[popup] storage 登录字段已批量清除 | keys=', AUTH_KEYS_TO_CLEAR)
      }
      resolve()
    })
  })

  // 1.5 登出后验证：确认 storage 已清空
  await new Promise<void>((resolve) => {
    chrome.storage.local.get(AUTH_KEYS_TO_CLEAR, (after) => {
      const remaining = AUTH_KEYS_TO_CLEAR.filter((k) => after[k] !== undefined)
      if (remaining.length > 0) {
        console.error('[popup] ⚠️ storage 清除不彻底，残留 key:', remaining)
      } else {
        console.log('[popup] ✅ storage 登录字段已全部清除')
      }
      resolve()
    })
  })

  // 2. 通知 SW 清空内存中的 token 缓存
  await sendMessageToBackground(ChromeMessageType.CLEAR_TOKEN_CACHE, {})

  // 3. 通知 SW + Content Script 重置全部提取状态，并清空 sidepanel_state
  await sendMessageToBackground(ChromeMessageType.RESET_EXTRACTION_STATE, {
    clearSidePanelStorage: true,
  })

  isLoggedIn.value = false
  userInfo.value = null
}

onMounted(async () => {
  await Promise.all([checkBackend(), checkLoginState()])
})
</script>

<template>
  <div class="popup-root">
    <header class="popup-header">
      <h1>AI Career Copilot</h1>
      <div
        class="status-dot"
        :class="backendStatus"
        :title="`后端: ${backendStatus}`"
      />
    </header>

    <!-- 未登录：显示 LoginPanel -->
    <LoginPanel
      v-if="!isLoggedIn"
      :default-backend-url="BACKEND_URL"
      @logged-in="handleLoggedIn"
    />

    <!-- 已登录：显示用户面板 -->
    <div v-else class="user-panel">
      <div class="user-info">
        <div class="user-avatar">
          {{ userInfo?.name?.charAt(0).toUpperCase() ?? 'U' }}
        </div>
        <div class="user-meta">
          <div class="user-name">{{ userInfo?.name ?? '未知用户' }}</div>
          <div class="user-email">{{ userInfo?.email }}</div>
        </div>
      </div>

      <div class="popup-actions">
        <button class="primary-btn" @click="openSidePanel">
          🚀 打开海投助手
        </button>
        <button class="ghost-btn" @click="checkBackend">
          重新检测后端
        </button>
        <button class="danger-btn" @click="handleLogout">
          登出
        </button>
      </div>
    </div>

    <footer class="popup-footer">v0.0.1 · 本地开发版</footer>
  </div>
</template>

<style scoped>
.popup-root {
  width: 320px;
  padding: 12px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.popup-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.popup-header h1 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: #1f2937;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #9ca3af;
}

.status-dot.ok {
  background: #10b981;
}

.status-dot.fail {
  background: #ef4444;
}

.user-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  background: #f9fafb;
  border-radius: 8px;
}

.user-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #3b82f6;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
}

.user-meta {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-email {
  font-size: 11px;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.popup-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.primary-btn {
  padding: 10px 12px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.primary-btn:hover {
  background: #2563eb;
}

.ghost-btn {
  padding: 8px 12px;
  background: transparent;
  color: #6b7280;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.ghost-btn:hover {
  background: #f9fafb;
}

.danger-btn {
  padding: 8px 12px;
  background: transparent;
  color: #dc2626;
  border: 1px solid #fecaca;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.danger-btn:hover {
  background: #fef2f2;
}

.popup-footer {
  margin-top: 12px;
  font-size: 11px;
  color: #9ca3af;
  text-align: center;
}
</style>
