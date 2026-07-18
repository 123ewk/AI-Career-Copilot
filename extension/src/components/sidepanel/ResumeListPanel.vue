<script setup lang="ts">
/**
 * 简历列表面板（左栏）
 *
 * 职责：
 * - 渲染简历列表（ResumeListItem）
 * - 提供上传按钮（隐藏 file input + 触发按钮）
 * - 显示加载/空/错误状态
 * - 上传成功后自动刷新列表
 */
import { ref, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useResumeStore } from '../../stores/resume'
import ResumeListItem from './ResumeListItem.vue'

const store = useResumeStore()
const {
  resumes,
  selectedResumeId,
  isLoadingList,
  isUploading,
  uploadError,
  listError,
} = storeToRefs(store)

const fileInputRef = ref<HTMLInputElement | null>(null)

onMounted(() => {
  // 首次进入 Tab 时拉取列表
  if (resumes.value.length === 0) {
    void store.fetchResumes()
  }
})

/** 触发文件选择 */
function triggerUpload() {
  fileInputRef.value?.click()
}

/** 文件选择后上传 */
async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  await store.uploadResume(file)

  // 清空 input，允许重复上传同一文件
  input.value = ''
}
</script>

<template>
  <div class="list-panel">
    <!-- 头部：上传按钮 -->
    <header class="list-header">
      <button class="upload-btn" :disabled="isUploading" @click="triggerUpload">
        <span v-if="isUploading" class="btn-spinner" />
        {{ isUploading ? '上传中...' : '上传简历' }}
      </button>
      <input
        ref="fileInputRef"
        type="file"
        accept=".pdf,.docx"
        hidden
        @change="onFileChange"
      />
    </header>

    <!-- 上传错误提示 -->
    <div v-if="uploadError" class="error-banner">
      {{ uploadError }}
    </div>

    <!-- 列表错误提示 -->
    <div v-if="listError" class="error-banner">
      {{ listError }}
    </div>

    <!-- 加载中 -->
    <div v-if="isLoadingList && resumes.length === 0" class="list-loading">
      <div class="spinner" />
    </div>

    <!-- 空状态 -->
    <div v-else-if="resumes.length === 0" class="list-empty">
      <div class="empty-icon">&#128196;</div>
      <p class="empty-title">暂无简历</p>
      <p class="empty-hint">上传 PDF 或 DOCX 文件开始使用</p>
    </div>

    <!-- 列表 -->
    <ul v-else class="resume-list">
      <ResumeListItem
        v-for="resume in resumes"
        :key="resume.id"
        :resume="resume"
        :is-selected="resume.id === selectedResumeId"
        @select="store.selectResume"
      />
    </ul>
  </div>
</template>

<style scoped>
.list-panel {
  width: 40%;
  min-width: 150px;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  overflow: hidden;
}

.list-header {
  padding: 10px 12px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.upload-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--accent);
  color: #ffffff;
  border: none;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.upload-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}

.upload-btn:disabled {
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
  to { transform: rotate(360deg); }
}

.error-banner {
  padding: 8px 12px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 11px;
  border-bottom: 1px solid #fecaca;
}

.list-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.list-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px;
  text-align: center;
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.empty-hint {
  font-size: 11px;
  color: var(--text-secondary);
}

.resume-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  margin: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
</style>
