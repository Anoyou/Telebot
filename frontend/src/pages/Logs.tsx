// 日志中心：runtime_log 拆成三个 tab —— 消息日志 / 插件日志 / 系统日志
//
// 消息日志（source=event）：incoming 消息进来、命令派发等业务事件，
// 适合用于"为什么我的 auto_reply 没回复 / 转发到底有没有发出"这类问题排查。
//
// 插件日志（source=plugin）：插件自己的 ctx.log 输出、插件 on_message 异常、命中/跳过原因，
// 适合用于"某个插件为什么没按预期工作"这类问题排查。
//
// 系统日志（source=system）：worker 启停、IPC reload、风控状态、技术异常，
// 适合用于"账号是不是真的 active / kill switch 是不是真的下发了"这类问题排查。
//
// 两个 tab 共享下方账号 / level / 关键词过滤；切换 tab 不重置过滤；自动刷新只在当前
// tab 上拉，避免重复请求。关键词搜索是**前端 substring 匹配**——后端拉 200 条之内
// 在浏览器里 grep；不打 DB 是为了让搜索响应零延时。
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/misc";
import { listRuntimeLogs } from "@/api/system";
import { listAccounts } from "@/api/accounts";
import { formatDateTime } from "@/lib/utils";
import type { RuntimeLogItem } from "@/api/types";

const LEVEL_VARIANT: Record<
  string,
  "secondary" | "warn" | "destructive" | "success"
> = {
  debug: "secondary",
  info: "success",
  warning: "warn",
  warn: "warn",
  error: "destructive",
};

type LogTab = "event" | "plugin" | "system";

const BUILTIN_PLUGIN_KEYS = [
  "auto_reply",
  "autorepeat",
  "codex_image",
  "forward",
  "game24",
  "scheduler",
];

export function Logs() {
  const [tab, setTab] = useState<LogTab>("event");
  const [accountId, setAccountId] = useState("");
  const [level, setLevel] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");
  const [pluginKey, setPluginKey] = useState("");

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">日志中心</h1>
        <p className="text-sm text-muted-foreground">
          消息、插件、系统三类日志分开看；默认 5 秒自动刷新
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">过滤</CardTitle>
          <CardDescription>账号 / 级别 / 关键词 / 自动刷新——两 tab 共用</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5 lg:items-end">
            <div className="space-y-1.5">
              <Label>账号</Label>
              <Select
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
              >
                <option value="">全部</option>
                {accountsQ.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name || a.phone}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>级别</Label>
              <Select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">全部</option>
                <option value="debug">debug</option>
                <option value="info">info</option>
                <option value="warning">warning</option>
                <option value="error">error</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>插件</Label>
              <PluginSelect value={pluginKey} onChange={setPluginKey} />
            </div>
            <div className="space-y-1.5 lg:col-span-1">
              <Label>关键词搜索</Label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pl-8 pr-8"
                  placeholder="比如：FloodWait / template_id=3 / sk-..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {search ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="absolute right-1 top-1/2 h-6 -translate-y-1/2 px-2 text-xs text-muted-foreground"
                    onClick={() => setSearch("")}
                    title="清空"
                  >
                    ✕
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>自动刷新</Label>
              <div className="flex h-10 items-center gap-2">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
                <span className="text-sm text-muted-foreground">
                  {autoRefresh ? "5s 拉取一次" : "已停止"}
                </span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={(v) => setTab(v as LogTab)}>
        <TabsList>
          <TabsTrigger value="event">消息日志</TabsTrigger>
          <TabsTrigger value="plugin">插件日志</TabsTrigger>
          <TabsTrigger value="system">系统日志</TabsTrigger>
        </TabsList>

        <TabsContent value="event">
          <LogTable
            source="event"
            accountId={accountId}
            level={level}
            pluginKey=""
            search={search}
            autoRefresh={autoRefresh && tab === "event"}
            description="收到消息、命令分发等入口事件。先确认消息有没有进入系统。"
          />
        </TabsContent>

        <TabsContent value="plugin">
          <LogTable
            source="plugin"
            accountId={accountId}
            level={level}
            pluginKey={pluginKey}
            search={search}
            autoRefresh={autoRefresh && tab === "plugin"}
            description="插件自己的运行记录和异常。排查「24 点、自动回复、转发为什么没反应」优先看这里。"
          />
        </TabsContent>

        <TabsContent value="system">
          <LogTable
            source="system"
            accountId={accountId}
            level={level}
            pluginKey=""
            search={search}
            autoRefresh={autoRefresh && tab === "system"}
            description="worker 启停、IPC reload、风控状态、平台级异常。排查「账号是不是活着」看这里。"
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── 单个 tab 的日志表 ─────────────────────────────────────────────
function LogTable({
  source,
  accountId,
  level,
  pluginKey,
  search,
  autoRefresh,
  description,
}: {
  source: "event" | "plugin" | "system";
  accountId: string;
  level: string;
  pluginKey: string;
  search: string;
  autoRefresh: boolean;
  description: string;
}) {
  const filters = {
    source,
    account_id: accountId || undefined,
    level: level || undefined,
    plugin_key: source === "plugin" && pluginKey ? pluginKey : undefined,
    limit: 200,
  };
  const logsQ = useQuery({
    queryKey: ["logs", filters],
    queryFn: () => listRuntimeLogs(filters),
    refetchInterval: autoRefresh ? 5_000 : false,
    refetchIntervalInBackground: false,
  });

  // 关键词过滤：纯前端 substring（不区分大小写）。在 200 条窗口内做，零延时。
  // 更高级的 regex / 字段联检后续可加；先满足"找到那条"的核心需求。
  const filtered = useMemo(() => {
    const all = logsQ.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((l) => {
      const detailText = l.detail ? JSON.stringify(l.detail).toLowerCase() : "";
      return l.message.toLowerCase().includes(q) || detailText.includes(q);
    });
  }, [logsQ.data, search]);

  const totalCount = logsQ.data?.length ?? 0;
  const showCount = filtered.length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {source === "event"
            ? "消息日志"
            : source === "plugin"
              ? "插件日志"
              : "系统日志"}
        </CardTitle>
        <CardDescription className="flex items-center justify-between gap-2">
          <span>{description}</span>
          {search.trim() || (source === "plugin" && pluginKey) ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              已过滤 <strong className="text-foreground">{showCount}</strong> /
              {" "}{totalCount}
            </span>
          ) : null}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {logsQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : filtered.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-40">时间</TableHead>
                <TableHead className="w-20">级别</TableHead>
                <TableHead className="w-24">账号</TableHead>
                <TableHead>发生了什么</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((l: RuntimeLogItem) => (
                <TableRow key={l.id}>
                  <TableCell className="font-mono text-xs">
                    {formatDateTime(l.created_at)}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        LEVEL_VARIANT[l.level.toLowerCase()] ?? "secondary"
                      }
                    >
                      {l.level.toUpperCase()}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {l.account_id ? `#${l.account_id}` : "—"}
                  </TableCell>
                  <TableCell className="text-xs whitespace-pre-wrap">
                    <div className="font-mono">
                      <HighlightedMessage text={l.message} keyword={search} />
                    </div>
                    {l.detail ? (
                      <LogDetail detail={l.detail} keyword={search} />
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {search.trim() ? (
              <>
                没找到匹配 <code className="font-mono">{search}</code> 的日志
                <br />
                <span className="text-xs">（仅在已加载的 {totalCount} 条窗口内搜索；想扩窗口可清空过滤）</span>
              </>
            ) : (
              <>
                该分类暂无日志
                {source === "event"
                  ? " — 让人给本账号发条消息，再回来看"
                  : source === "plugin"
                    ? " — 插件还没有输出运行记录"
                    : " — 没有错误是好事"}
              </>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function PluginSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const logsQ = useQuery({
    queryKey: ["logs", "plugin-keys"],
    queryFn: () => listRuntimeLogs({ source: "plugin", limit: 200 }),
    staleTime: 30_000,
  });
  const keys = useMemo(() => {
    const discovered = new Set<string>(BUILTIN_PLUGIN_KEYS);
    for (const row of logsQ.data ?? []) {
      const raw = row.detail?.plugin_key;
      if (typeof raw === "string" && raw.trim()) discovered.add(raw.trim());
    }
    return [...discovered].sort();
  }, [logsQ.data]);

  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">全部插件</option>
      {keys.map((key) => (
        <option key={key} value={key}>
          {key}
        </option>
      ))}
    </Select>
  );
}

function LogDetail({
  detail,
  keyword,
}: {
  detail: Record<string, unknown>;
  keyword: string;
}) {
  const rows = Object.entries(detail).filter(([, value]) => value !== undefined && value !== null);
  if (!rows.length) return null;
  return (
    <div className="mt-2 grid gap-1 rounded-md bg-muted/60 px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
      {rows.slice(0, 8).map(([key, value]) => (
        <div key={key} className="break-all">
          <span className="text-foreground/70">{key}: </span>
          <HighlightedMessage text={formatDetailValue(value)} keyword={keyword} />
        </div>
      ))}
    </div>
  );
}

function formatDetailValue(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// ── 关键词高亮：把匹配段落用 <mark> 包起来，便于一眼定位 ──
function HighlightedMessage({ text, keyword }: { text: string; keyword: string }) {
  const q = keyword.trim();
  if (!q) return <>{text}</>;
  // 分块：保留原大小写但匹配大小写不敏感
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const parts: React.ReactNode[] = [];
  let i = 0;
  let n = 0;
  while (true) {
    const idx = lower.indexOf(needle, i);
    if (idx < 0) {
      parts.push(text.slice(i));
      break;
    }
    if (idx > i) parts.push(text.slice(i, idx));
    parts.push(
      <mark
        key={`m${n++}`}
        className="bg-amber-200/60 dark:bg-amber-400/30 rounded px-0.5"
      >
        {text.slice(idx, idx + needle.length)}
      </mark>,
    );
    i = idx + needle.length;
    // 防御：避免空 needle 死循环
    if (needle.length === 0) break;
  }
  return <>{parts}</>;
}
