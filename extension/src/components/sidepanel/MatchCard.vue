<script setup lang="ts">
/**
 * 简历匹配结果卡片
 *
 * 职责：
 * - 展示匹配结果：综合分、BM25/语义子分、命中技能、缺失技能、建议
 * - 处理 loading / 完成 / 失败 / 未开始四种状态
 * - 提供手动重试按钮
 */
import { computed, inject, toRef, type Ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useSidePanelStore } from '../../stores/sidepanel'
import { useJobPipeline } from '../../composables/useJobPipeline'

const props = defineProps<{
  /** 岗位 ID */
  jobId: string
}>()

const store = useSidePanelStore()
const { taskResults } = storeToRefs(store)

/** 当前岗位的匹配结果 */
const matchResult = computed(() => taskResults.value[props.jobId]?.match)

/** 是否加载中 */
const isLoading = computed(
  () => matchResult.value?.status === 'pending' || matchResult.value?.status === 'running',
)

/** 是否已完成 */
const isCompleted = computed(() => matchResult.value?.status === 'completed')

/** 是否失败 */
const isFailed = computed(() => matchResult.value?.status === 'failed')

/** 匹配结果数据（在 computed 内直接收窄联合类型） */
const data = computed(() => {
  const r = matchResult.value
  return r && r.status === 'completed' ? r.result : null
})

/** 失败信息 */
const errorMessage = computed(() => {
  const r = matchResult.value
  return r && r.status === 'failed' ? r.errorMessage : null
})

/** 从 JobDetailPanel 注入的统一 sessionId */
const injectedSessionId = inject<Ref<string>>('jobSessionId')

/** 用于手动重试（match 为同步接口，sessionId 仅作兼容） */
const { retryStage } = useJobPipeline(
  toRef(() => props.jobId),
  injectedSessionId?.value ?? crypto.randomUUID(),
)

/** 重试匹配 */
function retry() {
  retryStage('match')
}

/** 根据分数返回颜色类 */
function scoreClass(score: number): string {
  if (score >= 80) return 'high'
  if (score >= 60) return 'medium'
  return 'low'
}
</script>

<template>
  <section class="match-card">
    <header class="card-header">
      <h3 class="card-title">匹配分析</h3>
      <span v-if="isLoading" class="status-badge running">
        <span class="pulse-dot" />
        计算中
      </span>
      <span v-else-if="isCompleted" class="status-badge completed">已完成</span>
      <span v-else-if="isFailed" class="status-badge failed">失败</span>
      <span v-else class="status-badge pending">待开始</span>
    </header>

    <!-- 加载中 -->
    <div v-if="isLoading" class="card-loading">
      <div class="spinner" />
      <p>正在计算简历与岗位匹配度…</p>
    </div>

    <!-- 失败 -->
    <div v-else-if="isFailed" class="card-error">
      <p class="error-text">{{ errorMessage }}</p>
      <button class="retry-btn" @click="retry">重试</button>
    </div>

    <!-- 未开始 -->
    <div v-else-if="!isCompleted" class="card-empty">
      <p>完成 JD 分析后，将自动计算匹配度</p>
    </div>

    <!-- 完成 -->
    <div v-else-if="data" class="card-body">
      <div class="score-section">
        <div class="score-main">
          <span class="score-value" :class="scoreClass(data.score_detail.combined_score)">
            {{ data.score_detail.combined_score.toFixed(1) }}
          </span>
          <span class="score-label">综合匹配分</span>
        </div>
        <div class="score-breakdown">
          <div class="sub-score">
            <span class="sub-label">关键词</span>
            <span class="sub-value">{{ data.score_detail.bm25_score.toFixed(1) }}</span>
          </div>
          <div class="sub-score">
            <span class="sub-label">语义</span>
            <span class="sub-value">{{ data.score_detail.semantic_score.toFixed(1) }}</span>
          </div>
        </div>
      </div>

      <div v-if="data.matched_skills.length > 0" class="info-block">
        <span class="info-label">命中技能</span>
        <div class="tag-list matched">
          <span v-for="(skill, index) in data.matched_skills" :key="index" class="info-tag">{{ skill }}</span>
        </div>
      </div>

      <div v-if="data.missing_skills.length > 0" class="info-block">
        <span class="info-label">缺失技能</span>
        <div class="tag-list missing">
          <span v-for="(skill, index) in data.missing_skills" :key="index" class="info-tag">{{ skill }}</span>
        </div>
      </div>

      <div v-if="data.suggestions.length > 0" class="info-block">
        <span class="info-label">投递建议</span>
        <ul class="bullet-list">
          <li v-for="(item, index) in data.suggestions" :key="index">{{ item }}</li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.match-card {
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

.score-section {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: var(--bg-base);
  border-radius: 6px;
}

.score-main {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.score-value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}

.score-value.high {
  color: var(--success);
}

.score-value.medium {
  color: var(--warning);
}

.score-value.low {
  color: var(--error);
}

.score-label {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-secondary);
}

.score-breakdown {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sub-score {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  min-width: 80px;
}

.sub-label {
  color: var(--text-secondary);
}

.sub-value {
  font-weight: 600;
  color: var(--text-primary);
}

.info-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.info-tag {
  padding: 3px 7px;
  font-size: 11px;
  border-radius: 4px;
}

.tag-list.matched .info-tag {
  background: #dcfce7;
  color: #166534;
}

.tag-list.missing .info-tag {
  background: #fee2e2;
  color: #991b1b;
}

.bullet-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: var(--text-primary);
}

.bullet-list li {
  margin-bottom: 4px;
}
</style>
