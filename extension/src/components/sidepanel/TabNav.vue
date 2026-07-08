<script setup lang="ts">
/**
 * SidePanel Tab 导航组件
 *
 * 职责：
 * - 渲染顶部一级 Tab 导航：岗位 / 沟通 / 简历 / 设置
 * - 高亮当前激活 Tab，使用胶囊下划线激活态
 * - Step 5 仅「岗位」Tab 可交互，其余 Tab 禁用并显示占位
 *
 * 设计动机：
 * - 与 ui-plugin-design.md 的导航架构对齐
 * - 为后续聊天、简历、设置 Tab 预留扩展入口
 */

/** Tab 配置 */
interface TabItem {
  key: 'jobs' | 'chat' | 'resume' | 'settings'
  label: string
  enabled: boolean
}

const tabs: TabItem[] = [
  { key: 'jobs', label: '岗位', enabled: true },
  { key: 'chat', label: '沟通', enabled: false },
  { key: 'resume', label: '简历', enabled: false },
  { key: 'settings', label: '设置', enabled: false },
]

const props = defineProps<{
  /** 当前激活的 Tab */
  activeTab: 'jobs' | 'chat' | 'resume' | 'settings'
}>()

const emit = defineEmits<{
  /** Tab 切换事件 */
  (e: 'update:activeTab', tab: 'jobs' | 'chat' | 'resume' | 'settings'): void
}>()

/** 点击 Tab */
function onClickTab(tab: TabItem) {
  if (!tab.enabled) return
  emit('update:activeTab', tab.key)
}
</script>

<template>
  <nav class="tab-nav">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      class="tab-item"
      :class="{ active: props.activeTab === tab.key, disabled: !tab.enabled }"
      :disabled="!tab.enabled"
      @click="onClickTab(tab)"
    >
      {{ tab.label }}
      <span v-if="!tab.enabled" class="soon-badge">待开发</span>
    </button>
  </nav>
</template>

<style scoped>
.tab-nav {
  display: flex;
  align-items: center;
  justify-content: space-around;
  padding: 0 8px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.tab-item {
  position: relative;
  flex: 1;
  padding: 10px 4px;
  background: transparent;
  border: none;
  border-radius: 0;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.15s ease;
}

.tab-item:hover:not(:disabled) {
  color: var(--accent);
  background: transparent;
}

.tab-item.active {
  color: var(--accent);
}

.tab-item.active::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 24px;
  height: 2px;
  background: var(--accent);
  border-radius: 1px;
}

.tab-item.disabled {
  color: var(--text-tertiary);
  cursor: not-allowed;
}

.soon-badge {
  display: inline-block;
  margin-left: 4px;
  padding: 0 4px;
  background: var(--bg-base);
  color: var(--text-tertiary);
  font-size: 9px;
  border-radius: 3px;
  line-height: 14px;
}
</style>
