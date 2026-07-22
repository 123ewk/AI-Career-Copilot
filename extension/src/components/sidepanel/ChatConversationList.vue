<script setup lang="ts">
/**
 * 对话列表组件（左栏）
 *
 * 职责：
 * - 渲染对话列表（HR 名 + 最后消息 + 未读标记）
 * - 高亮当前活跃对话
 * - 点击切换对话
 * - 空状态显示选择器诊断信息
 *
 * 空状态分支(2026-07-21 修复):
 * - 未打开聊天页:引导用户前往 BOSS 直聘聊天页
 * - 已打开聊天页但无选中对话:引导用户点击对话
 * - 已打开聊天页且 SW 缓存为空:提示正在等待数据
 */

import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useCommunicationStore } from '../../stores/communication'

const commStore = useCommunicationStore()
const { sortedConversations, activeConversationId, diagnostics, isOnChatPage } = storeToRefs(commStore)

/** 格式化诊断结果为可读列表 */
const diagnosticItems = computed(() => {
  if (!diagnostics.value) return []
  const d = diagnostics.value as Record<string, unknown>
  const items: { label: string; ok: boolean; detail: string }[] = []

  // 遍历诊断结果的每个区域
  for (const [section, sectionData] of Object.entries(d)) {
    if (section === 'url' || section === 'timestamp' || section === 'allChatClasses') continue
    if (typeof sectionData !== 'object' || !sectionData) continue
    for (const [key, val] of Object.entries(sectionData as Record<string, unknown>)) {
      if (typeof val !== 'object' || !val) continue
      const v = val as { found: boolean; count: number; selector: string; sampleText?: string }
      items.push({
        label: `${section}.${key}`,
        ok: v.found,
        detail: v.found
          ? `${v.count}个 | ${v.sampleText?.substring(0, 30) ?? ''}`
          : `未匹配: ${v.selector}`,
      })
    }
  }
  return items
})

/** 页面上发现的 chat 相关 class 列表 */
const chatClasses = computed(() => {
  if (!diagnostics.value) return []
  const d = diagnostics.value as Record<string, unknown>
  return (d.allChatClasses as string[]) ?? []
})

/**
 * 空状态文案(根据是否在聊天页 + 是否有活跃对话)
 *
 * 三种场景:
 * 1. 未在聊天页 → "请打开 BOSS 直聘聊天页"
 * 2. 在聊天页但无对话数据 → "正在加载对话列表...或点击 BOSS 页面任意对话"
 * 3. 在聊天页有对话数据但无选中 → 由列表项展示,不会进入此分支
 */
const emptyState = computed(() => {
  if (!isOnChatPage.value) {
    return {
      title: '未检测到聊天页',
      hint: '请在浏览器中打开 BOSS 直聘聊天页(https://www.zhipin.com/web/geek/chat)',
    }
  }
  return {
    title: '正在加载对话列表',
    hint: '请确保已在 BOSS 直聘聊天页点击一个对话(若已点击仍为空,请关闭重开 SidePanel)',
  }
})
</script>

<template>
  <div class="conversation-list">
    <div class="list-header">
      <span class="list-title">对话列表</span>
      <span class="list-count">{{ sortedConversations.length }}</span>
    </div>
    <div class="list-body">
      <div
        v-for="conv in sortedConversations"
        :key="conv.id"
        class="conv-item"
        :class="{ active: conv.id === activeConversationId }"
        @click="commStore.setActiveConversation(conv.id)"
      >
        <div class="conv-header">
          <span class="conv-name">{{ conv.recruiterName }}</span>
          <span v-if="conv.company" class="conv-company">{{ conv.company }}</span>
        </div>
        <div v-if="conv.jobTitle" class="conv-job">{{ conv.jobTitle }}</div>
        <div class="conv-last-msg">{{ conv.lastMessage || '(暂无消息)' }}</div>
        <span v-if="conv.messageCount > 0" class="conv-count">{{ conv.messageCount }}</span>
      </div>
      <div v-if="sortedConversations.length === 0" class="conv-empty">
        <div class="empty-title">{{ emptyState.title }}</div>
        <div class="empty-hint">{{ emptyState.hint }}</div>

        <!-- 选择器诊断信息 -->
        <div v-if="diagnosticItems.length > 0" class="diagnostics">
          <div class="diag-title">选择器诊断</div>
          <div
            v-for="(item, i) in diagnosticItems"
            :key="i"
            class="diag-item"
            :class="{ 'diag-ok': item.ok, 'diag-fail': !item.ok }"
          >
            <span class="diag-icon">{{ item.ok ? '✓' : '✗' }}</span>
            <span class="diag-label">{{ item.label }}</span>
            <span class="diag-detail">{{ item.detail }}</span>
          </div>
          <div v-if="chatClasses.length > 0" class="diag-classes">
            <div class="diag-title">页面发现的 class</div>
            <div class="class-tags">
              <span v-for="cls in chatClasses" :key="cls" class="class-tag">{{ cls }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conversation-list {
  width: 40%;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.list-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.list-count {
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-base);
  padding: 1px 6px;
  border-radius: 10px;
}

.list-body {
  flex: 1;
  overflow-y: auto;
}

.conv-item {
  display: flex;
  flex-direction: column;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  position: relative;
  transition: background 0.15s ease;
}

.conv-item:hover {
  background: var(--bg-hover);
}

.conv-item.active {
  background: var(--bg-active);
}

.conv-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.conv-header {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 2px;
}

.conv-company {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-job {
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-last-msg {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-count {
  position: absolute;
  top: 10px;
  right: 12px;
  font-size: 10px;
  color: var(--text-tertiary);
}

.conv-empty {
  padding: 24px;
  text-align: center;
  font-size: 13px;
  color: var(--text-tertiary);
}

.empty-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 6px;
}

.empty-hint {
  font-size: 12px;
  margin-bottom: 16px;
}

.diagnostics {
  text-align: left;
  background: var(--bg-base);
  border-radius: 6px;
  padding: 10px;
  font-size: 11px;
  max-height: 300px;
  overflow-y: auto;
}

.diag-title {
  font-weight: 600;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 6px;
  text-align: center;
}

.diag-item {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  padding: 2px 0;
  line-height: 1.4;
}

.diag-icon {
  flex-shrink: 0;
  width: 14px;
}

.diag-ok .diag-icon {
  color: #22c55e;
}

.diag-fail .diag-icon {
  color: #ef4444;
}

.diag-label {
  flex-shrink: 0;
  width: 120px;
  color: var(--text-secondary);
  font-family: monospace;
}

.diag-detail {
  color: var(--text-tertiary);
  word-break: break-all;
}

.diag-classes {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border);
}

.class-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}

.class-tag {
  background: var(--bg-hover);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: monospace;
  font-size: 10px;
  color: var(--text-secondary);
}
</style>
