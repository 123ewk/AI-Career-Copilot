/**
 * Popup 入口文件
 *
 * 职责：
 * - 创建 Vue App 实例并挂载到 popup.html 的 #app
 * - Popup 仅作为轻量入口：登录、后端地址配置、版本信息
 * - 主 UI 在 SidePanel 中，Popup 通过 chrome.sidePanel.open() 打开
 *
 * 设计动机：
 * - MV3 推荐模式：Popup 短暂存在，SidePanel 持久展示
 * - Popup 与 SidePanel 是两个独立 Vue App，通过 chrome.storage + 消息协议同步状态
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PopupApp from './App.vue'
import '../style.css'

createApp(PopupApp)
  .use(createPinia())
  .mount('#app')
