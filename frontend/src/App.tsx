import React from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/layout/RequireAuth";

import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { AccountList } from "@/pages/Accounts/List";
import { AccountWizard } from "@/pages/Accounts/Wizard";
import { AccountDetail } from "@/pages/Accounts/Detail";
import { AutoReplyConfig } from "@/pages/Features/AutoReply";
import { AutorepeatConfig } from "@/pages/Features/Autorepeat";
import { CodexImageConfigPage } from "@/pages/Features/CodexImageConfig";
import { ForwardConfig } from "@/pages/Features/Forward";
import { SchedulerConfig } from "@/pages/Features/Scheduler";
import { Game24ConfigPage } from "@/pages/Features/Game24Config";
import { FeatureTodoPage } from "@/pages/Features/TodoPage";
import { Logs } from "@/pages/Logs";
import { SettingsIndex } from "@/pages/Settings/Index";
import { CommandTemplates } from "@/pages/Settings/CommandTemplates";
import { Extensions } from "@/pages/Extensions";
import { AISettings } from "@/pages/AISettings";
import { Templates } from "@/pages/Templates";

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
            <Route path="new" element={<AccountWizard />} />
            <Route path=":aid" element={<AccountDetail />} />
            <Route path=":aid/features/auto_reply" element={<AutoReplyConfig />} />
            <Route path=":aid/features/autorepeat" element={<AutorepeatConfig />} />
            <Route path=":aid/features/codex_image" element={<CodexImageConfigPage />} />
            <Route path=":aid/features/forward" element={<ForwardConfig />} />
            <Route path=":aid/features/scheduler" element={<SchedulerConfig />} />
            <Route path=":aid/features/game24" element={<Game24ConfigPage />} />
            <Route
              path=":aid/features/:featureKey"
              element={
                <FeatureCatchAll />
              }
            />
          </Route>
          <Route path="plugins" element={<Extensions />} />
          <Route path="matrix" element={<Navigate to="/plugins" replace />} />
          <Route path="extensions" element={<Navigate to="/plugins" replace />} />
          <Route path="remote-plugins" element={<Navigate to="/plugins" replace />} />
          <Route path="logs" element={<Logs />} />
          <Route path="settings" element={<SettingsIndex />} />
          <Route path="settings/commands" element={<CommandTemplates />} />
          <Route path="templates" element={<Templates />} />
          <Route path="settings/plugins" element={<Navigate to="/plugins" replace />} />
          <Route path="ai" element={<AISettings />} />
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
