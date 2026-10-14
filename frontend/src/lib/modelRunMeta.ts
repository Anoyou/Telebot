// 模型执行信息行的纯逻辑层：实际作答模型摘要、工具调用数、阶段耗时标签。
// 与 ModelRunMeta.tsx（渲染层）分离，便于在 node --test 下直接回归。

export type ModelRunMetaUsage = Record<string, unknown> | null | undefined;

function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

function str(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  return null;
}

/** 工具调用数：优先实际调用 tool_calls，兼容旧 tool_count（历史消息）。 */
export function resolveToolCallCount(usage: ModelRunMetaUsage): number | null {
  if (!usage || typeof usage !== "object") return null;
  const actual = num(usage.tool_calls);
  if (actual != null) return actual;
  // schema_version>=2 前 tool_count 是暴露数，无法区分；仅当无 tool_calls 时降级
  if (num(usage.schema_version) === 2) return actual;
  return num(usage.tool_count);
}

function formatElapsed(ms: number | null): string | null {
  if (ms == null) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** 从 usage.stage_timings 抽出探测/路由/首 token 耗时标签。 */
export function formatStageTimings(usage: ModelRunMetaUsage): string[] {
  if (!usage || typeof usage !== "object") return [];
  const raw = usage.stage_timings;
  if (!raw || typeof raw !== "object") return [];
  const stages = raw as Record<string, unknown>;
  const bits: string[] = [];
  const verify = num(stages.verify_ms);
  const route = num(stages.route_ms);
  const first = num(stages.first_token_ms);
  if (verify != null) bits.push(`探测 ${formatElapsed(verify)}`);
  if (route != null) bits.push(`路由 ${formatElapsed(route)}`);
  if (first != null) bits.push(`首 token ${formatElapsed(first)}`);
  return bits.filter((bit): bit is string => Boolean(bit));
}

/**
 * 回复气泡下方展示的「实际作答」模型摘要。
 *
 * `usage.model` / `usage.provider_name` 就是实际产出回复的 Provider 与模型：
 * Magpie 等网关场景下是上游模型标识（如 `workbuddy/glm-5.3-flash`），不是用户选的那一个。
 * `requested` / `reported` 供单独列出「原模型 / 实际使用」。
 */
export function resolveModelRunSummary(usage: ModelRunMetaUsage): {
  summary: string;
  summaryModel: string;
  reported: string;
  requested: string;
  usedFallback: boolean;
} | null {
  if (!usage || typeof usage !== "object") return null;
  const provider = str(usage.provider_name);
  const model = str(usage.model);
  const reqProvider = str(usage.requested_provider_name);
  const reqModel = str(usage.requested_model);
  const reported = [provider, model].filter(Boolean).join(" · ");
  const requested = [reqProvider, reqModel].filter(Boolean).join(" · ");
  const usedFallback = Boolean(usage.used_fallback);
  const summaryModel = model || reqModel;
  const summary = [provider || reqProvider, summaryModel].filter(Boolean).join(" · ");
  if (!summary) return null;
  return { summary, summaryModel, reported, requested, usedFallback };
}
