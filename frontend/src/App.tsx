import React, { Suspense, lazy } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/layout/RequireAuth";

import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { AccountList } from "@/pages/Accounts/List";
import { Spinner } from "@/components/ui/misc";

// 把不影响首屏的页面拆成 lazy chunk：
//   - 用户最常进入的是 Dashboard 与账号列表，这些保持 eager；
//   - 插件中心、Logs（拖大 echarts）、设置子页、AI、模板、账号详情 / 向导 / 各
//     feature 配置页都按需加载。
//   - vite.config.ts 里另有 manualChunks 把 echarts / highlight.js / react-markdown
//     单独拆 chunk，大依赖只在用到的页面拉一次。
const AccountWizard = lazy(() => import("@/pages/Accounts/Wizard").then(m => ({ default: m.AccountWizard })));
const AccountDetail = lazy(() => import("@/pages/Accounts/Detail").then(m => ({ default: m.AccountDetail })));
const AutoReplyConfig = lazy(() => import("@/pages/Features/AutoReply").then(m => ({ default: m.AutoReplyConfig })));
const AutorepeatConfig = lazy(() => import("@/pages/Features/Autorepeat").then(m => ({ default: m.AutorepeatConfig })));
const CodexImageConfigPage = lazy(() => import("@/pages/Features/CodexImageConfig").then(m => ({ default: m.CodexImageConfigPage })));
const ForwardConfig = lazy(() => import("@/pages/Features/Forward").then(m => ({ default: m.ForwardConfig })));
const SchedulerConfig = lazy(() => import("@/pages/Features/Scheduler").then(m => ({ default: m.SchedulerConfig })));
const Game24ConfigPage = lazy(() => import("@/pages/Features/Game24Config").then(m => ({ default: m.Game24ConfigPage })));
const FeatureTodoPage = lazy(() => import("@/pages/Features/TodoPage").then(m => ({ default: m.FeatureTodoPage })));
const Logs = lazy(() => import("@/pages/Logs").then(m => ({ default: m.Logs })));
const SettingsIndex = lazy(() => import("@/pages/Settings/Index").then(m => ({ default: m.SettingsIndex })));
const CommandTemplates = lazy(() => import("@/pages/Settings/CommandTemplates").then(m => ({ default: m.CommandTemplates })));
const Extensions = lazy(() => import("@/pages/Extensions").then(m => ({ default: m.Extensions })));
const AISettings = lazy(() => import("@/pages/AISettings").then(m => ({ default: m.AISettings })));
const Templates = lazy(() => import("@/pages/Templates").then(m => ({ default: m.Templates })));

type AppErrorBoundaryState = { hasError: boolean };

// 已知有专属配置页的 feature key 列表
const FEATURE_CONFIG_PAGES: Record<string, { title: string; description: string }> = {
  auto_reply: { title: "自动回复", description: "不存在专属配置页路由" },
  autorepeat: { title: "自动复读", description: "不存在专属配置页路由" },
  codex_image: { title: "Codex 图片生成", description: "不存在专属配置页路由" },
  forward: { title: "消息转发", description: "不存在专属配置页路由" },
  scheduler: { title: "定时任务", description: "不存在专属配置页路由" },
  game24: { title: "24 点游戏", description: "不存在专属配置页路由" },
};

// 通配路由组件：已知 feature 显示 TodoPage，未知 feature 也显示占位
function FeatureCatchAll() {
  const { featureKey } = useParams<{ featureKey: string }>();
  const key = featureKey ?? "unknown";
  const info = FEATURE_CONFIG_PAGES[key] ?? {
    title: key,
    description: `功能「${key}」的配置页面尚未实现。可通过 API 直连完成基础配置。`,
  };
  return <FeatureTodoPage title={info.title} description={info.description} />;
}

function PageFallback() {
  return (
    <div className="flex h-[40vh] items-center justify-center">
      <Spinner className="text-primary" />
    </div>
  );
}

export class AppErrorBoundary extends React.Component<
  React.PropsWithChildren,
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown) {
    console.error("App crashed:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center p-6">
          <div className="w-full max-w-md rounded-lg border bg-card p-6 shadow-sm">
            <h1 className="text-lg font-semibold">页面发生错误</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              应用遇到未处理异常，请刷新页面重试。
            </p>
            <button
              type="button"
              className="mt-4 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
              onClick={() => window.location.reload()}
            >
              刷新页面
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="accounts">
            <Route index element={<AccountList />} />
            <Route
              path="new"
              element={
                <Suspense fallback={<PageFallback />}>
                  <AccountWizard />
                </Suspense>
              }
            />
            <Route
              path=":aid"
              element={
                <Suspense fallback={<PageFallback />}>
                  <AccountDetail />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/auto_reply"
              element={
                <Suspense fallback={<PageFallback />}>
                  <AutoReplyConfig />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/autorepeat"
              element={
                <Suspense fallback={<PageFallback />}>
                  <AutorepeatConfig />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/codex_image"
              element={
                <Suspense fallback={<PageFallback />}>
                  <CodexImageConfigPage />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/forward"
              element={
                <Suspense fallback={<PageFallback />}>
                  <ForwardConfig />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/scheduler"
              element={
                <Suspense fallback={<PageFallback />}>
                  <SchedulerConfig />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/game24"
              element={
                <Suspense fallback={<PageFallback />}>
                  <Game24ConfigPage />
                </Suspense>
              }
            />
            <Route
              path=":aid/features/:featureKey"
              element={
                <Suspense fallback={<PageFallback />}>
                  <FeatureCatchAll />
                </Suspense>
              }
            />
          </Route>
          <Route
            path="plugins"
            element={
              <Suspense fallback={<PageFallback />}>
                <Extensions />
              </Suspense>
            }
          />
          <Route
            path="scheduler"
            element={
              <Suspense fallback={<PageFallback />}>
                <SchedulerConfig />
              </Suspense>
            }
          />
          <Route path="matrix" element={<Navigate to="/plugins" replace />} />
          <Route path="extensions" element={<Navigate to="/plugins" replace />} />
          <Route path="remote-plugins" element={<Navigate to="/plugins" replace />} />
          <Route
            path="logs"
            element={
              <Suspense fallback={<PageFallback />}>
                <Logs />
              </Suspense>
            }
          />
          <Route
            path="settings"
            element={
              <Suspense fallback={<PageFallback />}>
                <SettingsIndex />
              </Suspense>
            }
          />
          <Route
            path="settings/commands"
            element={
              <Suspense fallback={<PageFallback />}>
                <CommandTemplates />
              </Suspense>
            }
          />
          <Route
            path="templates"
            element={
              <Suspense fallback={<PageFallback />}>
                <Templates />
              </Suspense>
            }
          />
          <Route path="settings/plugins" element={<Navigate to="/plugins" replace />} />
          <Route
            path="ai"
            element={
              <Suspense fallback={<PageFallback />}>
                <AISettings />
              </Suspense>
            }
          />
          <Route
            path="settings/llm-providers"
            element={<Navigate to="/ai" replace />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
    </Routes>
  );
}
