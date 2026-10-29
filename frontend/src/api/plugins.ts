import type { components } from "@/api/schema";
import { api } from "@/lib/api";

export interface PluginInstallOut {
  key: string;
  source: "builtin" | "zip" | "repo" | "local" | "git" | string;
  source_url?: string | null;
  source_label?: string | null;
  version: string;
  enabled: boolean;
  signature_ok: boolean | null;
  installed_path: string;
  manifest?: Record<string, unknown> | null;
  installed_at: string;
  updated_at: string;
}

export interface InstalledPluginOverviewTraceRef {
  trace_id: string;
  account_id: number | null;
  status: string | null;
  event_type: string | null;
  source_channel: string | null;
  started_at: string | null;
}

export interface InstalledPluginOverviewLoadError {
  source: string;
  account_id: number | null;
  load_status: string | null;
  message: string;
  updated_at: string | null;
}

export interface InstalledPluginOverviewAccountItem {
  account_id: number;
  account_name: string;
  enabled: boolean;
  state: string;
  last_error?: string | null;
  load_status?: string | null;
  last_load_error?: string | null;
  last_trace?: InstalledPluginOverviewTraceRef | null;
}

export interface InstalledPluginOverviewUpdateStatus {
  update_available: boolean;
  latest_version?: string | null;
  last_update_check_at?: string | null;
  last_update_check_error?: string | null;
}

export interface InstalledPluginOverviewItem {
  key: string;
  display_name: string;
  source: "builtin" | "zip" | "repo" | "local" | "git" | string;
  source_url?: string | null;
  source_label?: string | null;
  version?: string | null;
  global_enabled: boolean;
  signature_ok?: boolean | null;
  trust_tier?: string | null;
  lint_warnings: string[];
  update: InstalledPluginOverviewUpdateStatus;
  accounts: InstalledPluginOverviewAccountItem[];
  recent_load_error?: InstalledPluginOverviewLoadError | null;
  recent_trace?: InstalledPluginOverviewTraceRef | null;
}

export interface PluginChangelogResponse {
  key: string;
  available: boolean;
  content: string;
  truncated: boolean;
  message?: string | null;
}

export interface PluginBatchStateResult {
  enabled: boolean;
  succeeded: number;
  failed: number;
  reloaded_accounts: number;
  items: Array<{
    key: string;
    ok: boolean;
    code?: string | null;
    message?: string | null;
    plugin?: PluginInstallOut | null;
  }>;
}

export interface PluginInstallHistoryItem {
  id: number;
  plugin_key: string;
  event_type: "installed" | "updated" | "enabled" | "disabled" | "uninstalled" | string;
  version?: string | null;
  previous_version?: string | null;
  source?: string | null;
  source_label?: string | null;
  enabled?: boolean | null;
  signature_ok?: boolean | null;
  detail?: string | null;
  created_at: string;
}

export async function listInstalledPackages(): Promise<PluginInstallOut[]> {
  const { data } = await api.get<PluginInstallOut[]>(
    "/api/plugins/installed-packages",
  );
  return data;
}

export async function listInstalledOverview(): Promise<InstalledPluginOverviewItem[]> {
  const { data } = await api.get<InstalledPluginOverviewItem[]>(
    "/api/plugins/installed-overview",
  );
  return data;
}

export async function getInstalledPluginChangelog(key: string): Promise<PluginChangelogResponse> {
  const { data } = await api.get<PluginChangelogResponse>(
    `/api/plugins/install/${encodeURIComponent(key)}/changelog`,
  );
  return data;
}

export type PluginHookStatsResponse = components["schemas"]["PluginHookStatsResponse"];
export type PluginHookStat = components["schemas"]["PluginHookStatOut"];
export type PluginDeprecationUsage = components["schemas"]["PluginDeprecationUsageOut"];
export type PluginCapabilitiesResponse = components["schemas"]["PluginCapabilitiesResponse"];

/** 插件每个钩子 / 事件处理的调用数、耗时与失败数，以及已弃用接口的调用次数（0.107.0+）。 */
export async function getPluginHookStats(key: string, accountId?: number | null): Promise<PluginHookStatsResponse> {
  const { data } = await api.get<PluginHookStatsResponse>(
    `/api/plugins/${encodeURIComponent(key)}/stats`,
    { params: accountId ? { account_id: accountId } : undefined },
  );
  return data;
}

export async function resetPluginHookStats(key: string): Promise<void> {
  await api.delete(`/api/plugins/${encodeURIComponent(key)}/stats`);
}

/** 宿主能力注册表、Plugin API 版本与弃用登记。 */
export async function getPluginCapabilities(): Promise<PluginCapabilitiesResponse> {
  const { data } = await api.get<PluginCapabilitiesResponse>("/api/plugins/capabilities");
  return data;
}

export async function uploadPluginZip(
  file: File,
  signature?: File | null,
): Promise<PluginInstallOut> {
  const form = new FormData();
  form.append("file", file);
  if (signature) form.append("signature_file", signature);
  const { data } = await api.post<PluginInstallOut>(
    "/api/plugins/install/upload",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
    },
  );
  return data;
}

export async function enableInstall(key: string): Promise<PluginInstallOut> {
  const { data } = await api.post<PluginInstallOut>(
    `/api/plugins/install/${encodeURIComponent(key)}/enable`,
  );
  return data;
}

export async function disableInstall(key: string): Promise<PluginInstallOut> {
  const { data } = await api.post<PluginInstallOut>(
    `/api/plugins/install/${encodeURIComponent(key)}/disable`,
  );
  return data;
}

export async function batchSetInstallState(
  keys: string[],
  enabled: boolean,
): Promise<PluginBatchStateResult> {
  const { data } = await api.post<PluginBatchStateResult>(
    "/api/plugins/install/batch-state",
    { keys, enabled },
  );
  return data;
}

export async function listPluginInstallHistory(
  key: string,
): Promise<PluginInstallHistoryItem[]> {
  const { data } = await api.get<PluginInstallHistoryItem[]>(
    `/api/plugins/install/${encodeURIComponent(key)}/history`,
  );
  return data;
}

export async function uninstallPlugin(key: string): Promise<void> {
  await api.delete(`/api/plugins/install/${encodeURIComponent(key)}`);
}

// ── 插件声明式 UI：原生卡片（Plugin API 1.3）──
export type PluginUICard = components["schemas"]["PluginUICard"];
export type PluginUICardsResponse = components["schemas"]["PluginUICardsResponse"];
export type PluginUICardData = components["schemas"]["PluginUICardData"];
export type PluginUICardActionResponse = components["schemas"]["PluginUICardActionResponse"];
export type PluginUICardSlot = "plugin.overview" | "account.detail" | "dashboard";

/** 某个位置的卡片声明（不含数据）。 */
export async function listPluginUICards(params: {
  slot: PluginUICardSlot;
  pluginKey?: string;
  accountId?: number | null;
}): Promise<PluginUICardsResponse> {
  const { data } = await api.get<PluginUICardsResponse>("/api/plugin-ui/cards", {
    params: {
      slot: params.slot,
      ...(params.pluginKey ? { plugin_key: params.pluginKey } : {}),
      ...(params.accountId ? { account_id: params.accountId } : {}),
    },
  });
  return data;
}

/** 读取一张卡的数据；失败时 status=error（HTTP 仍是 200），只影响这张卡。 */
export async function readPluginUICard(card: Pick<PluginUICard, "plugin_key" | "card_id" | "slot">, accountId?: number | null): Promise<PluginUICardData> {
  const { data } = await api.post<PluginUICardData>(
    `/api/plugin-ui/cards/${encodeURIComponent(card.plugin_key)}/${encodeURIComponent(card.card_id)}/data`,
    { account_id: accountId ?? null, slot: card.slot },
  );
  return data;
}

/** 卡片按钮（插件 on_ui_action）。不自动重试。 */
export async function runPluginUICardAction(
  card: Pick<PluginUICard, "plugin_key" | "card_id" | "slot">,
  actionId: string,
  accountId?: number | null,
): Promise<PluginUICardActionResponse> {
  const { data } = await api.post<PluginUICardActionResponse>(
    `/api/plugin-ui/cards/${encodeURIComponent(card.plugin_key)}/${encodeURIComponent(card.card_id)}/actions/${encodeURIComponent(actionId)}`,
    { account_id: accountId ?? null, slot: card.slot },
  );
  return data;
}

// ── 插件页面与菜单（ui.pages / ui.actions，Plugin API 1.4）──
export type PluginUIEntriesResponse = components["schemas"]["PluginUIEntriesResponse"];
export type PluginUIPageEntry = components["schemas"]["PluginUIPageEntry"];
export type PluginUIMenuAction = components["schemas"]["PluginUIMenuAction"];
export type PluginUIPageSession = components["schemas"]["PluginUIPageSession"];
export type PluginUIBridgeResponse = components["schemas"]["PluginUIBridgeResponse"];
export type PluginUIMenuActionResponse = components["schemas"]["PluginUIMenuActionResponse"];
export type PluginUIMenu = "plugin.detail" | "account.detail";

/** 某个菜单位置的插件页面与操作；插件没有声明时为空（不显示任何入口）。 */
export async function listPluginUIEntries(params: {
  menu: PluginUIMenu;
  pluginKey?: string;
  accountId?: number | null;
}): Promise<PluginUIEntriesResponse> {
  const { data } = await api.get<PluginUIEntriesResponse>("/api/plugin-ui/entries", {
    params: {
      menu: params.menu,
      ...(params.pluginKey ? { plugin_key: params.pluginKey } : {}),
      ...(params.accountId ? { account_id: params.accountId } : {}),
    },
  });
  return data;
}

/** 打开插件页面：换一个 1 小时有效的页面地址（签名 token，不含登录凭据）。 */
export async function openPluginUIPage(pluginKey: string, pageId: string, accountId?: number | null): Promise<PluginUIPageSession> {
  const { data } = await api.post<PluginUIPageSession>(
    `/api/plugin-ui/pages/${encodeURIComponent(pluginKey)}/${encodeURIComponent(pageId)}/session`,
    { account_id: accountId ?? null },
  );
  return data;
}

/** 页面 Bridge 请求（宿主转发，服务器按页面声明的权限逐次校验）。 */
export async function callPluginUIBridge(
  pluginKey: string,
  pageId: string,
  method: string,
  params: Record<string, unknown>,
  accountId?: number | null,
): Promise<PluginUIBridgeResponse> {
  const { data } = await api.post<PluginUIBridgeResponse>(
    `/api/plugin-ui/pages/${encodeURIComponent(pluginKey)}/${encodeURIComponent(pageId)}/bridge`,
    { method, params, account_id: accountId ?? null },
  );
  return data;
}

/** 菜单项（插件 on_ui_action，10 秒）。不自动重试。 */
export async function runPluginUIMenuAction(
  action: Pick<PluginUIMenuAction, "plugin_key" | "action_id" | "menu">,
  accountId?: number | null,
): Promise<PluginUIMenuActionResponse> {
  const { data } = await api.post<PluginUIMenuActionResponse>(
    `/api/plugin-ui/actions/${encodeURIComponent(action.plugin_key)}/${encodeURIComponent(action.action_id)}`,
    { account_id: accountId ?? null, menu: action.menu },
  );
  return data;
}
