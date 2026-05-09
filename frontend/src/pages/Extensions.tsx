// 插件中心：账号级插件管理 + 插件管理（本地+远程）+ 开发指南
//
// Tab 1：账号插件管理 — 选账号 → 勾选启用/禁用插件列表
// Tab 2：插件管理 — 本地内置插件 + 远程插件（安装/卸载/更新）
// Tab 3：开发指南 — react-markdown 渲染 docs/PLUGIN-DEV-GUIDE.md
//
// 之前 /matrix 和 /extensions 两个独立菜单项被砍，访问会 redirect 到这里（App.tsx）。
// 远程插件原为独立 /remote-plugins 页面，现合并到 Tab 2。
// 功能矩阵已废弃，替换为账号级插件管理。
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  ExternalLink,
  GitFork,
  Package2,
  Puzzle,
  RefreshCw,
  Trash2,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github.css";
import devGuideMd from "../../../docs/PLUGIN-DEV-GUIDE.md?raw";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { getErrMsg } from "@/lib/api";
import { formatDateTime } from "@/lib/utils";
import { ConfigDialog } from "@/components/plugin/ConfigDialog";

import { getFeatureMatrix } from "@/api/features";
import { toggleAccountFeature } from "@/api/accounts";
import {
  getPluginGlobalConfig,
  setPluginGlobalConfig,
  getEffectiveConfig,
  updateAccountFeatureConfig,
} from "@/api/features";
import {
  listInstalledPackages,
  enableInstall,
  disableInstall,
  uninstallPlugin,
} from "@/api/plugins";
import {
  fetchRemotePlugins,
  installRemotePlugin,
  enableRemotePlugin,
  disableRemotePlugin,
  updateRemotePlugin,
  uninstallRemotePlugin,
} from "@/api/remotePlugin";
import type { RemotePlugin } from "@/types/remotePlugin";
import type { ConfigSchema } from "@/components/plugin/ConfigDialog";

// ── 常量 ──────────────────────────────────────────────────────────
type TabValue = "accounts" | "plugins" | "guide";
const PLUGINS_QK = ["installed-packages"] as const;
const REMOTE_QK = ["remote-plugins"] as const;
const FEATURE_CONFIG_PAGE_KEYS = new Set(["auto_reply", "autorepeat", "codex_image", "forward", "scheduler", "game24"]);

function featureConfigPath(aid: number | null | undefined, key: string): string | null {
  if (!aid || !FEATURE_CONFIG_PAGE_KEYS.has(key)) return null;
  return `/accounts/${aid}/features/${key}`;
}

function formatPluginVersion(version?: string | null) {
  const v = (version || "").trim();
  if (!v) return "-";
  return v.startsWith("v") ? v : `v${v}`;
}

// ── 顶层组件 ──────────────────────────────────────────────────────
export function Extensions() {
  const [tab, setTab] = useState<TabValue>("accounts");

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">插件中心</h1>
        <p className="text-sm text-muted-foreground">
          账号插件管理 + 插件管理 + 开发指南
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
        <TabsList>
          <TabsTrigger value="accounts" className="gap-1.5">
            <Users className="h-4 w-4" /> 账号插件管理
          </TabsTrigger>
          <TabsTrigger value="plugins" className="gap-1.5">
            <Puzzle className="h-4 w-4" /> 插件管理
          </TabsTrigger>
          <TabsTrigger value="guide" className="gap-1.5">
            <BookOpen className="h-4 w-4" /> 开发指南
          </TabsTrigger>
        </TabsList>

        <TabsContent value="accounts">
          <AccountPluginsTab />
        </TabsContent>
        <TabsContent value="plugins">
          <PluginsManagementTab onManageAccounts={() => setTab("accounts")} />
        </TabsContent>
        <TabsContent value="guide">
          <DevGuideTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 1：账号插件管理 — 选账号 → 勾选插件列表
// ═══════════════════════════════════════════════════════════════════
function AccountPluginsTab() {
  const nav = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
  });

  const [selectedAid, setSelectedAid] = useState<number | null>(null);
  const [configDialog, setConfigDialog] = useState<{
    key: string;
    name: string;
    schema: Record<string, unknown> | null;
    globalConfig: Record<string, unknown>;
    accountConfig: Record<string, unknown>;
  } | null>(null);

  // 自动选第一个账号
  if (data && data.accounts.length > 0 && selectedAid === null) {
    setSelectedAid(data.accounts[0].id);
  }

  const toggleMut = useMutation({
    mutationFn: async (vars: { aid: number; key: string; enabled: boolean }) =>
      toggleAccountFeature(vars.aid, vars.key, vars.enabled),
    onSuccess: (_d, vars) => {
      toast.success(vars.enabled ? "已启用" : "已禁用");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["matrix"] }), 500);
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const selectedAccount = data?.accounts.find((a) => a.id === selectedAid);
  const features = data?.features ?? [];

  // 获取 global config
  const globalConfigQ = useQuery({
    queryKey: ["plugin", "global", configDialog?.key ?? ""],
    queryFn: () => getPluginGlobalConfig(configDialog!.key),
    enabled: !!configDialog?.key,
  });

  // 获取 effective config
  const effectiveConfigQ = useQuery({
    queryKey: ["account", selectedAid ?? 0, "config", configDialog?.key ?? ""],
    queryFn: () => getEffectiveConfig(selectedAid!, configDialog!.key),
    enabled: !!selectedAid && !!configDialog?.key,
  });

  // 计算 account config = effective - global
  const accountConfig = configDialog?.globalConfig
    ? Object.fromEntries(
        Object.entries(effectiveConfigQ.data ?? {}).filter(
          ([k]) => !(k in configDialog.globalConfig)
        )
      )
    : (effectiveConfigQ.data ?? {});

  return (
    <>
    <Card>
      <CardHeader>
        <CardTitle className="text-base">账号插件管理</CardTitle>
        <CardDescription>
          选择账号，勾选启用/禁用该账号的功能插件。新账号自动继承默认插件集。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : !data || data.accounts.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            尚未绑定账号，请先在<span className="text-primary cursor-pointer" onClick={() => nav("/accounts")}>账号管理</span>中添加
          </p>
        ) : (
          <>
            {/* 账号选择 */}
            <div className="mb-4 flex items-center gap-3">
              <label className="text-sm text-muted-foreground">选择账号：</label>
              <Select
                value={selectedAid?.toString() ?? ""}
                onChange={(e) => setSelectedAid(Number(e.target.value))}
                className="w-48"
              >
                {data.accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </Select>
              {selectedAccount && (
                <span className="text-xs text-muted-foreground">
                  {features.filter((f) => selectedAccount.features[f.key] === "active").length} / {features.length} 已启用
                </span>
              )}
            </div>

            {/* 插件列表 — 勾选式 */}
            {selectedAccount && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>功能</TableHead>
                    <TableHead>类型</TableHead>
                    <TableHead className="text-center">启用</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {features.map((f) => {
                    const state = (selectedAccount.features[f.key] ?? "disabled") as string;
                    const isActive = state === "active";
                    return (
                      <TableRow key={f.key}>
                        <TableCell>
                          <div className="font-medium">{f.display_name}</div>
                          <div className="font-mono text-xs text-muted-foreground">{f.key}</div>
                        </TableCell>
                        <TableCell>
                          <Badge variant={f.is_builtin ? "secondary" : "outline"}>
                            {f.is_builtin ? "内置" : "第三方"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-center">
                          <button
                            className={cn(
                              "relative inline-flex h-6 w-11 items-center rounded-full transition-colors",
                              isActive ? "bg-primary" : "bg-gray-600"
                            )}
                            onClick={() =>
                              toggleMut.mutate({
                                aid: selectedAccount.id,
                                key: f.key,
                                enabled: !isActive,
                              })
                            }
                            disabled={toggleMut.isPending}
                          >
                            <span
                              className={cn(
                                "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                                isActive ? "translate-x-6" : "translate-x-1"
                              )}
                            />
                          </button>
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-9 px-3"
                            onClick={() => {
                              const path = featureConfigPath(selectedAccount.id, f.key);
                              if (path) {
                                nav(path);
                                return;
                              }
                              getPluginGlobalConfig(f.key)
                                .then((gc) => {
                                  setConfigDialog({
                                    key: f.key,
                                    name: f.display_name,
                                    schema: (f.config_schema as Record<string, unknown>) ?? null,
                                    globalConfig: gc,
                                    accountConfig: {},
                                  });
                                })
                                .catch(() => {
                                  setConfigDialog({
                                    key: f.key,
                                    name: f.display_name,
                                    schema: (f.config_schema as Record<string, unknown>) ?? null,
                                    globalConfig: {},
                                    accountConfig: {},
                                  });
                                });
                            }}
                          >
                            配置 →
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </>
        )}
      </CardContent>
    </Card>

    {/* 配置弹窗 */}
    <ConfigDialog
      open={!!configDialog}
      onOpenChange={(v) => !v && setConfigDialog(null)}
      pluginKey={configDialog?.key ?? ""}
      pluginName={configDialog?.name ?? ""}
      schema={(configDialog?.schema as unknown as ConfigSchema) ?? null}
      accountName={selectedAccount?.name}
      accountId={selectedAid}
      globalConfig={configDialog?.globalConfig ?? {}}
      accountConfig={accountConfig}
      onSave={async (globalVals, accountVals) => {
        if (!configDialog || !selectedAid) return;

        // 1. 保存 global config
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
          await updateAccountFeatureConfig(selectedAid, configDialog.key, accountVals);
        }

        // 3. 刷新数据
        qc.invalidateQueries({ queryKey: ["matrix"] });
        qc.invalidateQueries({ queryKey: ["plugin", "global", configDialog.key] });
        qc.invalidateQueries({ queryKey: ["account", selectedAid, "config", configDialog.key] });
      }}
    />
    </>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 2：插件管理 — 内置插件 + 远程插件统一展示
// ═══════════════════════════════════════════════════════════════════
function PluginsManagementTab({ onManageAccounts }: { onManageAccounts: () => void }) {
  return (
    <div className="space-y-4">
      <RemoteInstallCard />
      <InstalledPluginsSection onManageAccounts={onManageAccounts} />
    </div>
  );
}

// ── 远程安装输入栏 ──────────────────────────────────────────────
function RemoteInstallCard() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");

  const installMut = useMutation({
    mutationFn: () => installRemotePlugin({ source_url: url.trim() }),
    onSuccess: (row) => {
      toast.success(`已安装 ${row.name} v${row.version}（默认禁用，请在「账号插件管理」中按账号启用）`);
      setUrl("");
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">从 Git 仓库安装</CardTitle>
        <CardDescription>
          支持 GitHub / GitLab 等公开仓库，仓库根目录需含静态 <code>plugin.json</code>
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <input
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder="https://github.com/user/repo.git"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim()) installMut.mutate();
            }}
            disabled={installMut.isPending}
          />
          <Button
            onClick={() => installMut.mutate()}
            disabled={!url.trim() || installMut.isPending}
            className="shrink-0"
          >
            {installMut.isPending ? (
              <><Spinner className="mr-2 h-4 w-4" /> 安装中…</>
            ) : (
              <><Package2 className="mr-2 h-4 w-4" /> 安装</>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 已安装插件列表（内置 + 远程） ────────────────────────────────
function InstalledPluginsSection({ onManageAccounts }: { onManageAccounts: () => void }) {
  const qc = useQueryClient();

  const builtinQ = useQuery({
    queryKey: ["matrix"],
    queryFn: getFeatureMatrix,
    select: (data) => data.features.filter((f) => f.is_builtin),
  });

  const thirdPartyQ = useQuery({ queryKey: PLUGINS_QK, queryFn: listInstalledPackages });
  const remoteQ = useQuery({ queryKey: REMOTE_QK, queryFn: fetchRemotePlugins });

  const enableTPMut = useMutation({
    mutationFn: (key: string) => enableInstall(key),
    onSuccess: () => { toast.success("已启用"); qc.invalidateQueries({ queryKey: PLUGINS_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const disableTPMut = useMutation({
    mutationFn: (key: string) => disableInstall(key),
    onSuccess: () => { toast.success("已禁用"); qc.invalidateQueries({ queryKey: PLUGINS_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const uninstallTPMut = useMutation({
    mutationFn: (key: string) => uninstallPlugin(key),
    onSuccess: (_r, key) => { toast.success(`已卸载 ${key}`); qc.invalidateQueries({ queryKey: PLUGINS_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const enableRMMut = useMutation({
    mutationFn: (name: string) => enableRemotePlugin(name),
    onSuccess: () => { toast.success("已启用"); qc.invalidateQueries({ queryKey: REMOTE_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const disableRMMut = useMutation({
    mutationFn: (name: string) => disableRemotePlugin(name),
    onSuccess: () => { toast.success("已禁用"); qc.invalidateQueries({ queryKey: REMOTE_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const updateRMMut = useMutation({
    mutationFn: (name: string) => updateRemotePlugin(name),
    onSuccess: (row) => { toast.success(`已更新 → v${row.version}`); qc.invalidateQueries({ queryKey: REMOTE_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const uninstallRMMut = useMutation({
    mutationFn: (name: string) => uninstallRemotePlugin(name),
    onSuccess: (_r, name) => { toast.success(`已卸载 ${name}`); qc.invalidateQueries({ queryKey: REMOTE_QK }); },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const isLoading = builtinQ.isLoading || thirdPartyQ.isLoading || remoteQ.isLoading;
  const builtin = builtinQ.data ?? [];
  const thirdParty = thirdPartyQ.data ?? [];
  const remote = remoteQ.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">已安装插件</CardTitle>
        <CardDescription>
          内置插件 + 第三方插件 + 远程插件，统一展示
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-24 items-center justify-center"><Spinner className="text-primary" /></div>
        ) : builtin.length === 0 && thirdParty.length === 0 && remote.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">暂无已安装插件</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>插件</TableHead>
                <TableHead>类型</TableHead>
                <TableHead>版本</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {/* 内置插件 */}
              {builtin.map((f) => (
                <TableRow key={f.key}>
                  <TableCell>
                    <div className="font-medium">{f.display_name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{f.key}</div>
                  </TableCell>
                  <TableCell><Badge variant="secondary">内置</Badge></TableCell>
                  <TableCell>{formatPluginVersion(f.version)}</TableCell>
                  <TableCell><Badge variant="default">内置</Badge></TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="outline" onClick={onManageAccounts}>
                      按账号管理
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {/* 第三方插件 */}
              {thirdParty.map((row) => (
                <TableRow key={row.key}>
                  <TableCell>
                    <div className="font-medium">{row.key}</div>
                  </TableCell>
                  <TableCell><Badge variant="outline">第三方</Badge></TableCell>
                  <TableCell>{formatPluginVersion(row.version)}</TableCell>
                  <TableCell>
                    <Badge variant={row.enabled ? "default" : "outline"}>
                      {row.enabled ? "已启用" : "未启用"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      {row.enabled ? (
                        <Button size="sm" variant="outline" onClick={() => disableTPMut.mutate(row.key)} disabled={disableTPMut.isPending}>禁用</Button>
                      ) : (
                        <Button size="sm" onClick={() => enableTPMut.mutate(row.key)} disabled={enableTPMut.isPending}>启用</Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => { if (confirm(`确认卸载「${row.key}」？`)) uninstallTPMut.mutate(row.key); }} disabled={uninstallTPMut.isPending}>卸载</Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {/* 远程插件 */}
              {remote.map((p) => (
                <TableRow key={`rm-${p.name}`}>
                  <TableCell>
                    <div className="font-medium">{p.display_name || p.name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{p.name}</div>
                  </TableCell>
                  <TableCell><Badge variant="outline"><GitFork className="inline h-3 w-3 mr-1" />远程</Badge></TableCell>
                  <TableCell>{formatPluginVersion(p.version)}</TableCell>
                  <TableCell>
                    <Badge variant={p.enabled ? "default" : "outline"}>
                      {p.enabled ? "已启用" : "未启用"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      {p.enabled ? (
                        <Button size="sm" variant="outline" onClick={() => disableRMMut.mutate(p.name)} disabled={disableRMMut.isPending}>禁用</Button>
                      ) : (
                        <Button size="sm" onClick={() => enableRMMut.mutate(p.name)} disabled={enableRMMut.isPending}>启用</Button>
                      )}
                      <Button size="sm" variant="outline" onClick={() => updateRMMut.mutate(p.name)} disabled={updateRMMut.isPending} title="从远程更新">
                        <RefreshCw className="h-3 w-3" />
                      </Button>
                      <Button size="sm" variant="ghost" className="text-destructive" onClick={() => { if (confirm(`确认卸载「${p.name}」？`)) uninstallRMMut.mutate(p.name); }} disabled={uninstallRMMut.isPending}>卸载</Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 3：开发指南
// ═══════════════════════════════════════════════════════════════════
function DevGuideTab() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">插件开发指南</CardTitle>
        <CardDescription>
          源文件：<code>docs/PLUGIN-DEV-GUIDE.md</code>，构建时打包进前端
        </CardDescription>
      </CardHeader>
      <CardContent>
        <article className="prose prose-sm max-w-none dark:prose-invert">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {devGuideMd}
          </ReactMarkdown>
        </article>
      </CardContent>
    </Card>
  );
}
