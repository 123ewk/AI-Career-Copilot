<script setup lang="ts">
/**
 * 简历 Tab 容器组件
 *
 * 职责：
 * - 组合 ResumeListPanel（左栏）和 ResumeDetailPanel（右栏）
 * - 左右分栏布局，与 Job Tab 保持一致
 */
import ResumeListPanel from './ResumeListPanel.vue'
import ResumeDetailPanel from './ResumeDetailPanel.vue'
</script>

<template>
  <div class="resume-tab-content">
    <ResumeListPanel />
    <ResumeDetailPanel />
  </div>
</template>

<style scoped>
.resume-tab-content {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}
</style>
