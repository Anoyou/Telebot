import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, History } from "lucide-react";

import { getPluginCapabilities, getPluginCompatUsage } from "@/api/plugins";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import { SectionHeader } from "@/components/ui/status";
import { getErrMsg } from "@/lib/api";
import { compatPluginLabel, sortCompatUsageItems, summarizeCompatUsage } from "@/lib/compatUsage";
import { formatRelativeSeconds } from "@/lib/pluginHookStats";
import { cn } from "@/lib/utils";

/**
 * 兼容层使用统计（只读，第 22 轮）：宿主为旧插件保留的 20 项兼容层各被哪些插件用了多少次。
 * 只给管理员做“还有没有人在用”的判断依据；不删除、不警告，插件作者和群用户都看不到。
 * 旧服务器（capabilities 没有 compat_usage）不显示。
 */
export function CompatUsageCard() {
  const [expanded, setExpanded] = useState(false);
  const capsQ = useQuery({
    queryKey: ["plugins", "capabilities"],
    queryFn: getPluginCapabilities,
    staleTime: 5 * 60_000,
  });
  const supported = capsQ.data?.compat_usage === true;
  const usageQ = useQuery({
    queryKey: ["plugins", "compat-usage"],
    queryFn: getPluginCompatUsage,
    enabled: supported && expanded,
    staleTime: 30_000,
  });

  if (!supported) return null;

  const data = usageQ.data;
  const items = sortCompatUsageItems(data?.items);
  const summary = summarizeCompatUsage(data?.items);

  return (
    <Card data-testid="compat-usage-card">
      <CardHeader className="pb-3">
        <SectionHeader
          icon={History}
          title="兼容层使用统计"
          description="宿主为旧插件、旧数据保留的兼容路径各被哪些插件用到、用了多少次。只读，只统计不删除，也不会提醒插件作者或群用户。"
          actions={(
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
            >
              <ChevronDown className={cn("mr-1 h-4 w-4 transition-transform", expanded && "rotate-180")} />
              {expanded ? "收起" : "查看统计"}
            </Button>
          )}
        />
      </CardHeader>
      {expanded ? (
        <CardContent className="space-y-3">
          {usageQ.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-4 w-4" />正在读取兼容层使用统计
            </div>
          ) : usageQ.isError ? (
            <p className="text-sm text-destructive">读取失败：{getErrMsg(usageQ.error)}</p>
          ) : data ? (
            <>
              {data.available === false ? (
                <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
                  {data.message || "统计暂时读不到，稍后再试。"}
                </div>
              ) : null}
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" data-testid="compat-usage-summary">
                <MetaBadge tone={summary.used > 0 ? "info" : "neutral"}>
                  {summary.used} / {summary.trackable} 项有人在用
                </MetaBadge>
                <span>累计 {summary.total.toLocaleString()} 次</span>
              </div>
              <p className="text-xs text-muted-foreground">
                各进程最多每 {Math.round((data.flush_interval_seconds || 60) / 60) || 1} 分钟写入一次，刚发生的使用可能稍后才出现；“暂无使用”只代表这台服务器自统计上线以来没有用到。
              </p>
              <ul className="divide-y rounded-md border">
                {items.map((item) => (
                  <li key={item.id} className="px-3 py-2.5" data-testid={`compat-usage-${item.id}`}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                          <span className="font-mono text-xs text-muted-foreground">{item.id}</span>
                          <span className="min-w-0 break-words">{item.name}</span>
                        </div>
                        <div className="mt-0.5 text-xs text-muted-foreground">{item.counts}</div>
                      </div>
                      <div className="shrink-0 text-right text-xs">
                        {!item.trackable ? (
                          <MetaBadge tone="outline">无法计数</MetaBadge>
                        ) : (item.total ?? 0) > 0 ? (
                          <>
                            <div className="font-medium tabular-nums">{(item.total ?? 0).toLocaleString()} 次</div>
                            <div className="text-muted-foreground">最近 {formatRelativeSeconds(item.last_at)}</div>
                          </>
                        ) : (
                          <span className="text-muted-foreground">暂无使用</span>
                        )}
                      </div>
                    </div>
                    {(item.plugins ?? []).length > 0 ? (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {(item.plugins ?? []).slice(0, 8).map((row) => (
                          <MetaBadge key={row.plugin_key} tone="neutral" title={`最近 ${formatRelativeSeconds(row.last_at)}`}>
                            {compatPluginLabel(row.plugin_key)} · {row.count.toLocaleString()}
                          </MetaBadge>
                        ))}
                        {(item.plugins ?? []).length > 8 ? (
                          <span className="text-xs text-muted-foreground">等 {(item.plugins ?? []).length} 个插件</span>
                        ) : null}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  );
}
