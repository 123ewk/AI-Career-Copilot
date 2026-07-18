<script setup lang="ts">
/**
 * 简历详情面板（右栏）
 *
 * 职责：
 * - 展示选中简历的结构化数据：技能、教育、经历、项目
 * - 提供"设为活跃"和"删除"操作
 * - 处理加载中/空状态
 */
import { storeToRefs } from 'pinia'
import { useResumeStore } from '../../stores/resume'
import ResumeSectionCard from './ResumeSectionCard.vue'
const store = useResumeStore()
const { resumeDetail, isLoadingDetail, selectedResumeId } = storeToRefs(store)

/** 当前简历是否为活跃简历 */
const isActive = computed(() => resumeDetail.value?.is_active ?? false)

/** 格式化日期范围 */
function formatDateRange(start?: string | null, end?: string | null): string {
  if (!start && !end) return ''
  if (start && end) return `${start} - ${end}`
  if (start) return `${start} - 至今`
  return `至 ${end}`
}

/** 设为活跃 */
async function onSetActive() {
  if (!selectedResumeId.value) return
  await store.setActiveResume(selectedResumeId.value)
}

/** 删除简历 */
async function onDelete() {
  if (!selectedResumeId.value) return
  if (!window.confirm('确定要删除这份简历吗？')) return
  await store.deleteResume(selectedResumeId.value)
}

import { computed } from 'vue'
</script>

<template>
  <!-- 加载中 -->
  <div v-if="isLoadingDetail" class="detail-panel">
    <div class="detail-loading">
      <div class="spinner" />
      <p>加载简历详情...</p>
    </div>
  </div>

  <!-- 无详情 -->
  <div v-else-if="!resumeDetail" class="detail-panel">
    <div class="detail-empty">
      <div class="empty-icon">&#128196;</div>
      <p class="empty-title">选择左侧简历查看详情</p>
    </div>
  </div>

  <!-- 详情内容 -->
  <div v-else class="detail-panel">
    <!-- 头部操作区 -->
    <header class="detail-header">
      <div class="header-actions">
        <button
          v-if="!isActive"
          class="action-btn primary"
          @click="onSetActive"
        >
          设为活跃
        </button>
        <span v-else class="active-label">当前活跃简历</span>
        <button class="action-btn danger" @click="onDelete">删除</button>
      </div>
    </header>

    <!-- 技能标签 -->
    <div v-if="resumeDetail.skills.length > 0" class="skills-section">
      <h4 class="section-label">技能标签</h4>
      <div class="tag-list">
        <span v-for="(skill, i) in resumeDetail.skills" :key="i" class="skill-tag">{{ skill }}</span>
      </div>
    </div>

    <!-- 结构化数据 -->
    <div class="sections">
      <!-- 教育经历 -->
      <ResumeSectionCard
        v-if="resumeDetail.structured_data.education.length > 0"
        title="教育经历"
        :count="resumeDetail.structured_data.education.length"
        :default-open="true"
      >
        <div
          v-for="(edu, i) in resumeDetail.structured_data.education"
          :key="i"
          class="item-card"
        >
          <div class="item-header">
            <span class="item-title">{{ edu.school }}</span>
            <span class="item-date">{{ formatDateRange(edu.start_date, edu.end_date) }}</span>
          </div>
          <div v-if="edu.degree || edu.major" class="item-sub">
            {{ [edu.degree, edu.major].filter(Boolean).join(' · ') }}
          </div>
          <p v-if="edu.description" class="item-desc">{{ edu.description }}</p>
        </div>
      </ResumeSectionCard>

      <!-- 工作经历 -->
      <ResumeSectionCard
        v-if="resumeDetail.structured_data.experience.length > 0"
        title="工作经历"
        :count="resumeDetail.structured_data.experience.length"
        :default-open="true"
      >
        <div
          v-for="(exp, i) in resumeDetail.structured_data.experience"
          :key="i"
          class="item-card"
        >
          <div class="item-header">
            <span class="item-title">{{ exp.company }}</span>
            <span class="item-date">{{ formatDateRange(exp.start_date, exp.end_date) }}</span>
          </div>
          <div v-if="exp.position" class="item-sub">{{ exp.position }}</div>
          <p v-if="exp.description" class="item-desc">{{ exp.description }}</p>
        </div>
      </ResumeSectionCard>

      <!-- 项目经历 -->
      <ResumeSectionCard
        v-if="resumeDetail.structured_data.projects.length > 0"
        title="项目经历"
        :count="resumeDetail.structured_data.projects.length"
        :default-open="true"
      >
        <div
          v-for="(proj, i) in resumeDetail.structured_data.projects"
          :key="i"
          class="item-card"
        >
          <div class="item-header">
            <span class="item-title">{{ proj.name }}</span>
            <span class="item-date">{{ formatDateRange(proj.start_date, proj.end_date) }}</span>
          </div>
          <div v-if="proj.role" class="item-sub">{{ proj.role }}</div>
          <p v-if="proj.description" class="item-desc">{{ proj.description }}</p>
          <div v-if="proj.tech_stack.length > 0" class="tech-tags">
            <span v-for="(tech, j) in proj.tech_stack" :key="j" class="tech-tag">{{ tech }}</span>
          </div>
        </div>
      </ResumeSectionCard>

      <!-- 无结构化数据 -->
      <div
        v-if="resumeDetail.structured_data.education.length === 0 && resumeDetail.structured_data.experience.length === 0 && resumeDetail.structured_data.projects.length === 0"
        class="no-structured"
      >
        <p>暂无结构化数据</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.detail-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
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
  to { transform: rotate(360deg); }
}

.detail-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  color: var(--text-secondary);
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 13px;
  color: var(--text-primary);
}

.detail-header {
  padding: 12px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-btn {
  padding: 6px 12px;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.action-btn.primary {
  background: var(--accent);
  color: #ffffff;
}

.action-btn.primary:hover {
  background: var(--accent-hover);
}

.action-btn.danger {
  background: #fef2f2;
  color: #991b1b;
}

.action-btn.danger:hover {
  background: #fee2e2;
}

.active-label {
  font-size: 12px;
  color: #166534;
  font-weight: 500;
}

.skills-section {
  padding: 12px;
  border-bottom: 1px solid var(--border);
}

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0 0 8px;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.skill-tag {
  padding: 3px 7px;
  background: #eff6ff;
  color: #1e40af;
  font-size: 11px;
  border-radius: 4px;
}

.sections {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.item-card {
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}

.item-card:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.item-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.item-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.item-date {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.item-sub {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.item-desc {
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.5;
  margin: 4px 0 0;
  white-space: pre-wrap;
}

.tech-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.tech-tag {
  padding: 2px 6px;
  background: #f0fdf4;
  color: #166534;
  font-size: 10px;
  border-radius: 3px;
}

.no-structured {
  padding: 20px;
  text-align: center;
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
