import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import {
  confirmSystemAgentAction,
  listSystemAgentActions,
  rejectSystemAgentAction,
  type SystemAgentAction,
} from "@/api/systemAgent";
import { PageHeader, PageShell } from "@/components/layout/PageScaffold";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/misc";
import { getErrMsg } from "@/lib/api";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/feedback/EmptyState";
import { useAssistantDock } from "@/components/assistant/AssistantDock";
import { AgentMark } from "@/components/assistant/AgentMark";

function formatExpiry(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function sourceLabel(action: SystemAgentAction): { text: string; href?: string } {
  const title = action.session_title || (action.session_id ? `会话 ${action.session_id.slice(0, 8)}…` : "未知会话");
  if (action.session_origin === "scheduled") {
    return {
      text: `定时 · ${title}`,
      href: action.session_id ? `/assistant?session=${action.session_id}` : undefined,
    };
  }
  if (action.session_id) {
    return {
      text: title,
      href: `/assistant?session=${action.session_id}`,
    };
  }
  return { text: title };
}

export function ActionsInboxPage() {
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);
  const { setCollapsed: setDockCollapsed } = useAssistantDock();
  const [batchBusy, setBatchBusy] = useState(false);

  const pendingQ = useQuery({
    queryKey: ["system-agent", "actions", "pending-inbox"],
    queryFn: () => listSystemAgentActions({ status: "pending", limit: 100 }),
    refetchInterval: 15_000,
  });

  const items = useMemo(() => pendingQ.data || [], [pendingQ.data]);

  const invalidate = async () => {
    await qc.invalidateQueries({ queryKey: ["system-agent", "actions"] });
  };

  const confirmMut = useMutation({
    mutationFn: (id: string) => confirmSystemAgentAction(id),
    onSuccess: async (res) => {
      if (res.ok) toast.success(res.already_final ? "操作已完成" : "已确认并执行");
      else toast.error(res.error_message || "执行失败");
      await invalidate();
    },
    onError: (e) => toast.error(getErrMsg(e)),
    onSettled: () => setBusyId(null),
  });

  const rejectMut = useMutation({
    mutationFn: (id: string) => rejectSystemAgentAction(id),
    onSuccess: async () => {
      toast.message("已拒绝");
      await invalidate();
    },
    onError: (e) => toast.error(getErrMsg(e)),
    onSettled: () => setBusyId(null),
  });

  const onBatchReject = async () => {
    if (!items.length) return;
    if (!(await confirmDialog({ title: `批量拒绝 ${items.length} 条待确认操作？`, description: "这些操作不会执行，Agent 会收到「已拒绝」。", confirmLabel: "全部拒绝", tone: "danger" }))) return;
    setBatchBusy(true);
    let ok = 0;
    let fail = 0;
    for (const item of items) {
      try {
        await rejectSystemAgentAction(item.id);
        ok += 1;
      } catch {
        fail += 1;
      }
    }
    setBatchBusy(false);
    await invalidate();
    if (fail === 0) toast.success(`已拒绝 ${ok} 条`);
    else toast.message(`拒绝 ${ok} 条，失败 ${fail} 条`);
  };

  return (
    <PageShell>
      <PageHeader
        title="待确认"
        description="Agent 挂起的写操作在这里确认或拒绝；入口挂在侧栏顶部「Agent」的红点上。"
        icon={Inbox}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setDockCollapsed(false)}>
              <AgentMark className="mr-1 h-4 w-4" />
              回到 Agent
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void pendingQ.refetch()}
              disabled={pendingQ.isFetching}
            >
              <RefreshCw className={cn("mr-1 h-4 w-4", pendingQ.isFetching && "animate-spin")} />
              刷新
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!items.length || batchBusy}
              onClick={() => void onBatchReject()}
            >
              批量拒绝
            </Button>
          </div>
        }
      />

      {pendingQ.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={Inbox}
          size="lg"
          title="没有待确认的操作"
          description="Agent 发起的写操作会先停在这里等你确认。"
          action={
            <Button type="button" size="sm" variant="outline" onClick={() => setDockCollapsed(false)}>
              打开 Agent
            </Button>
          }
        />
      ) : (
        <ul className="space-y-3">
          {items.map((action) => {
            const source = sourceLabel(action);
            const dangerous = action.risk === "dangerous";
            const busy = busyId === action.id || batchBusy;
            return (
              <li
                key={action.id}
                className={cn(
                  "rounded-xl border bg-card p-4 shadow-sm",
                  dangerous && "border-amber-500/40",
                )}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {dangerous ? (
                        <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-800 dark:text-amber-200">
                          <AlertTriangle className="h-3 w-3" />
                          危险
                        </span>
                      ) : (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                          普通
                        </span>
                      )}
                      <span className="font-medium">{action.summary || action.tool_name}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      工具 <code className="text-[11px]">{action.tool_name}</code>
                      {action.account_id != null ? ` · 账号 #${action.account_id}` : null}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      来源：{" "}
                      {source.href ? (
                        <Link to={source.href} className="text-primary hover:underline">
                          {source.text}
                        </Link>
                      ) : (
                        source.text
                      )}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      过期：{formatExpiry(action.expires_at)}
                    </p>
                    {typeof action.preview?.warning === "string" && action.preview.warning ? (
                      <p className="text-xs text-amber-700 dark:text-amber-300">
                        ⚠️ {action.preview.warning}
                      </p>
                    ) : null}
                    {typeof action.preview?.note === "string" && action.preview.note ? (
                      <p className="text-xs text-muted-foreground">ℹ️ {action.preview.note}</p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button
                      type="button"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        setBusyId(action.id);
                        confirmMut.mutate(action.id);
                      }}
                    >
                      确认
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => {
                        setBusyId(action.id);
                        rejectMut.mutate(action.id);
                      }}
                    >
                      拒绝
                    </Button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </PageShell>
  );
}

export default ActionsInboxPage;
