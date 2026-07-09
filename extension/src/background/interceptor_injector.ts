/**
 * Content Script 注入补偿器
 *
 * 职责：
 * - 当 chrome.tabs.sendMessage 收到 "Receiving end does not exist" 时，尝试向目标 Tab 动态注入 Content Script
 * - manifest.json 已声明 content_scripts，正常刷新页面会自动注入；本文件作为扩展更新/异常场景下的兜底
 *
 * 设计动机：
 * - router.ts 在 REFRESH_JOBS 重试路径中引用本模块，缺失会导致构建失败
 * - MV3 扩展更新后，已打开的 Tab 不会自动重新注入 Content Script，需要手动补偿
 */

/**
 * 尝试向指定 Tab 注入 Content Script
 *
 * @param tabId 目标 Tab ID
 * @returns 是否注入成功
 */
/**
 * 从 manifest.json 读取 Content Script 入口文件路径
 *
 * Vite 构建后文件会带哈希，不能硬编码；从 manifest 取实际路径可保证动态注入与自动注入一致。
 */
function getContentScriptFiles(): string[] {
  const manifest = chrome.runtime.getManifest()
  const cs = manifest.content_scripts?.find((entry) =>
    entry.matches?.some((pattern) => pattern.includes('zhipin.com')),
  )
  return cs?.js ?? []
}

export async function ensureBossContentScriptInjected(
  tabId: number,
): Promise<boolean> {
  try {
    const files = getContentScriptFiles()
    if (files.length === 0) {
      console.warn('[ensureBossContentScriptInjected] manifest 中未找到 Boss Content Script')
      return false
    }

    // manifest content_scripts 会在页面刷新时自动注入；
    // 动态注入仅作为补偿，失败时返回 false 让上层提示用户刷新页面
    await chrome.scripting.executeScript({
      target: { tabId },
      files,
    })
    return true
  } catch (err) {
    console.warn(
      '[ensureBossContentScriptInjected] 动态注入 Content Script 失败:',
      err,
    )
    return false
  }
}
