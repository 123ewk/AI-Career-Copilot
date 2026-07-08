import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { crx } from '@crxjs/vite-plugin'
import manifest from './manifest.json' with { type: 'json' }

// Vite 配置：Chrome MV3 Extension 多入口构建
//
// 设计动机：
// - @crxjs/vite-plugin 自动处理 manifest.json 中引用的源码路径
//   （service_worker.ts、content.ts、popup.html、sidepanel.html）
// - 开发模式提供 HMR（HTML/CSS），SW 与 content script 仍需手动 reload
// - 构建产物输出到 dist/，可直接在 chrome://extensions 加载
//
// 注意：
// - manifest.json 必须在项目根目录（与 vite.config.ts 同级）
// - manifest 中引用的源码路径相对于项目根目录
// - HTML 入口（popup.html、sidepanel.html）必须显式在 manifest 中声明
export default defineConfig({
  plugins: [
    vue(),
    crx({ manifest }),
  ],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 禁用 modulepreload：chrome_message.ts 等共享模块同时被
    // content script（isolated world）和 popup/sidepanel（extension world）引用，
    // Vite 默认注入的 <link rel="modulepreload"> 会被 Chrome 判定为
    // cross-world extension resource mismatch 并产生警告。
    // 扩展页面加载的是本地文件，禁用 preload 对性能影响可忽略。
    modulePreload: false,
    rollupOptions: {
      // CRXJS 会自动注入 manifest 引用的入口，这里不需要额外配置 input
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    hmr: {
      port: 5173,
    },
  },
})
