/**
 * source_url → jobId 映射存储
 *
 * 职责：
 * - 在 chrome.storage.local 中持久化 source_url（Boss 详情页 URL）到后端 Job UUID 的映射
 * - 提供 setMapping / getJobId / bulkSet / clear 等异步 API
 * - 屏蔽 chrome.storage.local 的 Promise 包装细节
 *
 * 设计动机：
 * - MV3 Service Worker 空闲 30 秒后会被 Chrome 回收，模块级变量会丢失
 *   → 必须用 chrome.storage.local 持久化，SW 重启后能恢复映射
 * - JOB_DETAIL_EXTRACTED handler 收到 source_url 时需要查到对应 jobId 才能调用
 *   PATCH /api/jobs/{job_id}，映射丢失会导致详情补充失败
 * - 用单一 key 集中存储所有映射，避免 storage.local 中零散 key 难以管理
 *
 * 数据结构：
 *   storage.local["source_url_map"] = {
 *     "https://www.zhipin.com/job_detail/abc.html": "uuid-1234",
 *     "https://www.zhipin.com/job_detail/def.html": "uuid-5678",
 *     ...
 *   }
 *
 * 容量评估：
 * - 单条映射约 200 字节（URL ~120 + UUID 36 + JSON 结构开销）
 * - storage.local 上限 5MB，可存约 2.5 万条映射，远超单次海投规模
 * - 长期使用可能累积过期映射，提供 clear() 用于手动清理
 *
 * 并发安全：
 * - chrome.storage.local 是原子读写，但「读-改-写」不是原子的
 * - JOBS_EXTRACTED handler 串行处理批量创建（避免并发写冲突）
 * - 多个 SW 实例同时写的情况极少（Chrome 通常单实例 SW），暂不加锁
 */

/** chrome.storage.local 中的存储 key */
const STORAGE_KEY = 'source_url_map'

/** 映射表结构：source_url → jobId */
type SourceUrlMap = Record<string, string>

/**
 * 从 chrome.storage.local 加载完整映射表
 *
 * SW 重启后第一次调用会触发 I/O，后续调用无缓存（每次都读 storage）
 * 不做内存缓存的原因：
 * - SW 随时可能被回收，缓存的内存随时失效
 * - storage.local 读操作很快（< 5ms），不需要缓存
 *
 * @returns 完整的映射表（空对象表示无映射）
 */
async function loadMap(): Promise<SourceUrlMap> {
  return new Promise((resolve) => {
    chrome.storage.local.get([STORAGE_KEY], (result) => {
      const map = result[STORAGE_KEY] as SourceUrlMap | undefined
      resolve(map ?? {})
    })
  })
}

/**
 * 将完整映射表写回 chrome.storage.local
 *
 * @param map 完整映射表
 */
async function saveMap(map: SourceUrlMap): Promise<void> {
  return new Promise((resolve) => {
    chrome.storage.local.set({ [STORAGE_KEY]: map }, () => {
      resolve()
    })
  })
}

/**
 * 写入单条映射（读-改-写）
 *
 * 用于 JOBS_EXTRACTED handler 在创建 Job 成功后记录映射
 *
 * @param sourceUrl Boss 详情页 URL（key）
 * @param jobId 后端 Job UUID（value）
 */
export async function setMapping(
  sourceUrl: string,
  jobId: string,
): Promise<void> {
  if (!sourceUrl || !jobId) {
    throw new Error(`setMapping 参数非法: sourceUrl=${sourceUrl}, jobId=${jobId}`)
  }
  const map = await loadMap()
  map[sourceUrl] = jobId
  await saveMap(map)
}

/**
 * 批量写入映射（单次读-单次写，比循环 setMapping 高效）
 *
 * 用于 JOBS_EXTRACTED handler 批量创建后一次性写入所有映射
 *
 * @param entries [sourceUrl, jobId] 数组
 */
export async function bulkSet(
  entries: Array<[string, string]>,
): Promise<void> {
  if (entries.length === 0) return
  const map = await loadMap()
  for (const [sourceUrl, jobId] of entries) {
    if (!sourceUrl || !jobId) {
      // 单条非法不中断整批写入，记录警告后跳过
      console.warn(
        '[source_url_map] bulkSet 跳过非法条目: sourceUrl=, jobId=',
        sourceUrl,
        jobId,
      )
      continue
    }
    map[sourceUrl] = jobId
  }
  await saveMap(map)
}

/**
 * 查询单条映射
 *
 * 用于 JOB_DETAIL_EXTRACTED handler 通过 source_url 反查 jobId
 *
 * @param sourceUrl Boss 详情页 URL
 * @returns jobId（未找到时返回 undefined）
 */
export async function getJobId(
  sourceUrl: string,
): Promise<string | undefined> {
  if (!sourceUrl) return undefined
  const map = await loadMap()
  return map[sourceUrl]
}

/**
 * 清空所有映射
 *
 * 用于：
 * - 用户切换账号时清理上一账号的映射（避免跨账号污染）
 * - 长期使用后清理过期映射（手动触发）
 */
export async function clear(): Promise<void> {
  await saveMap({})
  console.log('[source_url_map] 已清空所有映射')
}

/**
 * 获取当前映射总数（用于调试 / 监控）
 *
 * @returns 映射条目数
 */
export async function size(): Promise<number> {
  const map = await loadMap()
  return Object.keys(map).length
}
