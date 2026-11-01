// 插件市场的纯函数（不依赖网络与别名导入，便于 node --test 直接测试）。

export type MarketFilterItem = {
  key: string;
  kind: string;
  name: { zh: string; en: string };
  summary?: string;
  description?: string | null;
  author?: string;
  tags?: string[];
  commands?: string[];
  installed?: unknown;
  update_available?: boolean;
  pinned_version?: string | null;
};

export const TRUST_LABELS: Record<string, string> = {
  official: "官方",
  community: "社区",
  unreviewed: "未审核",
};

/** 信任档位的大白话说明（详情页显示）。 */
export const TRUST_DESCRIPTIONS: Record<string, string> = {
  official: "TelePilot 官方插件库发布，随服务器版本做过兼容检查。",
  community: "来自你添加的社区来源，由来源维护者负责，官方没有审核。",
  unreviewed: "没有人审核过这个插件的代码。插件在服务器里运行、能用它声明的权限，安装前请确认来源可信。",
};

/** 通道名称（索引可以写别的名字，未知的原样显示）。 */
export const CHANNEL_LABELS: Record<string, string> = {
  stable: "稳定版",
  beta: "测试版",
};

export function channelLabel(name: string | null | undefined): string {
  if (!name) return "";
  return CHANNEL_LABELS[name] ?? name;
}

/** 插件权限的大白话说明（未知权限原样显示 key）。 */
export const PERMISSION_DESCRIPTIONS: Record<string, string> = {
  send_message: "以你的账号或 Bot 发消息",
  read_chat: "读取它被启用的群里的消息",
  edit_message: "编辑它自己发出的消息",
  delete_message: "删除消息（如自动清理指令和提示）",
  payout: "按游戏规则派发群内游戏币（如魔力值）",
  send_file: "发送图片或文件",
  external_http: "访问外部网站（只限它声明的域名）",
  resolve_entity: "查询用户或群的名字与 ID",
  ai_text: "调用你配置的 AI 模型（会消耗 AI 额度）",
  ai_vision: "调用 AI 识别图片（会消耗 AI 额度）",
  ai: "调用你配置的 AI 模型（会消耗 AI 额度）",
  moderate_chat: "群管理操作（禁言、踢人等）",
  agent_skills: "读取 Agent Skills",
  click_bot_button: "替你点击其它 Bot 消息上的按钮",
};

export function describePermission(permission: string): string {
  return PERMISSION_DESCRIPTIONS[permission] ?? permission;
}

/** 使用说明里的 {prefix} 换成指令前缀（默认 ,）。 */
export function renderUsage(text: string | null | undefined, prefix = ","): string {
  return String(text ?? "").split("{prefix}").join(prefix);
}

/** 分类标签：每个类型的插件数，按固定顺序（未知类型排最后）。 */
export function kindCounts(items: Array<{ kind: string }>): Array<{ kind: string; count: number }> {
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1);
  const order = Object.keys(KIND_LABELS);
  return [...counts.entries()]
    .map(([kind, count]) => ({ kind, count }))
    .sort((a, b) => {
      const ia = order.indexOf(a.kind);
      const ib = order.indexOf(b.kind);
      return (ia < 0 ? order.length : ia) - (ib < 0 ? order.length : ib) || a.kind.localeCompare(b.kind);
    });
}

type ChannelInfo = { name: string; ref?: string | null; version?: string | null };

/** 服务器 0.117.0+ 才返回 available_channels；旧服务器没有这个字段时隐藏通道相关界面。 */
export function marketChannels(item: { available_channels?: ChannelInfo[] | null }): ChannelInfo[] {
  return Array.isArray(item.available_channels) ? item.available_channels : [];
}

export function hasMultipleChannels(item: { available_channels?: ChannelInfo[] | null }): boolean {
  return marketChannels(item).length > 1;
}

/** 切换通道是否会降到更低的版本（只比较纯数字主版本段；看不出来时返回 false）。 */
export function isChannelDowngrade(installed: string | null | undefined, target: string | null | undefined): boolean {
  if (!installed || !target) return false;
  const parse = (v: string) => {
    const [core, pre] = v.split("-", 2);
    return { nums: core.split(".").map((n) => Number.parseInt(n, 10) || 0), pre: pre ?? null };
  };
  const a = parse(installed);
  const b = parse(target);
  for (let i = 0; i < Math.max(a.nums.length, b.nums.length); i += 1) {
    const x = a.nums[i] ?? 0;
    const y = b.nums[i] ?? 0;
    if (x !== y) return y < x;
  }
  if (a.pre === b.pre) return false;
  if (a.pre === null) return true; // 1.0.0 → 1.0.0-beta.1 是降级
  if (b.pre === null) return false;
  return b.pre < a.pre;
}

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
      item.description ?? "",
      item.author,
      ...(item.tags ?? []),
      ...(item.commands ?? []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
}
