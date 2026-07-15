/**
 * BOSS 直聘 API 抓包脚本
 *
 * 用 Playwright 启动 Chrome（复用用户登录态），访问 BOSS 列表页，
 * 拦截所有 /wapi/ 开头的网络请求，保存到 scripts/boss-api-capture.json。
 *
 * 用法：
 *   cd extension
 *   npx playwright install chromium  # 首次需要安装浏览器
 *   node scripts/capture-boss-api.mjs
 *
 * 可选环境变量：
 *   BOSS_URL=https://www.zhipin.com/web/geek/jobs?query=python  指定初始页面
 *   CAPTURE_SECONDS=15                                           抓包时长（默认 15 秒）
 */

import { chromium } from 'playwright'
import { writeFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const OUTPUT_FILE = join(__dirname, 'boss-api-capture.json')
const DEFAULT_URL = 'https://www.zhipin.com/web/geek/jobs'
const CAPTURE_SECONDS = parseInt(process.env.CAPTURE_SECONDS || '15', 10)

// Chrome 用户数据目录（复用登录态）
// Windows: C:\Users\<user>\AppData\Local\Google\Chrome\User Data
const CHROME_USER_DATA_DIR = process.env.CHROME_USER_DATA_DIR ||
  join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'User Data')

const targetUrl = process.env.BOSS_URL || DEFAULT_URL

console.log(`[capture] 启动 BOSS 直聘 API 抓包`)
console.log(`[capture] 目标页面: ${targetUrl}`)
console.log(`[capture] 抓包时长: ${CAPTURE_SECONDS} 秒`)
console.log(`[capture] Chrome 数据目录: ${CHROME_USER_DATA_DIR}`)
console.log()

// 收集到的 API 请求
const capturedRequests = []

async function main() {
  // 使用 persistent context 复用 Chrome 登录态
  // channel: 'chrome' 使用系统安装的 Chrome（非 Chromium），兼容性更好
  const context = await chromium.launchPersistentContext(CHROME_USER_DATA_DIR, {
    channel: 'chrome',
    headless: false,        // 必须有头模式，BOSS 检测无头浏览器
    viewport: { width: 1440, height: 900 },
    args: [
      '--disable-blink-features=AutomationControlled',  // 隐藏自动化特征
      '--no-first-run',
      '--no-default-browser-check',
    ],
    ignoreDefaultArgs: ['--enable-automation'],  // 移除自动化标志
  })

  const page = context.pages()[0] || await context.newPage()

  // 监听所有网络请求的响应
  page.on('response', async (response) => {
    const url = response.url()
    // 只关注 /wapi/ 相关的 API 请求
    if (!url.includes('/wapi/')) return

    const request = response.request()
    const entry = {
      url,
      method: request.method(),
      status: response.status(),
      timestamp: new Date().toISOString(),
      postData: request.postData() || null,
      responseHeaders: {},
      responseBody: null,
    }

    // 保存响应头
    try {
      entry.responseHeaders = await response.allHeaders()
    } catch {}

    // 尝试解析响应体
    try {
      const body = await response.text()
      // 只保存前 50KB，避免文件过大
      entry.responseBody = body.length > 50000 ? body.slice(0, 50000) + '...[truncated]' : body

      // 检测是否包含职位数据
      try {
        const json = JSON.parse(body)
        if (json.zpData?.jobList || json.data?.jobList || json.zpData?.list) {
          entry.hasJobData = true
          entry.jobCount = (json.zpData?.jobList || json.data?.jobList || json.zpData?.list || []).length
          console.log(`  [HIT] ${url.slice(0, 100)} → ${entry.jobCount} 个职位`)
        }
      } catch {}
    } catch {
      entry.responseBody = '[failed to read body]'
    }

    capturedRequests.push(entry)
    console.log(`  [${response.status()}] ${request.method()} ${url.slice(0, 120)}`)
  })

  // 访问 BOSS 列表页
  console.log(`\n[capture] 正在打开 ${targetUrl} ...\n`)
  await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })

  // 等待页面加载和 API 请求完成
  console.log(`[capture] 页面已加载，等待 ${CAPTURE_SECONDS} 秒收集 API 请求...\n`)
  console.log(`[capture] 提示：你可以滚动页面、点击筛选条件来触发更多 API 请求\n`)

  await page.waitForTimeout(CAPTURE_SECONDS * 1000)

  // 保存结果
  const result = {
    capturedAt: new Date().toISOString(),
    targetUrl,
    totalRequests: capturedRequests.length,
    jobDataRequests: capturedRequests.filter(r => r.hasJobData).length,
    requests: capturedRequests,
  }

  writeFileSync(OUTPUT_FILE, JSON.stringify(result, null, 2), 'utf-8')
  console.log(`\n[capture] 完成！共捕获 ${capturedRequests.length} 个 /wapi/ 请求`)
  console.log(`[capture] 其中 ${result.jobDataRequests} 个包含职位数据`)
  console.log(`[capture] 结果已保存到: ${OUTPUT_FILE}`)

  // 打印摘要
  if (capturedRequests.length > 0) {
    console.log(`\n[capture] === API URL 摘要 ===`)
    const uniqueUrls = [...new Set(capturedRequests.map(r => new URL(r.url).pathname))]
    uniqueUrls.forEach(u => console.log(`  ${u}`))
  }

  await context.close()
}

main().catch((err) => {
  console.error('[capture] 致命错误:', err.message)
  process.exit(1)
})
