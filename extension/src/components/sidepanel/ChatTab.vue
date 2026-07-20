<script setup lang="ts">
/**
 * 沟通 Tab 容器组件
 *
 * 职责：
 * - 渲染左右分栏布局：对话列表（40%）+ 消息面板（60%）
 * - 管理聊天页状态（是否在聊天页）
 *
 * 设计动机：
 * - 与 Job/Resume Tab 的左右分栏布局保持视觉一致
 * - 对话列表和消息面板各自独立，通过 communication store 联动
 */

import { onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useCommunicationStore } from '../../stores/communication'
import ChatConversationList from './ChatConversationList.vue'
import ChatMessagePanel from './ChatMessagePanel.vue'

const commStore = useCommunicationStore()
const { isOnChatPage, activeConversation } = storeToRefs(commStore)

onMounted(() => {
  commStore.loadFromStorage()
})
</script>

<template>
  <div class="chat-tab">
    <!-- 未在聊天页时的提示 -->
    <div v-if="!isOnChatPage" class="chat-empty">
      <div class="empty-icon">💬</div>
      <h2 class="empty-title">请打开 BOSS 直聘聊天页</h2>
      <p class="empty-desc">
        打开 <code>zhipin.com/web/geek/chat</code><br />
        后将自动检测对话并提供 AI 回复建议
      </p>
    </div>

    <!-- 聊天页已就绪 -->
    <template v-else>
      <ChatConversationList />
      <ChatMessagePanel v-if="activeConversation" :key="activeConversation.id" />
      <div v-else class="detail-empty">
        <div class="empty-icon">👈</div>
        <p class="empty-title">选择左侧对话查看详情</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.chat-tab {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  padding: 24px;
  text-align: center;
}

.empty-icon {
  font-size: 32px;
  margin-bottom: 12px;
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.empty-desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.empty-desc code {
  background: var(--bg-base);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
}

.detail-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  padding: 24px;
  text-align: center;
}
</style>
