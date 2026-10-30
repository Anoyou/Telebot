// 全局横幅：前后端构建版本不一致、紧急停止、值守模式。
//
// 设计：
//  - 单独组件，不耦合 TopBar；放在 AppShell 内顶端
//  - 紧急停止与 TopBar 按钮共享 react-query cache key，点切换会立即联动
//  - 值守与插件页「运行模式」卡共享 ["system", "runtime-profile"]；两者都是「出事先止血」，
//    开启时都在这里常驻提示（第 20 轮：此前值守没有任何全局提示）
//  - 旧服务器没有 /api/platform/profile 时请求失败 → 不显示值守横幅
//  - 都不显示时返回 null（不占空间）
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MoonStar, RefreshCw, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getBackendVersion, getRuntimeProfile, restoreRuntimeProfile } from "@/api/system";
import { api, getErrMsg } from "@/lib/api";
import { APP_VERSION } from "@/lib/version";

interface KillSwitchState {
  enabled: boolean;
}

async function fetchKillSwitch(): Promise<KillSwitchState> {
  const { data } = await api.get<KillSwitchState>("/api/system/kill-switch");
  return data;
}

export function GlobalAlertBar() {
  return (
    <>
      <VersionMismatchBar />
      <KillSwitchBar />
      <SafeWatchBar />
    </>
  );
}

function VersionMismatchBar() {
  const { data, error } = useQuery({
    queryKey: ["system", "version"],
    queryFn: getBackendVersion,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    retry: 1,
    refetchOnWindowFocus: true,
  });

  if (error || !data || data.version === APP_VERSION) return null;
  return <VersionMismatchContent backendVersion={data.version} />;
}

async function hardRefreshWithoutSw(): Promise<void> {
  if ("serviceWorker" in navigator) {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((registration) => registration.unregister()));
    } catch {
      // 继续清理其它缓存并强制刷新。
    }
  }
  if ("caches" in window) {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch {
      // Cache Storage 不可用时仍继续强制刷新。
    }
  }
  const target = new URL(window.location.href);
  target.searchParams.set("_v", String(Date.now()));
  window.location.replace(target.toString());
}

function VersionMismatchContent({ backendVersion }: { backendVersion: string }) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 border-b border-warning/40 bg-warning/10 px-4 py-2 text-sm text-warning"
    >
      <div className="flex min-w-0 items-center gap-2">
        <RefreshCw className="h-4 w-4 shrink-0" />
        <span className="font-medium">前端资源需要刷新</span>
        <span className="hidden text-warning sm:inline">
          前端 v{APP_VERSION} · 后端 v{backendVersion}，请清理旧 PWA 缓存
        </span>
      </div>
      <Button
        size="sm"
        variant="outline"
        className="shrink-0 border-warning/50 bg-warning/15 hover:bg-warning/25"
        onClick={() => void hardRefreshWithoutSw()}
      >
        清缓存重载
      </Button>
    </div>
  );
}

// ── 紧急停止 ────────────────────────────────────────────
function KillSwitchBar() {
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["system", "kill-switch"],
    queryFn: fetchKillSwitch,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  const mut = useMutation({
    mutationFn: async () => {
      await api.post("/api/system/kill-switch", { enabled: false });
    },
    onSuccess: () => {
      toast.success("已解除紧急停止，账号恢复运行");
      setConfirmOpen(false);
      qc.invalidateQueries({ queryKey: ["system", "kill-switch"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!data?.enabled) return null;

  return (
    <>
      <div
        role="alert"
        data-global-alert="kill-switch"
        className="
          flex items-center justify-between gap-3
          border-b border-destructive/40 bg-destructive/10 px-4 py-2
          text-sm text-destructive
        "
      >
        <div className="flex min-w-0 items-center gap-2">
          <ShieldAlert className="h-4 w-4 shrink-0" />
          <span className="font-medium">紧急停止已开启</span>
          <span className="hidden text-muted-foreground sm:inline">
            所有账号 worker 已停止，解除后自动恢复 active 账号
          </span>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={mut.isPending}
          onClick={() => setConfirmOpen(true)}
        >
          解除
        </Button>
      </div>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>解除紧急停止？</DialogTitle>
            <DialogDescription>
              解除后状态为 active 的账号 worker 会自动重新启动，自动回复、转发、定时任务和 AI 指令恢复处理。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="!flex !flex-row gap-2 sm:space-x-0 [&>*]:min-w-0 [&>*]:flex-1 sm:[&>*]:flex-none">
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={mut.isPending}>
              取消
            </Button>
            <Button onClick={() => mut.mutate()} loading={mut.isPending} loadingText="正在解除">
              确认解除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ── 值守模式 ────────────────────────────────────────────
const SAFE_WATCH_STATUS_TEXT: Record<string, string> = {
  applying: "正在进入值守",
  active: "值守中",
  restoring: "正在恢复生产",
  failed: "值守收敛失败",
};

function SafeWatchBar() {
  const qc = useQueryClient();
  const location = useLocation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["system", "runtime-profile"],
    queryFn: getRuntimeProfile,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    staleTime: 5_000,
    // 旧服务器没有该接口：失败即不显示，不重试刷屏。
    retry: false,
  });

  const restoreMut = useMutation({
    mutationFn: restoreRuntimeProfile,
    onSuccess: () => {
      toast.success("已恢复生产：按进入值守前的快照还原，插件投递与定时任务恢复");
      setConfirmOpen(false);
    },
    onError: (err) => toast.error(getErrMsg(err)),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["system", "runtime-profile"] });
      qc.invalidateQueries({ queryKey: ["system", "capabilities"] });
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
      qc.invalidateQueries({ queryKey: ["platform", "tree"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
      qc.invalidateQueries({ queryKey: ["account"] });
    },
  });

  if (data?.active_profile !== "safe_watch") return null;
  const statusText = SAFE_WATCH_STATUS_TEXT[data.status] ?? "值守中";
  const busy = data.status === "applying" || data.status === "restoring";
  const onPluginsPage = location.pathname === "/plugins";

  return (
    <>
      <div
        role="alert"
        data-global-alert="safe-watch"
        className="flex items-center justify-between gap-3 border-b border-warning/40 bg-warning/10 px-4 py-2 text-sm text-warning"
      >
        <div className="flex min-w-0 items-center gap-2">
          <MoonStar className="h-4 w-4 shrink-0" />
          <span className="font-medium">{statusText}</span>
          <span className="hidden text-muted-foreground sm:inline">
            插件投递、定时任务和资金动作已暂停；消息照常记录，内置管理命令可用
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {onPluginsPage ? null : (
            <Button asChild size="sm" variant="ghost" className="hidden sm:inline-flex">
              <Link to="/plugins">查看运行模式</Link>
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={busy || restoreMut.isPending}
            onClick={() => setConfirmOpen(true)}
          >
            恢复生产
          </Button>
        </div>
      </div>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>恢复生产？</DialogTitle>
            <DialogDescription>
              按进入值守前的快照逐项还原模块开关，插件投递、定时任务和资金动作恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="!flex !flex-row gap-2 sm:space-x-0 [&>*]:min-w-0 [&>*]:flex-1 sm:[&>*]:flex-none">
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={restoreMut.isPending}>
              取消
            </Button>
            <Button onClick={() => restoreMut.mutate()} loading={restoreMut.isPending} loadingText="正在恢复">
              确认恢复生产
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
