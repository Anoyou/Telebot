import type { PluginHookStat, PluginHookStatsResponse } from "@/api/plugins";

const HOOK_LABELS: Record<string, string> = {
  on_event: "事件处理",
  on_interaction: "交互入口（旧）",
  on_message: "消息",
  on_message_edited: "消息编辑",
  on_direct_message: "低延时直通",
  on_command: "命令",
  on_startup: "启动",
  on_config_action: "配置动作",
  system_agent_tool: "助手工具",
  "middleware.inbound": "入站中间件",
  "middleware.outbound": "出站中间件",
  "middleware.delivered": "投递回调",
};

/** 钩子的中文名；未知钩子原样显示。 */
export function hookLabel(hook: string): string {
  return HOOK_LABELS[hook] ?? hook;
}

/** 事件类型显示；"-" 表示未区分。 */
export function eventTypeLabel(eventType: string | null | undefined): string {
  if (!eventType || eventType === "-") return "";
  return eventType;
}

export function formatDurationMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (value >= 10_000) return `${Math.round(value / 1000)} 秒`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)} 秒`;
  return `${Math.round(value)} 毫秒`;
}

export function formatRate(value: number | null | undefined): string {
  if (!value) return "0%";
  const percent = value * 100;
  return percent >= 10 ? `${Math.round(percent)}%` : `${percent.toFixed(1)}%`;
}

/** Unix 秒 → 相对时间（“3 分钟前”）。 */
export function formatRelativeSeconds(value: number | null | undefined, nowMs: number = Date.now()): string {
  if (!value) return "-";
  const diff = Math.max(0, Math.round(nowMs / 1000 - value));
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86_400)} 天前`;
}

export type HookHealth = "ok" | "warn" | "danger" | "idle";

/** 单行健康度：失败率 ≥ 20% 或最近有超时为 danger，有失败为 warn。 */
export function hookHealth(row: Pick<PluginHookStat, "calls" | "failed" | "timeout" | "failure_rate">): HookHealth {
  if (!row.calls) return "idle";
  const rate = row.failure_rate ?? 0;
  if (rate >= 0.2) return "danger";
  if ((row.failed ?? 0) > 0 || (row.timeout ?? 0) > 0) return "warn";
  return "ok";
}

export interface HookStatsSummary {
  calls: number;
  failed: number;
  timeout: number;
  avgMs: number | null;
  hooks: number;
  deprecatedCalls: number;
  /** 插件代码同步阻塞事件循环 ≥200ms 的次数与累计毫秒（0.109.0+）。 */
  blocked: number;
  blockingMs: number;
}

export function summarizeHookStats(data: PluginHookStatsResponse | undefined | null): HookStatsSummary {
  const hooks = data?.hooks ?? [];
  let timedCalls = 0;
  let sumMs = 0;
  for (const row of hooks) {
    const timed = (row.ok ?? 0) + (row.failed ?? 0) + (row.timeout ?? 0);
    timedCalls += timed;
    sumMs += row.sum_ms ?? 0;
  }
  return {
    calls: data?.totals?.calls ?? 0,
    failed: data?.totals?.fail ?? 0,
    timeout: data?.totals?.timeout ?? 0,
    avgMs: timedCalls ? Math.round(sumMs / timedCalls) : null,
    hooks: hooks.length,
    deprecatedCalls: (data?.deprecations ?? []).reduce((total, item) => total + (item.count ?? 0), 0),
    blocked: data?.totals?.blocked ?? 0,
    blockingMs: data?.totals?.blocking_ms ?? 0,
  };
}
