<script setup lang="ts">
/**
 * 简历结构化数据可折叠区块
 *
 * 职责：
 * - 渲染教育/经历/项目的可折叠区块
 * - 头部显示标题 + 条目数
 * - 点击头部展开/收起内容
 */
import { ref } from 'vue'

const props = defineProps<{
  /** 区块标题 */
  title: string
  /** 条目数量 */
  count: number
  /** 默认是否展开 */
  defaultOpen?: boolean
}>()

const isOpen = ref(props.defaultOpen ?? true)

function toggle() {
  isOpen.value = !isOpen.value
}
</script>

<template>
  <section class="section-card">
    <header class="section-header" @click="toggle">
      <div class="header-left">
        <span class="chevron" :class="{ open: isOpen }">&#9654;</span>
        <h4 class="section-title">{{ title }}</h4>
      </div>
      <span class="count-badge">{{ count }}</span>
    </header>
    <div v-show="isOpen" class="section-body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.section-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s ease;
}

.section-header:hover {
  background: var(--bg-base);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.chevron {
  font-size: 8px;
  color: var(--text-tertiary);
  transition: transform 0.2s ease;
}

.chevron.open {
  transform: rotate(90deg);
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.count-badge {
  padding: 1px 6px;
  background: var(--bg-base);
  color: var(--text-secondary);
  font-size: 11px;
  border-radius: 10px;
}

.section-body {
  padding: 0 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>
