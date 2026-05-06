// Vite 配置：开发端口 5173；/api 代理到后端 8000；监听所有网卡以支持局域网访问；启用 PWA
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // 自动更新：新 SW 安装好后下次启动自动激活；前端再监听 needRefresh 提示用户刷新
      registerType: "autoUpdate",
      // 我们在 src/pwa.ts 里手动 import 'virtual:pwa-register' 注册并接管更新提示，
      // 所以关掉插件的自动注入，避免双重注册。
      injectRegister: null,
      // dev 模式默认 **不启 SW**：之前一旦启用，浏览器里安装的 SW 会缓存住 dist/
      // 让 vite dev 改的源码看不见（症状：刷新后页面还是旧版本，"以为代码没生效"）。
      // 想测 PWA 安装/离线功能：跑 `pnpm build && pnpm preview` 用 prod 构建调试。
      devOptions: {
        enabled: false,
        type: "module",
        navigateFallback: "index.html",
      },
      includeAssets: [
        "favicon.ico",
        "apple-touch-icon.png",
        "robots.txt",
      ],
      manifest: {
        name: "Telegram Userbot 管理系统",
        short_name: "Userbot",
        description: "Telegram Userbot 管理后台",
        lang: "zh-CN",
        start_url: "/",
        scope: "/",
        display: "standalone",
        orientation: "portrait",
        background_color: "#0b0f17",
        theme_color: "#26a6de",
        icons: [
          {
            src: "/pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/pwa-maskable-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        // 不缓存后端 API：始终走网络
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/openapi\.json$/],
        globPatterns: ["**/*.{js,css,html,ico,png,svg,webp,woff,woff2}"],
        runtimeCaching: [
          {
            // 静态资源：StaleWhileRevalidate
            urlPattern: ({ request }) =>
              ["style", "script", "worker", "image", "font"].includes(request.destination),
            handler: "StaleWhileRevalidate",
            options: { cacheName: "assets" },
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: true, // 监听 0.0.0.0，允许同网段设备通过 http://<本机IP>:5173 访问
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  preview: {
    host: true,
    port: 5173,
  },
});
