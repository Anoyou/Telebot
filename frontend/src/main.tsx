// 入口文件：挂载 React + Router + Query Client + Toaster
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";

import App, { AppErrorBoundary } from "./App";
import "./index.css";
import { registerPWA } from "./pwa";

// 全局 query client：默认 30s 缓存、失焦不刷新（避免与 401 跳转冲突）
const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AppErrorBoundary>
          <App />
        </AppErrorBoundary>
      </BrowserRouter>
      <Toaster richColors closeButton position="top-right" />
    </QueryClientProvider>
  </React.StrictMode>,
);

// 注册 Service Worker（生产构建会预缓存静态资源；开发环境也启用便于真机测试）
registerPWA();
