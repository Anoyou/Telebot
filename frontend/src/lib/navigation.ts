import type { NavigateFunction, To } from "react-router-dom";

import type { PlatformCapabilities, PlatformModuleKey } from "@/api/types";

export function goBackOr(nav: NavigateFunction, fallback: To) {
  if (window.history.length > 1) {
    nav(-1);
  } else {
    nav(fallback);
  }
}

/** 路由路径 → 依赖的平台能力模块；无映射则始终显示。 */
export const ROUTE_CAPABILITY_MODULE: Record<string, PlatformModuleKey> = {
  "/ai": "ai",
  "/assistant": "ai",
  "/interaction": "interaction_bot",
  "/webhooks": "webhooks",
  "/ledger": "ledger",
  "/dispatch-debug": "dispatch_debug",
};

export type CapabilityEnabledMap = Partial<Record<PlatformModuleKey, boolean>>;

/** 从 capabilities 响应构建 enabled 映射；缺失时默认 true。 */
export function capabilityEnabledMap(
  caps: PlatformCapabilities | null | undefined,
  fallbackAiEnabled = true,
): CapabilityEnabledMap {
  const map: CapabilityEnabledMap = {
    ai: fallbackAiEnabled,
    interaction_bot: true,
    webhooks: true,
    ledger: true,
    dispatch_debug: true,
  };
  for (const mod of caps?.modules ?? []) {
    const key = mod.key as PlatformModuleKey;
    if (key in map) {
      map[key] = Boolean(mod.desired_enabled);
    }
  }
  // 兼容：若 capabilities 尚未加载，仍可用 settings.ai_enabled
  if (!caps?.modules?.length) {
    map.ai = fallbackAiEnabled;
  }
  return map;
}

export function isRouteEnabledByCapabilities(
  path: string,
  enabled: CapabilityEnabledMap,
): boolean {
  // 匹配最长前缀（/ai 与 /ai/liveness）
  const matched = Object.keys(ROUTE_CAPABILITY_MODULE)
    .filter((route) => path === route || path.startsWith(`${route}/`))
    .sort((a, b) => b.length - a.length)[0];
  if (!matched) return true;
  const moduleKey = ROUTE_CAPABILITY_MODULE[matched];
  return enabled[moduleKey] !== false;
}

export type CapabilityGatePhase = "pending" | "paused" | "ready";

/** 能力 gate 的纯决策：把两个查询状态收敛成"骨架 / 暂停页 / 放行"。 */
export function resolveCapabilityGatePhase(params: {
  capsLoading: boolean;
  caps: PlatformCapabilities | null | undefined;
  moduleKey: PlatformModuleKey;
  fallbackAiEnabled: boolean;
}): CapabilityGatePhase {
  // 只等 capabilities 查询：settings 仅仅是 ai 键的兜底来源，缺失时按
  // default-true 处理即可。若把 settings 也纳入等待条件，会出现
  // "settings 已返回、capabilities 还在飞"的窗口——gate 提前放行，
  // 已关闭模块的内容先闪一下再跳暂停页。
  if (params.capsLoading) {
    return "pending";
  }
  const enabled = capabilityEnabledMap(params.caps, params.fallbackAiEnabled);
  return enabled[params.moduleKey] === false ? "paused" : "ready";
}

export function filterNavByCapabilities<T extends { to: string }>(
  items: T[],
  enabled: CapabilityEnabledMap,
): T[] {
  return items.filter((item) => isRouteEnabledByCapabilities(item.to, enabled));
}

export function moduleLabel(key: PlatformModuleKey | string): string {
  const labels: Record<string, string> = {
    ai: "AI",
    interaction_bot: "交互",
    webhooks: "入站 Webhook",
    ledger: "资金台账",
    dispatch_debug: "命中调试",
  };
  return labels[key] ?? key;
}

export function runtimeStateLabel(state: string | undefined | null): string {
  switch (state) {
    case "ready":
      return "运行中";
    case "starting":
      return "启动中";
    case "quiescing":
      return "停用中";
    case "stopped":
      return "已暂停";
    case "failed":
      return "失败";
    default:
      return state || "未知";
  }
}

export function pluginRuntimeAvailabilityLabel(
  availability: string | null | undefined,
): string {
  switch (availability) {
    case "partial":
      return "部分可用";
    case "paused":
      return "已暂停";
    case "transitioning":
      return "等待热加载";
    case "ready":
    default:
      return "正常";
  }
}
