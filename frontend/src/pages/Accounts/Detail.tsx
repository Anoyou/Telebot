// 账号详情：3 个 Tab —— 概览 / 插件启停 / 风控
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Ban,
  Bot,
  ChevronRight,
  Gauge,
  LayoutDashboard,
  Loader2,
  Network,
  Power,
  Shield,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { ConfigDialog } from "@/components/plugin/ConfigDialog";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Spinner } from "@/components/ui/misc";
import { AccountAvatar } from "@/components/AccountAvatar";
import { AccountStatusBadge } from "@/components/AccountStatusBadge";
import { MaskedPhone } from "@/components/MaskedPhone";
import { IgnoredTab } from "@/pages/Accounts/IgnoredTab";
import { CommandsTab } from "@/pages/Accounts/CommandsTab";
import {
  deleteAccount,
  getAccount,
  listAccountFeatures,
  patchAccount,
  pauseAccount,
  resumeAccount,
  toggleAccountFeature,
  updateAccountFeatureConfig,
} from "@/api/accounts";
import {
  getPluginGlobalConfig,
  setPluginGlobalConfig,
  getEffectiveConfig,
} from "@/api/features";
import { listProxies, testProxy } from "@/api/proxies";
import { listDeviceProfiles } from "@/api/device-profiles";
import {
  getAccountRateLimit,
  getHumanize,
  patchAccountRateLimit,
  patchHumanize,
  strictRateLimit,
} from "@/api/system";
import { getFeatureMatrix } from "@/api/features";
import { getErrMsg } from "@/lib/api";
import { cn, formatDateTime } from "@/lib/utils";
import { Select } from "@/components/ui/select";
import type { HumanizeConfig, ProxyTestResult } from "@/api/types";
import { actionHint, actionLabel } from "@/lib/rate-actions";
import type { ConfigSchema } from "@/components/plugin/ConfigDialog";

// 功能列表从 feature-matrix API 动态获取，不再硬编码
const FEATURE_CONFIG_PAGE_KEYS = new Set(["auto_reply", "autorepeat", "codex_image", "forward", "scheduler", "game24"]);

function featureConfigPath(aid: number, key: string): string | null {
  if (!aid || !FEATURE_CONFIG_PAGE_KEYS.has(key)) return null;
  return `/accounts/${aid}/features/${key}`;
}

export function AccountDetail() {
  const params = useParams();
  const [searchParams] = useSearchParams();
  const aid = Number(params.aid);
  const nav = useNavigate();
  const qc = useQueryClient();

  const detailQ = useQuery({
    queryKey: ["account", aid],
    queryFn: () => getAccount(aid),
    enabled: !!aid,
  });

  const [configDialog, setConfigDialog] = useState<{
    key: string;
    name: string;
    schema: Record<string, unknown> | null;
    globalConfig: Record<string, unknown>;
    accountConfig: Record<string, unknown>;
  } | null>(null);

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid,
  });

  // 动态获取已注册功能列表（替代硬编码 FEATURE_KEYS）
  const featureListQ = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
    select: (data) => data.features,
  });

  // 获取 global config
  const globalConfigQ = useQuery({
    queryKey: ["plugin", "global", configDialog?.key ?? ""],
    queryFn: () => getPluginGlobalConfig(configDialog!.key),
    enabled: !!configDialog?.key,
  });

  // 获取 effective config（合并后的最终配置）
  const effectiveConfigQ = useQuery({
    queryKey: ["account", aid, "config", configDialog?.key ?? ""],
    queryFn: () => getEffectiveConfig(aid, configDialog!.key),
    enabled: !!aid && !!configDialog?.key,
  });

  // 计算 account config = effective config - global config
  const accountConfig = configDialog?.globalConfig
    ? Object.fromEntries(
        Object.entries(effectiveConfigQ.data ?? {}).filter(
          ([k]) => !(k in configDialog.globalConfig)
        )
      )
    : (effectiveConfigQ.data ?? {});

  const rateQ = useQuery({
    queryKey: ["account", aid, "rate-limit"],
    queryFn: () => getAccountRateLimit(aid),
    enabled: !!aid,
  });

  // ===================== 操作 mutations =====================
  const toggleStatusMut = useMutation({
    mutationFn: async (pause: boolean) =>
      pause ? pauseAccount(aid) : resumeAccount(aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已下发指令");
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // "重启 worker"快捷操作：暂停 → 1 秒 → 启动；让 runtime.py 启动钩子重新调一次
  // client.get_me() 回填 tg_user_id / tg_username。
  const restartWorkerMut = useMutation({
    mutationFn: async () => {
      await pauseAccount(aid);
      await new Promise((r) => setTimeout(r, 1000));
      await resumeAccount(aid);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已重启 worker；几秒后字段会自动刷新");
      // 5 秒后再拉一次详情，让 UI 自动出来
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["account", aid] });
      }, 5000);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteAccount(aid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success("已删除");
      nav("/accounts", { replace: true });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const featureMut = useMutation({
    mutationFn: async (vars: { key: string; enabled: boolean }) =>
      toggleAccountFeature(aid, vars.key, vars.enabled),
    onSuccess: (_d, vars) => {
      toast.success(`${vars.enabled ? "已启用" : "已禁用"}：${vars.key}`);
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const ratePatchMut = useMutation({
    mutationFn: async (vars: { action: string; per_minute: number | null }) =>
      patchAccountRateLimit(aid, vars.action, { per_minute: vars.per_minute }),
    onSuccess: () => {
      toast.success("已保存（worker 热加载）");
      qc.invalidateQueries({ queryKey: ["account", aid, "rate-limit"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const strictMut = useMutation({
    mutationFn: () => strictRateLimit(aid, { multiplier: 0.5, ttl_seconds: 7200 }),
    onSuccess: () => {
      toast.success("已紧急调严：阈值 ×0.5 维持 2 小时");
      qc.invalidateQueries({ queryKey: ["account", aid, "rate-limit"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  if (!aid) return <p>账号 ID 不合法</p>;
  if (detailQ.isLoading)
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner className="text-primary" />
      </div>
    );
  if (!detailQ.data) return <p>账号不存在</p>;

  const acc = detailQ.data;
  // 老账号 / 异常账号可能 tg_user_id / tg_username 都是 null：worker 启动时
  // 会调 client.get_me() 自动回填（runtime.py:107）。这里给个友好提示，让用户
  // 明白"为什么这两栏是空的"以及怎么解。
  const idMissing = acc.tg_user_id == null && !acc.tg_username;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <Button variant="ghost" size="sm" onClick={() => nav("/accounts")}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回列表
        </Button>
        <AccountAvatar
          id={acc.id}
          name={acc.display_name}
          username={acc.tg_username}
          size={36}
        />
        <h1 className="min-w-0 truncate text-2xl font-semibold tracking-tight">
          {acc.display_name ||
            (acc.tg_username ? `@${acc.tg_username}` : `#${acc.id}`)}
        </h1>
        <AccountStatusBadge status={acc.status} />
      </div>

      <Tabs defaultValue={searchParams.get("tab") || "overview"}>
        <TabsList>
          <TabsTrigger value="overview" className="gap-1.5">
            <LayoutDashboard className="h-4 w-4" /> 概览
          </TabsTrigger>
          <TabsTrigger value="features" className="gap-1.5">
            <Bot className="h-4 w-4" /> 插件启停
          </TabsTrigger>
          <TabsTrigger value="commands" className="gap-1.5">
            <Shield className="h-4 w-4" /> 命令
          </TabsTrigger>
          <TabsTrigger value="rate" className="gap-1.5">
            <Gauge className="h-4 w-4" /> 风控基础
          </TabsTrigger>
          <TabsTrigger value="proxy" className="gap-1.5">
            <Network className="h-4 w-4" /> 出口/伪装
          </TabsTrigger>
          <TabsTrigger value="ignored" className="gap-1.5">
            <Ban className="h-4 w-4" /> 忽略的群组
          </TabsTrigger>
        </TabsList>

        {/* 概览 */}
        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">基本信息</CardTitle>
              <CardDescription>账号基础属性与运行控制</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {idMissing ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  <div className="mb-1.5">
                    ⚠ 该账号尚未同步 Telegram 用户 ID 与用户名。worker 启动时会
                    自动通过 <code>client.get_me()</code> 回填——但只在那一刻执行一次。
                  </div>
                  <div className="mb-2">
                    当前账号状态：<span className="font-medium">{acc.status}</span>。
                    点下面按钮一键重启 worker，几秒后这两栏会出现。
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    className="bg-amber-100 hover:bg-amber-200 border-amber-300"
                    disabled={restartWorkerMut.isPending}
                    onClick={() => restartWorkerMut.mutate()}
                  >
                    {restartWorkerMut.isPending ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : null}
                    重启 worker 同步
                  </Button>
                </div>
              ) : null}
              <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-muted-foreground">账号 ID（系统）</dt>
                  <dd>#{acc.id}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Telegram 用户 ID</dt>
                  <dd className="font-mono">{acc.tg_user_id ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Telegram 用户名</dt>
                  <dd className="font-mono">
                    {acc.tg_username ? `@${acc.tg_username}` : "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">电话</dt>
                  <dd>
                    <MaskedPhone phone={acc.phone} iconClassName="h-4 w-4" />
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">显示名</dt>
                  <dd>{acc.display_name || "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">绑定时间</dt>
                  <dd>{formatDateTime(acc.created_at)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">冷启动结束</dt>
                  <dd>{acc.cold_start_until || "—"}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">备注</dt>
                  <dd>{acc.notes || "—"}</dd>
                </div>
              </dl>

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggleStatusMut.mutate(acc.status === "active")}
                >
                  <Power className="mr-1 h-4 w-4" />
                  {acc.status === "active" ? "暂停账号" : "启动账号"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-destructive"
                  onClick={() => {
                    const label =
                      acc.display_name ||
                      (acc.tg_username ? `@${acc.tg_username}` : `#${acc.id}`);
                    if (
                      confirm(
                        `二次确认：删除账号 ${label}，将撤销 session 并清空所有规则。`,
                      )
                    )
                      deleteMut.mutate();
                  }}
                >
                  <Trash2 className="mr-1 h-4 w-4" /> 删除账号
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 插件启停 */}
        <TabsContent value="features">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">插件启停</CardTitle>
              <CardDescription>
                每个功能可独立启停。开启后跳到对应配置页配置规则
              </CardDescription>
            </CardHeader>
            <CardContent>
              {featuresQ.isLoading || featureListQ.isLoading ? (
                <div className="flex h-20 items-center justify-center">
                  <Spinner className="text-primary" />
                </div>
              ) : (
                <ul className="divide-y">
                  {(featureListQ.data ?? []).map((f) => {
                    const item = featuresQ.data?.find(
                      (x) => x.feature_key === f.key,
                    );
                    const enabled = !!item?.enabled;
                    return (
                      <li
                        key={f.key}
                        className="flex items-center justify-between py-3"
                      >
                        <div>
                          <div className="font-medium">{f.display_name}</div>
                          <div className="text-xs text-muted-foreground">
                            {item?.state ? `状态：${item.state}` : "未启用"}
                            {item?.last_error
                              ? ` · 最近错误：${item.last_error}`
                              : ""}
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          <Switch
                            checked={enabled}
                            onCheckedChange={(v) =>
                              featureMut.mutate({ key: f.key, enabled: v })
                            }
                          />
                          {enabled && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => {
                                const path = featureConfigPath(aid, f.key);
                                if (path) {
                                  nav(path);
                                  return;
                                }
                                // 打开配置弹窗时同时获取 global config
                                getPluginGlobalConfig(f.key)
                                  .then((gc) => {
                                    setConfigDialog({
                                      key: f.key,
                                      name: f.display_name,
                                      schema: (f.config_schema as Record<string, unknown>) ?? null,
                                      globalConfig: gc,
                                      accountConfig: item?.config ?? {},
                                    });
                                  })
                                  .catch(() => {
                                    // 如果获取失败，使用空配置
                                    setConfigDialog({
                                      key: f.key,
                                      name: f.display_name,
                                      schema: (f.config_schema as Record<string, unknown>) ?? null,
                                      globalConfig: {},
                                      accountConfig: item?.config ?? {},
                                    });
                                  });
                              }}
                            >
                              配置 →
                            </Button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

          <ConfigDialog
            open={!!configDialog}
            onOpenChange={(v) => !v && setConfigDialog(null)}
            pluginKey={configDialog?.key ?? ""}
            pluginName={configDialog?.name ?? ""}
            schema={(configDialog?.schema as unknown as ConfigSchema) ?? null}
            accountName={acc.display_name || acc.phone}
            accountId={aid}
            globalConfig={configDialog?.globalConfig ?? {}}
            accountConfig={accountConfig}
            onSave={async (globalVals, accountVals) => {
              if (!configDialog) return;

              // 1. 保存 global config（如果有变化）
              const schema = configDialog.schema as unknown as ConfigSchema | null;
              if (schema?.properties) {
                const globalFields = Object.entries(schema.properties)
                  .filter(([, f]) => f.level === "global")
                  .map(([k]) => k);
                const hasGlobalChanges = globalFields.some(
                  (k) => globalVals[k] !== configDialog.globalConfig[k]
                );
                if (hasGlobalChanges) {
                  const globalOnlyVals: Record<string, unknown> = {};
                  for (const k of globalFields) {
                    globalOnlyVals[k] = globalVals[k];
                  }
                  await setPluginGlobalConfig(configDialog.key, globalOnlyVals);
                }
              }

              // 2. 保存 account config
              if (Object.keys(accountVals).length > 0) {
                await updateAccountFeatureConfig(aid, configDialog.key, accountVals);
              }

              // 3. 刷新数据
              qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
              qc.invalidateQueries({ queryKey: ["plugin", "global", configDialog.key] });
              qc.invalidateQueries({ queryKey: ["account", aid, "config", configDialog.key] });
              qc.invalidateQueries({ queryKey: ["matrix"] });
            }}
          />
        </TabsContent>

        {/* 自定义命令（账号 × 模板 启用关系） */}
        <TabsContent value="commands">
          <CommandsTab aid={aid} />
        </TabsContent>

        {/* 风控基础 */}
        <TabsContent value="rate">
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between">
                <div>
                  <CardTitle className="text-base">风控阈值（基础版）</CardTitle>
                  <CardDescription>
                    仅展示当前账号生效的 RateLimitRule，可编辑
                    per_minute；进阶配置请到模板页
                  </CardDescription>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => {
                    if (confirm("确认要紧急调严？阈值 ×0.5，TTL 2 小时"))
                      strictMut.mutate();
                  }}
                >
                  紧急调严 ½ × 2h
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {rateQ.isLoading ? (
                <div className="flex h-20 items-center justify-center">
                  <Spinner className="text-primary" />
                </div>
              ) : rateQ.data && rateQ.data.rules.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>动作</TableHead>
                      <TableHead>每分钟</TableHead>
                      <TableHead>每小时</TableHead>
                      <TableHead>策略</TableHead>
                      <TableHead className="text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rateQ.data.rules.map((r) => (
                      <RateRow
                        key={r.action}
                        action={r.action}
                        perMinute={r.per_minute ?? null}
                        perHour={r.per_hour ?? null}
                        policy={r.policy}
                        onSave={(v) =>
                          ratePatchMut.mutate({ action: r.action, per_minute: v })
                        }
                      />
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  尚无风控配置
                </p>
              )}

              {/* 拟人化（humanize）配置：折叠面板，默认收起 */}
              <div className="mt-4 border-t pt-4">
                <HumanizePanel aid={aid} />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 出口 / 代理 + 设备伪装 */}
        <TabsContent value="proxy" className="space-y-4">
          <ProxyTab aid={aid} currentProxyId={acc.proxy_id ?? null} />
          <DeviceProfileTab
            aid={aid}
            currentProfileId={acc.device_profile_id ?? null}
          />
        </TabsContent>

        {/* 忽略群组 / peer */}
        <TabsContent value="ignored">
          <IgnoredTab aid={aid} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// 出口/代理 tab：选代理 + 立即测试 + 保存
function ProxyTab({
  aid,
  currentProxyId,
}: {
  aid: number;
  currentProxyId: number | null;
}) {
  const qc = useQueryClient();
  const proxiesQ = useQuery({ queryKey: ["proxies"], queryFn: listProxies });
  const [selected, setSelected] = useState<string>(
    currentProxyId !== null ? String(currentProxyId) : "",
  );
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ProxyTestResult | null>(null);

  const saveMut = useMutation({
    mutationFn: () =>
      patchAccount(aid, {
        proxy_id: selected ? Number(selected) : null,
      }),
    onSuccess: () => {
      toast.success("已保存。worker 重启后生效（账号详情 → 概览 → 暂停 → 恢复）");
      qc.invalidateQueries({ queryKey: ["account", aid] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  async function handleTest() {
    if (!selected) {
      toast.error("请先选一个代理");
      return;
    }
    setTesting(true);
    setResult(null);
    try {
      const r = await testProxy(Number(selected));
      setResult(r);
    } catch (err) {
      toast.error(getErrMsg(err));
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">出口 / 代理</CardTitle>
        <CardDescription>
          为该账号绑定一个代理（SOCKS5 / HTTP / MTProxy）；空 = 直连。修改后 worker 须重启
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1.5 max-w-xl">
          <label className="text-xs text-muted-foreground">绑定代理</label>
          <div className="flex gap-2">
            <Select
              className="flex-1"
              value={selected}
              onChange={(e) => {
                setSelected(e.target.value);
                setResult(null);
              }}
            >
              <option value="">直连（不走代理）</option>
              {proxiesQ.data?.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  [{p.type}] {p.host}:{p.port}
                  {p.username ? ` @${p.username}` : ""}
                </option>
              ))}
            </Select>
            <Button
              variant="outline"
              onClick={handleTest}
              disabled={!selected || testing}
            >
              {testing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Activity className="h-4 w-4" />
              )}
              <span className="ml-1">测试</span>
            </Button>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={
                saveMut.isPending ||
                (selected ? Number(selected) : null) === currentProxyId
              }
            >
              保存
            </Button>
          </div>
        </div>

        {/* 测试结果 */}
        {result ? (
          result.ok ? (
            <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              ✓ 通过 · {result.latency_ms}ms · {result.country || "?"}
              {result.city ? ` · ${result.city}` : ""}
              {result.exit_ip ? ` · 出口 IP ${result.exit_ip}` : ""}
            </div>
          ) : (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              ✗ {result.error || "未知错误"}
            </div>
          )
        ) : null}

        {!proxiesQ.isLoading && (proxiesQ.data?.length ?? 0) === 0 ? (
          <p className="rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">
            代理库为空。先到「系统设置 → 代理库」新建
          </p>
        ) : null}

        <div className="border-t pt-3 text-xs text-muted-foreground">
          ⚠ 修改代理不会立即生效；保存后请在「概览」tab 暂停并恢复账号让 worker 重启用新代理。
        </div>
      </CardContent>
    </Card>
  );
}

// 设备伪装 tab：选 profile + 保存。与 ProxyTab 同位级。
//
// ⚠ 切换 profile 不会让 TG 端立即显示新设备名 —— TG 把设备名绑在 auth_key 上，
// 切换后必须让账号重新登录（删除/重登）才会重新注册到 TG 那边。
function DeviceProfileTab({
  aid,
  currentProfileId,
}: {
  aid: number;
  currentProfileId: number | null;
}) {
  const qc = useQueryClient();
  const profilesQ = useQuery({
    queryKey: ["device-profiles"],
    queryFn: listDeviceProfiles,
  });
  const [selected, setSelected] = useState<string>(
    currentProfileId !== null ? String(currentProfileId) : "",
  );

  const saveMut = useMutation({
    mutationFn: () =>
      patchAccount(aid, {
        device_profile_id: selected ? Number(selected) : null,
      }),
    onSuccess: () => {
      toast.success("已保存。账号下次重新登录时 TG 才会显示新设备名");
      qc.invalidateQueries({ queryKey: ["account", aid] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const currentSelected = selected ? profilesQ.data?.find((p) => p.id === Number(selected)) : null;
  const defaultProfile = profilesQ.data?.find((p) => p.is_default) ?? null;
  const previewProfile = currentSelected ?? defaultProfile;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">设备伪装</CardTitle>
        <CardDescription>
          决定 TG 设备列表里看到的设备名 / 系统 / 客户端版本。空 = 用系统默认 profile。
          <br />
          ⚠ 切换 profile 对**已登录的 session 无效**；TG 把设备名绑在 auth_key 上，
          要让 TG 显示新名字必须让账号重新登录。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1.5 max-w-xl">
          <Label className="text-xs text-muted-foreground">设备伪装 profile</Label>
          <div className="flex flex-wrap gap-2">
            <Select
              className="min-w-[16rem] flex-1"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="">
                跟随系统默认
                {defaultProfile ? `（${defaultProfile.name}）` : ""}
              </option>
              {profilesQ.data?.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                  {p.is_default ? " ★" : ""}
                </option>
              ))}
            </Select>
            <Button
              onClick={() => saveMut.mutate()}
              disabled={
                saveMut.isPending ||
                (selected ? Number(selected) : null) === currentProfileId
              }
            >
              保存
            </Button>
          </div>
        </div>

        {previewProfile ? (
          <div className="rounded-lg border bg-muted/30 p-3 text-xs">
            <div className="mb-1 font-medium">TG 设备列表中将显示：</div>
            <div className="font-mono text-foreground">
              {previewProfile.device_model}
            </div>
            <div className="font-mono text-muted-foreground">
              {previewProfile.system_version} · {previewProfile.app_version}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              lang: {previewProfile.lang_code} / {previewProfile.system_lang_code}
            </div>
          </div>
        ) : null}

        <div className="border-t pt-3 text-xs text-muted-foreground">
          要新增 / 修改 profile，请到「系统设置 → 设备伪装库」。
        </div>
      </CardContent>
    </Card>
  );
}

// 单行内联编辑：per_minute 输入 + dirty 时显示保存
function RateRow(props: {
  action: string;
  perMinute: number | null;
  perHour: number | null;
  policy: string;
  onSave: (v: number | null) => void;
}) {
  const label = actionLabel(props.action);
  const hint = actionHint(props.action);
  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium">{label}</span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {props.action}
          </span>
          {hint ? (
            <span className="text-xs text-muted-foreground">{hint}</span>
          ) : null}
        </div>
      </TableCell>
      <TableCell>
        <RateInput initial={props.perMinute} onSave={props.onSave} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        {props.perHour ?? "—"}
      </TableCell>
      <TableCell className="text-muted-foreground">{props.policy}</TableCell>
      <TableCell />
    </TableRow>
  );
}

function RateInput({
  initial,
  onSave,
}: {
  initial: number | null;
  onSave: (v: number | null) => void;
}) {
  const [val, setVal] = useState(initial?.toString() ?? "");
  const dirty = val !== (initial?.toString() ?? "");
  return (
    <div className="flex items-center gap-2">
      <Input
        className="h-8 w-24"
        value={val}
        onChange={(e) => setVal(e.target.value.replace(/[^0-9]/g, ""))}
      />
      {dirty && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onSave(val ? Number(val) : null)}
        >
          保存
        </Button>
      )}
    </div>
  );
}

// ── 拟人化（humanize）折叠面板 ──────────────────────────────────────
// 默认收起：高级用户才需要调；保存时只下发改过的字段（PATCH 语义）
function HumanizePanel({ aid }: { aid: number }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);

  const humanQ = useQuery({
    queryKey: ["account", aid, "humanize"],
    queryFn: () => getHumanize(aid),
    enabled: !!aid && open, // 折叠面板没展开前不去拉
  });

  // 本地编辑态：仅在数据加载后初始化一次
  const [draft, setDraft] = useState<HumanizeConfig | null>(null);
  useEffect(() => {
    if (humanQ.data && draft === null) setDraft(humanQ.data);
  }, [humanQ.data, draft]);

  const saveMut = useMutation({
    mutationFn: (body: Partial<HumanizeConfig>) => patchHumanize(aid, body),
    onSuccess: (data) => {
      toast.success("拟人化配置已保存（worker 热加载）");
      setDraft(data);
      qc.invalidateQueries({ queryKey: ["account", aid, "humanize"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const dirty =
    draft !== null && humanQ.data !== undefined && !shallowEqual(draft, humanQ.data);

  return (
    <div className="space-y-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ChevronRight
          className={cn("h-4 w-4 transition-transform", open && "rotate-90")}
        />
        <span>人类化（humanize）配置</span>
        <span className="ml-2 text-xs">{open ? "收起" : "展开"}</span>
      </button>

      {open ? (
        humanQ.isLoading || draft === null ? (
          <div className="flex h-16 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : (
          <div className="space-y-4 rounded-md border bg-muted/20 p-4 text-sm">
            {/* 模拟"对方正在输入" */}
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="hz-typing">模拟"对方正在输入"</Label>
                <p className="text-xs text-muted-foreground">
                  发送前先 typing N ms，更像真人
                </p>
              </div>
              <Switch
                id="hz-typing"
                checked={draft.typing_simulate}
                onCheckedChange={(v) =>
                  setDraft({ ...draft, typing_simulate: v })
                }
              />
            </div>

            {/* typing 时长范围（min~max ms） */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="hz-tmin">typing 最短 (ms)</Label>
                <Input
                  id="hz-tmin"
                  inputMode="numeric"
                  className="h-8"
                  value={String(draft.typing_min_ms)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      typing_min_ms: clampInt(e.target.value, 0, 60_000),
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="hz-tmax">typing 最长 (ms)</Label>
                <Input
                  id="hz-tmax"
                  inputMode="numeric"
                  className="h-8"
                  value={String(draft.typing_max_ms)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      typing_max_ms: clampInt(e.target.value, 0, 60_000),
                    })
                  }
                />
              </div>
            </div>
            {draft.typing_min_ms > draft.typing_max_ms ? (
              <p className="text-xs text-destructive">
                最短不能大于最长
              </p>
            ) : null}

            {/* typing 触发概率 */}
            <div className="space-y-1">
              <Label htmlFor="hz-tprob">触发 typing 的概率（0–100%）</Label>
              <Input
                id="hz-tprob"
                inputMode="numeric"
                className="h-8 w-32"
                value={String(draft.typing_probability)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    typing_probability: clampInt(e.target.value, 0, 100),
                  })
                }
              />
            </div>

            {/* 阅读后再回 + 抖动比例 */}
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="hz-read">回复前先标记已读</Label>
                <p className="text-xs text-muted-foreground">
                  对方更不容易察觉是机器人
                </p>
              </div>
              <Switch
                id="hz-read"
                checked={draft.read_before_reply}
                onCheckedChange={(v) =>
                  setDraft({ ...draft, read_before_reply: v })
                }
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="hz-jit">人类化抖动比例（0–100%）</Label>
              <Input
                id="hz-jit"
                inputMode="numeric"
                className="h-8 w-32"
                value={String(draft.jitter_pct)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    jitter_pct: clampInt(e.target.value, 0, 100),
                  })
                }
              />
              <p className="text-xs text-muted-foreground">
                所有等待时间会在 ±{draft.jitter_pct}% 范围内随机偏移
              </p>
            </div>

            {/* 活跃时段（可选） */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="hz-ws">活跃开始（HH:MM，可空）</Label>
                <Input
                  id="hz-ws"
                  className="h-8"
                  placeholder="09:00"
                  value={draft.active_window_start ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      active_window_start: e.target.value || null,
                    })
                  }
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="hz-we">活跃结束（HH:MM，可空）</Label>
                <Input
                  id="hz-we"
                  className="h-8"
                  placeholder="23:00"
                  value={draft.active_window_end ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      active_window_end: e.target.value || null,
                    })
                  }
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label htmlFor="hz-cold">冷启动天数</Label>
              <Input
                id="hz-cold"
                inputMode="numeric"
                className="h-8 w-32"
                value={String(draft.cold_start_days)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    cold_start_days: clampInt(e.target.value, 0, 90),
                  })
                }
              />
              <p className="text-xs text-muted-foreground">
                新账号在该天数内自动调严风控
              </p>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <Button
                size="sm"
                disabled={
                  !dirty ||
                  saveMut.isPending ||
                  draft.typing_min_ms > draft.typing_max_ms
                }
                onClick={() => saveMut.mutate(draft)}
              >
                {saveMut.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : null}
                保存
              </Button>
              {dirty ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setDraft(humanQ.data ?? draft)}
                >
                  撤销
                </Button>
              ) : null}
            </div>
          </div>
        )
      ) : null}
    </div>
  );
}

// 把字符串转 int 并夹到 [min, max]；空字符串当 0
function clampInt(s: string, min: number, max: number): number {
  const cleaned = s.replace(/[^0-9]/g, "");
  if (!cleaned) return min;
  const n = parseInt(cleaned, 10);
  return Math.max(min, Math.min(max, Number.isNaN(n) ? min : n));
}

// 浅比较两个 humanize 对象，用来判断 dirty
function shallowEqual(a: object, b: object): boolean {
  const ar = a as Record<string, unknown>;
  const br = b as Record<string, unknown>;
  const keys = new Set([...Object.keys(ar), ...Object.keys(br)]);
  for (const k of keys) {
    if (ar[k] !== br[k]) return false;
  }
  return true;
}
