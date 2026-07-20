<script setup lang="ts">
/**
 * 消息面板组件（右栏）
 *
 * 职责：
 * - 渲染当前对话的消息历史（气泡布局）
 * - 渲染 AI 回复区域（可编辑 textarea + 操作按钮）
 * - 支持审核模式（注入输入框）和自动模式（注入 + 发送）
 */

import { ref, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useCommunicationStore } from '../../stores/communication'

const commStore = useCommunicationStore()
const {
  activeConversation,
  activeMessages,
  activeSuggestedReply,
  autoSendEnabled,
} = storeToRefs(commStore)

const isEditing = ref(false)
const editText = ref('')

/** 开始编辑 AI 建议 */
function startEdit() {
  if (!activeSuggestedReply.value) return
  editText.value = activeSuggestedReply.value.text
  isEditing.value = true
}

/** 保存编辑 */
function saveEdit() {
  if (!activeConversation.value) return
  commStore.updateSuggestedReply(activeConversation.value.id, editText.value)
  isEditing.value = false
}

/** 取消编辑 */
function cancelEdit() {
  isEditing.value = false
}

/** 生成回复 */
function handleGenerate() {
  if (!activeConversation.value) return
  void commStore.requestReply(activeConversation.value.id)
}

/** 审核模式：注入到输入框 */
function handleInject() {
  if (!activeConversation.value) return
  void commStore.injectReply(activeConversation.value.id)
}

/** 自动模式：注入 + 发送 */
function handleAutoSend() {
  if (!activeConversation.value) return
  void commStore.autoSendReply(activeConversation.value.id)
}

/** 生成中状态 */
const isGenerating = computed(() => activeSuggestedReply.value?.isGenerating ?? false)
</script>

<template>
  <div class="message-panel">
    <!-- 头部：HR 名 + 操作 -->
    <div class="panel-header">
      <span class="recruiter-name">{{ activeConversation?.recruiterName }}</span>
      <div class="header-actions">
        <label class="auto-send-toggle">
          <input
            type="checkbox"
            :checked="autoSendEnabled"
            @change="autoSendEnabled = !autoSendEnabled"
          />
          <span class="toggle-label">自动发送</span>
        </label>
      </div>
    </div>

    <!-- 消息历史 -->
    <div class="message-history">
      <div
        v-for="(msg, idx) in activeMessages"
        :key="idx"
        class="message-bubble"
        :class="msg.role === 'user' ? 'msg-right' : 'msg-left'"
      >
        <div class="bubble-text">{{ msg.text }}</div>
        <div v-if="msg.timestamp" class="bubble-time">{{ msg.timestamp }}</div>
      </div>
      <div v-if="activeMessages.length === 0" class="msg-empty">
        暂无消息
      </div>
    </div>

    <!-- AI 回复区域 -->
    <div class="reply-section">
      <!-- 生成中 -->
      <div v-if="isGenerating" class="reply-loading">
        <div class="spinner-small" />
        <span>AI 正在生成回复...</span>
      </div>

      <!-- 错误 -->
      <div v-else-if="activeSuggestedReply?.error" class="reply-error">
        <span>{{ activeSuggestedReply.error }}</span>
        <button class="btn-retry" @click="handleGenerate">重试</button>
      </div>

      <!-- 有建议 -->
      <template v-else-if="activeSuggestedReply?.text">
        <!-- 编辑模式 -->
        <div v-if="isEditing" class="reply-edit">
          <textarea
            v-model="editText"
            class="edit-textarea"
            rows="4"
          />
          <div class="edit-actions">
            <button class="btn-save" @click="saveEdit">保存</button>
            <button class="btn-cancel" @click="cancelEdit">取消</button>
          </div>
        </div>

        <!-- 展示模式 -->
        <div v-else class="reply-preview">
          <div class="preview-text" @dblclick="startEdit">{{ activeSuggestedReply.text }}</div>
          <div class="reply-actions">
            <button class="btn-edit" @click="startEdit">编辑</button>
            <button class="btn-inject" @click="handleInject">使用此回复</button>
            <button
              v-if="autoSendEnabled"
              class="btn-auto-send"
              @click="handleAutoSend"
            >
              自动发送
            </button>
          </div>
        </div>
      </template>

      <!-- 空状态 -->
      <div v-else class="reply-empty">
        <button class="btn-generate" @click="handleGenerate">
          生成 AI 回复
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message-panel {
  width: 60%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.recruiter-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.auto-send-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
}

.toggle-label {
  font-size: 11px;
}

/* 消息历史 */
.message-history {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-bubble {
  max-width: 80%;
  padding: 8px 12px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.5;
}

.msg-left {
  align-self: flex-start;
  background: var(--bg-card);
  color: var(--text-primary);
  border-bottom-left-radius: 4px;
}

.msg-right {
  align-self: flex-end;
  background: var(--accent);
  color: white;
  border-bottom-right-radius: 4px;
}

.bubble-time {
  font-size: 10px;
  opacity: 0.7;
  margin-top: 4px;
}

.msg-empty {
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
  padding: 24px;
}

/* AI 回复区域 */
.reply-section {
  border-top: 1px solid var(--border);
  padding: 12px;
  flex-shrink: 0;
}

.reply-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 13px;
}

.spinner-small {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.reply-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #e74c3c;
  font-size: 13px;
}

.btn-retry {
  padding: 4px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.reply-preview .preview-text {
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.5;
  padding: 8px;
  background: var(--bg-base);
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
}

.reply-actions {
  display: flex;
  gap: 8px;
}

.reply-actions button {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-primary);
  transition: background 0.15s ease;
}

.reply-actions button:hover {
  background: var(--bg-hover);
}

.btn-inject {
  background: var(--accent) !important;
  color: white !important;
  border-color: var(--accent) !important;
}

.btn-auto-send {
  background: #27ae60 !important;
  color: white !important;
  border-color: #27ae60 !important;
}

.reply-edit .edit-textarea {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  resize: vertical;
  background: var(--bg-base);
  color: var(--text-primary);
}

.edit-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.edit-actions button {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--border);
}

.btn-save {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.btn-cancel {
  background: var(--bg-card);
  color: var(--text-primary);
}

.reply-empty {
  text-align: center;
}

.btn-generate {
  padding: 8px 20px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.btn-generate:hover {
  opacity: 0.9;
}
</style>
