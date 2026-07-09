<script setup lang="ts">
/**
 * 岗位详情容器
 *
 * 职责：
 * - 展示选中岗位的基础信息（标题/公司/薪资/地点/标签）
 * - 管理分析流水线生命周期：JD 补充完成后自动触发 analyze → match → communication
 * - 嵌入 AnalysisCard / MatchCard / CommunicationCard / ApplyButton
 *
 * 设计动机：
 * - 每个详情面板实例独立管理一个岗位的流水线，组件卸载时自动清理监听器
 * - 流水线启动条件：岗位已补充 JD（hasJdText）且 analyze 未开始/未进行中
 */
import { computed, onMounted, onUnmounted, provide, ref, toRef, watch } from 'vue'
import type { DisplayJob } from '../../stores/sidepanel'
import { useJobPipeline } from '../../composables/useJobPipeline'
import {
  ChromeMessageType,
  sendMessageToBackground,
} from '../../messaging/chrome_message'
import AnalysisCard from './AnalysisCard.vue'
import MatchCard from './MatchCard.vue'
import CommunicationCard from './CommunicationCard.vue'
import ApplyButton from './ApplyButton.vue'

const props = defineProps<{
  /** 当前选中的岗位 */
  job: DisplayJob
}>()

/** 当前岗位的 jobId（可能未创建成功） */
const jobId = computed(() => props.job.id)

/** 当前会话 ID，用于 analyze 和 communication */
const sessionId = ref(crypto.randomUUID())

/** 向子组件（AnalysisCard / CommunicationCard）提供统一 sessionId，确保重试时 session 一致 */
provide('jobSessionId', sessionId)

/** 岗位 ID 的响应式引用，传给 useJobPipeline */
const jobIdRef = toRef(() => props.job.id)

/** 使用流水线 composable */
const { startPipeline, dispose } = useJobPipeline(jobIdRef, sessionId.value)

/**
 * 尝试启动流水线
 *
 * 条件：
 * - 岗位已创建成功（有 jobId）
 * - 详情面板 JD 已补充（hasJdText）
 * - analyze 任务尚未开始或不在进行中
 */
function tryStartPipeline() {
  const id = jobId.value
  if (!id || !props.job.hasJdText) return

  // 如果 analyze 已经有结果或正在运行，不再重复启动
  // 由 useJobPipeline 内部再次检查 running 状态
  startPipeline()
}

onMounted(() => {
  tryStartPipeline()
})

// 监听 hasJdText 变化，补充 JD 后自动启动流水线
watch(
  () => props.job.hasJdText,
  (hasJdText) => {
    if (hasJdText) {
      tryStartPipeline()
    }
  },
)

onUnmounted(() => {
  dispose()
})

// ==================== 手动触发 Boss 详情加载 ====================

/** 是否正在请求 Content Script 点击 Boss 卡片 */
const isLoadingDetail = ref(false)
/** 手动触发后的错误提示 */
const loadDetailError = ref<string | null>(null)

/**
 * 请求 Content Script 在 Boss 页面点击对应岗位卡片
 *
 * 原因：
 * - 原流程需要用户先在 SidePanel 点击卡片，再手动点击 Boss 页面同名卡片，两步操作
 * - 本函数把第二步自动化：SidePanel 发送 LOAD_JOB_DETAIL 消息给 Service Worker，
 *   Service Worker 转发给 Content Script，Content Script 找到匹配的 .job-card-box 并点击
 * - 点击后 Boss 详情面板加载 JD，Content Script 自动提取并发送 JOB_DETAIL_EXTRACTED，
 *   最终 SidePanel 收到 JOB_DETAIL_PATCHED，hasJdText 置为 true，自动触发 analyze
 */
async function handleLoadDetail() {
  const sourceUrl = props.job.sourceUrl
  if (!sourceUrl) {
    loadDetailError.value = '岗位 sourceUrl 为空，无法定位 Boss 页面卡片'
    return
  }

  isLoadingDetail.value = true
  loadDetailError.value = null

  try {
    const resp = await sendMessageToBackground(ChromeMessageType.LOAD_JOB_DETAIL, {
      sourceUrl,
    })
    if (!resp.ok) {
      loadDetailError.value = resp.error ?? '加载 Boss 详情失败'
    }
  } catch (err) {
    loadDetailError.value = err instanceof Error ? err.message : String(err)
  } finally {
    isLoadingDetail.value = false
  }
}

/**
 * 监听 Enter 键，快速触发详情加载
 *
 * 仅在详情面板聚焦且 JD 未加载时响应，避免全局拦截影响其他输入
 */
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !props.job.hasJdText && !isLoadingDetail.value) {
    void handleLoadDetail()
  }
}
</script>

<template>
  <div
    class="job-detail-panel"
    tabindex="0"
    @keydown="onKeydown"
  >
    <!-- 岗位基础信息 -->
    <div class="detail-header">
      <h2 class="detail-title">{{ job.title }}</h2>
      <div class="detail-company">{{ job.company }}</div>
      <div class="detail-meta">
        <span v-if="job.salaryRaw" class="detail-salary">{{ job.salaryRaw }}</span>
        <span v-if="job.location" class="detail-location">{{ job.location }}</span>
      </div>
      <div v-if="job.tags.length > 0" class="detail-tags">
        <span v-for="(tag, index) in job.tags" :key="index" class="tag">{{ tag }}</span>
      </div>
      <div v-if="!job.id" class="detail-warning">
        ⚠️ 岗位未创建成功，无法进行分析与投递
      </div>
      <div v-else-if="!job.hasJdText" class="detail-hint">
        <span class="pulse-dot" />
        点击下方按钮或按 Enter，自动在 Boss 页面加载详情并触发 AI 分析
      </div>
      <button
        v-if="job.id && !job.hasJdText"
        class="load-detail-btn"
        :disabled="isLoadingDetail"
        @click="handleLoadDetail"
      >
        <span v-if="isLoadingDetail" class="btn-spinner" />
        <span v-else>加载 Boss 详情并分析</span>
      </button>
      <div v-if="loadDetailError" class="detail-error">
        {{ loadDetailError }}
      </div>
    </div>

    <!-- 结果卡片区 -->
    <div v-if="job.id" class="detail-cards">
      <AnalysisCard :job-id="job.id" />
      <MatchCard :job-id="job.id" />
      <CommunicationCard :job-id="job.id" />
      <ApplyButton :job="job" />
    </div>
  </div>
</template>

<style scoped>
.job-detail-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg-base);
  overflow-y: auto;
}

.detail-header {
  padding: 12px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.detail-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
  line-height: 1.4;
}

.detail-company {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 12px;
}

.detail-salary {
  color: #ea580c;
  font-weight: 500;
}

.detail-location {
  color: var(--text-secondary);
}

.detail-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag {
  padding: 3px 7px;
  background: var(--bg-base);
  color: var(--text-secondary);
  font-size: 11px;
  border-radius: 4px;
}

.detail-warning {
  margin-top: 10px;
  padding: 8px 10px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 12px;
  border-radius: 6px;
}

.detail-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 8px 10px;
  background: #eff6ff;
  color: #1e40af;
  font-size: 12px;
  border-radius: 6px;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--info);
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

.detail-cards {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.load-detail-btn {
  margin-top: 10px;
  width: 100%;
  padding: 8px 12px;
  background: var(--accent);
  color: #ffffff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.load-detail-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.load-detail-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.detail-error {
  margin-top: 8px;
  padding: 8px 10px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 11px;
  border-radius: 6px;
}
</style>
