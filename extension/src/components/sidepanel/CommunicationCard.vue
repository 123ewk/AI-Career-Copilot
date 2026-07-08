<script setup lang="ts">
/**
 * 沟通话术结果卡片
 *
 * 职责：
 * - 展示 AI 生成的沟通话术：招呼语、跟进语、完整对话
 * - 支持一键复制各段话术
 * - 处理 loading / 完成 / 失败 / 未开始四种状态
 */
import { computed, inject, ref, toRef, type Ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useSidePanelStore } from '../../stores/sidepanel'
import { useJobPipeline } from '../../composables/useJobPipeline'

const props = defineProps<{
  /** 岗位 ID */
  jobId: string
}>()

const store = useSidePanelStore()
const { taskResults } = storeToRefs(store)

/** 当前岗位的话术结果 */
const communicationResult = computed(() => taskResults.value[props.jobId]?.communication)

/** 是否加载中 */
const isLoading = computed(
  () => communicationResult.value?.status === 'pending' || communicationResult.value?.status === 'running',
)

/** 是否已完成 */
const isCompleted = computed(() => communicationResult.value?.status === 'completed')

/** 是否失败 */
const isFailed = computed(() => communicationResult.value?.status === 'failed')

/** 话术结果数据（在 computed 内直接收窄联合类型） */
const data = computed(() => {
  const r = communicationResult.value
  return r && r.status === 'completed' ? r.result : null
})

/** 失败信息 */
const errorMessage = computed(() => {
  const r = communicationResult.value
  return r && r.status === 'failed' ? r.errorMessage : null
})

/** 从 JobDetailPanel 注入的统一 sessionId */
const injectedSessionId = inject<Ref<string>>('jobSessionId')

/** 用于手动重试（优先使用注入的 sessionId，保证与 analyze 同 session） */
const { retryStage } = useJobPipeline(
  toRef(() => props.jobId),
  injectedSessionId?.value ?? crypto.randomUUID(),
)

/** 重试话术生成 */
function retry() {
  retryStage('communication')
}

/** 最近一次复制成功的文案类型 */
const copiedType = ref<string | null>(null)

/** 复制文本到剪贴板 */
async function copyText(text: string, type: string) {
  try {
    await navigator.clipboard.writeText(text)
    copiedType.value = type
    // 2 秒后清除提示
    setTimeout(() => {
      copiedType.value = null
    }, 2000)
  } catch (err) {
    console.error('[CommunicationCard] 复制失败:', err)
  }
}
</script>

<template>
  <section class="communication-card">
    <header class="card-header">
      <h3 class="card-title">沟通话术</h3>
      <span v-if="isLoading" class="status-badge running">
        <span class="pulse-dot" />
        生成中
      </span>
      <span v-else-if="isCompleted" class="status-badge completed">已完成</span>
      <span v-else-if="isFailed" class="status-badge failed">失败</span>
      <span v-else class="status-badge pending">待开始</span>
    </header>

    <!-- 加载中 -->
    <div v-if="isLoading" class="card-loading">
      <div class="spinner" />
      <p>正在生成沟通话术…</p>
    </div>

    <!-- 失败 -->
    <div v-else-if="isFailed" class="card-error">
      <p class="error-text">{{ errorMessage }}</p>
      <button class="retry-btn" @click="retry">重试</button>
    </div>

    <!-- 未开始 -->
    <div v-else-if="!isCompleted" class="card-empty">
      <p>完成匹配分析后，将自动生成沟通话术</p>
    </div>

    <!-- 完成 -->
    <div v-else-if="data" class="card-body">
      <div class="script-block">
        <div class="script-header">
          <span class="script-label">招呼语</span>
          <button class="copy-btn" @click="copyText(data.greeting, 'greeting')">
            {{ copiedType === 'greeting' ? '已复制' : '复制' }}
          </button>
        </div>
        <p class="script-text">{{ data.greeting }}</p>
      </div>

      <div class="script-block">
        <div class="script-header">
          <span class="script-label">跟进语</span>
          <button class="copy-btn" @click="copyText(data.follow_up, 'follow_up')">
            {{ copiedType === 'follow_up' ? '已复制' : '复制' }}
          </button>
        </div>
        <p class="script-text">{{ data.follow_up }}</p>
      </div>

      <div class="script-block">
        <div class="script-header">
          <span class="script-label">完整对话</span>
          <button class="copy-btn" @click="copyText(data.full_script, 'full_script')">
            {{ copiedType === 'full_script' ? '已复制' : '复制' }}
          </button>
        </div>
        <p class="script-text full">{{ data.full_script }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.communication-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 500;
}

.status-badge.running {
  background: #dbeafe;
  color: #1e40af;
}

.status-badge.completed {
  background: #dcfce7;
  color: #166534;
}

.status-badge.failed {
  background: #fee2e2;
  color: #991b1b;
}

.status-badge.pending {
  background: var(--bg-base);
  color: var(--text-secondary);
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

.card-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px;
  color: var(--text-secondary);
  font-size: 12px;
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 8px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.card-error {
  padding: 12px;
  background: #fef2f2;
  border-radius: 6px;
}

.error-text {
  color: #991b1b;
  font-size: 12px;
  margin-bottom: 10px;
}

.retry-btn {
  padding: 5px 10px;
  font-size: 11px;
}

.card-empty {
  padding: 16px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 12px;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.script-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  background: var(--bg-base);
  border-radius: 6px;
}

.script-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.script-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.copy-btn {
  width: auto;
  padding: 3px 8px;
  background: transparent;
  color: var(--accent);
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 11px;
}

.copy-btn:hover {
  background: var(--bg-card);
}

.script-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.script-text.full {
  max-height: 160px;
  overflow-y: auto;
}
</style>
