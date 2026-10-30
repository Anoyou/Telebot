import { Navigate, Route, Routes } from "react-router-dom";

import { SchedulerWhitelistRedirect } from "./SchedulerCommandWhitelist";
import { OperationsSchedulerPage } from "./Scheduler";
import { OperationsTemplatesPage } from "./Templates";

/**
 * 指令与任务共用一个懒加载边界。首次进入时一次加载整个工作区，后续切换
 * 只替换子页内容，不再卸载工作区壳或闪回全页加载骨架。
 */
export function OperationsWorkspaceRoutes() {
  return (
    <Routes>
      <Route index element={<Navigate to="/operations/templates" replace />} />
      <Route path="templates" element={<OperationsTemplatesPage />} />
      <Route path="scheduler" element={<OperationsSchedulerPage />} />
      {/* 第 20 轮第二批：白名单并入「定时任务」，旧地址跳转并保留 aid */}
      <Route path="auto-command-whitelist" element={<SchedulerWhitelistRedirect />} />
      <Route path="*" element={<Navigate to="/operations/templates" replace />} />
    </Routes>
  );
}
