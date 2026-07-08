<script setup lang="ts">
/**
 * JD 分析结果卡片
 *
 * 职责：
 * - 展示 AI 对 JD 的分析结果：技能、关键词、资历、难度、薪资区间、公司信息、隐藏要求
 * - 处理 loading / 完成 / 失败 / 未开始四种状态
 * - 提供手动重试按钮
 *
 * 设计动机：
 * - 卡片独立负责自己的渲染和重试，不依赖父组件传递具体结果
 * - 统一从 Store 读取任务状态，保持数据一致性
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

/** 当前岗位的分析结果 */
const analyzeResult = computed(() => taskResults.value[props.jobId]?.analyze)

/** 是否加载中 */
const isLoading = computed(
  () => analyzeResult.value?.status === 'pending' || analyzeResult.value?.status === 'running',
)

/** 是否已完成 */
const isCompleted = computed(() => analyzeResult.value?.status === 'completed')

/** 是否失败 */
const isFailed = computed(() => analyzeResult.value?.status === 'failed')

/** 分析结果数据（在 computed 内直接收窄联合类型，避免 TS 无法推断 result 字段） */
const data = computed(() => {
  const r = analyzeResult.value
  return r && r.status === 'completed' ? r.result : null
})

/** 失败信息 */
const errorMessage = computed(() => {
  const r = analyzeResult.value
  return r && r.status === 'failed' ? r.errorMessage : null
})

/** 从 JobDetailPanel 注入的统一 sessionId，保证 analyze/communication 使用同一 session */
const injectedSessionId = inject<Ref<string>>('jobSessionId')

/** 用于手动重试的流水线（优先使用注入的 sessionId，回退到新的 UUID） */
const { retryStage } = useJobPipeline(
  toRef(() => props.jobId),
  injectedSessionId?.value ?? crypto.randomUUID(),
)

/** 重试分析 */
function retry() {
  retryStage('analyze')
}

/** 将难度枚举转换为可读文案 */
function formatDifficulty(difficulty: string | null | undefined): string {
  const map: Record<string, string> = {
    easy: '简单',
    medium: '中等',
    hard: '困难',
    expert: '专家',
  }
  return difficulty ? map[difficulty] ?? difficulty : '-'
}

/** 将资历枚举转换为可读文案 */
function formatSeniority(seniority: string | null | undefined): string {
  const map: Record<string, string> = {
    intern: '实习',
    entry: '应届',
    junior: '初级',
    mid: '中级',
    senior: '高级',
    lead: '负责人',
    principal: '专家',
  }
  return seniority ? map[seniority] ?? seniority : '-'
}

/** 格式化薪资区间 */
function formatSalaryRange(min?: number, max?: number, unit?: string): string {
  if (min == null || max == null) return '-'
  return `${min}-${max} ${unit ?? ''}`
}
</script>

<template>
  <section class="analysis-card">
    <header class="card-header">
      <h3 class="card-title">JD 分析</h3>
      <span v-if="isLoading" class="status-badge running">
        <span class="pulse-dot" />
        分析中
      </span>
      <span v-else-if="isCompleted" class="status-badge completed">已完成</span>
      <span v-else-if="isFailed" class="status-badge failed">失败</span>
      <span v-else class="status-badge pending">待开始</span>
    </header>

    <!-- 加载中 -->
    <div v-if="isLoading" class="card-loading">
      <div class="spinner" />
      <p>正在分析 JD，提取技能与要求…</p>
    </div>

    <!-- 失败 -->
    <div v-else-if="isFailed" class="card-error">
      <p class="error-text">{{ errorMessage }}</p>
      <button class="retry-btn" @click="retry">重试</button>
    </div>

    <!-- 未开始 -->
    <div v-else-if="!isCompleted" class="card-empty">
      <p>点击岗位卡片并打开 Boss 详情面板，将自动分析 JD</p>
    </div>

    <!-- 完成 -->
    <div v-else-if="data" class="card-body">
      <div class="info-row">
        <span class="info-label">资历要求</span>
        <span class="info-value">{{ formatSeniority(data.seniority) }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">难度评级</span>
        <span class="info-value">{{ formatDifficulty(data.difficulty) }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">薪资区间</span>
        <span class="info-value">{{ formatSalaryRange(data.salary_range?.min, data.salary_range?.max, data.salary_range?.unit) }}</span>
      </div>

      <div v-if="data.company_info" class="info-row">
        <span class="info-label">公司信息</span>
        <span class="info-value">
          {{ [data.company_info.industry, data.company_info.scale, data.company_info.stage].filter(Boolean).join(' · ') || '-' }}
        </span>
      </div>

      <div v-if="data.skills.length > 0" class="info-block">
        <span class="info-label">技能要求</span>
        <div class="tag-list">
          <span v-for="(skill, index) in data.skills" :key="index" class="info-tag">{{ skill }}</span>
        </div>
      </div>

      <div v-if="data.keywords.length > 0" class="info-block">
        <span class="info-label">关键词</span>
        <div class="tag-list">
          <span v-for="(kw, index) in data.keywords" :key="index" class="info-tag">{{ kw }}</span>
        </div>
      </div>

      <div v-if="data.hidden_requirements.length > 0" class="info-block">
        <span class="info-label">隐藏要求</span>
        <ul class="bullet-list">
          <li v-for="(item, index) in data.hidden_requirements" :key="index">{{ item }}</li>
        </ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.analysis-card {
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
  gap: 10px;
}

.info-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.info-label {
  color: var(--text-secondary);
  flex-shrink: 0;
  min-width: 60px;
}

.info-value {
  color: var(--text-primary);
  font-weight: 500;
}

.info-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.info-block .info-label {
  min-width: auto;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.info-tag {
  padding: 3px 7px;
  background: #eff6ff;
  color: #1e40af;
  font-size: 11px;
  border-radius: 4px;
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
