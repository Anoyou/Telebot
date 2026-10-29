// 插件市场的纯函数（不依赖网络与别名导入，便于 node --test 直接测试）。

export type MarketFilterItem = {
  key: string;
  kind: string;
  name: { zh: string; en: string };
  summary?: string;
  author?: string;
  tags?: string[];
  installed?: unknown;
  update_available?: boolean;
  pinned_version?: string | null;
};

export const TRUST_LABELS: Record<string, string> = {
  official: "官方",
  community: "社区",
  unreviewed: "未审核",
};

export const KIND_LABELS: Record<string, string> = {
  interactive: "互动玩法",
  automation: "自动化",
  utility: "工具",
  middleware: "消息中间件",
  payment_source: "转账来源",
  ui: "界面扩展",
};

/** 更新角标：有新版本、未固定的插件数（同一插件多个来源只算一次）。 */
export function marketUpdateCount(catalog: { updates_available?: number } | undefined): number {
  return catalog?.updates_available ?? 0;
}

export function filterMarketItems<T extends MarketFilterItem>(
  items: T[],
  {
    query,
    kind,
    onlyUpdates,
    onlyInstalled,
  }: { query: string; kind: string; onlyUpdates: boolean; onlyInstalled: boolean },
): T[] {
  const needle = query.trim().toLowerCase();
  return items.filter((item) => {
    if (kind !== "all" && item.kind !== kind) return false;
    if (onlyUpdates && !(item.update_available && !item.pinned_version)) return false;
    if (onlyInstalled && !item.installed) return false;
    if (!needle) return true;
    const haystack = [
      item.key,
      item.name.zh,
      item.name.en,
      item.summary,
      item.author,
      ...(item.tags ?? []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}
