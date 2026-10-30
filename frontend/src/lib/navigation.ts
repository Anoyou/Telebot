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
    webhooks: "Webhook",
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

/** 命中调试预填参数（日志 → 命中调试「用这条消息复现」）。全部可省略。 */
export interface DispatchDebugPrefill {
  accountId?: number | null;
  chatId?: number | null;
  chatType?: string | null;
  senderId?: number | null;
  text?: string | null;
  via?: string | null;
}

const DISPATCH_DEBUG_VIAS = new Set(["userbot", "interaction_bot", "webhook"]);
const DISPATCH_DEBUG_CHAT_TYPES = new Set(["group", "supergroup", "private", "channel"]);

/** 按 chat_id 推断会话类型：-100… 超级群，其它负数普通群，正数私聊。 */
export function inferChatType(chatId: number | null | undefined): string | undefined {
  if (chatId == null || !Number.isFinite(chatId) || chatId === 0) return undefined;
  if (chatId <= -1_000_000_000_000) return "supergroup";
  return chatId < 0 ? "group" : "private";
}

/** 生成 /dispatch-debug 链接；来源不是命中调试支持的值（如 account_bot、scheduler）时不带 via。 */
export function dispatchDebugHref(prefill: DispatchDebugPrefill = {}): string {
  const params = new URLSearchParams();
  if (prefill.accountId != null) params.set("account_id", String(prefill.accountId));
  if (prefill.chatId != null) params.set("chat_id", String(prefill.chatId));
  const chatType = prefill.chatType ?? inferChatType(prefill.chatId);
  if (chatType && DISPATCH_DEBUG_CHAT_TYPES.has(chatType)) params.set("chat_type", chatType);
  if (prefill.senderId != null) params.set("sender_id", String(prefill.senderId));
  if (prefill.via && DISPATCH_DEBUG_VIAS.has(prefill.via)) params.set("via", prefill.via);
  const text = prefill.text?.trim();
  if (text) params.set("text", text.slice(0, 2000));
  const query = params.toString();
  return query ? `/dispatch-debug?${query}` : "/dispatch-debug";
}

/** 读回 dispatchDebugHref 写入的参数；非法值丢弃。 */
export function parseDispatchDebugPrefill(search: URLSearchParams): DispatchDebugPrefill {
  const num = (key: string) => {
    const raw = search.get(key);
    if (raw == null || raw.trim() === "") return undefined;
    const value = Number(raw);
    return Number.isSafeInteger(value) ? value : undefined;
  };
  const via = search.get("via");
  const chatType = search.get("chat_type");
  return {
    accountId: num("account_id"),
    chatId: num("chat_id"),
    chatType: chatType && DISPATCH_DEBUG_CHAT_TYPES.has(chatType) ? chatType : undefined,
    senderId: num("sender_id"),
    text: search.get("text") ?? undefined,
    via: via && DISPATCH_DEBUG_VIAS.has(via) ? via : undefined,
  };
}
