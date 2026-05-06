// 日志中心：runtime_log 拆成两个 tab —— 消息日志 / 系统日志
//
// 消息日志（source=event）：incoming 消息进来、plugin 命中、命令派发等业务事件，
// 适合用于"为什么我的 auto_reply 没回复 / 转发到底有没有发出"这类问题排查。
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

type LogTab = "event" | "system";

export function Logs() {
  const [tab, setTab] = useState<LogTab>("event");
  const [accountId, setAccountId] = useState("");
  const [level, setLevel] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [search, setSearch] = useState("");

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">日志中心</h1>
        <p className="text-sm text-muted-foreground">
          消息日志（业务事件）与系统日志（worker / 错误）分开看；默认 5 秒自动刷新
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">过滤</CardTitle>
          <CardDescription>账号 / 级别 / 关键词 / 自动刷新——两 tab 共用</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 lg:items-end">
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
          <TabsTrigger value="event">📨 消息日志</TabsTrigger>
          <TabsTrigger value="system">⚙️ 系统日志</TabsTrigger>
        </TabsList>

        <TabsContent value="event">
          <LogTable
            source="event"
            accountId={accountId}
            level={level}
            search={search}
            autoRefresh={autoRefresh && tab === "event"}
            description="incoming 消息事件、plugin 命中、命令派发——排查「为什么没回复 / 转发出去没」用这里"
          />
        </TabsContent>

        <TabsContent value="system">
          <LogTable
            source="system"
            accountId={accountId}
            level={level}
            search={search}
            autoRefresh={autoRefresh && tab === "system"}
            description="worker 启停、IPC reload、风控状态、技术异常——排查「账号是不是真的活着」用这里"
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
  search,
  autoRefresh,
  description,
}: {
  source: "event" | "system";
  accountId: string;
  level: string;
  search: string;
  autoRefresh: boolean;
  description: string;
}) {
  const filters = {
    source,
    account_id: accountId || undefined,
    level: level || undefined,
    limit: 200,
  };
  const logsQ = useQuery({
    queryKey: ["logs", filters],
    queryFn: () => listRuntimeLogs(filters),
    refetchInterval: autoRefresh ? 5_000 : false,
  });

  // 关键词过滤：纯前端 substring（不区分大小写）。在 200 条窗口内做，零延时。
  // 更高级的 regex / 字段联检后续可加；先满足"找到那条"的核心需求。
  const filtered = useMemo(() => {
    const all = logsQ.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((l) => l.message.toLowerCase().includes(q));
  }, [logsQ.data, search]);

  const totalCount = logsQ.data?.length ?? 0;
  const showCount = filtered.length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          {source === "event" ? "消息日志" : "系统日志"}
        </CardTitle>
        <CardDescription className="flex items-center justify-between gap-2">
          <span>{description}</span>
          {search.trim() ? (
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
                <TableHead>消息</TableHead>
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
                  <TableCell className="font-mono text-xs whitespace-pre-wrap">
                    <HighlightedMessage text={l.message} keyword={search} />
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
                  : " — 没有错误是好事"}
              </>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
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
