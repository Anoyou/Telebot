// Dashboard：轻量概览工作台。账号列表和系统状态都从页面正文改为锚定浮层。
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  Boxes,
  Cpu,
  LayoutDashboard,
  Plus,
  Sparkles,
  type LucideIcon,
  Users,
} from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AccountSummaryCard } from "@/components/AccountSummaryCard";
import { Spinner } from "@/components/ui/misc";
import { listAccounts } from "@/api/accounts";
import { listLLMProviders } from "@/api/commands";
import { getResourceDashboard } from "@/api/system";
import type { ResourceDashboard } from "@/api/types";

export function Dashboard() {
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const accountsOpen = searchParams.get("accounts") === "1";
  const guideActive = searchParams.get("guide") === "1";
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: listAccounts,
  });
  const providersQ = useQuery({
    queryKey: ["llm-providers"],
    queryFn: listLLMProviders,
  });
  const resourceQ = useQuery({
    queryKey: ["system", "resource-dashboard"],
    queryFn: getResourceDashboard,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const setGuideActive = (active: boolean) => {
    const next = new URLSearchParams(searchParams);
    if (active) next.set("guide", "1");
    else next.delete("guide");
    setSearchParams(next);
  };

  const accounts = accountsQ.data ?? [];
  const providers = providersQ.data ?? [];
  const activeAccounts = accounts.filter((account) => account.status === "active").length;
  const readyProviders = providers.filter(
    (provider) => provider.has_api_key || provider.provider === "ollama",
  ).length;
  const workerValue = accountsQ.isLoading ? "-" : `${activeAccounts}/${accounts.length}`;
  const providerValue = providersQ.isLoading ? "-" : `${readyProviders}/${providers.length}`;
  const logValue = resourceQ.data
    ? `${resourceQ.data.logs.last_5m_total}`
    : resourceQ.isLoading
      ? "-"
      : "0";

  return (
    <div className="space-y-5 pb-24 md:space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="inline-flex items-center gap-2 text-3xl font-bold tracking-tight">
            <LayoutDashboard className="h-6 w-6 text-primary" />
            概览
          </h1>
          <p className="mt-1 text-base text-muted-foreground">
            集中查看 TelePilot 的账号、模块、AI 和资源运行情况。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            className={guideActive ? "siri-glow" : undefined}
            onClick={() => setGuideActive(!guideActive)}
          >
            <Sparkles className="mr-2 h-4 w-4 text-primary" />
            新手指引
          </Button>
          <Button asChild>
            <Link to="/accounts/new">
              <Plus className="mr-2 h-4 w-4" />
              新增账号
            </Link>
          </Button>
        </div>
      </div>

      {guideActive ? (
        <GuidePanel
          onAddAccount={() => nav("/accounts/new?guide=1")}
          onGoSettings={() => nav("/settings?tab=platform&guide=1")}
          onGoPlugins={() => nav("/plugins?guide=1")}
          onDone={() => setGuideActive(false)}
        />
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <AccountWorkerTile
          value={workerValue}
          accounts={accounts}
          isLoading={accountsQ.isLoading}
          open={accountsOpen}
          onOpenChange={(open) => {
            const next = new URLSearchParams(searchParams);
            if (open) next.set("accounts", "1");
            else next.delete("accounts");
            setSearchParams(next, { replace: true });
          }}
        />
        <OverviewTile
          icon={Sparkles}
          title="AI"
          value={providerValue}
          description="可调用模型 / 已配置模型"
          to="/ai?tab=providers"
        />
        <OverviewTile
          icon={Boxes}
          title="模块中心"
          value="指令与插件"
          description="管理指令和自动化"
          to="/plugins"
        />
        <OverviewTile
          icon={Activity}
          title="5 分钟日志"
          value={logValue}
          description={`错误 ${resourceQ.data?.logs.last_5m_error ?? 0} / 警告 ${resourceQ.data?.logs.last_5m_warn ?? 0}`}
          to="/logs"
        />
      </div>

      <div>
        <ResourceUsageCard
          data={resourceQ.data}
          isLoading={resourceQ.isLoading}
          error={resourceQ.error}
        />
      </div>
    </div>
  );
}

function AccountWorkerTile({
  value,
  accounts,
  isLoading,
  open,
  onOpenChange,
}: {
  value: string;
  accounts: Awaited<ReturnType<typeof listAccounts>>;
  isLoading: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const compactAccounts = useCompactOverlay();
  const singleAccount = accounts.length <= 1;

  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger asChild>
        <button type="button" className="block min-w-0 text-left">
          <TileCard
            icon={Users}
            title="账号 Worker"
            value={value}
            description="运行中 / 总账号，点击查看全部账号"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="center"
        collisionPadding={12}
        sideOffset={8}
        className={
          singleAccount
            ? "max-h-[min(72vh,42rem)] w-[min(30rem,calc(100vw-1rem))] p-0 data-[state=open]:animate-none sm:w-[min(30rem,calc(100vw-2rem))]"
            : "max-h-[min(72vh,42rem)] w-[min(54rem,calc(100vw-1rem))] p-0 data-[state=open]:animate-none sm:w-[min(54rem,calc(100vw-2rem))]"
        }
        style={{ overflowY: "auto" }}
      >
        <div className="border-b px-4 py-3">
          <div className="text-base font-semibold">账号 Worker</div>
          <div className="mt-1 text-sm text-muted-foreground">
            所有 Telegram 账号的运行状态、出网信息和快捷入口。
          </div>
        </div>
        <div className="p-4">
          {isLoading ? (
            <div className="flex h-36 items-center justify-center">
              <Spinner className="text-primary" />
            </div>
          ) : accounts.length === 0 ? (
            <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
              尚未绑定账号，请从概览顶部新增账号。
            </div>
          ) : compactAccounts ? (
            <div className="space-y-2">
              {accounts.map((account) => (
                <CompactAccountRow key={account.id} account={account} />
              ))}
            </div>
          ) : (
            <div className={singleAccount ? "grid max-w-[28rem] gap-3" : "grid gap-3 lg:grid-cols-2"}>
              {accounts.map((account) => (
                <AccountSummaryCard key={account.id} account={account} />
              ))}
            </div>
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function CompactAccountRow({
  account,
}: {
  account: Awaited<ReturnType<typeof listAccounts>>[number];
}) {
  const title = account.display_name || `#${account.id}`;
  return (
    <Link
      to={`/accounts/${account.id}`}
      className="flex min-w-0 items-center justify-between gap-3 rounded-xl border border-border/70 bg-muted/35 px-3 py-2.5 text-sm transition hover:bg-accent"
    >
      <div className="min-w-0">
        <div className="truncate font-medium">{title}</div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {account.tg_username ? `@${account.tg_username}` : account.phone}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="text-xs font-semibold text-foreground">
          {accountStatusLabel(account.status)}
        </div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">
          {account.enabled_features} 项
        </div>
      </div>
    </Link>
  );
}

function accountStatusLabel(status: string) {
  const map: Record<string, string> = {
    active: "运行中",
    paused: "已暂停",
    floodwait: "限流",
    dead: "停用",
    login_required: "需重登",
  };
  return map[status] ?? status;
}

function OverviewTile({
  icon: Icon,
  title,
  value,
  description,
  to,
  onClick,
  asButton = false,
}: {
  icon: LucideIcon;
  title: string;
  value: string;
  description: string;
  to?: string;
  onClick?: () => void;
  asButton?: boolean;
}) {
  const content = (
    <TileCard icon={Icon} title={title} value={value} description={description} />
  );

  if (asButton) {
    return (
      <button type="button" className="block min-w-0 text-left" onClick={onClick}>
        {content}
      </button>
    );
  }

  return (
    <Link to={to ?? "/"} className="group block min-w-0">
      {content}
    </Link>
  );
}

function useCompactOverlay() {
  const [compact, setCompact] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 640px)").matches;
  });

  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  return compact;
}

function GuidePanel({
  onAddAccount,
  onGoSettings,
  onGoPlugins,
  onDone,
}: {
  onAddAccount: () => void;
  onGoSettings: () => void;
  onGoPlugins: () => void;
  onDone: () => void;
}) {
  return (
    <Card className="siri-glow-soft">
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="inline-flex items-center gap-2 text-xl">
            <Sparkles className="h-5 w-5 text-primary" />
            新手指引
          </CardTitle>
          <CardDescription className="mt-1">
            只保留大内容指引：从账号接入、前缀通知到模块启用，一次看清。
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={onDone}>
          收起
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3 lg:grid-cols-3">
        <GuideStep no="1" title="添加并启用账号" onAction={onAddAccount} action="新增账号">
          先新增 Telegram 账号，系统会为它启动独立 worker。
        </GuideStep>
        <GuideStep no="2" title="设置前缀与通知" onAction={onGoSettings} action="去设置">
          确认触发前缀，并把重要事件推送到合适的通知渠道。
        </GuideStep>
        <GuideStep no="3" title="启用模块与指令" onAction={onGoPlugins} action="打开模块">
          在模块中心启用指令、插件和自动化能力，再按账号配置。
        </GuideStep>
      </CardContent>
    </Card>
  );
}

function GuideStep({
  no,
  title,
  children,
  action,
  onAction,
}: {
  no: string;
  title: string;
  children: ReactNode;
  action: string;
  onAction: () => void;
}) {
  return (
    <div className="rounded-xl border border-border/70 bg-muted/35 p-4">
      <div className="flex items-center gap-2 font-semibold">
        <span className="grid h-8 w-8 place-items-center rounded-full border bg-card text-xs">{no}</span>
        {title}
      </div>
      <p className="mt-3 min-h-10 text-sm leading-6 text-muted-foreground">{children}</p>
      <Button size="sm" className="mt-4" onClick={onAction}>
        {action}
        <ArrowRight className="ml-1 h-4 w-4" />
      </Button>
    </div>
  );
}

function TileCard({
  icon: Icon,
  title,
  value,
  description,
}: {
  icon: LucideIcon;
  title: string;
  value: string;
  description: string;
}) {
  return (
    <Card className="h-full transition duration-200 hover:-translate-y-0.5 hover:shadow-[0_1px_2px_hsl(220_20%_20%/0.04),0_22px_54px_hsl(220_20%_20%/0.09)]">
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="min-w-0">
          <CardTitle className="inline-flex max-w-full items-center gap-2 truncate">
            <Icon className="h-4 w-4 shrink-0 text-primary" />
            <span className="truncate">{title}</span>
          </CardTitle>
          <CardDescription className="mt-3 text-sm leading-5">
            {description}
          </CardDescription>
        </div>
      </CardHeader>
      <CardFooter className="pt-0">
        <div className="truncate text-2xl font-bold tracking-tight">{value}</div>
      </CardFooter>
    </Card>
  );
}

function ResourceUsageCard({
  data,
  isLoading,
  error,
}: {
  data: ResourceDashboard | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="inline-flex items-center gap-2">
              <Activity className="h-4 w-4 text-primary" />
              资源占用
            </CardTitle>
            <CardDescription className="mt-1">
              上方是 TelePilot 应用占用；下方是宿主机/服务器整体资源。
            </CardDescription>
          </div>
          {data?.host.sampled_at ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              {new Date(data.host.sampled_at * 1000).toLocaleTimeString()}
            </span>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Spinner className="text-primary" />
          </div>
        ) : error || !data ? (
          <div className="rounded-xl border px-3 py-2 text-xs alert-danger">
            读取资源占用失败：{(error as Error)?.message || "未知错误"}
          </div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <MetricCard
                icon={Cpu}
                label="应用总 CPU"
                value={percent(data.project_total.cpu_percent)}
                hint={processScopeHint(data)}
              />
              <ProcessMemoryCard data={data} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Metric label="服务器 CPU" value={percent(data.host.cpu_percent)} />
              <Metric
                label="服务器内存"
                value={hostMemoryLabel(
                  data.host.memory_used_percent,
                  data.host.memory_total_mb,
                )}
              />
              <Metric label="服务器磁盘使用" value={percent(data.host.disk_used_percent)} />
              <Metric label="服务器磁盘剩余" value={gb(data.host.disk_free_gb)} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-border/70 bg-muted/35 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
        <Icon className="h-4 w-4 text-primary" />
      </div>
      <p className="text-2xl font-bold tracking-tight">{value}</p>
      <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{hint}</p>
    </div>
  );
}

function ProcessMemoryCard({ data }: { data: ResourceDashboard }) {
  const memoryMb = processMemoryMb(data.project_total);
  const totalMb = saneMemoryTotalMb(data.host.memory_total_mb);
  const rows = buildProcessMemoryRows(data);
  const compactOverlay = useCompactOverlay();

  return (
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <button type="button" className="block min-w-0 text-left">
          <MetricCard
            icon={Activity}
            label="应用总内存"
            value={formatMb(memoryMb)}
            hint={projectMemoryHint(memoryMb, totalMb, data)}
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={compactOverlay ? "center" : "end"}
        collisionPadding={12}
        sideOffset={8}
        className="max-h-[min(72vh,34rem)] w-[min(34rem,calc(100vw-1rem))] p-0 data-[state=open]:animate-none sm:w-[min(34rem,calc(100vw-2rem))]"
        style={{ overflowY: "auto" }}
      >
        <div className="border-b px-4 py-3">
          <div className="text-base font-semibold">应用内存明细</div>
          <div className="mt-1 text-sm text-muted-foreground">
            主进程和 worker 优先显示 USS；数据库、Redis、前端来自 Docker stats。
          </div>
        </div>
        <div className="space-y-2 p-4">
          {data.container_probe_error ? (
            <div className="rounded-xl border px-3 py-2 text-xs text-muted-foreground">
              {data.container_probe_error}
            </div>
          ) : null}
          {rows.map((row) => (
            <div
              key={row.key}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-muted/35 p-3"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{row.label}</div>
                <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                  {row.meta} · CPU {percent(row.cpu)}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-sm font-semibold">{formatMb(row.memoryMb)}</div>
                <div className="text-[11px] text-muted-foreground">{row.basis}</div>
              </div>
            </div>
          ))}
          {rows.length === 0 ? (
            <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
              暂无可展示的进程明细。
            </div>
          ) : null}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function percent(v: number | null | undefined): string {
  return typeof v === "number" ? `${v.toFixed(1)}%` : "-";
}

function formatMb(v: number | null | undefined): string {
  if (typeof v !== "number") return "-";
  if (v >= 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(2)} TB`;
  if (v >= 1024) return `${(v / 1024).toFixed(2)} GB`;
  return `${v.toFixed(1)} MB`;
}

function gb(v: number | null | undefined): string {
  return typeof v === "number" ? `${v.toFixed(2)} GB` : "-";
}

function processMemoryMb(resource: { uss_mb?: number | null; rss_mb?: number | null }) {
  return typeof resource.uss_mb === "number" ? resource.uss_mb : resource.rss_mb;
}

function processMemoryBasis(resource: { uss_mb?: number | null; rss_mb?: number | null }) {
  return typeof resource.uss_mb === "number" ? "USS" : "RSS";
}

type ProcessMemoryRow = {
  key: string;
  label: string;
  meta: string;
  cpu?: number | null;
  memoryMb?: number | null;
  basis: string;
};

type ContainerResource = ResourceDashboard["containers"][number];

function buildProcessMemoryRows(data: ResourceDashboard): ProcessMemoryRow[] {
  const rows = [
    {
      key: "main",
      label: "Web 主进程",
      meta: `pid=${data.main_process.pid ?? "-"}`,
      cpu: data.main_process.cpu_percent,
      memoryMb: processMemoryMb(data.main_process),
      basis: processMemoryBasis(data.main_process),
    },
    ...data.workers.map((worker) => ({
      key: `worker-${worker.account_id}-${worker.pid ?? "na"}`,
      label: `账号 worker #${worker.account_id}`,
      meta: `pid=${worker.pid ?? "-"}`,
      cpu: worker.cpu_percent,
      memoryMb: processMemoryMb(worker),
      basis: processMemoryBasis(worker),
    })),
    ...(data.other_processes ?? []).map((proc, index) => ({
      key: `child-${proc.pid ?? index}`,
      label: "子进程",
      meta: `pid=${proc.pid ?? "-"}`,
      cpu: proc.cpu_percent,
      memoryMb: processMemoryMb(proc),
      basis: processMemoryBasis(proc),
    })),
    ...(data.containers ?? []).map((container, index) => ({
      key: `container-${container.id ?? container.name ?? index}`,
      label: containerLabel(container),
      meta: container.name,
      cpu: container.cpu_percent,
      memoryMb: container.memory_mb,
      basis:
        typeof container.memory_percent === "number"
          ? `容器 ${percent(container.memory_percent)}`
          : "容器",
    })),
  ];
  return rows
    .filter((row) => typeof row.memoryMb === "number" || typeof row.cpu === "number")
    .sort((a, b) => (b.memoryMb ?? 0) - (a.memoryMb ?? 0));
}

function containerLabel(container: ContainerResource) {
  const service = (container.service || "").toLowerCase();
  if (service === "postgres") return "PostgreSQL 容器";
  if (service === "redis") return "Redis 容器";
  if (service === "frontend") return "前端容器";
  if (container.name.toLowerCase().includes("postgres")) return "PostgreSQL 容器";
  if (container.name.toLowerCase().includes("redis")) return "Redis 容器";
  if (container.name.toLowerCase().includes("frontend")) return "前端容器";
  return "项目容器";
}

function processScopeHint(data: ResourceDashboard) {
  const extra = data.other_processes?.length ?? 0;
  const containers = data.containers?.length ?? 0;
  const parts = ["Web 主进程", "账号 worker"];
  if (extra > 0) parts.push(`${extra} 个子进程`);
  if (containers > 0) parts.push(`${containers} 个项目容器`);
  if (containers === 0 && data.container_probe_error) parts.push("容器指标未读到");
  return parts.join(" + ");
}

function projectMemoryHint(
  memoryMb: number | null | undefined,
  totalMb: number | null | undefined,
  data: ResourceDashboard,
): string {
  const containerCount = data.containers?.length ?? 0;
  if (data.container_probe_error && containerCount === 0) {
    if (typeof memoryMb !== "number" || typeof totalMb !== "number" || totalMb <= 0) {
      return "仅进程内存，容器指标未读到";
    }
    return `仅进程内存，约占服务器总内存 ${((memoryMb / totalMb) * 100).toFixed(1)}%；容器指标未读到`;
  }
  if (typeof memoryMb !== "number" || typeof totalMb !== "number" || totalMb <= 0) {
    return containerCount > 0
      ? "含项目容器，服务器总内存占比未知"
      : "服务器总内存占比未知";
  }
  const basis =
    containerCount > 0
      ? "进程独占内存 + 项目容器内存"
      : data.project_total.uss_mb != null
        ? "独占内存"
        : "RSS";
  return `${basis}，约占服务器总内存 ${((memoryMb / totalMb) * 100).toFixed(1)}%`;
}

function saneMemoryTotalMb(totalMb: number | null | undefined): number | null {
  if (typeof totalMb !== "number" || totalMb <= 0) return null;
  // 防御旧 macOS vm_stat fallback 把累计计数当总内存，避免展示 800TB 这类离谱值。
  return totalMb > 64 * 1024 * 1024 ? null : totalMb;
}

function hostMemoryLabel(
  usedPercent: number | null | undefined,
  totalMb: number | null | undefined,
): string {
  const saneTotalMb = saneMemoryTotalMb(totalMb);
  if (saneTotalMb === null && usedPercent != null) return "读取异常";
  const percentText = percent(usedPercent);
  return saneTotalMb !== null ? `${percentText} / ${formatMb(saneTotalMb)}` : percentText;
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-border/70 bg-muted/35 p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold">{value}</p>
      {hint ? <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
