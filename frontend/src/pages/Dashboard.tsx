// Dashboard：系统状态总览 + 账号状态卡
//
// 顶部新加 SystemHealthCard：DB / alembic / Redis / providers / proxies / workers
// 用 30s 轮询自动刷新，让"配置改动 / 子服务挂掉"这类变化几十秒内可见。
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import { SystemHealthCard } from "@/components/SystemHealthCard";
import { Spinner } from "@/components/ui/misc";
import { listAccounts } from "@/api/accounts";
import { getResourceDashboard } from "@/api/system";

export function Dashboard() {
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });
  const resourceQ = useQuery({
    queryKey: ["system", "resource-dashboard"],
    queryFn: getResourceDashboard,
    // 15s 默认（原 5s）：1C 机器上每 5s 跑一次进程扫描 + 5min log count 是常驻负担。
    // refetchIntervalInBackground=false：tab 切走后停止轮询，省 VPS 资源。
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">系统概览</h1>
        <p className="text-sm text-muted-foreground">
          系统状态 + 多账号运行状态一览
        </p>
      </div>

      {/* 系统状态卡（DB / alembic / Redis / providers / proxies / workers）*/}
      <SystemHealthCard />

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">资源占用</CardTitle>
          <p className="text-sm text-muted-foreground">
            主机与本项目整体资源快照（15 秒刷新）
          </p>
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          <div className="flex items-center justify-end">
            {resourceQ.data?.host.sampled_at ? (
              <span className="text-xs text-muted-foreground">
                采样时间：{new Date(resourceQ.data.host.sampled_at * 1000).toLocaleTimeString()}
              </span>
            ) : null}
          </div>
          {resourceQ.isLoading ? (
            <div className="flex h-16 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : resourceQ.error || !resourceQ.data ? (
            <div className="rounded border px-3 py-2 text-xs alert-danger">
              读取资源占用失败：{(resourceQ.error as Error)?.message || "未知错误"}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Metric label="主机 CPU" value={percent(resourceQ.data.host.cpu_percent)} />
                <Metric label="主机内存" value={percent(resourceQ.data.host.memory_used_percent)} />
                <Metric label="磁盘使用" value={percent(resourceQ.data.host.disk_used_percent)} />
                <Metric label="磁盘剩余" value={gb(resourceQ.data.host.disk_free_gb)} />
              </div>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Metric label="项目 CPU" value={percent(resourceQ.data.project_total.cpu_percent)} />
                <Metric label="项目内存" value={mb(resourceQ.data.project_total.rss_mb)} />
                <Metric
                  label="Worker 存活/应运行"
                  value={`${resourceQ.data.worker_alive}/${resourceQ.data.worker_desired_running}`}
                />
                <Metric
                  label="5 分钟日志"
                  value={`${resourceQ.data.logs.last_5m_total}（W${resourceQ.data.logs.last_5m_warn}/E${resourceQ.data.logs.last_5m_error}）`}
                />
              </div>
              {resourceQ.data.workers.length > 0 ? (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">Worker 内存 Top（最多 8 个）</p>
                  <div className="space-y-1">
                    {resourceQ.data.workers.map((w) => (
                      <div
                        key={`${w.account_id}-${w.pid ?? "na"}`}
                        className="flex flex-col gap-1 rounded border px-2 py-1.5 text-xs sm:flex-row sm:items-center sm:justify-between"
                      >
                        <span className="font-mono">
                          aid={w.account_id} · pid={w.pid ?? "-"} · {w.alive ? "alive" : "down"}
                        </span>
                        <span className="keep-words text-muted-foreground">
                          CPU {percent(w.cpu_percent)} · MEM {mb(w.rss_mb)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      {/* 账号状态卡 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">账号状态</CardTitle>
          <p className="text-sm text-muted-foreground">
            多账号运行状态一览与快捷入口
          </p>
        </CardHeader>
        <CardContent className="pt-0">
          {accountsQ.isLoading ? (
            <div className="flex h-24 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : accountsQ.data && accountsQ.data.length > 0 ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {accountsQ.data.map((a) => (
                <AccountSummaryCard key={a.id} account={a} />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 py-10 text-sm text-muted-foreground">
              <span>尚未绑定任何 TG 账号</span>
              <Button asChild size="sm">
                <Link to="/accounts/new">立即绑定</Link>
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function percent(v: number | null | undefined): string {
  return typeof v === "number" ? `${v.toFixed(1)}%` : "-";
}

function mb(v: number | null | undefined): string {
  return typeof v === "number" ? `${v.toFixed(1)} MB` : "-";
}

function gb(v: number | null | undefined): string {
  return typeof v === "number" ? `${v.toFixed(2)} GB` : "-";
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border bg-muted/20 p-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  );
}
