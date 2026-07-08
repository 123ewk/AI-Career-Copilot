<script setup lang="ts">
/**
 * 记录投递按钮
 *
 * 职责：
 * - 判断当前岗位是否可投递（已创建、已补充 JD、未投递过）
 * - 调用 RECORD_APPLICATION 消息通知 SW 记录投递
 * - 成功后更新 Store 防止重复投递
 *
 * 设计动机：
 * - 投递按钮只关心“能否投递”和“记录投递”，不处理后续流程
 * - 禁用条件明确，避免误触导致重复记录
 */
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import type { DisplayJob } from '../../stores/sidepanel'
import { useSidePanelStore } from '../../stores/sidepanel'
import {
  ChromeMessageType,
  sendMessageToBackground,
} from '../../messaging/chrome_message'

const props = defineProps<{
  /** 当前岗位 */
  job: DisplayJob
}>()

const store = useSidePanelStore()
const { taskResults, isApplied } = storeToRefs(store)

/** 是否正在请求中 */
const isSubmitting = ref(false)

/** 提交结果错误信息 */
const submitError = ref<string | null>(null)

/** 当前岗位的匹配结果（用于传给后端） */
const matchResult = computed(() =>
  props.job.id ? taskResults.value[props.job.id]?.match : undefined,
)

/** 匹配分 */
const matchScore = computed(() => {
  if (matchResult.value?.status !== 'completed') return undefined
  return matchResult.value.result.score_detail.combined_score
})

/** 按钮禁用原因 */
const disabledReason = computed((): string | null => {
  if (!props.job.id) return '岗位未创建成功'
  if (!props.job.hasJdText) return '请先打开 Boss 详情面板补充 JD'
  if (isApplied.value(props.job.id)) return '已记录投递'
  if (isSubmitting.value) return '提交中...'
  return null
})

/** 按钮是否禁用 */
const isDisabled = computed(() => disabledReason.value !== null)

/** 按钮文案 */
const buttonText = computed((): string => {
  if (!props.job.id) return '岗位未创建'
  if (!props.job.hasJdText) return '等待补充 JD'
  if (isApplied.value(props.job.id)) return '已投递'
  if (isSubmitting.value) return '提交中...'
  return '记录投递'
})

/** 点击记录投递 */
async function handleApply() {
  if (isDisabled.value || !props.job.id) return

  isSubmitting.value = true
  submitError.value = null

  try {
    const resp = await sendMessageToBackground(ChromeMessageType.RECORD_APPLICATION, {
      jobId: props.job.id,
      matchScore: matchScore.value,
    })

    if (!resp.ok) {
      submitError.value = resp.error ?? '记录投递失败'
      return
    }

    store.markApplied(props.job.id)
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : '网络异常，请重试'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <section class="apply-section">
    <button
      class="apply-btn"
      :class="{ applied: job.id && isApplied(job.id) }"
      :disabled="isDisabled"
      :title="disabledReason ?? ''"
      @click="handleApply"
    >
      {{ buttonText }}
    </button>
    <p v-if="submitError" class="error-msg">{{ submitError }}</p>
    <p v-else-if="matchScore != null" class="match-score-hint">
      当前匹配分 {{ matchScore.toFixed(1) }} 分
    </p>
  </section>
</template>

<style scoped>
.apply-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
}

.apply-btn {
  width: 100%;
  padding: 10px 16px;
  background: var(--accent);
  color: #ffffff;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease;
}

.apply-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.apply-btn:disabled {
  background: var(--bg-base);
  color: var(--text-secondary);
  cursor: not-allowed;
}

.apply-btn.applied {
  background: #dcfce7;
  color: #166534;
}

.error-msg {
  margin: 0;
  padding: 8px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 11px;
  border-radius: 4px;
}

.match-score-hint {
  margin: 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 11px;
}
</style>
