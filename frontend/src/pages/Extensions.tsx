// 插件安装与管理：插件包安装/更新/卸载 + 开发指南
//
// Tab 1：安装与更新 — 本地内置插件 + 远程插件（安装/卸载/更新）
// Tab 2：开发指南 — 内置完整模块开发文档工作台
//
// 账号级启停与配置统一回 /plugins 首页，避免“安装页”和“插件中心”双入口重复。
// 远程插件原为独立 /remote-plugins 页面，现在统一收口到 /plugins/manage。
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Brain,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Code2,
  FileText,
  GitFork,
  Globe2,
  ListChecks,
  Network,
  Power,
  Puzzle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github.css";
import aiGuideMd from "../../../docs/PLUGIN-AI.md?raw";
import apiReferenceMd from "../../../docs/PLUGIN-API-REFERENCE.md?raw";
import cheatsheetMd from "../../../docs/PLUGIN-CHEATSHEET.md?raw";
import devGuideMd from "../../../docs/PLUGIN-DEV-GUIDE.md?raw";
import httpGuideMd from "../../../docs/PLUGIN-HTTP.md?raw";
import overviewMd from "../../../docs/PLUGIN-OVERVIEW.md?raw";
import remoteGuideMd from "../../../docs/PLUGIN-REMOTE.md?raw";
import safetyGuideMd from "../../../docs/PLUGIN-SAFETY.md?raw";

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
import { MetaBadge } from "@/components/ui/meta-badge";
import { Spinner } from "@/components/ui/misc";
import { SectionHeader, SignalPill } from "@/components/ui/status";
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
  checkRemotePluginUpdates,
  uninstallRemotePlugin,
} from "@/api/remotePlugin";
import { getSystemSettings, patchSystemSettings } from "@/api/system";
import {
  addPluginRepo,
  deletePluginRepo,
  fetchPluginRepos,
  fetchLocalPlugins,
  fetchRepoPlugins,
  installLocalPlugin,
  installFromRepo,
} from "@/api/pluginRepo";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { RemotePlugin } from "@/types/remotePlugin";

// ── 常量 ──────────────────────────────────────────────────────────
type TabValue = "plugins" | "guide";
type DevDocId =
  | "all"
  | "dev-guide"
  | "overview"
  | "api-reference"
  | "http"
  | "safety"
  | "remote"
  | "cheatsheet"
  | "ai";

type DevDoc = {
  id: DevDocId;
  title: string;
  description: string;
  path: string;
  markdown: string;
  icon: LucideIcon;
};

const PLUGINS_QK = ["installed-packages"] as const;
const REMOTE_QK = ["remote-plugins"] as const;
const PLUGIN_REPOS_QK = ["plugin-repos"] as const;
const NEW_ACCOUNT_GUIDE_SEEN_KEY = "telebot.accounts.new_account_guide_seen.v4";
const DEV_DOCS: DevDoc[] = [
  {
    id: "dev-guide",
    title: "索引与路线",
    description: "模块市场路线、文档分篇和 0.x 安全策略入口。",
    path: "docs/PLUGIN-DEV-GUIDE.md",
    markdown: devGuideMd,
    icon: BookOpen,
  },
  {
    id: "overview",
    title: "模块概览",
    description: "快速开始、模块结构、Route A 与 Route B 的边界。",
    path: "docs/PLUGIN-OVERVIEW.md",
    markdown: overviewMd,
    icon: FileText,
  },
  {
    id: "api-reference",
    title: "API 参考",
    description: "Plugin、Manifest、PluginContext、配置、派发、日志和前端集成。",
    path: "docs/PLUGIN-API-REFERENCE.md",
    markdown: apiReferenceMd,
    icon: Code2,
  },
  {
    id: "http",
    title: "HTTP facade",
    description: "第三方模块访问外部 HTTP 的权限、配额和调用约束。",
    path: "docs/PLUGIN-HTTP.md",
    markdown: httpGuideMd,
    icon: Network,
  },
  {
    id: "safety",
    title: "安全边界",
    description: "权限声明、交互 Bot、工程规范和安全合规要求。",
    path: "docs/PLUGIN-SAFETY.md",
    markdown: safetyGuideMd,
    icon: ShieldCheck,
  },
  {
    id: "remote",
    title: "远程模块",
    description: "远程安装、manifest 读取、worker loader 与更新回滚。",
    path: "docs/PLUGIN-REMOTE.md",
    markdown: remoteGuideMd,
    icon: Globe2,
  },
  {
    id: "cheatsheet",
    title: "速查表",
    description: "最常用契约、文件结构、权限和验证命令的短清单。",
    path: "docs/PLUGIN-CHEATSHEET.md",
    markdown: cheatsheetMd,
    icon: ListChecks,
  },
  {
    id: "ai",
    title: "AI facade",
    description: "ctx.ai 文本能力、权限声明、降级路径和运行时约束。",
    path: "docs/PLUGIN-AI.md",
    markdown: aiGuideMd,
    icon: Brain,
  },
];

const DOC_LINK_TO_ID: Record<string, DevDocId> = DEV_DOCS.reduce<Record<string, DevDocId>>(
  (acc, doc) => {
    const pathParts = doc.path.split("/");
    const filename = pathParts[pathParts.length - 1];
    if (filename) {
      acc[filename] = doc.id;
      acc[`./${filename}`] = doc.id;
      acc[`docs/${filename}`] = doc.id;
      acc[`../docs/${filename}`] = doc.id;
    }
    return acc;
  },
  {},
);

function formatPluginVersion(version?: string | null) {
  const v = (version || "").trim();
  if (!v) return "-";
  return v.startsWith("v") ? v : `v${v}`;
}

function toastPluginLintWarnings(row: RemotePlugin) {
  const warnings = row.lint_warnings ?? [];
  if (!warnings.length) return;
  toast.warning(`模块 ${row.name} 有 ${warnings.length} 条开发规范警告`, {
    description: warnings[0],
  });
}

function remoteVersionLabel(plugin: RemotePlugin) {
  if (plugin.update_available) return "不是最新版";
  if (plugin.last_update_check_error) return "检查失败";
  if (plugin.source_url?.startsWith("local://")) return "本地导入";
  if (plugin.last_update_check_at) return "已是最新版";
  return "未检查";
}

function remoteVersionTone(plugin: RemotePlugin): "neutral" | "success" | "warn" | "outline" {
  if (plugin.update_available) return "warn";
  if (plugin.last_update_check_error) return "warn";
  if (plugin.source_url?.startsWith("local://")) return "outline";
  if (plugin.last_update_check_at) return "success";
  return "neutral";
}

function parseManageTab(value: string | null): TabValue {
  return value === "plugins" || value === "guide"
    ? value
    : "plugins";
}

function stripFirstHeading(markdown: string) {
  return markdown.replace(/^#\s+.*(?:\r?\n)+/, "").trim();
}

function buildCompleteDevGuide() {
  return DEV_DOCS.map((doc, index) => {
    const level = index === 0 ? "#" : "##";
    return `${level} ${doc.title}\n\n> 源文件：\`${doc.path}\`\n\n${stripFirstHeading(doc.markdown)}`;
  }).join("\n\n---\n\n");
}

function normalizeDocHref(href?: string) {
  if (!href) return null;
  const [pathPart, anchorPart] = href.split("#");
  const normalizedPath = pathPart.replace(/^\.\//, "");
  const id = DOC_LINK_TO_ID[pathPart] ?? DOC_LINK_TO_ID[normalizedPath];
  return id ? { id, anchor: anchorPart ? `#${anchorPart}` : "" } : null;
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
        <Card>
          <CardHeader>
            <SectionHeader
              icon={Puzzle}
              title="模块安装与管理"
              description="这里负责安装、更新、卸载远程模块；装好后回模块中心按账号启用和配置。"
            />
          </CardHeader>
        </Card>
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
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onToggle}
        className="liquid-glass justify-start text-primary hover:text-primary"
        aria-label="打开新手指引"
      >
        <Sparkles className="h-4 w-4" />
        新手指引：安装后回模块中心启用
      </Button>
    );
  }

  return (
    <Card className="max-w-2xl border-primary/30 bg-card/95 shadow-lg shadow-primary/10">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">3. 启用指令模板或调用模块</CardTitle>
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
      <RemoteUpdateSettingsCard />
      <LocalImportCard />
      <RemoteInstallCard />
      <InstalledPluginsSection />
    </div>
  );
}

function RemoteUpdateSettingsCard() {
  const qc = useQueryClient();
  const settingsQ = useQuery({ queryKey: ["system", "settings"], queryFn: getSystemSettings });
  const cfg = settingsQ.data?.remote_plugin_update_check ?? { enabled: true, interval_minutes: 360 };
  const [enabled, setEnabled] = useState(cfg.enabled);
  const [interval, setInterval] = useState(String(cfg.interval_minutes));

  useEffect(() => {
    setEnabled(cfg.enabled);
    setInterval(String(cfg.interval_minutes));
  }, [cfg.enabled, cfg.interval_minutes]);

  const saveMut = useMutation({
    mutationFn: () =>
      patchSystemSettings({
        remote_plugin_update_check: {
          enabled,
          interval_minutes: Number(interval) || 360,
        },
      }),
    onSuccess: () => {
      toast.success("远程模块自动检查设置已保存");
      qc.invalidateQueries({ queryKey: ["system", "settings"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const checkMut = useMutation({
    mutationFn: checkRemotePluginUpdates,
    onSuccess: (res) => {
      toast.success(`检查完成：${res.update_available} 个模块有更新`);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader className="pb-3">
        <SectionHeader
          icon={RefreshCw}
          title="远程模块更新检查"
          description="后台只检查是否有新版本，不会自动安装；发现更新后会在模块中心和已安装模块里提示。"
        />
      </CardHeader>
      <CardContent className="flex flex-col gap-3 md:flex-row md:items-end">
        <div className="flex items-center gap-3 rounded-md border px-3 py-2">
          <Switch checked={enabled} onCheckedChange={setEnabled} />
          <div>
            <div className="text-sm font-medium">自动检查</div>
            <div className="text-xs text-muted-foreground">{enabled ? "已开启" : "已关闭"}</div>
          </div>
        </div>
        <div className="w-full space-y-1.5 md:w-56">
          <Label>检查间隔（分钟）</Label>
          <Input
            inputMode="numeric"
            value={interval}
            onChange={(e) => setInterval(e.target.value.replace(/[^0-9]/g, ""))}
            placeholder="360"
          />
          <div className="text-xs text-muted-foreground">最小 30，最大 10080</div>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
            保存
          </Button>
          <Button variant="outline" onClick={() => checkMut.mutate()} disabled={checkMut.isPending}>
            {checkMut.isPending ? <Spinner className="mr-2 h-4 w-4" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            立即检查
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function LocalImportCard() {
  const qc = useQueryClient();
  const localQ = useQuery({ queryKey: ["local-plugins"], queryFn: fetchLocalPlugins });
  const installLocalMut = useMutation({
    mutationFn: (name: string) => installLocalPlugin(name),
    onSuccess: (row) => {
      toast.success(`已导入本地插件 ${row.name} v${row.version}`);
      toastPluginLintWarnings(row);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: PLUGINS_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
      qc.invalidateQueries({ queryKey: ["local-plugins"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader className="pb-3">
        <SectionHeader
          icon={GitFork}
          title="本地导入"
          description={<>把按开发文档编写好的插件目录放到 <code>plugins/local_imports/</code>，然后在这里一键导入用于本地调试。</>}
        />
      </CardHeader>
      <CardContent>
        {localQ.isLoading ? (
          <div className="flex h-16 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : (localQ.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">
            还没发现可导入插件。请先把插件目录放入 <code>plugins/local_imports/</code>（目录内需包含 <code>plugin.json</code>）。
          </p>
        ) : (
          <div className="space-y-2">
            {(localQ.data ?? []).map((p) => (
              <div key={p.name} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{p.display_name || p.name}</div>
                  <div className="truncate text-xs text-muted-foreground">{p.subdir || p.name} · v{p.version}</div>
                </div>
                <Button
                  size="sm"
                  disabled={installLocalMut.isPending || p.installed}
                  onClick={() => installLocalMut.mutate(p.name)}
                >
                  {p.installed ? "已导入" : "导入"}
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
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
      toastPluginLintWarnings(row);
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
      toastPluginLintWarnings(row);
      qc.invalidateQueries({ queryKey: REMOTE_QK });
      qc.invalidateQueries({ queryKey: ["matrix"] });
      qc.invalidateQueries({ queryKey: ["repo-plugins", expandedRepoId] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return (
    <Card>
      <CardHeader className="pb-3">
        <SectionHeader
          icon={GitFork}
          title="模块仓库"
          description="添加 Git 仓库后浏览并安装模块"
        />
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
                  <MetaBadge tone="outline" className="shrink-0">
                    {expandedRepoId === repo.id && pluginsQ.isLoading ? "加载中…" : "仓库"}
                  </MetaBadge>
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
                                  <MetaBadge tone="success">可更新</MetaBadge>
                                ) : p.installed ? (
                                  <MetaBadge>已安装</MetaBadge>
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
    select: (data) => data.features.filter((f) => f.is_builtin && f.key !== "forward"),
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
    onSuccess: (res) => {
      const suffix = typeof res.applied === "number" ? `，已同步 ${res.applied} 个账号` : "";
      toast.success(`已启用远程模块${suffix}`);
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
      toastPluginLintWarnings(row);
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
  const [expandedWarnings, setExpandedWarnings] = useState<Set<string>>(() => new Set());
  const builtin = builtinQ.data ?? [];
  const thirdParty = thirdPartyQ.data ?? [];
  const remote = remoteQ.data ?? [];
  const matrixQ = useQuery({ queryKey: ["matrix"], queryFn: getFeatureMatrix });
  const accounts = matrixQ.data?.accounts ?? [];
  const accountCount = accounts.length;
  const remoteEnabledCount = (name: string) =>
    accounts.filter((account) => account.feature_enabled?.[name] ?? account.features[name] !== "disabled").length;
  const toggleWarnings = (name: string) => {
    setExpandedWarnings((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <Card>
      <CardHeader>
        <SectionHeader
          icon={Puzzle}
          title="已安装模块"
          description="这里管理模块包本身；账号级启停和配置统一回模块中心处理。"
          meta={(
            <SignalPill
              tone="neutral"
              label="总计"
              value={builtin.length + thirdParty.length + remote.length}
              className="h-8"
            />
          )}
        />
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
                <TableHead>版本状态</TableHead>
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
                  <TableCell><MetaBadge>内置</MetaBadge></TableCell>
                  <TableCell>{formatPluginVersion(f.version)}</TableCell>
                  <TableCell><MetaBadge tone="success">随系统更新</MetaBadge></TableCell>
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
                  <TableCell><MetaBadge>第三方</MetaBadge></TableCell>
                  <TableCell>{formatPluginVersion(row.version)}</TableCell>
                  <TableCell>
                    <MetaBadge tone="outline">本地安装</MetaBadge>
                    <div className="mt-1 text-xs text-muted-foreground">
                      状态 {row.enabled ? "已启用" : "未启用"}
                    </div>
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
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="font-medium">{p.display_name || p.name}</div>
                      {p.update_available ? <MetaBadge tone="warn">有新版本</MetaBadge> : null}
                      {(p.lint_warnings?.length ?? 0) > 0 ? (
                        <button
                          type="button"
                          className="inline-flex"
                          onClick={() => toggleWarnings(p.name)}
                          aria-expanded={expandedWarnings.has(p.name)}
                        >
                          <MetaBadge tone="warn">
                            规范警告
                            <ChevronDown
                              className={cn(
                                "h-3 w-3 transition-transform",
                                expandedWarnings.has(p.name) && "rotate-180",
                              )}
                            />
                          </MetaBadge>
                        </button>
                      ) : null}
                    </div>
                    <div className="font-mono text-xs text-muted-foreground">{p.name}</div>
                    {p.update_available && p.latest_version ? (
                      <div className="mt-1 text-xs text-muted-foreground">
                        当前 {formatPluginVersion(p.version)}，远程 {formatPluginVersion(p.latest_version)}
                      </div>
                    ) : null}
                    {p.last_update_check_error ? (
                      <div className="mt-1 text-xs text-destructive">
                        更新检查失败：{p.last_update_check_error}
                      </div>
                    ) : null}
                    {(p.lint_warnings?.length ?? 0) > 0 && expandedWarnings.has(p.name) ? (
                      <div className="mt-2 space-y-1 rounded-md border border-amber-500/30 bg-amber-50/70 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
                        {p.lint_warnings?.map((warning, index) => (
                          <div key={`${p.name}-warning-${index}`} className="leading-5">
                            {warning}
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell>
                    {p.source_url?.startsWith("local://") ? (
                      <MetaBadge>本地导入</MetaBadge>
                    ) : (
                      <MetaBadge><GitFork className="h-3 w-3" />远程</MetaBadge>
                    )}
                  </TableCell>
                  <TableCell>{formatPluginVersion(p.version)}</TableCell>
                  <TableCell>
                    <MetaBadge tone={remoteVersionTone(p)}>
                      {remoteVersionLabel(p)}
                    </MetaBadge>
                    {p.update_available && p.latest_version ? (
                      <div className="mt-1 text-xs text-muted-foreground">
                        远程 {formatPluginVersion(p.latest_version)}
                      </div>
                    ) : null}
                    {accountCount > 0 ? (
                      <div className="mt-1 text-xs text-muted-foreground">
                        账号启用 {remoteEnabledCount(p.name)}/{accountCount}
                      </div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        size="sm"
                        variant={p.update_available ? "default" : "outline"}
                        onClick={() => updateRMMut.mutate(p.name)}
                        disabled={updateRMMut.isPending || p.source_url?.startsWith("local://")}
                        title={p.source_url?.startsWith("local://") ? "本地导入插件不支持远程更新" : "从远程更新"}
                      >
                        <RefreshCw className="mr-1 h-3 w-3" />
                        {p.update_available ? "更新到新版" : "更新"}
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
// Tab 2：开发指南
// ═══════════════════════════════════════════════════════════════════
function DevGuideTab() {
  const completeDoc = useMemo<DevDoc>(
    () => ({
      id: "all",
      title: "完整文档",
      description: "把模块索引、概览、API、HTTP、安全、远程、速查和 AI facade 合并为一份可滚动正文。",
      path: "docs/PLUGIN-*.md",
      markdown: buildCompleteDevGuide(),
      icon: Sparkles,
    }),
    [],
  );
  const docs = useMemo(() => [completeDoc, ...DEV_DOCS], [completeDoc]);
  const [activeDocId, setActiveDocId] = useState<DevDocId>("all");
  const contentRef = useRef<HTMLDivElement | null>(null);
  const activeDoc = docs.find((doc) => doc.id === activeDocId) ?? completeDoc;
  const ActiveIcon = activeDoc.icon;
  const markdownComponents = useMemo<Components>(
    () => ({
      a({ href, children, ...props }) {
        const target = normalizeDocHref(href);
        if (target) {
          return (
            <button
              type="button"
              className="font-medium text-primary underline decoration-primary/35 underline-offset-4 transition-colors hover:text-primary/80"
              onClick={() => setActiveDocId(target.id)}
              title={target.anchor ? `${DOC_LINK_TO_ID[href ?? ""] ?? target.id}${target.anchor}` : undefined}
            >
              {children}
            </button>
          );
        }
        const external = href?.startsWith("http://") || href?.startsWith("https://");
        return (
          <a
            {...props}
            href={href}
            target={external ? "_blank" : undefined}
            rel={external ? "noreferrer" : undefined}
          >
            {children}
          </a>
        );
      },
    }),
    [],
  );

  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0 });
  }, [activeDocId]);

  return (
    <Card className="overflow-hidden">
      <CardHeader className="gap-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <CardTitle className="text-base">模块开发文档</CardTitle>
            <CardDescription className="mt-1">
              内置完整开发文档，支持直接阅读合集，也可以按主题查看每个分篇。
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2 md:justify-end">
            <SignalPill tone="primary" label="文档" value={`${DEV_DOCS.length} 篇`} />
            <SignalPill tone="neutral" label="当前" value={activeDoc.title} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="grid min-h-[680px] border-t border-border/70 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="border-b border-border/70 bg-muted/20 p-3 lg:border-b-0 lg:border-r">
            <nav className="flex gap-2 overflow-x-auto pb-1 lg:block lg:space-y-1 lg:overflow-visible lg:pb-0">
              {docs.map((doc) => {
                const Icon = doc.icon;
                const active = doc.id === activeDoc.id;
                return (
                  <button
                    key={doc.id}
                    type="button"
                    className={cn(
                      "group flex min-w-[11rem] items-start gap-3 rounded-lg border px-3 py-3 text-left text-sm transition lg:w-full",
                      active
                        ? "border-primary/30 bg-primary/10 text-foreground shadow-sm"
                        : "border-transparent bg-background/65 text-muted-foreground hover:border-border hover:bg-background hover:text-foreground",
                    )}
                    onClick={() => setActiveDocId(doc.id)}
                  >
                    <span
                      className={cn(
                        "mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md border",
                        active
                          ? "border-primary/25 bg-primary/10 text-primary"
                          : "border-border/70 bg-muted/60 text-muted-foreground group-hover:text-foreground",
                      )}
                    >
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{doc.title}</span>
                      <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                        {doc.description}
                      </span>
                    </span>
                  </button>
                );
              })}
            </nav>
          </aside>
          <section className="min-w-0 bg-background">
            <div className="border-b border-border/70 px-5 py-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-border/70 bg-muted/60 text-primary">
                  <ActiveIcon className="h-4 w-4" />
                </span>
                <h3 className="min-w-0 text-base font-semibold tracking-tight">{activeDoc.title}</h3>
                <MetaBadge tone="outline" mono className="max-w-full truncate">
                  {activeDoc.path}
                </MetaBadge>
              </div>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                {activeDoc.description}
              </p>
            </div>
            <div ref={contentRef} className="max-h-[72vh] min-h-[560px] overflow-auto px-5 py-5 md:px-7">
              <article className="prose prose-sm prose-pwa-safe max-w-none dark:prose-invert">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                  components={markdownComponents}
                >
                  {activeDoc.markdown}
                </ReactMarkdown>
              </article>
            </div>
          </section>
        </div>
      </CardContent>
    </Card>
  );
}
