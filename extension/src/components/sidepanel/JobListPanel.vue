<script setup lang="ts">
/**
 * 岗位列表容器
 *
 * 职责：
 * - 从 Pinia Store 读取岗位列表
 * - 渲染 JobListItem 卡片列表
 * - 处理选中、空状态、统计摘要
 *
 * 设计动机：
 * - 列表与详情解耦：JobListPanel 只负责列表渲染和选中回调
 * - 独立的滚动容器，避免长列表影响详情区
 */
import { storeToRefs } from 'pinia'
import { useSidePanelStore } from '../../stores/sidepanel'
import JobListItem from './JobListItem.vue'

const store = useSidePanelStore()
const { jobs, selectedSourceUrl, createdJobCount, failedJobCount } = storeToRefs(store)

/** 选中岗位 */
function selectJob(sourceUrl: string) {
  store.selectJob(sourceUrl)
}
</script>

<template>
  <div class="job-list-panel">
    <div class="list-header">
      <h2 class="list-title">岗位列表</h2>
      <span class="list-summary">
        {{ jobs.length }} 个 · {{ createdJobCount }} 已创建 · {{ failedJobCount }} 失败
      </span>
    </div>

    <div v-if="jobs.length === 0" class="list-empty">
      <p>暂无岗位数据</p>
      <p class="empty-hint">请打开 Boss 直聘列表页，系统将自动提取</p>
    </div>

    <ul v-else class="job-list">
      <JobListItem
        v-for="job in jobs"
        :key="job.sourceUrl"
        :job="job"
        :is-selected="job.sourceUrl === selectedSourceUrl"
        @select="selectJob"
      />
    </ul>
  </div>
</template>

<style scoped>
.job-list-panel {
  width: 40%;
  min-width: 150px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  background: var(--bg-base);
  overflow: hidden;
}

.list-header {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-card);
  flex-shrink: 0;
}

.list-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.list-summary {
  font-size: 11px;
  color: var(--text-secondary);
}

.list-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 12px;
}

.empty-hint {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.job-list {
  flex: 1;
  list-style: none;
  margin: 0;
  padding: 8px;
  overflow-y: auto;
  min-height: 0;
}
</style>
