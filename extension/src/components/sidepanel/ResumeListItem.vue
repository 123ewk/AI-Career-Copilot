<script setup lang="ts">
/**
 * 简历列表卡片（展示型组件）
 *
 * 职责：
 * - 渲染单个简历摘要信息：技能标签、工作年限、活跃状态
 * - 点击时通知父组件选中
 */
import type { ResumeSummary } from '../../types/resume'

const props = defineProps<{
  /** 简历摘要数据 */
  resume: ResumeSummary
  /** 是否为当前选中 */
  isSelected: boolean
}>()

const emit = defineEmits<{
  (e: 'select', resumeId: string): void
}>()

/** 格式化工作年限 */
function formatYears(years: number | null): string {
  if (years === null || years === undefined) return '-'
  if (years === 0) return '应届'
  return `${years} 年`
}
</script>

<template>
  <li
    class="resume-item"
    :class="{ selected: isSelected }"
    @click="emit('select', resume.id)"
  >
    <div class="item-top">
      <div class="item-info">
        <span class="years-badge">{{ formatYears(resume.experience_years) }}</span>
        <span v-if="resume.is_active" class="active-badge">活跃</span>
      </div>
    </div>

    <div v-if="resume.skills.length > 0" class="skill-tags">
      <span v-for="(skill, i) in resume.skills.slice(0, 4)" :key="i" class="skill-tag">
        {{ skill }}
      </span>
      <span v-if="resume.skills.length > 4" class="skill-more">+{{ resume.skills.length - 4 }}</span>
    </div>

    <div v-else class="no-skills">暂无技能标签</div>
  </li>
</template>

<style scoped>
.resume-item {
  padding: 10px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.resume-item:hover {
  border-color: var(--accent);
}

.resume-item.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-bg);
}

.item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.item-info {
  display: flex;
  align-items: center;
  gap: 6px;
}

.years-badge {
  padding: 2px 6px;
  background: var(--bg-base);
  color: var(--text-secondary);
  font-size: 11px;
  border-radius: 4px;
}

.active-badge {
  padding: 2px 6px;
  background: #dcfce7;
  color: #166534;
  font-size: 10px;
  font-weight: 500;
  border-radius: 4px;
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.skill-tag {
  padding: 2px 6px;
  background: #eff6ff;
  color: #1e40af;
  font-size: 10px;
  border-radius: 3px;
}

.skill-more {
  padding: 2px 6px;
  background: var(--bg-base);
  color: var(--text-tertiary);
  font-size: 10px;
  border-radius: 3px;
}

.no-skills {
  font-size: 11px;
  color: var(--text-tertiary);
}
</style>
