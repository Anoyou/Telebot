// Dashboard：系统状态总览 + 首页账号管理
//
// 顶部新加 SystemHealthCard：DB / alembic / Redis / providers / proxies / workers
// 用 30s 轮询自动刷新，让"配置改动 / 子服务挂掉"这类变化几十秒内可见。
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SystemHealthCard } from "@/components/SystemHealthCard";
import { AccountManagementPanel } from "@/components/accounts/AccountManagementPanel";
import { Spinner } from "@/components/ui/misc";
import { getResourceDashboard } from "@/api/system";

export function Dashboard() {
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
        <h1 className="text-2xl font-semibold tracking-tight">首页</h1>
        <p className="text-sm text-muted-foreground">
          系统状态、资源占用和账号管理都在这里。
        </p>
      </div>

      {/* 系统状态卡（DB / alembic / Redis / providers / proxies / workers）*/}
      <SystemHealthCard />

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">本项目资源占用</CardTitle>
          <p className="text-sm text-muted-foreground">
            统计 TelePilot 主进程 + 全部账号 worker 在这台服务器里实际占用的 CPU 和内存（15 秒刷新）
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
              <div className="grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
                <div className="rounded-2xl border bg-gradient-to-br from-sky-50 via-background to-emerald-50 p-4 dark:from-sky-950/20 dark:to-emerald-950/10">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    TelePilot 本项目合计
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <BigMetric
                      label="CPU 占用"
                      value={percent(resourceQ.data.project_total.cpu_percent)}
                      hint="主进程 + 所有账号 worker"
                    />
                    <BigMetric
                      label="内存占用"
                      value={mb(resourceQ.data.project_total.rss_mb)}
                      hint={projectMemoryHint(
                        resourceQ.data.project_total.rss_mb,
                        resourceQ.data.host.memory_total_mb,
                      )}
                    />
                  </div>
                </div>
                <div className="rounded-2xl border bg-muted/20 p-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    服务器总览
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <Metric label="整机 CPU 负载" value={percent(resourceQ.data.host.cpu_percent)} />
                    <Metric
                      label="整机内存"
                      value={hostMemoryLabel(
                        resourceQ.data.host.memory_used_percent,
                        resourceQ.data.host.memory_total_mb,
                      )}
                    />
                    <Metric label="磁盘使用" value={percent(resourceQ.data.host.disk_used_percent)} />
                    <Metric label="磁盘剩余" value={gb(resourceQ.data.host.disk_free_gb)} />
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <Metric
                  label="主进程"
                  value={`CPU ${percent(resourceQ.data.main_process.cpu_percent)} · ${mb(resourceQ.data.main_process.rss_mb)}`}
                />
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
                  <p className="text-xs text-muted-foreground">
                    Worker 进程明细（按内存排序，最多 8 个）
                  </p>
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

      <AccountManagementPanel
        title="账号管理"
        description="在首页直接完成新增、新手引导与账号启停等高频操作"
      />
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

function projectMemoryHint(
  rssMb: number | null | undefined,
  totalMb: number | null | undefined,
): string {
  if (typeof rssMb !== "number" || typeof totalMb !== "number" || totalMb <= 0) {
    return "服务器总内存占比未知";
  }
  return `约占服务器总内存 ${((rssMb / totalMb) * 100).toFixed(1)}%`;
}

function hostMemoryLabel(
  usedPercent: number | null | undefined,
  totalMb: number | null | undefined,
): string {
  const percentText = percent(usedPercent);
  return typeof totalMb === "number" ? `${percentText} / ${mb(totalMb)}` : percentText;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border bg-muted/20 p-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  );
}

function BigMetric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-xl border bg-background/80 p-3 shadow-sm">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{hint}</p>
    </div>
  );
}
