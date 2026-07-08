/**
 * SidePanel 入口文件
 *
 * 职责：
 * - 创建 Vue App 实例并挂载到 sidepanel.html 的 #app
 * - SidePanel 是主 UI，承载海投模式：岗位列表、详情、分析、匹配、话术
 * - 通过 Pinia 管理全局状态（jobs / selectedJobId / analysisMap 等）
 *
 * 设计动机：
 * - Chrome MV3 SidePanel API 提供持久展示区域，用户可边浏览 Boss 列表页边查看结果
 * - 与 Popup 分离：Popup 短暂存在用于入口，SidePanel 持久存在用于工作
 * - 复用 src/App.vue 作为根组件（spec Step 1.5 要求修改 src/App.vue）
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from '../App.vue'
import '../style.css'

createApp(App)
  .use(createPinia())
  .mount('#app')
