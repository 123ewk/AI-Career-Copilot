/**
 * 简历域 Pinia Store
 *
 * 职责：
 * - 管理简历列表、选中状态、详情缓存
 * - 封装所有简历 API 调用（通过 sendMessageToBackground 走 SW）
 * - 本地状态更新（setActive 后更新 is_active 标志，无需重新拉列表）
 *
 * 设计动机：
 * - 独立于 sidepanel.ts：简历域自包含，避免主 store 膨胀
 * - 所有请求通过 SW 中转：SW 负责 token 管理和 HTTP 请求
 * - 详情缓存：选中简历时拉取详情并缓存，切换时直接使用
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  sendMessageToBackground,
  ChromeMessageType,
  isExtensionContextValid,
} from '../messaging/chrome_message'
import type {
  ResumeSummary,
  ResumeResponse,
  ResumeUploadResponse,
  ResumeListResponse,
} from '../types/resume'

const RESUME_STORAGE_KEY = 'resume_state'

interface ResumePersistedState {
  resumes: ResumeSummary[]
  selectedResumeId: string | null
}

export const useResumeStore = defineStore('resume', () => {
  // ==================== State ====================

  const resumes = ref<ResumeSummary[]>([])
  const selectedResumeId = ref<string | null>(null)
  const resumeDetail = ref<ResumeResponse | null>(null)
  const isLoadingList = ref(false)
  const isLoadingDetail = ref(false)
  const isUploading = ref(false)
  const uploadError = ref<string | null>(null)
  const listError = ref<string | null>(null)

  // ==================== Computed ====================

  const selectedResume = computed(() =>
    resumes.value.find((r) => r.id === selectedResumeId.value) ?? null,
  )

  const activeResume = computed(() =>
    resumes.value.find((r) => r.is_active) ?? null,
  )

  // ==================== Actions ====================

  /** 拉取简历列表 */
  async function fetchResumes(limit = 20, offset = 0): Promise<void> {
    isLoadingList.value = true
    listError.value = null

    const resp = await sendMessageToBackground(ChromeMessageType.RESUME_LIST, {
      limit,
      offset,
    })

    if (resp.ok && resp.data) {
      const data = resp.data as ResumeListResponse
      resumes.value = data.items
      void saveToStorage()
    } else {
      listError.value = resp.error ?? '获取简历列表失败'
    }

    isLoadingList.value = false
  }

  /** 拉取简历详情 */
  async function fetchResumeDetail(resumeId: string): Promise<void> {
    isLoadingDetail.value = true

    const resp = await sendMessageToBackground(ChromeMessageType.RESUME_GET, {
      resumeId,
    })

    if (resp.ok && resp.data) {
      resumeDetail.value = resp.data as ResumeResponse
    } else {
      resumeDetail.value = null
    }

    isLoadingDetail.value = false
  }

  /** 上传简历文件 */
  async function uploadResume(file: File): Promise<boolean> {
    isUploading.value = true
    uploadError.value = null

    const arrayBuffer = await file.arrayBuffer()
    // 转为 Uint8Array 再发送：Chrome structured clone 对 TypedArray 支持更稳定，
    // 纯 ArrayBuffer 在 MV3 消息传递中可能被转为普通对象
    const fileData = Array.from(new Uint8Array(arrayBuffer))

    const resp = await sendMessageToBackground(ChromeMessageType.RESUME_UPLOAD, {
      filename: file.name,
      mimeType: file.type || 'application/octet-stream',
      fileData,
    })

    if (resp.ok && resp.data) {
      const data = resp.data as ResumeUploadResponse
      // 刷新列表
      await fetchResumes()
      // 自动选中新上传的简历
      selectResume(data.resume.id)
      isUploading.value = false
      return true
    }

    uploadError.value = resp.error ?? '上传失败'
    isUploading.value = false
    return false
  }

  /** 切换活跃简历 */
  async function setActiveResume(resumeId: string): Promise<boolean> {
    const resp = await sendMessageToBackground(
      ChromeMessageType.RESUME_SET_ACTIVE,
      { resumeId },
    )

    if (resp.ok) {
      // 本地更新 is_active 标志
      for (const r of resumes.value) {
        r.is_active = r.id === resumeId
      }
      // 如果当前选中的就是被切换的，更新详情
      if (resumeDetail.value?.id === resumeId) {
        resumeDetail.value.is_active = true
      }
      void saveToStorage()
      return true
    }

    return false
  }

  /** 删除简历 */
  async function deleteResume(resumeId: string): Promise<boolean> {
    const resp = await sendMessageToBackground(ChromeMessageType.RESUME_DELETE, {
      resumeId,
    })

    if (resp.ok) {
      // 从列表移除
      resumes.value = resumes.value.filter((r) => r.id !== resumeId)
      // 如果删除的是当前选中的，清除选中
      if (selectedResumeId.value === resumeId) {
        selectedResumeId.value = null
        resumeDetail.value = null
      }
      void saveToStorage()
      return true
    }

    return false
  }

  /** 选中简历（null 取消选中） */
  function selectResume(resumeId: string | null): void {
    selectedResumeId.value = resumeId
    void saveToStorage()
    if (resumeId) {
      void fetchResumeDetail(resumeId)
    } else {
      resumeDetail.value = null
    }
  }

  // ==================== Persistence ====================

  async function saveToStorage(): Promise<void> {
    if (!isExtensionContextValid()) return
    const state: ResumePersistedState = {
      resumes: resumes.value,
      selectedResumeId: selectedResumeId.value,
    }
    return new Promise((resolve) => {
      try {
        chrome.storage.local.set({ [RESUME_STORAGE_KEY]: state }, () => {
          if (chrome.runtime.lastError) {
            console.warn('[resume-store] 持久化失败:', chrome.runtime.lastError.message)
          }
          resolve()
        })
      } catch {
        resolve()
      }
    })
  }

  async function loadFromStorage(): Promise<void> {
    if (!isExtensionContextValid()) return
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get([RESUME_STORAGE_KEY], (result) => {
          const state = result[RESUME_STORAGE_KEY] as ResumePersistedState | undefined
          if (state && state.resumes && state.resumes.length > 0) {
            resumes.value = state.resumes
            if (state.selectedResumeId) {
              selectedResumeId.value = state.selectedResumeId
              void fetchResumeDetail(state.selectedResumeId)
            }
          }
          resolve()
        })
      } catch {
        resolve()
      }
    })
  }

  return {
    // State
    resumes,
    selectedResumeId,
    resumeDetail,
    isLoadingList,
    isLoadingDetail,
    isUploading,
    uploadError,
    listError,
    // Computed
    selectedResume,
    activeResume,
    // Actions
    fetchResumes,
    fetchResumeDetail,
    uploadResume,
    setActiveResume,
    deleteResume,
    selectResume,
    loadFromStorage,
  }
})
