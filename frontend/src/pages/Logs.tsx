import { useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  MessageSquareText,
  MousePointerClick,
  Puzzle,
  Search,
  ScrollText,
  ServerCog,
  ShieldCheck,
  TerminalSquare,
  Workflow,
} from "lucide-react";

import { listAccounts } from "@/api/accounts";
import { getFeatureMatrix } from "@/api/features";
import {
  getEventTrace,
  getHealthOverview,
  getPluginRuntimeDetail,
  getSystemSettings,
  getTraceOverview,
  listAuditLogs,
  listCommandTraces,
  listEventActions,
  listEventTraces,
  listPluginRuntimeStatus,
  listRuntimeLogs,
} from "@/api/system";
import type {
  AuditLogItem,
  EventActionItem,
  EventTraceDetail,
  EventSpanItem,
  EventTraceSummary,
  HealthOverview,
  PluginRuntimeDetail,
  PluginRuntimeStatusItem,
  RuntimeLogItem,
  TraceOverview,
} from "@/api/types";
import { PageHeader, PageShell } from "@/components/layout/PageScaffold";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/misc";
import { Select } from "@/components/ui/select";
import { SectionHeader, SignalPill } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDateTime } from "@/lib/utils";

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

type MainTab = "overview" | "events" | "plugins" | "commands" | "actions" | "raw";
type RuntimeSourceTab = "event" | "plugin" | "system";
type RawTab = "runtime" | "audit";

function parseMainTab(value: string | null): MainTab {
  if (value === "events" || value === "plugins" || value === "commands" || value === "actions" || value === "raw") {
    return value;
  }
  return "overview";
}

export function Logs() {
  const [searchParams] = useSearchParams();
  const initialTraceId = searchParams.get("trace_id") || "";
  const [mainTab, setMainTab] = useState<MainTab>(() => {
    const tab = parseMainTab(searchParams.get("tab"));
    return initialTraceId && tab === "overview" ? "events" : tab;
  });
  const [accountId, setAccountId] = useState(() => searchParams.get("account_id") || searchParams.get("aid") || "");
  const [keyword, setKeyword] = useState(() => searchParams.get("keyword") || "");
  const [status, setStatus] = useState(() => searchParams.get("status") || "");
  const [pluginKey, setPluginKey] = useState(() => searchParams.get("plugin_key") || "");
  const [eventType, setEventType] = useState(() => searchParams.get("event_type") || "");
  const [traceId, setTraceId] = useState(() => initialTraceId);
  const [reasonCode, setReasonCode] = useState(() => searchParams.get("reason_code") || searchParams.get("error_code") || "");
  const [chatId, setChatId] = useState(() => searchParams.get("chat_id") || "");
  const [messageId, setMessageId] = useState(() => searchParams.get("message_id") || "");
  const [senderUserId, setSenderUserId] = useState(() => searchParams.get("sender_user_id") || "");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selectedTraceId, setSelectedTraceId] = useState(() => initialTraceId);
  const [selectedPluginKey, setSelectedPluginKey] = useState(() => searchParams.get("plugin_key") || "");
  const traceIdFilter = traceId.trim();
  const reasonCodeFilter = reasonCode.trim();

  const accountsQ = useQuery({ queryKey: ["accounts"], queryFn: listAccounts });
  const matrixQ = useQuery({ queryKey: ["matrix"], queryFn: getFeatureMatrix });
  const settingsQ = useQuery({ queryKey: ["system", "settings"], queryFn: getSystemSettings });
  const timezone = settingsQ.data?.timezone || "";
  const commonTraceQuery = {
    account_id: accountId || undefined,
    keyword: keyword.trim() || undefined,
    status: status || undefined,
    plugin_key: pluginKey || undefined,
    event_type: eventType || undefined,
    trace_id: traceIdFilter || undefined,
    reason_code: reasonCodeFilter || undefined,
    chat_id: chatId.trim() || undefined,
    message_id: messageId.trim() || undefined,
    sender_user_id: senderUserId.trim() || undefined,
    since: localDateTimeToIso(since),
    until: localDateTimeToIso(until),
    limit: 100,
  };

  const overviewQ = useQuery({
    queryKey: ["logs", "trace", "overview", accountId],
    queryFn: () => getTraceOverview({ account_id: accountId || undefined }),
    refetchInterval: autoRefresh && mainTab === "overview" ? 5_000 : false,
  });
  const healthQ = useQuery({
    queryKey: ["system", "health-overview"],
    queryFn: getHealthOverview,
    refetchInterval: autoRefresh && mainTab === "overview" ? 10_000 : false,
  });
  const eventsQ = useQuery({
    queryKey: ["logs", "trace", "events", commonTraceQuery],
    queryFn: () => listEventTraces(commonTraceQuery),
    refetchInterval: autoRefresh && mainTab === "events" ? 5_000 : false,
  });
  const traceDetailQ = useQuery({
    queryKey: ["logs", "trace", "detail", selectedTraceId],
    queryFn: () => getEventTrace(selectedTraceId),
    enabled: Boolean(selectedTraceId),
  });
  const pluginsQ = useQuery({
    queryKey: ["logs", "trace", "plugins", accountId, pluginKey, status],
    queryFn: () =>
      listPluginRuntimeStatus({
        account_id: accountId || undefined,
        plugin_key: pluginKey || undefined,
        status: status || undefined,
        limit: 100,
      }),
    refetchInterval: autoRefresh && mainTab === "plugins" ? 5_000 : false,
  });
  const pluginDetailQ = useQuery({
    queryKey: ["logs", "trace", "plugin-detail", selectedPluginKey, accountId],
    queryFn: () => getPluginRuntimeDetail(selectedPluginKey, { account_id: accountId || undefined }),
    enabled: Boolean(selectedPluginKey),
  });
  const commandsQ = useQuery({
    queryKey: ["logs", "trace", "commands", accountId, keyword, since, until, reasonCodeFilter],
    queryFn: () => listCommandTraces({
      account_id: accountId || undefined,
      keyword: keyword || undefined,
      since: localDateTimeToIso(since),
      until: localDateTimeToIso(until),
      reason_code: reasonCodeFilter || undefined,
      limit: 100,
    }),
    refetchInterval: autoRefresh && mainTab === "commands" ? 5_000 : false,
  });
  const actionsQ = useQuery({
    queryKey: ["logs", "trace", "actions", accountId, pluginKey, status, traceIdFilter, reasonCodeFilter],
    queryFn: () =>
      listEventActions({
        account_id: accountId || undefined,
        plugin_key: pluginKey || undefined,
        status: status || undefined,
        trace_id: traceIdFilter || undefined,
        reason_code: reasonCodeFilter || undefined,
        limit: 100,
      }),
    refetchInterval: autoRefresh && mainTab === "actions" ? 5_000 : false,
  });

  return (
    <PageShell>
      <PageHeader
        title="日志中心"
        description="按 trace_id 追踪消息、插件、命令和发送动作；原始日志用于展开查看底层运行和审计细节。"
        icon={ScrollText}
      />

      <Card>
        <CardHeader>
          <SectionHeader
            title="排查过滤"
            description="先选账号和关键词，再进入消息链路、插件诊断或动作发送深挖。"
            meta={(
              <SignalPill
                tone={autoRefresh ? "success" : "neutral"}
                label="刷新"
                value={autoRefresh ? "自动" : "暂停"}
              />
            )}
          />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5 xl:items-end">
            <div className="space-y-1.5">
              <Label>账号</Label>
              <Select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                <option value="">全部账号</option>
                {accountsQ.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name || a.phone}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>状态</Label>
              <Select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">全部状态</option>
                <option value="ok">ok</option>
                <option value="running">running</option>
                <option value="skipped">skipped</option>
                <option value="warning">warning</option>
                <option value="failed">failed</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>插件</Label>
              <PluginSelect
                value={pluginKey}
                onChange={setPluginKey}
                options={matrixQ.data?.features.map((item) => item.key) ?? []}
              />
            </div>
            <div className="space-y-1.5">
              <Label>事件类型</Label>
              <Select value={eventType} onChange={(e) => setEventType(e.target.value)}>
                <option value="">全部事件</option>
                <option value="message">message</option>
                <option value="command">command</option>
                <option value="callback_query">callback_query</option>
                <option value="inline_query">inline_query</option>
                <option value="chosen_inline_result">chosen_inline_result</option>
                <option value="payment_confirmed">payment_confirmed</option>
                <option value="session_close">session_close</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>关键词</Label>
              <SearchInput value={keyword} onChange={setKeyword} />
            </div>
            <div className="space-y-1.5">
              <Label>Trace ID</Label>
              <div className="flex gap-2">
                <Input
                  className="min-w-0"
                  value={traceId}
                  onChange={(e) => {
                    const nextTraceId = e.target.value.trim();
                    setTraceId(nextTraceId);
                    setSelectedTraceId(nextTraceId);
                  }}
                  placeholder="evt_..."
                />
                {traceIdFilter ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="shrink-0"
                    onClick={() => {
                      setTraceId("");
                      setSelectedTraceId("");
                    }}
                  >
                    清空
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>原因代码</Label>
              <Input
                value={reasonCode}
                onChange={(e) => setReasonCode(e.target.value.trim())}
                placeholder="send_channel_deprecated"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Chat ID</Label>
              <Input value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="-100..." />
            </div>
            <div className="space-y-1.5">
              <Label>Message ID</Label>
              <Input value={messageId} onChange={(e) => setMessageId(e.target.value)} placeholder="消息 ID" />
            </div>
            <div className="space-y-1.5">
              <Label>用户 ID</Label>
              <Input value={senderUserId} onChange={(e) => setSenderUserId(e.target.value)} placeholder="sender user id" />
            </div>
            <div className="space-y-1.5">
              <Label>开始时间</Label>
              <Input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>结束时间</Label>
              <Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>自动刷新</Label>
              <div className="flex h-10 items-center gap-2">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
                <span className="text-sm text-muted-foreground">
                  {autoRefresh ? "5 秒" : "已暂停"}
                </span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Tabs value={mainTab} onValueChange={(v) => setMainTab(v as MainTab)}>
        <TabsList className="flex h-auto flex-wrap justify-start gap-1">
          <TabsTrigger value="overview" className="gap-1.5">
            <Activity className="h-4 w-4" /> 总览
          </TabsTrigger>
          <TabsTrigger value="events" className="gap-1.5">
            <Workflow className="h-4 w-4" /> 消息链路
          </TabsTrigger>
          <TabsTrigger value="plugins" className="gap-1.5">
            <Puzzle className="h-4 w-4" /> 插件诊断
          </TabsTrigger>
          <TabsTrigger value="commands" className="gap-1.5">
            <TerminalSquare className="h-4 w-4" /> 命令链路
          </TabsTrigger>
          <TabsTrigger value="actions" className="gap-1.5">
            <MousePointerClick className="h-4 w-4" /> 动作发送
          </TabsTrigger>
          <TabsTrigger value="raw" className="gap-1.5">
            <ServerCog className="h-4 w-4" /> 原始日志
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewPanel
            loading={overviewQ.isLoading}
            error={overviewQ.error}
            health={healthQ.data}
            healthLoading={healthQ.isLoading}
            healthError={healthQ.error}
            data={overviewQ.data}
            timezone={timezone}
            onTraceSelect={(traceId) => {
              setSelectedTraceId(traceId);
              setMainTab("events");
            }}
          />
        </TabsContent>

        <TabsContent value="events">
          <TraceExplorer
            traces={eventsQ.data ?? []}
            loading={eventsQ.isLoading}
            error={eventsQ.error}
            selectedTraceId={selectedTraceId}
            onSelectTrace={(nextTraceId) => {
              setSelectedTraceId(nextTraceId);
            }}
            detail={traceDetailQ.data}
            detailLoading={traceDetailQ.isLoading}
            detailError={traceDetailQ.error}
            timezone={timezone}
          />
        </TabsContent>

        <TabsContent value="plugins">
          <PluginDiagnostics
            plugins={pluginsQ.data ?? []}
            loading={pluginsQ.isLoading}
            error={pluginsQ.error}
            selectedPluginKey={selectedPluginKey}
            onSelectPlugin={setSelectedPluginKey}
            detail={pluginDetailQ.data}
            detailLoading={pluginDetailQ.isLoading}
            detailError={pluginDetailQ.error}
            timezone={timezone}
            onTraceSelect={(traceId) => {
              setSelectedTraceId(traceId);
              setMainTab("events");
            }}
          />
        </TabsContent>

        <TabsContent value="commands">
          <TraceListCard
            title="命令链路"
            description="管理员命令、sudo 命令和插件命令的调用链。"
            traces={commandsQ.data ?? []}
            loading={commandsQ.isLoading}
            error={commandsQ.error}
            selectedTraceId={selectedTraceId}
            onSelectTrace={(traceId) => {
              setSelectedTraceId(traceId);
              setMainTab("events");
            }}
            timezone={timezone}
          />
        </TabsContent>

        <TabsContent value="actions">
          <ActionsPanel
            actions={actionsQ.data ?? []}
            loading={actionsQ.isLoading}
            error={actionsQ.error}
            timezone={timezone}
            onTraceSelect={(traceId) => {
              setSelectedTraceId(traceId);
              setMainTab("events");
            }}
          />
        </TabsContent>

        <TabsContent value="raw">
          <RawLogsPanel timezone={timezone} />
        </TabsContent>
      </Tabs>
    </PageShell>
  );
}

function OverviewPanel({
  loading,
  error,
  health,
  healthLoading,
  healthError,
  data,
  timezone,
  onTraceSelect,
}: {
  loading: boolean;
  error?: unknown;
  health?: HealthOverview;
  healthLoading: boolean;
  healthError?: unknown;
  data?: TraceOverview;
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  if (loading) return <LoadingCard />;
  if (error) return <ErrorCard title="Trace 总览加载失败" error={error} />;
  const overview = data ?? {
    last_5m_total: 0,
    last_5m_failed: 0,
    last_5m_warning: 0,
    source_channel_counts: {},
    recent_errors: [],
    recent_failed_actions: [],
    recent_plugin_errors: [],
  };
  return (
    <div className="space-y-4">
      <HealthOverviewStrip data={health} traceOverview={overview} loading={healthLoading} error={healthError} />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <MetricCard title="最近 5 分钟事件" value={overview.last_5m_total} icon={Activity} tone="primary" />
        <MetricCard title="失败事件" value={overview.last_5m_failed} icon={AlertTriangle} tone="danger" />
        <MetricCard title="警告事件" value={overview.last_5m_warning} icon={ShieldCheck} tone="warn" />
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <RecentTraceCard title="最近错误链路" traces={overview.recent_errors} timezone={timezone} onTraceSelect={onTraceSelect} />
        <RecentActionCard title="最近失败动作" actions={overview.recent_failed_actions} timezone={timezone} onTraceSelect={onTraceSelect} />
        <RecentPluginCard title="最近插件异常" plugins={overview.recent_plugin_errors} timezone={timezone} />
      </div>
    </div>
  );
}

function MetricCard({
  title,
  value,
  icon: Icon,
  tone,
}: {
  title: string;
  value: number;
  icon: typeof Activity;
  tone: "primary" | "warn" | "danger";
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className={tone === "danger" ? "h-4 w-4 text-destructive" : tone === "warn" ? "h-4 w-4 text-amber-600" : "h-4 w-4 text-primary"} />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

function HealthOverviewStrip({
  data,
  traceOverview,
  loading,
  error,
}: {
  data?: HealthOverview;
  traceOverview?: Pick<TraceOverview, "source_channel_counts">;
  loading: boolean;
  error?: unknown;
}) {
  if (loading) return <InlineLoading />;
  if (error) return <ErrorHint text="系统健康状态加载失败" error={error} />;
  if (!data) return null;
  const workerValue = `${data.workers.runtime_desired_running_alive}/${data.workers.runtime_desired_running}`;
  const workerOk = data.workers.runtime_desired_running === data.workers.runtime_desired_running_alive &&
    data.workers.runtime_failing === 0;
  const sourceCounts = traceOverview?.source_channel_counts ?? {};
  const userbotCount = sourceCounts.userbot ?? 0;
  const interactionBotCount = sourceCounts.interaction_bot ?? 0;
  const externalNoticeCount = sourceCounts.external_payment_notice ?? 0;
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-3 xl:grid-cols-6">
      <HealthTile
        label="DB"
        tone={data.db.ok && data.alembic.ok ? "success" : "danger"}
        value={data.db.ok ? "可用" : "异常"}
        detail={data.alembic.ok ? "迁移一致" : data.alembic.error || "迁移待同步"}
      />
      <HealthTile
        label="Redis"
        tone={data.redis.ok ? "success" : "danger"}
        value={data.redis.ok ? "可用" : "异常"}
        detail={data.redis.error || "队列连接正常"}
      />
      <HealthTile
        label="UserBot"
        tone={workerOk ? "success" : "danger"}
        value={workerValue}
        detail={data.workers.runtime_failing > 0 ? `${data.workers.runtime_failing} 个 worker 失败` : "期望运行 / 已在线"}
      />
      <HealthTile
        label="交互 Bot"
        tone={interactionBotCount > 0 ? "success" : "neutral"}
        value={`${interactionBotCount}`}
        detail={interactionBotCount > 0 ? "近 5 分钟链路活跃" : "近 5 分钟未见交互 Bot trace"}
      />
      <HealthTile
        label="转账通知来源"
        tone={externalNoticeCount > 0 ? "success" : "neutral"}
        value={`${externalNoticeCount}`}
        detail={externalNoticeCount > 0 ? "近 5 分钟有外部通知 trace" : "无近期外部转账通知 trace"}
      />
      <HealthTile
        label="UserBot 事件"
        tone={userbotCount > 0 ? "success" : data.workers.runtime_desired_running_alive > 0 ? "neutral" : "danger"}
        value={`${userbotCount}`}
        detail={userbotCount > 0 ? "近 5 分钟有 userbot trace" : "无近期 trace，按 worker 在线判断"}
      />
    </div>
  );
}

function HealthTile({
  label,
  tone,
  value,
  detail,
}: {
  label: string;
  tone: "success" | "danger" | "neutral";
  value: string;
  detail: string;
}) {
  const className = tone === "success"
    ? "border-emerald-200 bg-emerald-50/60"
    : tone === "danger"
      ? "border-destructive/30 bg-destructive/5"
      : "border-border bg-muted/30";
  return (
    <div className={`rounded-md border p-3 ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {tone === "success" ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" />
        ) : tone === "danger" ? (
          <AlertTriangle className="h-4 w-4 text-destructive" />
        ) : (
          <Clock className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
      <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{detail}</div>
    </div>
  );
}

function TraceExplorer({
  traces,
  loading,
  error,
  selectedTraceId,
  onSelectTrace,
  detail,
  detailLoading,
  detailError,
  timezone,
}: {
  traces: EventTraceSummary[];
  loading: boolean;
  error?: unknown;
  selectedTraceId: string;
  onSelectTrace: (traceId: string) => void;
  detail?: EventTraceDetail;
  detailLoading: boolean;
  detailError?: unknown;
  timezone?: string;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      <TraceListCard
        title="消息链路"
        description="每条 Telegram 事件进入系统后的完整生命周期。"
        traces={traces}
        loading={loading}
        error={error}
        selectedTraceId={selectedTraceId}
        onSelectTrace={onSelectTrace}
        timezone={timezone}
      />
      <TraceDetailCard detail={detail} loading={detailLoading} error={detailError} timezone={timezone} />
    </div>
  );
}

function TraceListCard({
  title,
  description,
  traces,
  loading,
  error,
  selectedTraceId,
  onSelectTrace,
  timezone,
}: {
  title: string;
  description: string;
  traces: EventTraceSummary[];
  loading: boolean;
  error?: unknown;
  selectedTraceId: string;
  onSelectTrace: (traceId: string) => void;
  timezone?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <SectionHeader
          title={title}
          description={description}
          meta={<SignalPill tone="neutral" label="窗口" value={`${traces.length} 条`} />}
        />
      </CardHeader>
      <CardContent className="space-y-2">
        {loading ? (
          <InlineLoading />
        ) : error ? (
          <ErrorHint text="链路列表加载失败" error={error} />
        ) : traces.length ? (
          traces.map((trace) => (
            <button
              key={trace.trace_id}
              type="button"
              onClick={() => onSelectTrace(trace.trace_id)}
              className={`w-full rounded-md border p-3 text-left transition hover:border-primary/50 ${
                selectedTraceId === trace.trace_id ? "border-primary bg-primary/5" : "border-border bg-background"
              }`}
            >
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={trace.status} />
                    <Badge variant="secondary">{trace.event_type}</Badge>
                    {trace.source_channel ? <Badge variant="secondary">{trace.source_channel}</Badge> : null}
                  </div>
                  <div className="mt-2 break-all font-mono text-xs text-muted-foreground">{trace.trace_id}</div>
                </div>
                <div className="shrink-0 text-right text-xs text-muted-foreground">
                  {formatDateTime(trace.started_at, timezone)}
                </div>
              </div>
              <div className="mt-2 line-clamp-2 text-sm">
                {trace.text_preview || trace.sender_name || "无文本摘要"}
              </div>
              <InlineTraceSummary trace={trace} compact />
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                <span>插件 {trace.plugin_count}</span>
                <span>动作 {trace.action_count}</span>
                <span>错误 {trace.error_count}</span>
                {trace.chat_id ? <span>chat {trace.chat_id}</span> : null}
                <NativeRawMetaPill meta={trace.native_raw_meta} />
              </div>
            </button>
          ))
        ) : (
          <EmptyHint text="当前过滤条件下没有链路记录" />
        )}
      </CardContent>
    </Card>
  );
}

function TraceDetailCard({
  detail,
  loading,
  error,
  timezone,
}: {
  detail?: EventTraceDetail;
  loading: boolean;
  error?: unknown;
  timezone?: string;
}) {
  if (!detail && loading) return <LoadingCard />;
  if (error) return <ErrorCard title="链路详情加载失败" error={error} />;
  if (!detail) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>链路详情</CardTitle>
          <CardDescription>选择左侧一条 trace 查看时间线。</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyHint text="尚未选择 trace" />
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <SectionHeader
          title="链路详情"
          description={detail.trace_id}
          meta={(
            <div className="flex flex-wrap justify-end gap-1.5">
              <StatusBadge status={detail.status} />
              <SignalPill tone="neutral" label="耗时" value={detail.duration_ms == null ? "-" : `${detail.duration_ms}ms`} />
            </div>
          )}
        />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground md:grid-cols-4">
          <InfoCell label="事件" value={detail.event_type} />
          <InfoCell label="来源" value={detail.source_channel || "-"} />
          <InfoCell label="会话" value={detail.chat_id ?? "-"} />
          <InfoCell label="消息" value={detail.message_id ?? "-"} />
        </div>
        {detail.text_preview ? <p className="rounded-md bg-muted p-3 text-sm whitespace-pre-wrap">{detail.text_preview}</p> : null}
        <InlineTraceSummary trace={detail} actions={detail.actions} />
        <NativeRawSummary meta={detail.native_raw_meta} />
        <Timeline spans={detail.spans} actions={detail.actions} timezone={timezone} />
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-medium">高级数据</summary>
          <div className="mt-3 space-y-3">
            <JsonBlock title="native_raw_meta" value={detail.native_raw_meta} />
            <JsonBlock title="raw_summary" value={detail.raw_summary} />
            <JsonBlock title="payload_snapshot" value={detail.payload_snapshot} />
            {detail.related_runtime_logs.length ? (
              <JsonBlock title="related_runtime_logs" value={detail.related_runtime_logs} />
            ) : null}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}

function Timeline({
  spans,
  actions,
  timezone,
}: {
  spans: EventSpanItem[];
  actions: EventActionItem[];
  timezone?: string;
}) {
  const items = [
    ...spans.map((span) => ({ kind: "span" as const, ts: span.started_at, span })),
    ...actions.map((action) => ({ kind: "action" as const, ts: action.created_at, action })),
  ].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());

  if (!items.length) return <EmptyHint text="该 trace 暂无 span/action 明细" />;
  return (
    <div className="space-y-2">
      {items.map((item, index) => (
        <div key={`${item.kind}-${index}`} className="rounded-md border p-3">
          {item.kind === "span" ? (
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge status={item.span.status} />
                  <Badge variant="secondary">{item.span.phase}</Badge>
                  {item.span.component ? <Badge variant="secondary">{item.span.component}</Badge> : null}
                </div>
                <span className="text-xs text-muted-foreground">{formatDateTime(item.span.started_at, timezone)}</span>
              </div>
              <p className="text-sm">{item.span.message || reasonDisplay(item.span.reason_code) || "阶段完成"}</p>
              <TraceMeta pluginKey={item.span.plugin_key} entryKey={item.span.entry_key} reasonCode={item.span.reason_code} />
            </div>
          ) : (
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge status={item.action.status} />
                  <Badge variant="secondary">{item.action.action_type}</Badge>
                  {item.action.actual_send_via ? <Badge variant="secondary">{item.action.actual_send_via}</Badge> : null}
                </div>
                <span className="text-xs text-muted-foreground">{formatDateTime(item.action.created_at, timezone)}</span>
              </div>
              <p className="text-sm">{actionDisplayText(item.action)}</p>
              <TraceMeta pluginKey={item.action.plugin_key} reasonCode={item.action.error_code} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function PluginDiagnostics({
  plugins,
  loading,
  error,
  selectedPluginKey,
  onSelectPlugin,
  detail,
  detailLoading,
  detailError,
  timezone,
  onTraceSelect,
}: {
  plugins: PluginRuntimeStatusItem[];
  loading: boolean;
  error?: unknown;
  selectedPluginKey: string;
  onSelectPlugin: (key: string) => void;
  detail?: PluginRuntimeDetail;
  detailLoading: boolean;
  detailError?: unknown;
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <SectionHeader
            title="插件诊断"
            description="加载状态、最近调用和最近错误。"
            meta={<SignalPill tone="neutral" label="插件" value={plugins.length} />}
          />
        </CardHeader>
        <CardContent className="space-y-2">
          {loading ? <InlineLoading /> : error ? <ErrorHint text="插件诊断加载失败" error={error} /> : plugins.length ? plugins.map((plugin) => (
            <button
              key={`${plugin.account_id ?? "global"}-${plugin.plugin_key}`}
              type="button"
              onClick={() => onSelectPlugin(plugin.plugin_key)}
              className={`w-full rounded-md border p-3 text-left transition hover:border-primary/50 ${
                selectedPluginKey === plugin.plugin_key ? "border-primary bg-primary/5" : "border-border"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="break-all font-medium">{plugin.plugin_key}</span>
                <StatusBadge status={plugin.last_invocation_status || plugin.load_status} />
              </div>
              <div className="mt-2 text-xs text-muted-foreground">
                {plugin.last_load_error || plugin.last_trace_id || "暂无异常"}
              </div>
            </button>
          )) : <EmptyHint text="暂无插件运行状态" />}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>插件详情</CardTitle>
          <CardDescription>{selectedPluginKey || "选择插件查看最近调用"}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {detailLoading ? <InlineLoading /> : detailError ? <ErrorHint text="插件详情加载失败" error={detailError} /> : detail ? (
            <>
              <PluginStatusList statuses={detail.statuses} timezone={timezone} onTraceSelect={onTraceSelect} />
              <TraceMiniList traces={detail.recent_traces} timezone={timezone} onTraceSelect={onTraceSelect} />
              <JsonBlock title="最近 span" value={detail.recent_spans} />
            </>
          ) : <EmptyHint text="尚未选择插件" />}
        </CardContent>
      </Card>
    </div>
  );
}

function PluginStatusList({
  statuses,
  timezone,
  onTraceSelect,
}: {
  statuses: PluginRuntimeStatusItem[];
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  if (!statuses.length) return <EmptyHint text="暂无运行状态记录" />;
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">运行状态</div>
      {statuses.map((status) => (
        <div key={`${status.account_id ?? "global"}-${status.plugin_key}`} className="rounded-md border p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge status={status.last_invocation_status || status.load_status} />
              <Badge variant={status.enabled ? "secondary" : "outline"}>{status.enabled ? "已启用" : "未启用"}</Badge>
              {status.account_id ? <Badge variant="outline">账号 {status.account_id}</Badge> : null}
            </div>
            <span className="text-xs text-muted-foreground">{formatDateTime(status.updated_at, timezone)}</span>
          </div>
          <div className="mt-2 grid gap-2 text-xs text-muted-foreground md:grid-cols-3">
            <InfoCell label="加载状态" value={status.load_status || "-"} />
            <InfoCell label="版本" value={status.installed_version || "-"} />
            <InfoCell label="最近调用" value={status.last_invoked_at ? formatDateTime(status.last_invoked_at, timezone) : "-"} />
          </div>
          {status.last_load_error ? (
            <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
              {status.last_load_error}
            </p>
          ) : null}
          {status.last_trace_id ? (
            <Button variant="ghost" size="sm" className="mt-2 px-0" onClick={() => onTraceSelect(status.last_trace_id as string)}>
              查看最近 trace
            </Button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function ActionsPanel({
  actions,
  loading,
  error,
  timezone,
  onTraceSelect,
}: {
  actions: EventActionItem[];
  loading: boolean;
  error?: unknown;
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <SectionHeader
          title="动作发送"
          description="插件请求动作、平台实际通道、Telegram API 或 userbot 执行结果。"
          meta={<SignalPill tone="neutral" label="窗口" value={`${actions.length} 条`} />}
        />
      </CardHeader>
      <CardContent>
        {loading ? <InlineLoading /> : error ? <ErrorHint text="动作记录加载失败" error={error} /> : actions.length ? (
          <div className="space-y-2">
            {actions.map((action) => (
              <div key={action.action_id} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={action.status} />
                    <Badge variant="secondary">{action.action_type}</Badge>
                    {action.plugin_key ? <Badge variant="secondary">{action.plugin_key}</Badge> : null}
                  </div>
                  <span className="text-xs text-muted-foreground">{formatDateTime(action.created_at, timezone)}</span>
                </div>
                <div className="mt-2 grid gap-2 text-xs text-muted-foreground md:grid-cols-5">
                  <InfoCell label="请求通道" value={action.requested_send_via || "-"} />
                  <InfoCell label="实际通道" value={action.actual_send_via || "-"} />
                  <InfoCell label="目标会话" value={action.target_chat_id ?? "-"} />
                  <InfoCell label="消息 ID" value={action.telegram_message_id ?? action.target_message_id ?? "-"} />
                  <InfoCell label="Inline 结果" value={inlineResultCountLabel(action)} />
                </div>
                {action.error_message || action.error_code ? (
                  <p className="mt-2 text-sm text-destructive">{actionErrorLabel(action)}</p>
                ) : null}
                {action.detail ? (
                  <div className="mt-2">
                    <JsonBlock title="动作详情" value={action.detail} />
                  </div>
                ) : null}
                <Button variant="ghost" size="sm" className="mt-2 px-0" onClick={() => onTraceSelect(action.trace_id)}>
                  查看 trace
                </Button>
              </div>
            ))}
          </div>
        ) : <EmptyHint text="当前过滤条件下没有动作记录" />}
      </CardContent>
    </Card>
  );
}

function RawLogsPanel({ timezone }: { timezone?: string }) {
  const [rawTab, setRawTab] = useState<RawTab>("runtime");
  const [runtimeTab, setRuntimeTab] = useState<RuntimeSourceTab>("event");
  const [runtimeLevel, setRuntimeLevel] = useState("");
  const [runtimeSearch, setRuntimeSearch] = useState("");
  const [runtimeAutoRefresh, setRuntimeAutoRefresh] = useState(true);
  const [auditAction, setAuditAction] = useState("");
  const [auditSearch, setAuditSearch] = useState("");

  return (
    <Tabs value={rawTab} onValueChange={(v) => setRawTab(v as RawTab)}>
      <TabsList className="flex h-auto flex-wrap justify-start gap-1">
        <TabsTrigger value="runtime" className="gap-1.5"><Activity className="h-4 w-4" /> 运行日志</TabsTrigger>
        <TabsTrigger value="audit" className="gap-1.5"><ShieldCheck className="h-4 w-4" /> 审计日志</TabsTrigger>
      </TabsList>
      <TabsContent value="runtime" className="space-y-4">
        <Card>
          <CardHeader>
            <SectionHeader
              title="运行事件流"
              description="按时间展示 worker、插件和消息事件的运行记录；先看摘要，需要时展开原始 detail。"
            />
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-4 md:items-end">
              <div className="space-y-1.5">
                <Label>来源</Label>
                <Select value={runtimeTab} onChange={(e) => setRuntimeTab(e.target.value as RuntimeSourceTab)}>
                  <option value="event">消息事件</option>
                  <option value="plugin">插件日志</option>
                  <option value="system">系统日志</option>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>级别</Label>
                <Select value={runtimeLevel} onChange={(e) => setRuntimeLevel(e.target.value)}>
                  <option value="">全部</option>
                  <option value="debug">debug</option>
                  <option value="info">info</option>
                  <option value="warning">warning</option>
                  <option value="error">error</option>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>关键词</Label>
                <SearchInput value={runtimeSearch} onChange={setRuntimeSearch} />
              </div>
              <div className="space-y-1.5">
                <Label>刷新</Label>
                <div className="flex h-10 items-center gap-2">
                  <Switch checked={runtimeAutoRefresh} onCheckedChange={setRuntimeAutoRefresh} />
                  <span className="text-sm text-muted-foreground">{runtimeAutoRefresh ? "5 秒" : "暂停"}</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
        <RuntimeLogTable
          source={runtimeTab}
          level={runtimeLevel}
          search={runtimeSearch}
          autoRefresh={runtimeAutoRefresh}
          timezone={timezone}
        />
      </TabsContent>
      <TabsContent value="audit" className="space-y-4">
        <Card>
          <CardHeader>
            <SectionHeader
              title="面板操作流"
              description="记录 Web 面板里的启停、配置、插件更新和高风险操作，重点看谁在什么时候改了什么。"
            />
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 md:items-end">
              <div className="space-y-1.5">
                <Label>操作类型</Label>
                <Input value={auditAction} onChange={(e) => setAuditAction(e.target.value)} placeholder="account_bot.test" />
              </div>
              <div className="space-y-1.5">
                <Label>关键词</Label>
                <SearchInput value={auditSearch} onChange={setAuditSearch} />
              </div>
            </div>
          </CardContent>
        </Card>
        <AuditLogTable action={auditAction} search={auditSearch} timezone={timezone} />
      </TabsContent>
    </Tabs>
  );
}

function RuntimeLogTable({
  source,
  level,
  search,
  autoRefresh,
  timezone,
}: {
  source: RuntimeSourceTab;
  level: string;
  search: string;
  autoRefresh: boolean;
  timezone?: string;
}) {
  const filters = { source, level: level || undefined, limit: 100 };
  const logsQ = useQuery({
    queryKey: ["logs", "runtime", filters],
    queryFn: () => listRuntimeLogs(filters),
    refetchInterval: autoRefresh ? 5_000 : false,
    refetchIntervalInBackground: false,
  });
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return logsQ.data ?? [];
    return (logsQ.data ?? []).filter((row) => {
      const detail = row.detail ? JSON.stringify(row.detail).toLowerCase() : "";
      return row.message.toLowerCase().includes(q) || detail.includes(q);
    });
  }, [logsQ.data, search]);
  return (
    <Card>
      <CardHeader>
        <SectionHeader
          title="运行日志"
          description="每条记录提取 trace、插件、会话和原因代码，原始字段放在展开详情里。"
          meta={<SignalPill tone="neutral" label="窗口" value="100 条" />}
        />
      </CardHeader>
      <CardContent>
        {logsQ.isLoading ? <InlineLoading /> : logsQ.isError ? (
          <ErrorHint text="运行日志加载失败" error={logsQ.error} />
        ) : filtered.length ? (
          <div className="space-y-3">
            {filtered.map((row: RuntimeLogItem) => (
              <RuntimeLogCard key={row.id} row={row} keyword={search} timezone={timezone} />
            ))}
          </div>
        ) : <EmptyHint text="该分类暂无运行日志" />}
      </CardContent>
    </Card>
  );
}

function AuditLogTable({ action, search, timezone }: { action: string; search: string; timezone?: string }) {
  const logsQ = useQuery({
    queryKey: ["logs", "audit", { action: action || undefined, keyword: search || undefined, limit: 100 }],
    queryFn: () => listAuditLogs({ action: action || undefined, keyword: search || undefined, limit: 100 }),
  });
  const endpointMissing = isAxiosError(logsQ.error) &&
    (logsQ.error.response?.status === 404 || logsQ.error.response?.status === 405);
  return (
    <Card>
      <CardHeader>
        <SectionHeader
          title="审计日志"
          description="把 audit_log 翻译成可扫读的操作记录，展开后仍可查看原始 detail。"
          meta={<SignalPill tone="neutral" label="窗口" value="100 条" />}
        />
      </CardHeader>
      <CardContent>
        {logsQ.isLoading ? <InlineLoading /> : endpointMissing ? (
          <EmptyHint text="当前环境未提供审计日志接口" />
        ) : logsQ.isError ? (
          <ErrorHint text="审计日志加载失败" error={logsQ.error} />
        ) : logsQ.data?.length ? (
          <div className="space-y-3">
            {logsQ.data.map((row: AuditLogItem) => (
              <AuditLogCard key={row.id} row={row} keyword={search} timezone={timezone} />
            ))}
          </div>
        ) : <EmptyHint text="暂无审计日志" />}
      </CardContent>
    </Card>
  );
}

function RuntimeLogCard({
  row,
  keyword,
  timezone,
}: {
  row: RuntimeLogItem;
  keyword: string;
  timezone?: string;
}) {
  const detail = row.detail ?? {};
  const traceId = pickString(detail, ["trace_id", "context.trace_id", "event.trace_id"]);
  const pluginKey = pickString(detail, ["plugin_key", "feature_key", "module_key"]);
  const entryKey = pickString(detail, ["entry_key", "entry", "handler"]);
  const reasonCode = pickString(detail, ["reason_code", "error_code", "guard_level"]);
  const chatId = pickString(detail, ["chat_id", "target_chat_id", "message.chat_id", "event.chat_id"]);
  const messageId = pickString(detail, ["message_id", "target_message_id", "event.message_id"]);
  const actor = pickString(detail, ["display_name", "username", "user_id", "sender_user_id"]);
  const sourceLabel = runtimeSourceLabel(row.source);
  const level = row.level.toLowerCase();

  return (
    <article className="rounded-lg border bg-background p-3 transition-colors hover:border-primary/40 sm:p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={LEVEL_VARIANT[level] ?? "secondary"}>{row.level.toUpperCase()}</Badge>
            <Badge variant="secondary">{sourceLabel}</Badge>
            {pluginKey ? <Badge variant="secondary">{pluginKey}</Badge> : null}
            {entryKey ? <Badge variant="secondary">入口 {entryKey}</Badge> : null}
          </div>
          <p className="mt-2 break-words text-sm font-medium leading-6">
            <HighlightedMessage text={row.message} keyword={keyword} />
          </p>
          {reasonCode ? (
            <p className="mt-1 text-sm text-muted-foreground">
              原因：{reasonDisplay(reasonCode)}
            </p>
          ) : null}
        </div>
        <div className="shrink-0 text-xs text-muted-foreground sm:text-right">
          <div>{formatDateTime(row.created_at, timezone)}</div>
          <div className="mt-1 font-mono">#{row.id}</div>
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <InfoCell label="账号" value={row.account_id ? `#${row.account_id}` : "-"} />
        <InfoCell label="Trace ID" value={traceId || "-"} />
        <InfoCell label="会话" value={chatId || "-"} />
        <InfoCell label="消息" value={messageId || "-"} />
        {actor ? <InfoCell label="触发人" value={actor} /> : null}
      </div>

      <div className="mt-3 space-y-2">
        {traceId ? (
          <Button asChild variant="outline" size="sm">
            <Link to={`/logs?tab=events&trace_id=${encodeURIComponent(traceId)}`}>
              查看消息链路
            </Link>
          </Button>
        ) : null}
        <DetailDisclosure title="展开原始 detail" value={row.detail} />
      </div>
    </article>
  );
}

function AuditLogCard({
  row,
  keyword,
  timezone,
}: {
  row: AuditLogItem;
  keyword: string;
  timezone?: string;
}) {
  const detail = row.detail ?? {};
  const pluginKey = pickString(detail, ["plugin_key", "feature_key", "key"]);
  const accountId = pickString(detail, ["account_id", "aid"]);
  const status = pickString(detail, ["status", "state", "result"]);
  const version = pickString(detail, ["version", "new_version", "target_version"]);
  const summary = auditDetailSummary(detail, keyword);

  return (
    <article className="rounded-lg border bg-background p-3 transition-colors hover:border-primary/40 sm:p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="secondary">审计</Badge>
            {status ? <StatusBadge status={status} /> : null}
            {pluginKey ? <Badge variant="secondary">{pluginKey}</Badge> : null}
          </div>
          <p className="mt-2 break-words text-sm font-medium leading-6">
            <HighlightedMessage text={auditActionTitle(row.action)} keyword={keyword} />
          </p>
          <p className="mt-1 break-words text-sm text-muted-foreground">
            {summary || "该操作没有额外 detail。"}
          </p>
        </div>
        <div className="shrink-0 text-xs text-muted-foreground sm:text-right">
          <div>{formatDateTime(row.ts, timezone)}</div>
          <div className="mt-1 font-mono">#{row.id}</div>
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <InfoCell label="操作人" value={row.user_id ? `用户 #${row.user_id}` : "-"} />
        <InfoCell label="Action" value={row.action} />
        <InfoCell label="目标" value={row.target || "-"} />
        <InfoCell label="账号" value={accountId ? `#${accountId}` : "-"} />
        {version ? <InfoCell label="版本" value={version} /> : null}
      </div>

      <div className="mt-3">
        <DetailDisclosure title="展开审计 detail" value={row.detail} />
      </div>
    </article>
  );
}

function DetailDisclosure({ title, value }: { title: string; value?: Record<string, unknown> | null }) {
  if (!value || Object.keys(value).length === 0) return null;
  return (
    <details className="group rounded-md border border-border/70 bg-muted/20 px-3 py-2">
      <summary className="cursor-pointer select-none text-xs font-medium text-muted-foreground group-open:text-foreground">
        {title}
      </summary>
      <div className="mt-3">
        <JsonBlock title="原始字段" value={value} />
      </div>
    </details>
  );
}

function runtimeSourceLabel(source?: string | null): string {
  const value = (source || "").toLowerCase();
  if (value === "event" || value === "interaction" || value === "account_bot") return "消息事件";
  if (value === "plugin" || value === "builtin") return "插件日志";
  if (value === "system" || value === "worker") return "系统运行";
  return source || "运行日志";
}

function auditActionTitle(action: string): string {
  const labels: Record<string, string> = {
    "account_bot.test": "测试账号 Bot",
    "account_bot.save": "保存账号 Bot 配置",
    "interaction.rule.save": "保存交互规则",
    "plugin.install": "安装插件",
    "plugin.update": "更新插件",
    "plugin.remove": "移除插件",
    "plugin.enable": "启用插件",
    "plugin.disable": "停用插件",
    "settings.update": "更新系统设置",
  };
  if (labels[action]) return labels[action];
  return action.split(".").filter(Boolean).join(" / ") || action;
}

function auditDetailSummary(detail: Record<string, unknown>, keyword: string): ReactNode {
  const preferred = [
    "message",
    "error",
    "reason",
    "plugin_key",
    "feature_key",
    "repo_url",
    "branch",
    "account_id",
    "enabled",
    "status",
  ];
  const picked = preferred
    .filter((key) => key in detail)
    .map((key) => `${key}: ${stringifyShort(detail[key])}`);
  const fallback = Object.entries(detail)
    .filter(([key]) => !preferred.includes(key))
    .slice(0, Math.max(0, 4 - picked.length))
    .map(([key, value]) => `${key}: ${stringifyShort(value)}`);
  const summary = [...picked, ...fallback].slice(0, 4).join(" · ");
  if (!summary) return null;
  return <HighlightedMessage text={summary} keyword={keyword} />;
}

function InlineTraceSummary({
  trace,
  actions,
  compact = false,
}: {
  trace: EventTraceSummary | EventTraceDetail;
  actions?: EventActionItem[];
  compact?: boolean;
}) {
  const query = trace.inline_query || pickString(trace, ["payload_snapshot.inline_query.query", "raw_summary.inline_query.query", "payload_snapshot.query"]);
  const chosen = trace.chosen_inline_result_id || pickString(trace, ["payload_snapshot.chosen_inline_result.result_id", "raw_summary.chosen_inline_result.result_id"]);
  const choiceQuery = trace.chosen_inline_query || pickString(trace, ["payload_snapshot.chosen_inline_result.query", "raw_summary.chosen_inline_result.query"]);
  const inlineActions = (actions ?? []).filter((action) => action.action_type === "answer_inline_query");
  const resultCount = inlineActions.find((action) => action.inline_result_count != null)?.inline_result_count;
  const failedAction = inlineActions.find((action) => action.status === "failed" || action.error_code || action.error_message);

  if (!query && !chosen && resultCount == null && !failedAction && trace.event_type !== "inline_query" && trace.event_type !== "chosen_inline_result") {
    return null;
  }

  if (compact) {
    return (
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs text-muted-foreground">
        {query ? <span className="max-w-full break-all">Inline query: {query}</span> : null}
        {chosen ? <span className="max-w-full break-all">Chosen: {chosen}</span> : null}
      </div>
    );
  }

  return (
    <div className="rounded-md border border-primary/20 bg-primary/5 p-3 text-xs">
      <div className="mb-2 font-medium text-foreground">Inline 摘要</div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <InfoCell label="inline_query" value={query || "-"} />
        <InfoCell label="chosen_result" value={chosen || "-"} />
        <InfoCell label="chosen_query" value={choiceQuery || "-"} />
        <InfoCell label="answer 结果数" value={resultCount == null ? "-" : resultCount} />
      </div>
      {failedAction ? (
        <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-destructive">
          失败原因：{actionErrorLabel(failedAction)}
        </div>
      ) : null}
    </div>
  );
}

function NativeRawSummary({ meta }: { meta?: EventTraceSummary["native_raw_meta"] }) {
  if (!meta) return null;
  return (
    <div className="rounded-md border bg-muted/30 p-3 text-xs">
      <div className="mb-2 font-medium text-foreground">native_raw_meta 摘要</div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <InfoCell label="声明状态" value={meta.enabled ? "已允许" : "未允许"} />
        <InfoCell label="驱动" value={meta.driver || meta.source || "-"} />
        <InfoCell label="对象" value={meta.object || "-"} />
        <InfoCell label="持久化" value={meta.stored_in_trace ? `已保存 ${meta.size_bytes ?? "-"} bytes` : "未保存"} />
      </div>
      {meta.reason_code ? (
        <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-amber-900">
          {reasonDisplay(meta.reason_code)}
        </div>
      ) : null}
    </div>
  );
}

function NativeRawMetaPill({ meta }: { meta?: EventTraceSummary["native_raw_meta"] }) {
  if (!meta) return null;
  const label = meta.enabled ? "native_raw 已声明" : "native_raw 未授权";
  return (
    <span className={meta.enabled ? "text-amber-700" : "text-muted-foreground"}>
      {label}{meta.stored_in_trace ? " · 已保存" : ""}
    </span>
  );
}

function RecentTraceCard({
  title,
  traces,
  timezone,
  onTraceSelect,
}: {
  title: string;
  traces: EventTraceSummary[];
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {traces.length ? traces.map((trace) => (
          <button key={trace.trace_id} type="button" className="w-full rounded-md border p-3 text-left hover:border-primary/50" onClick={() => onTraceSelect(trace.trace_id)}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <StatusBadge status={trace.status} />
              <span className="text-xs text-muted-foreground">{formatDateTime(trace.started_at, timezone)}</span>
            </div>
            <div className="mt-2 break-all font-mono text-xs text-muted-foreground">{trace.trace_id}</div>
            <p className="mt-1 line-clamp-2 text-sm">{trace.text_preview || trace.event_type}</p>
          </button>
        )) : <EmptyHint text="暂无记录" />}
      </CardContent>
    </Card>
  );
}

function TraceMiniList({
  traces,
  timezone,
  onTraceSelect,
}: {
  traces: EventTraceSummary[];
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  if (!traces.length) return <EmptyHint text="暂无最近调用" />;
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">最近调用</div>
      {traces.map((trace) => (
        <button
          key={trace.trace_id}
          type="button"
          className="w-full rounded-md border p-3 text-left hover:border-primary/50"
          onClick={() => onTraceSelect(trace.trace_id)}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <StatusBadge status={trace.status} />
            <span className="text-xs text-muted-foreground">{formatDateTime(trace.started_at, timezone)}</span>
          </div>
          <div className="mt-2 break-all font-mono text-xs text-muted-foreground">{trace.trace_id}</div>
          <p className="mt-1 line-clamp-2 text-sm">{trace.text_preview || trace.event_type}</p>
        </button>
      ))}
    </div>
  );
}

function RecentActionCard({
  title,
  actions,
  timezone,
  onTraceSelect,
}: {
  title: string;
  actions: EventActionItem[];
  timezone?: string;
  onTraceSelect: (traceId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {actions.length ? actions.map((action) => (
          <button key={action.action_id} type="button" className="w-full rounded-md border p-3 text-left hover:border-primary/50" onClick={() => onTraceSelect(action.trace_id)}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <StatusBadge status={action.status} />
              <span className="text-xs text-muted-foreground">{formatDateTime(action.created_at, timezone)}</span>
            </div>
            <p className="mt-2 text-sm">{actionErrorLabel(action)}</p>
            <div className="mt-1 break-all font-mono text-xs text-muted-foreground">{action.plugin_key || action.trace_id}</div>
          </button>
        )) : <EmptyHint text="暂无记录" />}
      </CardContent>
    </Card>
  );
}

function RecentPluginCard({ title, plugins, timezone }: { title: string; plugins: PluginRuntimeStatusItem[]; timezone?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {plugins.length ? plugins.map((plugin) => (
          <div key={`${plugin.account_id ?? "global"}-${plugin.plugin_key}`} className="rounded-md border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="break-all font-medium">{plugin.plugin_key}</span>
              <StatusBadge status={plugin.load_status} />
            </div>
            <p className="mt-2 text-sm text-destructive">{plugin.last_load_error || "加载异常"}</p>
            <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(plugin.updated_at, timezone)}</p>
          </div>
        )) : <EmptyHint text="暂无记录" />}
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status?: string | null }) {
  const value = (status || "unknown").toLowerCase();
  const variant = value === "ok" || value === "success" || value === "active"
    ? "success"
    : value === "warning" || value === "warn" || value === "skipped"
      ? "warn"
      : value === "failed" || value === "error"
        ? "destructive"
        : "secondary";
  return <Badge variant={variant}>{status || "unknown"}</Badge>;
}

function TraceMeta({ pluginKey, entryKey, reasonCode }: { pluginKey?: string | null; entryKey?: string | null; reasonCode?: string | null }) {
  if (!pluginKey && !entryKey && !reasonCode) return null;
  return (
    <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
      {pluginKey ? <span>插件 {pluginKey}</span> : null}
      {entryKey ? <span>入口 {entryKey}</span> : null}
      {reasonCode ? <span>{reasonDisplay(reasonCode)}</span> : null}
    </div>
  );
}

function InfoCell({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md bg-muted px-2 py-1.5">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="break-all font-mono text-xs text-foreground">{String(value ?? "-")}</div>
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  if (value == null) return null;
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-muted-foreground">{title}</div>
      <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs leading-relaxed whitespace-pre-wrap break-all">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function PluginSelect({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  const keys = Array.from(new Set([...options, ...(value ? [value] : [])])).sort();
  return (
    <div className="space-y-1.5">
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">全部插件</option>
        {keys.map((key) => (
          <option key={key} value={key}>{key}</option>
        ))}
      </Select>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value.trim())}
        placeholder="或输入远程插件 key"
      />
    </div>
  );
}

function SearchInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input className="pl-9" value={value} onChange={(e) => onChange(e.target.value)} placeholder="搜索关键词" />
    </div>
  );
}

function LogDetail({ detail, keyword }: { detail: Record<string, unknown>; keyword: string }) {
  const entries = Object.entries(detail).slice(0, 8);
  if (!entries.length) return null;
  return (
    <div className="mt-2 grid gap-1 rounded-md bg-muted p-2 text-[11px] text-muted-foreground">
      {entries.map(([key, value]) => (
        <div key={key} className="break-all">
          <span className="font-medium text-foreground">{key}</span>:{" "}
          <HighlightedMessage text={stringifyShort(value)} keyword={keyword} />
        </div>
      ))}
    </div>
  );
}

function HighlightedMessage({ text, keyword }: { text: string; keyword: string }) {
  const q = keyword.trim();
  if (!q) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded bg-yellow-200 px-0.5 text-yellow-950">{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  );
}

function LoadingCard() {
  return (
    <Card>
      <CardContent className="flex h-32 items-center justify-center">
        <Spinner className="text-primary" />
      </CardContent>
    </Card>
  );
}

function ErrorCard({ title, error }: { title: string; error: unknown }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base text-destructive">{title}</CardTitle>
        <CardDescription>{errorMessage(error)}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Trace API 没有返回有效数据，请先检查后端日志、鉴权状态和服务健康。
        </p>
      </CardContent>
    </Card>
  );
}

function InlineLoading() {
  return (
    <div className="flex h-20 items-center justify-center">
      <Spinner className="text-primary" />
    </div>
  );
}

function ErrorHint({ text, error }: { text: string; error: unknown }) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
      <div className="font-medium text-destructive">{text}</div>
      <div className="mt-1 break-words text-muted-foreground">{errorMessage(error)}</div>
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return <p className="py-8 text-center text-sm text-muted-foreground">{text}</p>;
}

function errorMessage(error: unknown): string {
  if (isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message?: unknown }).message || error.message);
    }
    return error.message || `HTTP ${error.response?.status ?? "请求失败"}`;
  }
  if (error instanceof Error) return error.message;
  return String(error || "未知错误");
}

function stringifyShort(value: unknown): string {
  if (value == null) return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function actionDisplayText(action: EventActionItem): string {
  if (action.error_message || action.error_code) {
    return actionErrorLabel(action);
  }
  if (action.action_type === "answer_inline_query") {
    return `Inline 回答已记录，结果 ${inlineResultCountLabel(action)}`;
  }
  return "动作已记录";
}

function actionErrorLabel(action: EventActionItem): string {
  const label = reasonDisplay(action.error_code);
  if (label && action.error_message && action.error_message !== reasonLabel(action.error_code)) {
    return `${label}：${action.error_message}`;
  }
  return label || action.error_message || "动作失败";
}

function inlineResultCountLabel(action: EventActionItem): string {
  if (action.action_type !== "answer_inline_query" && action.inline_result_count == null) return "-";
  return action.inline_result_count == null ? "未知" : `${action.inline_result_count}`;
}

function pickString(source: unknown, paths: string[]): string | null {
  for (const path of paths) {
    const value = path.split(".").reduce<unknown>((current, key) => {
      if (!current || typeof current !== "object") return undefined;
      return (current as Record<string, unknown>)[key];
    }, source);
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number") return String(value);
  }
  return null;
}

function reasonLabel(code?: string | null): string {
  const labels: Record<string, string> = {
    action_failed: "动作执行失败",
    account_not_matched: "账号不匹配",
    account_bot_user_unauthorized: "账号 Bot 用户未授权",
    bot_token_missing: "Bot token 缺失",
    bot_not_configured: "交互 Bot 未配置",
    bot_self_message: "忽略交互 Bot 自身消息",
    callback_query: "按钮回调",
    command_matched: "命令已命中",
    command_not_matched: "未命中命令",
    command_unauthorized: "命令权限不足",
    contract_failed: "契约失败",
    contract_warning: "契约告警",
    callback_query_id_missing: "按钮回调 ID 缺失",
    empty_message_text: "消息文本为空",
    entry_key_missing: "入口缺失",
    event_bus_delivery_disabled: "Event Bus 投递已关闭",
    event_type_not_subscribed: "事件类型未订阅",
    filter_not_matched: "过滤条件未命中",
    handler_error: "处理器异常",
    inline_disabled: "Inline 已关闭",
    inline_query_answer_failed: "Inline 回答失败",
    inline_query_id_missing: "Inline Query ID 缺失",
    manifest_invalid: "Manifest 不合法",
    matched: "已命中",
    media_payload_empty: "媒体内容为空",
    media_payload_invalid: "媒体内容格式无效",
    media_payload_missing: "媒体内容缺失",
    native_raw_not_allowed: "未声明原生数据能力",
    native_raw_skipped: "原生数据未下发",
    permission_denied: "权限不足",
    plugin_disabled: "插件未启用",
    plugin_load_failed: "插件加载失败",
    plugin_not_installed: "插件未安装",
    plugin_runtime_error: "插件运行异常",
    rate_limited: "触发频控",
    scope_not_matched: "范围不匹配",
    send_channel_deprecated: "发送通道已废弃",
    session_control_action: "会话控制动作",
    settlement_requires_userbot: "结算需要 UserBot",
    session_expired: "会话已过期",
    session_not_found: "会话不存在",
    source_not_subscribed: "来源未订阅",
    subscription_load_failed: "订阅加载失败",
    subscription_not_matched: "订阅未命中",
    target_message_id_missing: "目标消息 ID 缺失",
    telegram_api_error: "Telegram API 错误",
    trace_write_failed: "Trace 写入降级",
    unsupported_send_via: "发送通道不支持",
    userbot_offline: "UserBot 离线",
  };
  return code ? labels[code] || code : "";
}

function reasonDisplay(code?: string | null): string {
  if (!code) return "";
  const label = reasonLabel(code);
  return label && label !== code ? `${label} (${code})` : code;
}

function localDateTimeToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}
