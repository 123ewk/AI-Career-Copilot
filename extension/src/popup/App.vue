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

/** 查询 SW 内存中的登录态 */
async function checkLoginState() {
  // GET_SW_STATE 是 SW 内部查询消息，不走 ChromeMessageType 枚举
  return new Promise<void>((resolve) => {
    chrome.runtime.sendMessage(
      { type: 'GET_SW_STATE' },
      (response: { ok: boolean; data?: SwState }) => {
        if (chrome.runtime.lastError || !response?.ok) {
          // SW 可能尚未启动或未注册 handler
          isLoggedIn.value = false
          resolve()
          return
        }
        const state = response.data
        isLoggedIn.value = state?.hasToken ?? false
        swBackendUrl.value = state?.backendUrl ?? BACKEND_URL
        resolve()
      },
    )
  })
}

/** 登录成功回调 */
function handleLoggedIn(payload: { user: UserInfo; backendUrl: string }) {
  userInfo.value = payload.user
  swBackendUrl.value = payload.backendUrl
  isLoggedIn.value = true
}

/** 打开 SidePanel（需要用户手势触发，Popup 点击符合 MV3 要求） */
async function openSidePanel() {
  await chrome.sidePanel.open({ windowId: chrome.windows.WINDOW_ID_CURRENT })
  // 关闭 Popup，让 SidePanel 获得焦点
  window.close()
}

/** 登出（清除 SW 内存中的 token） */
async function handleLogout() {
  await sendMessageToBackground(ChromeMessageType.AUTH_TOKEN_UPDATED, {
    accessToken: null,
    backendUrl: swBackendUrl.value,
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
