// 顶栏的"全系统状态"信号灯——一颗点替代之前误导的"主进程出口"徽章。
//
// 颜色语义：
//   🟢 绿  全部子系统正常
//   🟡 黄  有可恢复的告警（缺 api_key / 待登录账号 / 没代理 / alembic 落后等）
//   🔴 红  基础设施挂了（DB / Redis 不通），系统功能不可用
//   ⚪ 灰  数据还没拉到 / 探测失败
//
// 数据来自 ``/api/system/health-overview``——和 SystemHealthCard 共享 react-query
// cache key，因此一次请求覆盖全页面，不会有"两个组件各拉一次"。
//
// hover：弹出一个简短摘要"X 个账号需重登 / Y 条 LLM 缺 key / ..."；
// click：跳到 / （概览页）让用户看 SystemHealthCard 详情。
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getHealthOverview } from "@/api/system";
import type { HealthOverview } from "@/api/types";
import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "err" | "loading";

function aggregateTone(h: HealthOverview): Tone {
  // 红：基础设施挂了——DB 或 Redis 任一不可用
  if (!h.db.ok || !h.redis.ok) return "err";
  // 黄：有任何"应该处理但还能凑合用"的告警
  if (!h.alembic.ok) return "warn";
  if (h.providers.total > 0 && h.providers.with_api_key < h.providers.total)
    return "warn";
  if ((h.workers.by_status["dead"] ?? 0) > 0) return "warn";
  if ((h.workers.by_status["login_required"] ?? 0) > 0) return "warn";
  // 全绿
  return "ok";
}

function summarize(h: HealthOverview): string[] {
  const out: string[] = [];
  if (!h.db.ok) out.push("✗ 数据存储连不上");
  if (!h.redis.ok) out.push("✗ 实时通信不通");
  if (!h.alembic.ok && !h.alembic.error)
    out.push(`⚠ 数据库结构落后（${h.alembic.pending.length} 条迁移待跑）`);
  const noKey = h.providers.total - h.providers.with_api_key;
  if (h.providers.total > 0 && noKey > 0)
    out.push(`⚠ ${noKey} 个 AI 模型缺 api_key`);
  if (h.providers.total === 0) out.push("ℹ 还没配置 AI 模型");
  if (h.proxies.total === 0) out.push("ℹ 代理库为空");
  const dead = h.workers.by_status["dead"] ?? 0;
  const reauth = h.workers.by_status["login_required"] ?? 0;
  if (reauth) out.push(`⚠ ${reauth} 个账号需重登`);
  if (dead) out.push(`⚠ ${dead} 个账号已停用`);
  if (h.workers.total === 0) out.push("ℹ 还没绑定 TG 账号");
  return out;
}

export function HealthDot() {
  const nav = useNavigate();
  const q = useQuery({
    queryKey: ["system", "health-overview"],
    queryFn: getHealthOverview,
    refetchInterval: 30_000,
  });

  const tone: Tone = q.isLoading
    ? "loading"
    : q.error || !q.data
    ? "warn"
    : aggregateTone(q.data);

  const cls = {
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    err: "bg-rose-500",
    loading: "bg-muted-foreground/40",
  }[tone];

  const label = {
    ok: "全部正常",
    warn: "有告警",
    err: "出问题",
    loading: "加载中",
  }[tone];

  const issues = q.data ? summarize(q.data) : [];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 px-2 text-xs"
          title={`系统状态：${label}`}
        >
          {tone === "loading" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : (
            <span
              className={cn("inline-block h-2.5 w-2.5 rounded-full", cls)}
              aria-label={label}
            />
          )}
          <span className="hidden text-xs sm:inline">{label}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[280px] p-3">
        <div className="space-y-2 text-xs">
          <div className="flex items-center gap-2 border-b pb-2">
            {tone === "ok" ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            ) : tone === "err" ? (
              <AlertTriangle className="h-4 w-4 text-rose-600" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-amber-600" />
            )}
            <span className="text-sm font-medium">系统状态：{label}</span>
          </div>
          {q.isLoading ? (
            <div className="text-muted-foreground">读取中…</div>
          ) : q.error ? (
            <div className="text-rose-700">
              ⚠ 读取失败：{(q.error as Error).message}
            </div>
          ) : issues.length === 0 ? (
            <div className="text-muted-foreground">
              所有子系统状态正常 — DB / Redis / 模型 / 代理 / 账号 worker 都在工作。
            </div>
          ) : (
            <ul className="space-y-1">
              {issues.map((line, i) => (
                <li key={i} className="leading-snug">
                  {line}
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center justify-end border-t pt-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => nav("/")}
            >
              到概览查看详情 →
            </Button>
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
