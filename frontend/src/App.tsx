import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/layout/RequireAuth";

import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { AccountList } from "@/pages/Accounts/List";
import { AccountWizard } from "@/pages/Accounts/Wizard";
import { AccountDetail } from "@/pages/Accounts/Detail";
import { AutoReplyConfig } from "@/pages/Features/AutoReply";
import { ForwardConfig } from "@/pages/Features/Forward";
import { SchedulerConfig } from "@/pages/Features/Scheduler";
import { Logs } from "@/pages/Logs";
import { SettingsIndex } from "@/pages/Settings/Index";
import { CommandTemplates } from "@/pages/Settings/CommandTemplates";
import { Extensions } from "@/pages/Extensions";
import { AISettings } from "@/pages/AISettings";
import { Templates } from "@/pages/Templates";

type AppErrorBoundaryState = { hasError: boolean };

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
            <Route path=":aid/features/forward" element={<ForwardConfig />} />
            <Route path=":aid/features/scheduler" element={<SchedulerConfig />} />
          </Route>
          <Route path="extensions" element={<Extensions />} />
          <Route path="matrix" element={<Navigate to="/extensions" replace />} />
          <Route path="plugins" element={<Navigate to="/extensions" replace />} />
          <Route path="logs" element={<Logs />} />
          <Route path="settings" element={<SettingsIndex />} />
          <Route path="settings/commands" element={<CommandTemplates />} />
          <Route path="templates" element={<Templates />} />
          <Route path="settings/plugins" element={<Navigate to="/extensions" replace />} />
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
