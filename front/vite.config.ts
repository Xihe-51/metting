import fs from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
// import vueDevTools from 'vite-plugin-vue-devtools'

// ============ 局域网 HTTPS 支持 ============
// 浏览器只在「安全上下文」（HTTPS 或 localhost）下暴露 navigator.mediaDevices：
// 直接用 http://192.168.x.x 打开页面时 getUserMedia 为 undefined，摄像头/麦克风全部不可用。
// 因此局域网直连必须走 HTTPS，WebSocket 也随之升级为 wss（页面 https 时前端自动选 wss）。
//
// 证书由 backend/scripts/gen_dev_cert.ps1 生成到 <仓库根>/certs/ 下；
// 没有证书时自动退回 HTTP 并打印提示，保证原有开发流程不被打断。
const DEV_KEY_PATH = fileURLToPath(new URL('../certs/dev-key.pem', import.meta.url))
const DEV_CERT_PATH = fileURLToPath(new URL('../certs/dev-cert.pem', import.meta.url))
const hasDevCert = fs.existsSync(DEV_KEY_PATH) && fs.existsSync(DEV_CERT_PATH)

if (!hasDevCert) {
  console.warn(
    '[vite] 未找到自签证书 certs/dev-key.pem / certs/dev-cert.pem，当前以 HTTP 启动；\n' +
      '       局域网设备将无法调用摄像头和麦克风。请先执行 backend/scripts/gen_dev_cert.ps1 再重启。'
  )
}

// 后端协议与前端保持一致：证书存在时 run.py 也会以 HTTPS/WSS 启动
const backendTarget = hasDevCert ? 'https://127.0.0.1:8000' : 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueJsx(),
    // vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  define: {
    global: 'globalThis'
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // 有证书则启用自签 HTTPS；无证书时该字段整体不出现，保持 HTTP
    ...(hasDevCert
      ? {
          https: {
            key: fs.readFileSync(DEV_KEY_PATH),
            cert: fs.readFileSync(DEV_CERT_PATH)
          }
        }
      : {}),
    proxy: {
      // ws: true 让 /api 前缀的代理支持 WebSocket 升级（会议连接走 /api/v1/ws/...）
      '/api': {
        target: backendTarget,
        changeOrigin: true,
        secure: false,
        ws: true
      },
      '/recordings': {
        target: backendTarget,
        changeOrigin: true,
        secure: false
      },
      '/uploads': {
        target: backendTarget,
        changeOrigin: true,
        secure: false
      },
      '/static': {
        target: backendTarget,
        changeOrigin: true,
        secure: false
      }
    }
  }
})