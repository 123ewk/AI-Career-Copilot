/**
 * 岗位分析流水线 Composable
 *
 * 职责：
 * - 监听指定岗位的任务结果（analyze / match / communication）
 * - 当前置任务完成时，自动触发下一个任务：
 *   analyze 完成 → 触发 match
 *   match 完成 → 触发 communication
 * - 提供手动重试入口，供卡片错误状态使用
 *
 * 设计动机：
 * - 把“任务自动推进”逻辑从 Store 中剥离，保持 Store 纯状态管理
 * - 把“任务自动推进”逻辑从 App.vue 中剥离，避免 App.vue 过于臃肿
 * - 每个 JobDetailPanel 实例独立监听自己的 jobId，生命周期与组件一致
 */

import { watch, type Ref } from 'vue'
import {
  ChromeMessageType,
  sendMessageToBackground,
} from '../messaging/chrome_message'
import { useSidePanelStore } from '../stores/sidepanel'

/** 流水线可执行的任务阶段 */
type PipelineStage = 'analyze' | 'match' | 'communication'

/**
 * 使用岗位分析流水线
 *
 * @param jobId 岗位 ID（后端 UUID）
 * @param sessionId 当前会话 ID（用于 analyze 和 communication）
 */
export function useJobPipeline(jobId: Ref<string | undefined>, sessionId: string) {
  const store = useSidePanelStore()

  /**
   * 触发指定阶段任务
   *
   * @param stage 要触发的阶段
   */
  async function triggerStage(stage: PipelineStage) {
    const id = jobId.value
    if (!id) {
      console.warn(`[useJobPipeline] 无法触发 ${stage}，jobId 为空`)
      return
    }

    // 避免重复触发：如果该阶段已经在 pending/running，则跳过
    const current = store.taskResults[id]?.[stage]
    if (current?.status === 'pending' || current?.status === 'running') {
      console.log(`[useJobPipeline] ${stage} 已在进行中，跳过重复触发`)
      return
    }

    try {
      if (stage === 'analyze') {
        // 启动分析前先把状态置为 running，UI 可立即响应
        if (!store.taskResults[id]) {
          store.taskResults[id] = {}
        }
        store.taskResults[id].analyze = { status: 'running' }
        await sendMessageToBackground(ChromeMessageType.REQUEST_ANALYZE, {
          jobId: id,
          sessionId,
        })
      } else if (stage === 'match') {
        if (!store.taskResults[id]) {
          store.taskResults[id] = {}
        }
        store.taskResults[id].match = { status: 'running' }
        await sendMessageToBackground(ChromeMessageType.REQUEST_MATCH, { jobId: id })
      } else {
        if (!store.taskResults[id]) {
          store.taskResults[id] = {}
        }
        store.taskResults[id].communication = { status: 'running' }
        await sendMessageToBackground(ChromeMessageType.REQUEST_COMMUNICATION, {
          jobId: id,
          sessionId,
        })
      }
    } catch (err) {
      console.error(`[useJobPipeline] 触发 ${stage} 失败:`, err)
    }
  }

  /**
   * 手动重试某个阶段
   *
   * @param stage 要重试的阶段
   */
  function retryStage(stage: PipelineStage) {
    void triggerStage(stage)
  }

  /**
   * 启动完整流水线（从 analyze 开始）
   *
   * 若 analyze 已处于 running 或 completed 状态，则跳过，避免重复触发或覆盖已有结果。
   * 手动重试请使用 retryStage。
   */
  function startPipeline() {
    const id = jobId.value
    if (!id) {
      console.warn('[useJobPipeline] 无法启动流水线，jobId 为空')
      return
    }

    const current = store.taskResults[id]?.analyze
    if (current?.status === 'running' || current?.status === 'completed') {
      console.log('[useJobPipeline] analyze 已在进行或已完成，跳过自动启动')
      return
    }

    void triggerStage('analyze')
  }

  // 监听 analyze 结果，完成后自动触发 match
  const stopAnalyzeWatch = watch(
    () => store.taskResults[jobId.value ?? '']?.analyze,
    (analyzeResult) => {
      if (analyzeResult?.status === 'completed') {
        void triggerStage('match')
      }
    },
    { immediate: false },
  )

  // 监听 match 结果，完成后自动触发 communication
  const stopMatchWatch = watch(
    () => store.taskResults[jobId.value ?? '']?.match,
    (matchResult) => {
      if (matchResult?.status === 'completed') {
        void triggerStage('communication')
      }
    },
    { immediate: false },
  )

  /**
   * 清理监听器
   *
   * 在 JobDetailPanel unmount 时调用，防止内存泄漏和重复触发
   */
  function dispose() {
    stopAnalyzeWatch()
    stopMatchWatch()
  }

  return {
    startPipeline,
    retryStage,
    triggerStage,
    dispose,
  }
}
