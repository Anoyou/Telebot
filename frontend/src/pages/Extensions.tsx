// 插件安装与管理：插件包安装/更新/卸载 + 开发指南
//
// Tab 1：安装与更新 — 本地内置插件 + 远程插件（安装/卸载/更新）
// Tab 2：开发指南 — react-markdown 渲染 docs/PLUGIN-DEV-GUIDE.md
//
// 账号级启停与配置统一回 /plugins 首页，避免“安装页”和“插件中心”双入口重复。
// 远程插件原为独立 /remote-plugins 页面，现在统一收口到 /plugins/manage。
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BookOpen,
  ChevronRight,
  GitFork,
  Power,
  Puzzle,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
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
import { goBackOr } from "@/lib/navigation";
import { getErrMsg } from "@/lib/api";

import { getFeatureMatrix } from "@/api/features";
import {
  listInstalledPackages,
  enableInstall,
  disableInstall,
  uninstallPlugin,
} from "@/api/plugins";
import {
  fetchRemotePlugins,
  enableRemotePlugin,
  disableRemotePlugin,
  updateRemotePlugin,
  uninstallRemotePlugin,
} from "@/api/remotePlugin";
import {
  addPluginRepo,
  deletePluginRepo,
  fetchPluginRepos,
  fetchRepoPlugins,
  installFromRepo,
} from "@/api/pluginRepo";

// ── 常量 ──────────────────────────────────────────────────────────
type TabValue = "plugins" | "guide";
const PLUGINS_QK = ["installed-packages"] as const;
const REMOTE_QK = ["remote-plugins"] as const;
const PLUGIN_REPOS_QK = ["plugin-repos"] as const;
const NEW_ACCOUNT_GUIDE_SEEN_KEY = "telebot.accounts.new_account_guide_seen.v4";

function formatPluginVersion(version?: string | null) {
  const v = (version || "").trim();
  if (!v) return "-";
  return v.startsWith("v") ? v : `v${v}`;
}

function parseManageTab(value: string | null): TabValue {
  return value === "plugins" || value === "guide"
    ? value
    : "plugins";
}

// ── 顶层组件 ──────────────────────────────────────────────────────
export function Extensions() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const [tab, setTab] = useState<TabValue>(() => parseManageTab(tabParam));
  const [guideExpanded, setGuideExpanded] = useState(false);
  const guideActive = searchParams.get("guide") === "1";

  useEffect(() => {
    setTab(parseManageTab(tabParam));
  }, [tabParam]);

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Button variant="ghost" size="sm" onClick={() => goBackOr(nav, "/plugins")}>
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回上一页
        </Button>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">模块安装与管理</h1>
          <p className="text-sm text-muted-foreground">
            这里负责安装、更新、卸载远程模块；装好后回模块中心按账号启用和配置。
          </p>
        </div>
      </div>

      {guideActive ? (
      <PluginInstallGuide
        expanded={guideExpanded}
        onToggle={() => setGuideExpanded((v) => !v)}
        onBack={() => nav("/plugins?guide=1")}
        onDone={() => {
          if (typeof window !== "undefined") {
            localStorage.setItem(NEW_ACCOUNT_GUIDE_SEEN_KEY, "1");
          }
          const next = new URLSearchParams(searchParams);
          next.delete("guide");
          nav(`/plugins/manage${next.toString() ? `?${next.toString()}` : ""}`, { replace: true });
          setGuideExpanded(false);
        }}
      />
      ) : null}

      <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
        <TabsList>
          <TabsTrigger value="plugins" className="gap-1.5">
            <Puzzle className="h-4 w-4" /> 安装与更新
          </TabsTrigger>
          <TabsTrigger value="guide" className="gap-1.5">
            <BookOpen className="h-4 w-4" /> 开发指南
          </TabsTrigger>
        </TabsList>

        <TabsContent value="plugins">
          <PluginsManagementTab />
        </TabsContent>
        <TabsContent value="guide">
          <DevGuideTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function PluginInstallGuide({
  expanded,
  onToggle,
  onBack,
  onDone,
}: {
  expanded: boolean;
  onToggle: () => void;
  onBack: () => void;
  onDone: () => void;
}) {
  if (!expanded) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary shadow-sm shadow-primary/20 transition hover:bg-primary/15"
        aria-label="打开新手指引"
      >
        <Sparkles className="h-4 w-4" />
        新手指引：安装后回模块中心启用
      </button>
    );
  }

  return (
    <Card className="max-w-2xl border-primary/30 bg-card/95 shadow-lg shadow-primary/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">3. 启用命令模板或调用模块</CardTitle>
        <CardDescription>
          这里只负责安装、更新和卸载远程模块。安装完成后，回模块中心选择账号，再启用和配置对应模块。
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onBack}>
          返回模块中心 <ChevronRight className="ml-1 h-4 w-4" />
        </Button>
        <Button size="sm" variant="outline" onClick={onDone}>
          我学会了！
        </Button>
        <Button size="sm" variant="ghost" onClick={onToggle}>
          收起
        </Button>
      </CardContent>
    </Card>
  );
}


// ═══════════════════════════════════════════════════════════════════
// Tab 2：插件管理 — 内置插件 + 远程插件统一展示
// ═══════════════════════════════════════════════════════════════════
function PluginsManagementTab() {
  return (
    <div className="space-y-6">
      <RemoteInstallCard />
      <InstalledPluginsSection />
    </div>
  );
}

// ── 远程安装：仓库管理 + 浏览插件 ────────────────────────────────
function RemoteInstallCard() {
  const qc = useQueryClient();
  const [addUrl, setAddUrl] = useState("");
  const [addName, setAddName] = useState("");
  const [expandedRepoId, setExpandedRepoId] = useState<number | null>(null);

  // 已保存仓库列表（后端）
  const reposQ = useQuery({ queryKey: PLUGIN_REPOS_QK, queryFn: fetchPluginRepos });
  const repos = reposQ.data ?? [];

  // 仓库内插件列表
  const pluginsQ = useQuery({
    queryKey: ["repo-plugins", expandedRepoId],
    queryFn: () => fetchRepoPlugins(expandedRepoId!),
    enabled: expandedRepoId !== null,
  });

  // 添加仓库
  const addRepoMut = useMutation({
    mutationFn: () => addPluginRepo({ url: addUrl.trim(), name: addName.trim() || undefined }),
    onSuccess: (row) => {
      toast.success(`已添加仓库 ${row.name || row.url}`);
      setAddUrl("");
      setAddName("");
      qc.invalidateQueries({ queryKey: PLUGIN_REPOS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // 删除仓库
  const delRepoMut = useMutation({
    mutationFn: (id: number) => deletePluginRepo(id),
    onSuccess: () => {
      toast.success("已移除仓库");
      setExpandedRepoId(null);
      qc.invalidateQueries({ queryKey: PLUGIN_REPOS_QK });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  // 从仓库安装插件
  const installFromRepoMut = useMutation({
    mutationFn: ({ repoId, name }: { repoId: number; name: string }) =>
      installFromRepo(repoId, name),
    onSuccess: (row) => {
      toast.success(`已安装 ${row.name} v${row.version}`);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
      qc.invalidateQueries({ queryKey: ["repo-plugins", expandedRepoId] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const updateFromRepoMut = useMutation({
    mutationFn: (name: string) => updateRemotePlugin(name),
    onSuccess: (row) => {
      toast.success(`已更新 ${row.name} → v${row.version}`);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
      qc.invalidateQueries({ queryKey: ["repo-plugins", expandedRepoId] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">模块仓库</CardTitle>
        <CardDescription>
          添加 Git 仓库后浏览并安装模块
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 添加仓库 */}
        <div className="flex gap-2">
          <input
            className="flex h-9 w-40 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder="仓库名（可选）"
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
          />
          <input
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            placeholder="https://github.com/user/repo.git"
            value={addUrl}
            onChange={(e) => setAddUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && addUrl.trim()) addRepoMut.mutate();
            }}
            disabled={addRepoMut.isPending}
          />
          <Button
            onClick={() => addRepoMut.mutate()}
            disabled={!addUrl.trim() || addRepoMut.isPending}
            className="shrink-0"
          >
            {addRepoMut.isPending ? (
              <><Spinner className="mr-2 h-4 w-4" /> 添加中…</>
            ) : (
              "添加仓库"
            )}
          </Button>
        </div>

        {/* 仓库列表 */}
        {repos.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">暂无已保存的仓库</p>
        ) : (
          <div className="space-y-2">
            {repos.map((repo) => (
              <div key={repo.id} className="rounded-md border">
                <div
                  className="flex cursor-pointer items-center gap-2 px-3 py-2 hover:bg-accent/50"
                  onClick={() => setExpandedRepoId(expandedRepoId === repo.id ? null : repo.id)}
                >
                  <ChevronRight
                    className={cn("h-4 w-4 shrink-0 transition-transform", expandedRepoId === repo.id && "rotate-90")}
                  />
                  <span className="flex-1 truncate text-sm font-medium">
                    {repo.name || repo.url}
                  </span>
                  {repo.name && (
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {repo.url}
                    </span>
                  )}
                  <Badge variant="outline" className="shrink-0">
                    {expandedRepoId === repo.id && pluginsQ.isLoading ? "加载中…" : "仓库"}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 shrink-0 p-0 text-muted-foreground hover:text-destructive"
                    onClick={(e) => {
                      e.stopPropagation();
                      delRepoMut.mutate(repo.id);
                    }}
                    title="移除仓库"
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {/* 展开：仓库内插件列表 */}
                {expandedRepoId === repo.id && (
                  <div className="border-t px-3 py-2">
                    {pluginsQ.isLoading ? (
                      <div className="flex h-16 items-center justify-center">
                        <Spinner className="text-primary" />
                      </div>
                    ) : pluginsQ.isError ? (
                      <p className="py-2 text-center text-sm text-destructive">
                        加载失败：{getErrMsg(pluginsQ.error)}
                      </p>
                    ) : (pluginsQ.data ?? []).length === 0 ? (
                      <p className="py-2 text-center text-sm text-muted-foreground">仓库内未找到模块</p>
                    ) : (
                      <div className="space-y-1">
                        {(pluginsQ.data ?? []).map((p) => {
                          const canUpdate = !!p.installed && !!p.update_available;
                          return (
                          <div
                            key={p.name}
                            className="flex flex-col gap-2 rounded px-2 py-1.5 hover:bg-accent/30 sm:flex-row sm:items-center"
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium">{p.display_name || p.name}</span>
                                <span className="font-mono text-xs text-muted-foreground">v{p.version}</span>
                                {canUpdate ? (
                                  <Badge variant="default" className="text-xs">可更新</Badge>
                                ) : p.installed ? (
                                  <Badge variant="secondary" className="text-xs">已安装</Badge>
                                ) : null}
                              </div>
                              {p.description && (
                                <p className="truncate text-xs text-muted-foreground">{p.description}</p>
                              )}
                              {canUpdate && p.installed_version && (
                                <p className="text-xs text-muted-foreground">
                                  当前 {formatPluginVersion(p.installed_version)}，仓库 {formatPluginVersion(p.version)}
                                </p>
                              )}
                            </div>
                            <Button
                              size="sm"
                              variant={canUpdate ? "default" : p.installed ? "outline" : "default"}
                              className="h-7 shrink-0"
                              disabled={
                                (p.installed && !canUpdate)
                                || installFromRepoMut.isPending
                                || updateFromRepoMut.isPending
                              }
                              onClick={() =>
                                canUpdate
                                  ? updateFromRepoMut.mutate(p.name)
                                  : installFromRepoMut.mutate({ repoId: repo.id, name: p.name })
                              }
                            >
                              {canUpdate ? "更新" : p.installed ? "已安装" : "安装"}
                            </Button>
                          </div>
                        );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── 已安装插件列表（内置 + 远程） ────────────────────────────────
function InstalledPluginsSection() {
  const nav = useNavigate();
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
    onSuccess: () => {
      toast.success("已启用；回模块中心按账号开启和配置");
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const disableRMMut = useMutation({
    mutationFn: (name: string) => disableRemotePlugin(name),
    onSuccess: () => {
      toast.success("已禁用全局开关");
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const updateRMMut = useMutation({
    mutationFn: (name: string) => updateRemotePlugin(name),
    onSuccess: (row) => {
      toast.success(`已更新 ${row.name} → v${row.version}`);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });
  const uninstallRMMut = useMutation({
    mutationFn: (name: string) => uninstallRemotePlugin(name),
    onSuccess: (_r, name) => {
      toast.success(`已卸载 ${name}`);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const isLoading = builtinQ.isLoading || thirdPartyQ.isLoading || remoteQ.isLoading;
  const builtin = builtinQ.data ?? [];
  const thirdParty = thirdPartyQ.data ?? [];
  const remote = remoteQ.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">已安装模块</CardTitle>
        <CardDescription>
          这里管理模块包本身；账号级启停和配置统一回模块中心处理。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex h-24 items-center justify-center"><Spinner className="text-primary" /></div>
        ) : builtin.length === 0 && thirdParty.length === 0 && remote.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">暂无已安装模块</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>模块</TableHead>
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
                    <Button size="sm" variant="outline" onClick={() => nav("/plugins")}>
                      去模块中心
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
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button size="sm" variant="outline" onClick={() => updateRMMut.mutate(p.name)} disabled={updateRMMut.isPending} title="从远程更新">
                        <RefreshCw className="mr-1 h-3 w-3" />
                        更新
                      </Button>
                      {p.enabled ? (
                        <Button size="sm" variant="outline" onClick={() => disableRMMut.mutate(p.name)} disabled={disableRMMut.isPending}>
                          <X className="mr-1 h-3 w-3" />
                          禁用
                        </Button>
                      ) : (
                        <Button size="sm" onClick={() => enableRMMut.mutate(p.name)} disabled={enableRMMut.isPending}>
                          <Power className="mr-1 h-3 w-3" />
                          启用
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="outline"
                        className="border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
                        onClick={() => { if (confirm(`确认卸载「${p.name}」？`)) uninstallRMMut.mutate(p.name); }}
                        disabled={uninstallRMMut.isPending}
                      >
                        <Trash2 className="mr-1 h-3 w-3" />
                        卸载
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => nav("/plugins")}>
                        去模块中心
                      </Button>
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
    <Card className="overflow-hidden">
      <CardHeader>
        <CardTitle className="text-base">模块开发指南</CardTitle>
        <CardDescription>
          源文件：<code>docs/PLUGIN-DEV-GUIDE.md</code>（远程安装与沙箱规则见
          <code className="ml-1">docs/REMOTE-PLUGIN-GUIDE.md</code>）
        </CardDescription>
      </CardHeader>
      <CardContent className="min-w-0 overflow-hidden">
        <article className="prose prose-sm prose-pwa-safe max-w-none dark:prose-invert">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {devGuideMd}
          </ReactMarkdown>
        </article>
      </CardContent>
    </Card>
  );
}
