import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ListTree } from "lucide-react";

import { getSystemSettings, getSystemSettingsMeta } from "@/api/system";
import type { SystemSettingMeta, SystemSettingsMeta } from "@/api/types";
import { flattenSettings, formatSettingValue, originalName, readSettingValue } from "@/lib/serverParams";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

// 第 21 轮：「全部服务器参数」一览。每个参数显示 中文名 · 英文原名、一句大白话说明、当前值，
// 以及在哪个页面修改（跳转链接）。中文名与说明来自后端 GET /api/system/settings/meta（与 iOS 同源）；
// 旧服务器没有该接口时按原键名列出。只读一览，不在这里提交修改（各参数都有专门的编辑位置）。

function matches(param: SystemSettingMeta, keyword: string): boolean {
  if (!keyword) return true;
  const haystack = [param.key, param.label, param.description, ...(param.also_in ?? []).map((loc) => loc.label)]
    .join(" ")
    .toLowerCase();
  return haystack.includes(keyword.toLowerCase());
}

function ParamRow({ param, settings }: { param: SystemSettingMeta; settings: unknown }) {
  const value = readSettingValue(settings, param.key);
  const locations = param.also_in ?? [];
  return (
    <li className="space-y-1.5 py-3" data-testid={`server-param-${param.key}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <span className="text-sm font-medium">{param.label}</span>
          <span className="mx-1.5 text-muted-foreground">·</span>
          <code className="break-all text-xs text-muted-foreground">{originalName(param.key)}</code>
        </div>
        <span className="font-mono text-sm tabular-nums" data-testid={`server-param-value-${param.key}`}>
          {formatSettingValue(value, param)}
        </span>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        {param.description}
        {param.min != null && param.max != null ? ` 范围 ${param.min}～${param.max}。` : ""}
        {param.zero_means ? ` 0 = ${param.zero_means}。` : ""}
      </p>
      {param.read_only_reason ? (
        <p className="text-xs leading-5 text-muted-foreground">只读：{param.read_only_reason}</p>
      ) : null}
      {locations.length ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {locations.map((loc) =>
            loc.web_route ? (
              <Link
                key={`${loc.label}-${loc.web_route}`}
                to={loc.web_route}
                className="inline-flex items-center gap-0.5 text-primary hover:underline"
              >
                去「{loc.label}」修改
                <ArrowUpRight className="h-3 w-3" aria-hidden />
              </Link>
            ) : (
              <span key={loc.label} className="text-muted-foreground">
                在「{loc.label}」修改
              </span>
            ),
          )}
          {locations.some((loc) => loc.ios_label) ? (
            <span className="text-muted-foreground">
              App：{locations.filter((loc) => loc.ios_label).map((loc) => loc.ios_label).join("、")}
            </span>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function MetaGroups({ meta, settings, keyword }: { meta: SystemSettingsMeta; settings: unknown; keyword: string }) {
  const groups = meta.groups
    .map((group) => ({ group, params: meta.params.filter((param) => param.group === group.id && matches(param, keyword)) }))
    .filter((entry) => entry.params.length);
  if (!groups.length) return <p className="py-4 text-sm text-muted-foreground">没有匹配的参数。</p>;
  return (
    <div className="space-y-2">
      {groups.map(({ group, params }) => (
        <details
          key={group.id}
          data-testid={`server-param-group-${group.id}`}
          className="rounded-md border border-border/70 bg-muted/10 px-3"
          open={Boolean(keyword)}
        >
          <summary className="cursor-pointer select-none py-2.5 text-sm font-medium">
            {group.label}
            <span className="ml-2 text-xs font-normal text-muted-foreground">{params.length} 项 · {group.description}</span>
          </summary>
          <ul className="divide-y divide-border/60">
            {params.map((param) => (
              <ParamRow key={param.key} param={param} settings={settings} />
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}

export function ServerParamsOverview() {
  const [keyword, setKeyword] = useState("");
  const settingsQ = useQuery({ queryKey: ["system", "settings"], queryFn: getSystemSettings });
  const metaQ = useQuery({ queryKey: ["system", "settings-meta"], queryFn: getSystemSettingsMeta, staleTime: 5 * 60_000 });
  const fallbackRows = useMemo(
    () => flattenSettings(settingsQ.data).filter((row) => !keyword || row.key.toLowerCase().includes(keyword.toLowerCase())),
    [settingsQ.data, keyword],
  );
  const meta = metaQ.data;

  return (
    <Card data-testid="server-params-overview">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ListTree className="h-4 w-4" aria-hidden />
          全部服务器参数
        </CardTitle>
        <CardDescription>
          服务器上每个参数的中文名、英文原名和一句说明。这里只看不改：点「去 xx 修改」到对应页面调整，同一个参数只在一个地方改，避免两处不一致。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索参数：中文名、英文原名或页面"
          aria-label="搜索服务器参数"
          className="max-w-sm"
        />
        {settingsQ.isLoading || metaQ.isLoading ? (
          <p className="text-sm text-muted-foreground">正在读取服务器参数…</p>
        ) : meta ? (
          <MetaGroups meta={meta} settings={settingsQ.data} keyword={keyword.trim()} />
        ) : (
          <div className="space-y-2" data-testid="server-params-fallback">
            <p className="text-xs text-muted-foreground">这个服务器版本还没有参数说明，按原键名列出；升级服务器后会显示中文名。</p>
            <ul className="divide-y divide-border/60 rounded-md border border-border/70 px-3">
              {fallbackRows.map((row) => (
                <li key={row.key} className="flex flex-wrap items-baseline justify-between gap-2 py-2 text-sm">
                  <code className="text-xs">{row.key}</code>
                  <span className="font-mono tabular-nums">{formatSettingValue(row.value)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
