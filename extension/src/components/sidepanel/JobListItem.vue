<script setup lang="ts">
/**
 * 单个岗位卡片
 *
 * 职责：
 * - 展示岗位标题、公司、薪资、地点、标签
 * - 展示创建状态徽标（已创建 / 失败 / 创建中）
 * - 响应点击事件，高亮选中态
 *
 * 设计动机：
 * - 卡片化设计，信息密度适中，便于快速浏览
 * - 选中态与未选中态视觉区分明显
 */
import type { DisplayJob } from '../../stores/sidepanel'

const props = defineProps<{
  /** 岗位数据 */
  job: DisplayJob
  /** 是否被选中 */
  isSelected: boolean
}>()

const emit = defineEmits<{
  /** 点击选中 */
  (e: 'select', sourceUrl: string): void
}>()

/** 点击卡片 */
function onClick() {
  emit('select', props.job.sourceUrl)
}

/** 根据创建状态返回徽标样式 */
function statusBadgeClass() {
  switch (props.job.createStatus) {
    case 'created':
      return 'created'
    case 'failed':
      return 'failed'
    case 'creating':
    case 'pending':
    default:
      return 'pending'
  }
}

/** 根据创建状态返回徽标文案 */
function statusBadgeText() {
  switch (props.job.createStatus) {
    case 'created':
      return '已创建'
    case 'failed':
      return '失败'
    case 'creating':
      return '创建中'
    case 'pending':
    default:
      return '待创建'
  }
}
</script>

<template>
  <li
    class="job-item"
    :class="{ selected: isSelected }"
    @click="onClick"
  >
    <div class="job-main">
      <h3 class="job-title" :title="job.title">{{ job.title }}</h3>
      <span class="status-badge" :class="statusBadgeClass()">{{ statusBadgeText() }}</span>
    </div>
    <div class="job-company">{{ job.company }}</div>
    <div v-if="job.salaryRaw || job.location" class="job-meta">
      <span v-if="job.salaryRaw" class="job-salary">{{ job.salaryRaw }}</span>
      <span v-if="job.location" class="job-location">{{ job.location }}</span>
    </div>
    <div v-if="job.tags.length > 0" class="job-tags">
      <span v-for="(tag, index) in job.tags.slice(0, 3)" :key="index" class="tag">{{ tag }}</span>
      <span v-if="job.tags.length > 3" class="tag more">+{{ job.tags.length - 3 }}</span>
    </div>
    <div v-if="job.createError" class="job-error" :title="job.createError">
      {{ job.createError }}
    </div>
  </li>
</template>

<style scoped>
.job-item {
  padding: 10px;
  margin-bottom: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.job-item:hover {
  border-color: var(--accent);
}

.job-item.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-bg);
}

.job-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.job-title {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.status-badge {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 9px;
  font-weight: 500;
  line-height: 14px;
}

.status-badge.created {
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

.job-company {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.job-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 11px;
}

.job-salary {
  color: #ea580c;
  font-weight: 500;
}

.job-location {
  color: var(--text-secondary);
}

.job-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tag {
  padding: 2px 5px;
  background: var(--bg-base);
  color: var(--text-secondary);
  font-size: 10px;
  border-radius: 4px;
}

.tag.more {
  background: transparent;
  color: var(--text-tertiary);
}

.job-error {
  margin-top: 6px;
  padding: 6px 8px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 10px;
  border-radius: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
