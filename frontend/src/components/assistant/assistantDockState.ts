export type AssistantOutcomeStatus = "complete" | "failed";

export type AssistantOutcomeSignal = {
  id: number;
  status: AssistantOutcomeStatus;
};

/** 从账号页、插件页等带账号的页面进入 Agent 时的路由参数（第 20 轮）。 */
export const AGENT_ACCOUNT_PARAM = "agent_account";
export const AGENT_FROM_PARAM = "agent_from";

/** 进入 Agent 时所在页面的账号上下文。 */
export type AgentPageAccount = {
  id: number;
  /** 来源页面，用于提示「已带入账号（来自…）」。 */
  source: AgentAccountSource;
};

export type AgentAccountSource = "account" | "plugins" | "plugin_config";

export const AGENT_ACCOUNT_SOURCE_LABEL: Record<AgentAccountSource, string> = {
  account: "账号页",
  plugins: "插件页",
  plugin_config: "插件配置",
};

export function shouldOpenAssistantDock(pathname: string, search: string): boolean {
  const params = new URLSearchParams(search);
  return pathname === "/assistant" || Boolean(params.get("session")) || agentAccountFromSearch(search) != null;
}

function positiveInt(raw: string | null | undefined): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function accountSource(raw: string | null): AgentAccountSource | null {
  return raw === "account" || raw === "plugins" || raw === "plugin_config" ? raw : null;
}

/** 解析 `?agent_account=ID&agent_from=account|plugins|plugin_config`；无效值返回 null。 */
export function agentAccountFromSearch(search: string): AgentPageAccount | null {
  const params = new URLSearchParams(search);
  const id = positiveInt(params.get(AGENT_ACCOUNT_PARAM));
  if (id == null) return null;
  return { id, source: accountSource(params.get(AGENT_FROM_PARAM)) ?? "account" };
}

/** 账号详情 `/accounts/:aid` 与账号下的插件配置 `/accounts/:aid/features/…` 自带账号上下文。 */
export function agentAccountFromPath(pathname: string): AgentPageAccount | null {
  const match = /^\/accounts\/(\d+)(\/features(\/|$))?/.exec(pathname);
  if (!match) return null;
  const id = positiveInt(match[1]);
  if (id == null) return null;
  return { id, source: match[2] ? "plugin_config" : "account" };
}

/** 给当前地址加上「带账号进入 Agent」参数（保留原有查询参数）。 */
export function withAgentAccountSearch(search: string, account: AgentPageAccount): string {
  const params = new URLSearchParams(search);
  params.set(AGENT_ACCOUNT_PARAM, String(account.id));
  params.set(AGENT_FROM_PARAM, account.source);
  const next = params.toString();
  return next ? `?${next}` : "";
}

/** 去掉「带账号进入 Agent」参数；Dock 判断路由是否变化时忽略它们（被 Agent 消费不算换页）。 */
export function withoutAgentAccountSearch(search: string): string {
  const params = new URLSearchParams(search);
  params.delete(AGENT_ACCOUNT_PARAM);
  params.delete(AGENT_FROM_PARAM);
  const next = params.toString();
  return next ? `?${next}` : "";
}

export type NewSessionAccountPayload = {
  account_id?: number | null;
  default_account?: boolean;
};

/**
 * 新建 Agent 会话时的账号字段（第 20 轮）。
 * - 指定账号（页面带入或用户在草稿里选了账号）→ 直接用；
 * - 用户显式选「系统级」→ 不关联；
 * - 否则带 default_account=true：新服务器（features.agent_session_default_account）只有 1 个账号时自动关联；
 *   旧服务器忽略该字段，由客户端在只看到 1 个账号时自己填 account_id。多账号保持系统级。
 */
export function newSessionAccountPayload(input: {
  selectedAccountId: number | null;
  explicitGlobal: boolean;
  accountIds: readonly number[] | null;
  serverSupportsDefault: boolean;
}): NewSessionAccountPayload {
  if (input.selectedAccountId != null) return { account_id: input.selectedAccountId };
  if (input.explicitGlobal) return { account_id: null };
  if (!input.serverSupportsDefault && input.accountIds?.length === 1) {
    return { account_id: input.accountIds[0], default_account: true };
  }
  return { account_id: null, default_account: true };
}

/** 草稿（尚未建会话）时账号下拉显示的值：只有 1 个账号且用户没选「系统级」时预选它。 */
export function draftDisplayAccountId(input: {
  selectedAccountId: number | null;
  explicitGlobal: boolean;
  accountIds: readonly number[] | null;
}): number | null {
  if (input.selectedAccountId != null) return input.selectedAccountId;
  if (input.explicitGlobal) return null;
  return input.accountIds?.length === 1 ? input.accountIds[0] : null;
}

export function nextAssistantOutcomeSignal(
  current: AssistantOutcomeSignal | null,
  status: AssistantOutcomeStatus,
): AssistantOutcomeSignal {
  return {
    id: (current?.id ?? 0) + 1,
    status,
  };
}
