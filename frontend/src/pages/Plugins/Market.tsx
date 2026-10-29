// 插件市场（服务器 0.113.0+）：浏览、搜索、安装、更新角标、一键全部更新、来源管理、更新参数、健康窗口回滚。
//
// 旧服务器没有 /api/plugin-market 接口时显示“请更新服务器”，插件仓库 / Git / ZIP / 本地导入仍在“插件管理”里可用。
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Download,
  Lock,
  LockOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Store,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { PageShell } from "@/components/layout/PageScaffold";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/feedback/EmptyState";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";
import {
  KIND_LABELS,
  TRUST_LABELS,
  acceptMarketRollout,
  addMarketSource,
  deleteMarketSource,
  fetchPluginMarketCatalog,
  filterMarketItems,
  installFromMarket,
  patchMarketSource,
  rollbackMarketRollout,
  saveMarketSettings,
  setMarketPin,
  updateFromMarket,
  type PluginMarketCatalog,
  type PluginMarketItem,
  type PluginMarketSource,
  type PluginMarketUpdateResult,
} from "@/api/pluginMarket";

import { PluginWorkspaceHeader } from "./WorkspaceHeader";

export const PLUGIN_MARKET_QUERY_KEY = ["plugin-market", "catalog"] as const;

function trustTone(trust: string): "success" | "info" | "warn" {
  if (trust === "official") return "success";
  if (trust === "community") return "info";
  return "warn";
}

function isMissingEndpoint(error: unknown): boolean {
  const status = (error as { response?: { status?: number } } | null)?.response?.status;
  return status === 404 || status === 405;
}

const UPDATE_STATUS_LABELS: Record<string, string> = {
  updated: "已更新",
  skipped: "跳过",
  pinned: "已固定",
  failed: "失败",
};

export function PluginsMarketPage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [onlyUpdates, setOnlyUpdates] = useState(false);
  const [onlyInstalled, setOnlyInstalled] = useState(false);
  const [updateResult, setUpdateResult] = useState<PluginMarketUpdateResult | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const catalogQ = useQuery({
    queryKey: PLUGIN_MARKET_QUERY_KEY,
    queryFn: () => fetchPluginMarketCatalog(false),
    retry: (count, error) => !isMissingEndpoint(error) && count < 1,
  });
  const catalog = catalogQ.data;

  const setCatalog = (next: PluginMarketCatalog) => queryClient.setQueryData(PLUGIN_MARKET_QUERY_KEY, next);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: PLUGIN_MARKET_QUERY_KEY });

  const refreshM = useMutation({
    mutationFn: () => fetchPluginMarketCatalog(true),
    onSuccess: (next) => {
      setCatalog(next);
      toast.success("已重新拉取所有来源");
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const installM = useMutation({
    mutationFn: (item: PluginMarketItem) => {
      setBusyKey(item.id);
      return installFromMarket({ source_id: item.source_id, key: item.key });
    },
    onSuccess: (_data, item) => {
      toast.success(`已安装 ${item.name.zh}，到插件中心按账号启用`);
      void invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
    onSettled: () => setBusyKey(null),
  });

  const updateM = useMutation({
    mutationFn: (keys?: string[]) => {
      setBusyKey(keys && keys.length === 1 ? keys[0] : "__all__");
      return updateFromMarket(keys);
    },
    onSuccess: (result) => {
      setUpdateResult(result);
      if (result.failed) toast.warning(`更新完成：${result.updated} 个成功，${result.failed} 个失败`);
      else toast.success(`更新完成：${result.updated} 个已更新`);
      void invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
    onSettled: () => setBusyKey(null),
  });

  const pinM = useMutation({
    mutationFn: ({ key, pinned }: { key: string; pinned: boolean }) => setMarketPin(key, pinned),
    onSuccess: (data) => {
      toast.success(data.pinned_version ? `已固定在 ${data.pinned_version}` : "已取消固定");
      void invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const rollbackM = useMutation({
    mutationFn: (key: string) => rollbackMarketRollout(key),
    onSuccess: () => {
      toast.success("已回滚到更新前的版本");
      void invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const acceptM = useMutation({
    mutationFn: (key: string) => acceptMarketRollout(key),
    onSuccess: () => {
      toast.success("已确认新版本正常");
      void invalidate();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  const items = useMemo(
    () => filterMarketItems(catalog?.items ?? [], { query, kind, onlyUpdates, onlyInstalled }),
    [catalog?.items, query, kind, onlyUpdates, onlyInstalled],
  );
  const kinds = useMemo(
    () => Array.from(new Set((catalog?.items ?? []).map((item) => item.kind))).sort(),
    [catalog?.items],
  );
  const watching = (catalog?.rollouts ?? []).filter((item) => item.status === "watching");
  const recentRollbacks = (catalog?.rollouts ?? []).filter(
    (item) => item.status === "rolled_back" || item.status === "rollback_failed",
  );
  const updateCount = catalog?.updates_available ?? 0;

  if (catalogQ.isError && isMissingEndpoint(catalogQ.error)) {
    return (
      <PageShell>
        <PluginWorkspaceHeader activeTab="market" />
        <Card data-testid="plugin-market-unsupported">
          <CardContent className="py-8">
            <EmptyState
              icon={AlertTriangle}
              title="服务器版本过旧，暂不支持插件市场"
              description="插件市场需要 TelePilot 服务器 0.113.0 或更新。请先更新服务器；在此之前仍可在“插件管理”里用插件仓库、Git、ZIP 或本地导入安装插件。"
            />
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <PluginWorkspaceHeader activeTab="market" />

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Store className="h-5 w-5" />
              插件市场
            </CardTitle>
            <CardDescription>
              从官方和你添加的来源浏览插件。安装或更新后会观察
              {catalog?.settings.health_window_minutes ?? 10} 分钟，出现加载失败、熔断或失败率明显升高会自动回滚到旧版本。
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => refreshM.mutate()}
              disabled={refreshM.isPending}
              data-testid="plugin-market-refresh"
            >
              {refreshM.isPending ? <Spinner className="h-4 w-4" /> : <RefreshCw className="h-4 w-4" />}
              刷新来源
            </Button>
            <Button
              size="sm"
              onClick={() => updateM.mutate(undefined)}
              disabled={updateCount === 0 || updateM.isPending}
              data-testid="plugin-market-update-all"
            >
              {busyKey === "__all__" ? <Spinner className="h-4 w-4" /> : <Download className="h-4 w-4" />}
              全部更新{updateCount ? `（${updateCount}）` : ""}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索名称、简介、标签"
                className="pl-8"
                data-testid="plugin-market-search"
              />
            </div>
            <Select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="sm:w-40"
              data-testid="plugin-market-kind"
            >
              <option value="all">全部类型</option>
              {kinds.map((value) => (
                <option key={value} value={value}>
                  {KIND_LABELS[value] ?? value}
                </option>
              ))}
            </Select>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={onlyUpdates} onCheckedChange={setOnlyUpdates} data-testid="plugin-market-only-updates" />
              只看可更新
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={onlyInstalled} onCheckedChange={setOnlyInstalled} />
              只看已安装
            </label>
          </div>

          {watching.length > 0 && (
            <div className="space-y-2 rounded-lg border border-info/40 bg-info/5 p-3" data-testid="plugin-market-watching">
              <div className="text-sm font-medium">正在观察的更新</div>
              {watching.map((rollout) => (
                <div key={rollout.key} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span>
                    {rollout.key}：{rollout.from_version} → {rollout.to_version}
                    {rollout.deadline ? `，观察到 ${formatDateTime(rollout.deadline)}` : ""}
                  </span>
                  <span className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => acceptM.mutate(rollout.key ?? "")}
                      disabled={acceptM.isPending}
                    >
                      <ShieldCheck className="h-4 w-4" />
                      确认正常
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => rollbackM.mutate(rollout.key ?? "")}
                      disabled={rollbackM.isPending}
                      data-testid={`plugin-market-rollback-${rollout.key}`}
                    >
                      <RotateCcw className="h-4 w-4" />
                      立即回滚
                    </Button>
                  </span>
                </div>
              ))}
            </div>
          )}
          {recentRollbacks.length > 0 && (
            <div className="space-y-1 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm" data-testid="plugin-market-rollbacks">
              <div className="font-medium">已自动回滚</div>
              {recentRollbacks.map((rollout) => (
                <div key={rollout.key}>
                  {rollout.key}：{rollout.to_version} → {rollout.from_version}（{rollout.reason || "不健康"}）
                </div>
              ))}
            </div>
          )}

          {catalogQ.isLoading ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : catalogQ.isError ? (
            <EmptyState icon={AlertTriangle} title="读取插件市场失败" description={getErrMsg(catalogQ.error)} />
          ) : items.length === 0 ? (
            <EmptyState icon={Store} title="没有符合条件的插件" description="换个关键词，或在下方添加更多来源。" />
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" data-testid="plugin-market-grid">
              {items.map((item) => (
                <MarketItemCard
                  key={item.id}
                  item={item}
                  busy={busyKey === item.id || busyKey === item.key || busyKey === "__all__"}
                  onInstall={() => installM.mutate(item)}
                  onUpdate={() => updateM.mutate([item.key])}
                  onPin={(pinned) => pinM.mutate({ key: item.key, pinned })}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {catalog && <MarketSourcesCard catalog={catalog} onChanged={invalidate} />}
      {catalog && <MarketSettingsCard catalog={catalog} onSaved={invalidate} />}

      <Dialog open={updateResult !== null} onOpenChange={(open) => !open && setUpdateResult(null)}>
        <DialogContent data-testid="plugin-market-update-result">
          <DialogHeader>
            <DialogTitle>更新结果</DialogTitle>
            <DialogDescription>
              并发 {updateResult?.concurrency ?? 0} 个同时更新：{updateResult?.updated ?? 0} 个已更新，
              {updateResult?.skipped ?? 0} 个跳过，{updateResult?.failed ?? 0} 个失败。
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-80 space-y-2 overflow-y-auto">
            {(updateResult?.items ?? []).map((item) => (
              <div key={item.key} className="flex items-start justify-between gap-3 rounded-md border p-2 text-sm">
                <div className="min-w-0">
                  <div className="font-medium">{item.display_name || item.key}</div>
                  <div className="text-muted-foreground">
                    {item.from_version ?? "?"} → {item.to_version ?? "?"}
                    {item.message ? ` · ${item.message}` : ""}
                    {item.health_check ? " · 进入健康观察" : ""}
                  </div>
                </div>
                <MetaBadge
                  tone={item.status === "updated" ? "success" : item.status === "failed" ? "danger" : "neutral"}
                >
                  {UPDATE_STATUS_LABELS[item.status] ?? item.status}
                </MetaBadge>
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button onClick={() => setUpdateResult(null)}>好的</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageShell>
  );
}

function MarketItemCard({
  item,
  busy,
  onInstall,
  onUpdate,
  onPin,
}: {
  item: PluginMarketItem;
  busy: boolean;
  onInstall: () => void;
  onUpdate: () => void;
  onPin: (pinned: boolean) => void;
}) {
  const installed = item.installed;
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3" data-testid={`plugin-market-item-${item.key}`}>
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-muted text-xl" aria-hidden>
          {item.icon || "🧩"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate font-medium">{item.name.zh}</span>
            <MetaBadge tone={trustTone(item.trust)}>{TRUST_LABELS[item.trust] ?? item.trust}</MetaBadge>
            {item.update_available && !item.pinned_version && (
              <MetaBadge tone="info" data-testid={`plugin-market-badge-${item.key}`}>
                可更新
              </MetaBadge>
            )}
            {item.pinned_version && <MetaBadge tone="outline">已固定 {item.pinned_version}</MetaBadge>}
            {item.auto_update && installed && <MetaBadge tone="neutral">自动更新</MetaBadge>}
          </div>
          <div className="text-xs text-muted-foreground">
            {KIND_LABELS[item.kind] ?? item.kind} · v{item.version}
            {installed ? ` · 已装 v${installed.version}` : ""} · {item.source_name}
          </div>
        </div>
      </div>
      {item.summary && <p className="line-clamp-3 text-sm text-muted-foreground">{item.summary}</p>}
      {item.permissions.length > 0 && (
        <div className="text-xs text-muted-foreground">权限：{item.permissions.join("、")}</div>
      )}
      {!item.compatible && (
        <div className="text-xs text-destructive">{item.incompatible_reason || "与当前服务器不兼容"}</div>
      )}
      {installed && !installed.same_source && (
        <div className="text-xs text-muted-foreground">已安装的版本来自别的仓库，这里不提示更新。</div>
      )}
      <div className="mt-auto flex flex-wrap gap-2 pt-1">
        {!installed ? (
          <Button
            size="sm"
            onClick={onInstall}
            disabled={busy || !item.compatible}
            data-testid={`plugin-market-install-${item.key}`}
          >
            {busy ? <Spinner className="h-4 w-4" /> : <Download className="h-4 w-4" />}
            安装
          </Button>
        ) : item.update_available ? (
          <Button
            size="sm"
            onClick={onUpdate}
            disabled={busy || Boolean(item.pinned_version)}
            data-testid={`plugin-market-update-${item.key}`}
          >
            {busy ? <Spinner className="h-4 w-4" /> : <Download className="h-4 w-4" />}
            更新到 v{item.version}
          </Button>
        ) : (
          <MetaBadge tone="success">已是最新</MetaBadge>
        )}
        {installed && installed.same_source && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onPin(!item.pinned_version)}
            data-testid={`plugin-market-pin-${item.key}`}
          >
            {item.pinned_version ? <LockOpen className="h-4 w-4" /> : <Lock className="h-4 w-4" />}
            {item.pinned_version ? "取消固定" : "固定版本"}
          </Button>
        )}
      </div>
    </div>
  );
}

function sourceStatusText(source: PluginMarketSource): string {
  if (!source.enabled) return source.available ? "已停用" : "服务器已关闭官方源";
  if (source.status === "ok") return `${source.plugin_count} 个插件`;
  if (source.status === "fallback") return `拉取失败，使用内置副本（${source.plugin_count} 个）`;
  if (source.status === "stale") return `拉取失败，显示上次结果（${source.plugin_count} 个）`;
  return "拉取失败";
}

function MarketSourcesCard({ catalog, onChanged }: { catalog: PluginMarketCatalog; onChanged: () => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [trust, setTrust] = useState("unreviewed");

  const addM = useMutation({
    mutationFn: () => addMarketSource({ url: url.trim(), name: name.trim() || null, trust }),
    onSuccess: () => {
      toast.success("已添加来源");
      setUrl("");
      setName("");
      onChanged();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });
  const patchM = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof patchMarketSource>[1] }) =>
      patchMarketSource(id, body),
    onSuccess: () => onChanged(),
    onError: (error) => toast.error(getErrMsg(error)),
  });
  const deleteM = useMutation({
    mutationFn: (id: string) => deleteMarketSource(id),
    onSuccess: () => {
      toast.success("已删除来源");
      onChanged();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });

  return (
    <Card data-testid="plugin-market-sources">
      <CardHeader>
        <CardTitle>来源管理</CardTitle>
        <CardDescription>
          官方源不能删除，可以停用；自定义来源填 registry.json 的 https 地址。只有“官方”等级的来源可以打开自动更新（默认关闭）。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {catalog.sources.map((source) => (
          <div
            key={source.id}
            className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
            data-testid={`plugin-market-source-${source.id}`}
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{source.name}</span>
                <MetaBadge tone={trustTone(source.trust)}>{TRUST_LABELS[source.trust] ?? source.trust}</MetaBadge>
                {source.builtin && <MetaBadge tone="outline">内置</MetaBadge>}
              </div>
              <div className="truncate text-xs text-muted-foreground">{source.url || "（未配置）"}</div>
              <div className="text-xs text-muted-foreground">
                {sourceStatusText(source)}
                {source.fetched_at ? ` · 更新于 ${formatDateTime(source.fetched_at)}` : ""}
                {source.error ? ` · ${source.error}` : ""}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <label className="flex items-center gap-2">
                <Switch
                  checked={source.enabled}
                  disabled={!source.available}
                  onCheckedChange={(enabled) => patchM.mutate({ id: source.id, body: { enabled } })}
                  data-testid={`plugin-market-source-enabled-${source.id}`}
                />
                启用
              </label>
              {source.trust === "official" && (
                <label className="flex items-center gap-2">
                  <Switch
                    checked={source.auto_update}
                    disabled={!source.enabled}
                    onCheckedChange={(auto_update) => patchM.mutate({ id: source.id, body: { auto_update } })}
                    data-testid={`plugin-market-source-auto-${source.id}`}
                  />
                  自动更新
                </label>
              )}
              {!source.builtin && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => deleteM.mutate(source.id)}
                  data-testid={`plugin-market-source-delete-${source.id}`}
                >
                  <Trash2 className="h-4 w-4" />
                  删除
                </Button>
              )}
            </div>
          </div>
        ))}
        <div className="grid gap-2 rounded-lg border border-dashed p-3 sm:grid-cols-[2fr_1fr_auto_auto] sm:items-end">
          <div className="space-y-1">
            <Label htmlFor="market-source-url">来源地址</Label>
            <Input
              id="market-source-url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/registry.json"
              data-testid="plugin-market-source-url"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="market-source-name">名称（可选）</Label>
            <Input id="market-source-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="market-source-trust">信任等级</Label>
            <Select
              id="market-source-trust"
              value={trust}
              onChange={(event) => setTrust(event.target.value)}
              data-testid="plugin-market-source-trust"
            >
              <option value="unreviewed">未审核</option>
              <option value="community">社区</option>
              <option value="official">官方</option>
            </Select>
          </div>
          <Button
            onClick={() => addM.mutate()}
            disabled={!url.trim() || addM.isPending}
            data-testid="plugin-market-source-add"
          >
            <Plus className="h-4 w-4" />
            添加
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function MarketSettingsCard({ catalog, onSaved }: { catalog: PluginMarketCatalog; onSaved: () => void }) {
  const [concurrency, setConcurrency] = useState(String(catalog.settings.update_concurrency));
  const [windowMinutes, setWindowMinutes] = useState(String(catalog.settings.health_window_minutes));
  const saveM = useMutation({
    mutationFn: () =>
      saveMarketSettings({
        update_concurrency: Math.max(1, Math.min(8, Number(concurrency) || 3)),
        health_window_minutes: Math.max(3, Math.min(60, Number(windowMinutes) || 10)),
      }),
    onSuccess: (settings) => {
      setConcurrency(String(settings.update_concurrency));
      setWindowMinutes(String(settings.health_window_minutes));
      toast.success("已保存更新参数");
      onSaved();
    },
    onError: (error) => toast.error(getErrMsg(error)),
  });
  return (
    <Card data-testid="plugin-market-settings">
      <CardHeader>
        <CardTitle>更新参数</CardTitle>
        <CardDescription>
          多个插件一起更新时同时进行的数量（1–8，默认 3），以及更新后观察多久再确认健康（3–60 分钟，默认 10）。
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <div className="space-y-1">
          <Label htmlFor="market-concurrency">同时更新数量</Label>
          <Input
            id="market-concurrency"
            type="number"
            min={1}
            max={8}
            value={concurrency}
            onChange={(event) => setConcurrency(event.target.value)}
            data-testid="plugin-market-concurrency"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="market-window">健康观察（分钟）</Label>
          <Input
            id="market-window"
            type="number"
            min={3}
            max={60}
            value={windowMinutes}
            onChange={(event) => setWindowMinutes(event.target.value)}
          />
        </div>
        <Button onClick={() => saveM.mutate()} disabled={saveM.isPending} data-testid="plugin-market-settings-save">
          保存
        </Button>
      </CardContent>
    </Card>
  );
}
