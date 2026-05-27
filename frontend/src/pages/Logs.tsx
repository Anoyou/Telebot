import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Activity, MessageSquareText, Puzzle, Search, ScrollText, ServerCog, ShieldCheck } from "lucide-react";

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
import { PageHeader, PageShell } from "@/components/layout/PageScaffold";
import { Spinner } from "@/components/ui/misc";
import { getSystemSettings, listAuditLogs, listRuntimeLogs } from "@/api/system";
import { listAccounts } from "@/api/accounts";
import { formatDateTime } from "@/lib/utils";
import type { AuditLogItem, RuntimeLogItem } from "@/api/types";

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

type MainTab = "runtime" | "audit";
type RuntimeSourceTab = "event" | "plugin" | "system";

const BUILTIN_PLUGIN_KEYS = [
  "auto_reply",
  "autorepeat",
  "codex_image",
  "forward",
  "game24",
  "scheduler",
];

export function Logs() {
  const [mainTab, setMainTab] = useState<MainTab>("runtime");

  const [runtimeTab, setRuntimeTab] = useState<RuntimeSourceTab>("event");
  const [runtimeAccountId, setRuntimeAccountId] = useState("");
  const [runtimeLevel, setRuntimeLevel] = useState("");
  const [runtimePluginKey, setRuntimePluginKey] = useState("");
  const [runtimeSearch, setRuntimeSearch] = useState("");
  const [runtimeAutoRefresh, setRuntimeAutoRefresh] = useState(true);

  const [auditUserId, setAuditUserId] = useState("");
  const [auditAction, setAuditAction] = useState("");
  const [auditSearch, setAuditSearch] = useState("");

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });
  const settingsQ = useQuery({
    queryKey: ["system", "settings"],
    queryFn: getSystemSettings,
  });
  const timezone = settingsQ.data?.timezone || "";

  return (
    <PageShell>
      <PageHeader
        title="日志中心"
        description="运行日志与审计日志分开展示，避免混查；默认轻量查询窗口。"
        icon={ScrollText}
      />

      <Tabs value={mainTab} onValueChange={(v) => setMainTab(v as MainTab)}>
        <TabsList>
          <TabsTrigger value="runtime" className="gap-1.5">
            <Activity className="h-4 w-4" /> 运行日志
          </TabsTrigger>
          <TabsTrigger value="audit" className="gap-1.5">
            <ShieldCheck className="h-4 w-4" /> 审计日志
          </TabsTrigger>
        </TabsList>

        <TabsContent value="runtime" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">运行日志过滤</CardTitle>
              <CardDescription>账号 / 级别 / 模块 / 关键词 / 自动刷新</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5 lg:items-end">
                <div className="space-y-1.5">
                  <Label>账号</Label>
                  <Select
                    value={runtimeAccountId}
                    onChange={(e) => setRuntimeAccountId(e.target.value)}
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
                  <Select
                    value={runtimeLevel}
                    onChange={(e) => setRuntimeLevel(e.target.value)}
                  >
                    <option value="">全部</option>
                    <option value="debug">debug</option>
                    <option value="info">info</option>
                    <option value="warning">warning</option>
                    <option value="error">error</option>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>模块</Label>
                  <PluginSelect value={runtimePluginKey} onChange={setRuntimePluginKey} />
                </div>
                <div className="space-y-1.5">
                  <Label>关键词搜索</Label>
                  <SearchInput value={runtimeSearch} onChange={setRuntimeSearch} />
                </div>
                <div className="space-y-1.5">
                  <Label>自动刷新</Label>
                  <div className="flex h-10 items-center gap-2">
                    <Switch
                      checked={runtimeAutoRefresh}
                      onCheckedChange={setRuntimeAutoRefresh}
                    />
                    <span className="text-sm text-muted-foreground">
                      {runtimeAutoRefresh ? "5s 拉取一次" : "已停止"}
                    </span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          <Tabs
            value={runtimeTab}
            onValueChange={(v) => setRuntimeTab(v as RuntimeSourceTab)}
          >
            <TabsList>
              <TabsTrigger value="event" className="gap-1.5">
                <MessageSquareText className="h-4 w-4" /> 消息日志
              </TabsTrigger>
              <TabsTrigger value="plugin" className="gap-1.5">
                <Puzzle className="h-4 w-4" /> 模块日志
              </TabsTrigger>
              <TabsTrigger value="system" className="gap-1.5">
                <ServerCog className="h-4 w-4" /> 系统日志
              </TabsTrigger>
            </TabsList>

            <TabsContent value="event">
              <RuntimeLogTable
                source="event"
                accountId={runtimeAccountId}
                level={runtimeLevel}
                pluginKey=""
                search={runtimeSearch}
                autoRefresh={runtimeAutoRefresh && mainTab === "runtime" && runtimeTab === "event"}
                timezone={timezone}
                description="收到消息、指令分发等入口事件。"
              />
            </TabsContent>

            <TabsContent value="plugin">
              <RuntimeLogTable
                source="plugin"
                accountId={runtimeAccountId}
                level={runtimeLevel}
                pluginKey={runtimePluginKey}
                search={runtimeSearch}
                autoRefresh={runtimeAutoRefresh && mainTab === "runtime" && runtimeTab === "plugin"}
                timezone={timezone}
                description="模块运行记录和异常。"
              />
            </TabsContent>

            <TabsContent value="system">
              <RuntimeLogTable
                source="system"
                accountId={runtimeAccountId}
                level={runtimeLevel}
                pluginKey=""
                search={runtimeSearch}
                autoRefresh={runtimeAutoRefresh && mainTab === "runtime" && runtimeTab === "system"}
                timezone={timezone}
                description="worker 启停、IPC reload、平台级异常。"
              />
            </TabsContent>
          </Tabs>
        </TabsContent>

        <TabsContent value="audit" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">审计日志过滤</CardTitle>
              <CardDescription>用户 / 操作类型 / 关键词（与运行日志过滤独立）</CardDescription>
            </CardHeader>
            <CardContent>
              <AuditFilters
                userId={auditUserId}
                onUserIdChange={setAuditUserId}
                action={auditAction}
                onActionChange={setAuditAction}
                search={auditSearch}
                onSearchChange={setAuditSearch}
              />
            </CardContent>
          </Card>

          <AuditLogTable userId={auditUserId} action={auditAction} search={auditSearch} timezone={timezone} />
        </TabsContent>
      </Tabs>
    </PageShell>
  );
}

function RuntimeLogTable({
  source,
  accountId,
  level,
  pluginKey,
  search,
  autoRefresh,
  timezone,
  description,
}: {
  source: RuntimeSourceTab;
  accountId: string;
  level: string;
  pluginKey: string;
  search: string;
  autoRefresh: boolean;
  timezone?: string;
  description: string;
}) {
  const filters = {
    source,
    account_id: accountId || undefined,
    level: level || undefined,
    plugin_key: source === "plugin" && pluginKey ? pluginKey : undefined,
    limit: 100,
  };
  const logsQ = useQuery({
    queryKey: ["logs", "runtime", filters],
    queryFn: () => listRuntimeLogs(filters),
    refetchInterval: autoRefresh ? 5_000 : false,
    refetchIntervalInBackground: false,
  });

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
          {source === "event" ? "消息日志" : source === "plugin" ? "模块日志" : "系统日志"}
        </CardTitle>
        <CardDescription className="flex items-center justify-between gap-2">
          <span>{description}</span>
          {search.trim() || (source === "plugin" && pluginKey) ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              已过滤 <strong className="text-foreground">{showCount}</strong> / {totalCount}
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
          <div className="overflow-x-auto">
            <Table className="min-w-[760px]">
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
                    <TableCell className="font-mono text-xs">{formatDateTime(l.created_at, timezone)}</TableCell>
                    <TableCell>
                      <Badge variant={LEVEL_VARIANT[l.level.toLowerCase()] ?? "secondary"}>
                        {l.level.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{l.account_id ? `#${l.account_id}` : "—"}</TableCell>
                    <TableCell className="text-xs whitespace-pre-wrap">
                      <div className="font-mono">
                        <HighlightedMessage text={l.message} keyword={search} />
                      </div>
                      {l.detail ? <LogDetail detail={l.detail} keyword={search} /> : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {search.trim() ? (
              <>
                没找到匹配 <code className="font-mono">{search}</code> 的日志
                <br />
                <span className="text-xs">（仅在已加载的 {totalCount} 条窗口内搜索）</span>
              </>
            ) : (
              <>该分类暂无日志</>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function AuditFilters({
  userId,
  onUserIdChange,
  action,
  onActionChange,
  search,
  onSearchChange,
}: {
  userId: string;
  onUserIdChange: (v: string) => void;
  action: string;
  onActionChange: (v: string) => void;
  search: string;
  onSearchChange: (v: string) => void;
}) {
  const actionsQ = useQuery({
    queryKey: ["logs", "audit", "actions"],
    queryFn: () => listAuditLogs({ limit: 500 }),
    staleTime: 30_000,
  });

  const actions = useMemo(() => {
    const discovered = new Set<string>();
    for (const row of actionsQ.data ?? []) {
      if (row.action.trim()) discovered.add(row.action.trim());
    }
    return [...discovered].sort();
  }, [actionsQ.data]);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 lg:items-end">
      <div className="space-y-1.5">
        <Label>用户 ID</Label>
        <Input
          placeholder="例如：1"
          value={userId}
          onChange={(e) => onUserIdChange(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label>Action</Label>
        <Select value={action} onChange={(e) => onActionChange(e.target.value)}>
          <option value="">全部</option>
          {actions.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label>关键词搜索</Label>
        <SearchInput value={search} onChange={onSearchChange} />
      </div>
    </div>
  );
}

function AuditLogTable({
  userId,
  action,
  search,
  timezone,
}: {
  userId: string;
  action: string;
  search: string;
  timezone?: string;
}) {
  const uid = Number(userId);
  const qUserId = Number.isInteger(uid) && uid > 0 ? uid : undefined;

  const logsQ = useQuery({
    queryKey: [
      "logs",
      "audit",
      { user_id: qUserId, action: action || undefined, keyword: search || undefined, limit: 100 },
    ],
    queryFn: () =>
      listAuditLogs({
        user_id: qUserId,
        action: action || undefined,
        keyword: search.trim() || undefined,
        limit: 100,
      }),
  });

  const filtered = useMemo(() => {
    const all = logsQ.data ?? [];
    if (!action) return all;
    return all.filter((l) => l.action === action);
  }, [logsQ.data, action]);

  const endpointMissing = isAxiosError(logsQ.error) &&
    (logsQ.error.response?.status === 404 || logsQ.error.response?.status === 405);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">审计日志</CardTitle>
        <CardDescription>列：ts / user_id / action / target / detail 摘要</CardDescription>
      </CardHeader>
      <CardContent>
        {logsQ.isLoading ? (
          <div className="flex h-20 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : endpointMissing ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            当前环境未提供审计日志 endpoint（预期：/api/logs/audit）
          </p>
        ) : filtered.length > 0 ? (
          <div className="overflow-x-auto">
            <Table className="min-w-[860px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">ts</TableHead>
                  <TableHead className="w-20">user_id</TableHead>
                  <TableHead className="w-48">action</TableHead>
                  <TableHead className="w-44">target</TableHead>
                  <TableHead>detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((l: AuditLogItem) => (
                  <TableRow key={l.id}>
                    <TableCell className="font-mono text-xs">{formatDateTime(l.ts, timezone)}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {l.user_id ?? "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{l.action}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground break-all">
                      {l.target || "—"}
                    </TableCell>
                    <TableCell className="text-xs whitespace-pre-wrap">
                      {l.detail ? (
                        <AuditDetail detail={l.detail} keyword={search} />
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-muted-foreground">暂无符合条件的 audit 日志</p>
        )}
      </CardContent>
    </Card>
  );
}

function SearchInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        className="pl-8 pr-8"
        placeholder="关键词"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {value ? (
        <Button
          variant="ghost"
          size="sm"
          className="absolute right-1 top-1/2 h-6 -translate-y-1/2 px-2 text-xs text-muted-foreground"
          onClick={() => onChange("")}
          title="清空"
        >
          ✕
        </Button>
      ) : null}
    </div>
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
    queryFn: () => listRuntimeLogs({ source: "plugin", limit: 100 }),
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
      <option value="">全部模块</option>
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

function AuditDetail({
  detail,
  keyword,
}: {
  detail: Record<string, unknown>;
  keyword: string;
}) {
  const rows = Object.entries(detail).filter(([, value]) => value !== undefined && value !== null);
  if (!rows.length) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="grid gap-1 rounded-md bg-muted/60 px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
      {rows.slice(0, 5).map(([key, value]) => (
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

function HighlightedMessage({ text, keyword }: { text: string; keyword: string }) {
  const q = keyword.trim();
  if (!q) return <>{text}</>;
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
    if (needle.length === 0) break;
  }
  return <>{parts}</>;
}
