// 插件市场（服务器 0.113.0+）：浏览 / 搜索 / 安装 / 并发更新 / 来源管理 / 固定版本 / 健康窗口回滚。
import { api } from "@/lib/api";
import type { components } from "@/api/schema";
import type { RemotePlugin } from "@/types/remotePlugin";

export type PluginMarketCatalog = components["schemas"]["PluginMarketCatalog"];
export type PluginMarketItem = components["schemas"]["PluginMarketItem"];
export type PluginMarketSource = components["schemas"]["PluginMarketSource"];
export type PluginMarketSettings = components["schemas"]["PluginMarketSettings"];
export type PluginMarketRollout = components["schemas"]["PluginMarketRollout"];
export type PluginMarketUpdateResult = components["schemas"]["PluginMarketUpdateResult"];
export type PluginMarketSourceCreate = components["schemas"]["PluginMarketSourceCreate"];
export type PluginMarketSourcePatch = components["schemas"]["PluginMarketSourcePatch"];
export type PluginMarketSettingsPatch = components["schemas"]["PluginMarketSettingsPatch"];
export type PluginMarketChannel = components["schemas"]["PluginMarketChannel"];

const BASE = "/api/plugin-market";
// 安装 / 更新要克隆仓库并等待 worker 重载，多个插件并行时也可能超过默认超时。
const MARKET_LONG_TIMEOUT_MS = 300000;

export async function fetchPluginMarketCatalog(refresh = false): Promise<PluginMarketCatalog> {
  const { data } = await api.get<PluginMarketCatalog>(`${BASE}/catalog`, {
    params: refresh ? { refresh: true } : undefined,
    timeout: 60000,
  });
  return data;
}

export async function installFromMarket(body: {
  source_id: string;
  key: string;
  default_enabled?: boolean;
  /** 服务器 0.117.0+：安装哪个通道；旧服务器忽略此字段（只装稳定版），所以只在选了非稳定通道时才传。 */
  channel?: string | null;
}): Promise<RemotePlugin> {
  const { data } = await api.post<RemotePlugin>(`${BASE}/install`, body, {
    timeout: MARKET_LONG_TIMEOUT_MS,
  });
  return data;
}

export async function updateFromMarket(keys?: string[]): Promise<PluginMarketUpdateResult> {
  const { data } = await api.post<PluginMarketUpdateResult>(
    `${BASE}/update`,
    keys && keys.length ? { keys } : {},
    { timeout: MARKET_LONG_TIMEOUT_MS },
  );
  return data;
}

/** 服务器 0.117.0+：把已安装插件切到另一个通道（稳定 ↔ 测试），结果形状与更新相同。 */
export async function switchMarketChannel(body: {
  key: string;
  channel: string;
  source_id?: string | null;
}): Promise<PluginMarketUpdateResult> {
  const { data } = await api.post<PluginMarketUpdateResult>(`${BASE}/channel`, body, {
    timeout: MARKET_LONG_TIMEOUT_MS,
  });
  return data;
}

export async function addMarketSource(body: PluginMarketSourceCreate): Promise<PluginMarketSource> {
  const { data } = await api.post<PluginMarketSource>(`${BASE}/sources`, body);
  return data;
}

export async function patchMarketSource(
  id: string,
  body: PluginMarketSourcePatch,
): Promise<PluginMarketSource> {
  const { data } = await api.patch<PluginMarketSource>(`${BASE}/sources/${encodeURIComponent(id)}`, body);
  return data;
}

export async function deleteMarketSource(id: string): Promise<void> {
  await api.delete(`${BASE}/sources/${encodeURIComponent(id)}`);
}

export async function saveMarketSettings(body: PluginMarketSettingsPatch): Promise<PluginMarketSettings> {
  const { data } = await api.put<PluginMarketSettings>(`${BASE}/settings`, body);
  return data;
}

export async function setMarketPin(key: string, pinned: boolean): Promise<{ key: string; pinned_version?: string | null }> {
  const { data } = await api.put(`${BASE}/pins/${encodeURIComponent(key)}`, { pinned });
  return data;
}

export async function rollbackMarketRollout(key: string): Promise<PluginMarketRollout> {
  const { data } = await api.post<PluginMarketRollout>(
    `${BASE}/rollouts/${encodeURIComponent(key)}/rollback`,
    undefined,
    { timeout: MARKET_LONG_TIMEOUT_MS },
  );
  return data;
}

export async function acceptMarketRollout(key: string): Promise<PluginMarketRollout> {
  const { data } = await api.post<PluginMarketRollout>(`${BASE}/rollouts/${encodeURIComponent(key)}/accept`);
  return data;
}

export {
  KIND_LABELS,
  TRUST_DESCRIPTIONS,
  TRUST_LABELS,
  channelLabel,
  describePermission,
  filterMarketItems,
  hasMultipleChannels,
  isChannelDowngrade,
  kindCounts,
  marketChannels,
  marketUpdateCount,
  renderUsage,
} from "@/lib/pluginMarket";
