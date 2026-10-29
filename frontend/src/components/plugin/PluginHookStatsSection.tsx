import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { getPluginHookStats, resetPluginHookStats, type PluginHookStat } from "@/api/plugins";
import { Button } from "@/components/ui/button";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import { SignalPill } from "@/components/ui/status";
import { getErrMsg } from "@/lib/api";
import {
  eventTypeLabel,
  formatDurationMs,
  formatRate,
  formatRelativeSeconds,
  hookHealth,
  hookLabel,
  summarizeHookStats,
} from "@/lib/pluginHookStats";

const HEALTH_TONE = {
  ok: "success",
  warn: "warn",
  danger: "danger",
  idle: "outline",
} as const;

const HEALTH_LABEL = {
  ok: "正常",
  warn: "有失败",
  danger: "失败偏多",
  idle: "无调用",
} as const;

/** 插件详情里的「运行统计」：每个钩子 / 事件处理的调用数、耗时、失败数，以及已弃用接口调用。 */
export function PluginHookStatsSection({
  pluginKey,
  accountNames,
}: {
  pluginKey: string;
  accountNames?: Record<number, string>;
}) {
  const queryClient = useQueryClient();
  const statsQ = useQuery({
    queryKey: ["plugin-hook-stats", pluginKey],
    queryFn: () => getPluginHookStats(pluginKey),
    retry: false,
    staleTime: 15_000,
  });
  const resetMut = useMutation({
    mutationFn: () => resetPluginHookStats(pluginKey),
    onSuccess: async () => {
      toast.success("已清零运行统计");
      await queryClient.invalidateQueries({ queryKey: ["plugin-hook-stats", pluginKey] });
    },
    onError: (error) => toast.error(`清零失败：${getErrMsg(error)}`),
  });

  const data = statsQ.data;
  const summary = summarizeHookStats(data);
  const hooks = data?.hooks ?? [];
  const deprecations = data?.deprecations ?? [];

  return (
    <section className="rounded-md border border-border/70 bg-background/80 p-4" data-plugin-hook-stats>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium">运行统计</div>
          <div className="text-xs text-muted-foreground">
            按钩子统计调用数、耗时与失败数，保留到最近一次调用后 {data?.retention_days ?? 7} 天
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 px-2 text-xs"
            disabled={statsQ.isFetching}
            onClick={() => void statsQ.refetch()}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            刷新
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 px-2 text-xs"
            disabled={resetMut.isPending || hooks.length + deprecations.length === 0}
            onClick={() => {
              if (window.confirm("清零这个插件的运行统计和弃用计数？")) resetMut.mutate();
            }}
          >
            <RotateCcw className="mr-1 h-3.5 w-3.5" />
            清零
          </Button>
        </div>
      </div>

      {statsQ.isLoading ? (
        <div className="flex h-16 items-center justify-center"><Spinner className="text-primary" /></div>
      ) : statsQ.isError ? (
        <div className="text-sm text-destructive">运行统计读取失败：{getErrMsg(statsQ.error)}</div>
      ) : data && !data.available ? (
        <div className="text-sm text-muted-foreground">{data.message || "统计存储暂时不可用。"}</div>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <SignalPill tone={summary.calls > 0 ? "primary" : "neutral"} label="调用" value={summary.calls} />
            <SignalPill tone={summary.failed > 0 ? "danger" : "neutral"} label="失败" value={summary.failed} />
            <SignalPill tone={summary.timeout > 0 ? "warn" : "neutral"} label="超时" value={summary.timeout} />
            <SignalPill tone="neutral" label="平均耗时" value={formatDurationMs(summary.avgMs)} />
          </div>

          {deprecations.length > 0 ? (
            <div className="mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2" data-plugin-deprecations>
              <div className="flex items-center gap-2 text-xs font-medium text-warning">
                <AlertTriangle className="h-3.5 w-3.5" />
                使用了已弃用接口 {summary.deprecatedCalls} 次
              </div>
              <div className="mt-2 space-y-1.5 text-xs text-warning">
                {deprecations.map((item) => (
                  <div key={`${item.account_id}-${item.id}`} className="break-all leading-5">
                    <span className="font-mono">{item.id}</span> × {item.count}
                    {item.summary ? `：${item.summary}` : ""}
                    {item.replacement ? `；请改用 ${item.replacement}` : ""}
                    {item.removal ? `（计划移除：${item.removal}）` : ""}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {hooks.length === 0 ? (
            <div className="mt-3 text-xs text-muted-foreground">还没有调用记录。插件收到事件、执行配置动作或启动后，这里会显示统计。</div>
          ) : (
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              {hooks.map((row) => (
                <HookStatRow
                  key={`${row.account_id}-${row.hook}-${row.event_type}`}
                  row={row}
                  accountName={row.account_id ? accountNames?.[row.account_id] : undefined}
                />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function HookStatRow({ row, accountName }: { row: PluginHookStat; accountName?: string }) {
  const health = hookHealth(row);
  const eventType = eventTypeLabel(row.event_type);
  const skipped = (row.circuit_open ?? 0) + (row.skipped ?? 0);
  return (
    <div className="min-w-0 rounded-md border border-border/60 bg-muted/20 px-3 py-2" data-plugin-hook-row={row.hook}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="text-sm font-medium">{hookLabel(row.hook)}</span>
        {eventType ? <span className="truncate font-mono text-[11px] text-muted-foreground">{eventType}</span> : null}
        <MetaBadge tone={HEALTH_TONE[health]}>{HEALTH_LABEL[health]}</MetaBadge>
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">
        {row.account_id ? `${accountName || `账号 #${row.account_id}`} · ` : ""}
        最近 {formatRelativeSeconds(row.last_at)}
      </div>
      <dl className="mt-2 grid grid-cols-4 gap-1 text-xs">
        <HookMetric label="调用" value={row.calls} />
        <HookMetric label="失败率" value={formatRate(row.failure_rate)} />
        <HookMetric label="平均" value={formatDurationMs(row.avg_ms)} />
        <HookMetric label="P95" value={formatDurationMs(row.p95_ms)} />
      </dl>
      {(row.failed ?? 0) + (row.timeout ?? 0) + skipped > 0 ? (
        <div className="mt-1 text-[11px] text-muted-foreground">
          失败 {row.failed ?? 0} · 超时 {row.timeout ?? 0}
          {skipped > 0 ? ` · 熔断/更新跳过 ${skipped}` : ""}
        </div>
      ) : null}
      {row.last_error ? (
        <div className="mt-1 line-clamp-2 break-all text-[11px] text-destructive" title={row.last_error}>
          最近错误（{formatRelativeSeconds(row.last_error_at)}）：{row.last_error}
        </div>
      ) : null}
    </div>
  );
}

function HookMetric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium tabular-nums">{value}</dd>
    </div>
  );
}
