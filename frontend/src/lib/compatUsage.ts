import type { PluginCompatUsageItem } from "@/api/plugins";

export const COMPAT_HOST_PLUGIN_KEY = "-";

export interface CompatUsageSummary {
  /** 有计数的兼容项数（不含无法计数的 C01 / C07） */
  trackable: number;
  /** 被用到过（次数 > 0）的兼容项数 */
  used: number;
  /** 所有兼容项的累计命中次数 */
  total: number;
}

export function summarizeCompatUsage(items: PluginCompatUsageItem[] | null | undefined): CompatUsageSummary {
  const list = items ?? [];
  return {
    trackable: list.filter((item) => item.trackable).length,
    used: list.filter((item) => (item.total ?? 0) > 0).length,
    total: list.reduce((sum, item) => sum + (item.total ?? 0), 0),
  };
}

/** 插件 key 的显示名：'-' 表示与具体插件无关的宿主兜底。 */
export function compatPluginLabel(pluginKey: string): string {
  return pluginKey === COMPAT_HOST_PLUGIN_KEY ? "宿主（无插件）" : pluginKey;
}

/** 卡片排序：用到过的按次数降序在前，没用到的保持清单顺序，无法计数的放最后。 */
export function sortCompatUsageItems(items: PluginCompatUsageItem[] | null | undefined): PluginCompatUsageItem[] {
  const list = [...(items ?? [])];
  const rank = (item: PluginCompatUsageItem) => (!item.trackable ? 2 : (item.total ?? 0) > 0 ? 0 : 1);
  return list
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const ra = rank(a.item);
      const rb = rank(b.item);
      if (ra !== rb) return ra - rb;
      if (ra === 0 && (b.item.total ?? 0) !== (a.item.total ?? 0)) return (b.item.total ?? 0) - (a.item.total ?? 0);
      return a.index - b.index;
    })
    .map(({ item }) => item);
}
