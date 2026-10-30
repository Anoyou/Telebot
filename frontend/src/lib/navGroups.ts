// 第 20 轮 Web 信息架构第二批：侧栏三组 + 共用「常用入口」。
//
// 纯函数（不依赖别名导入与 React），便于 node --test 直接测试。
//
//  - 常用：用户自选的一级入口（默认 插件 · 交互 · 概览），同时决定桌面侧栏第一组和手机底栏。
//  - 管理：账号 · AI · 指令与任务 · 资金台账 · 日志；没被选进「常用」的 概览/插件/交互 也回到这里。
//  - 工具与设置：Webhook · 命中调试 · 服务器设置（不可选进常用，组可折叠）。
//  - Agent 不参与分组：桌面侧栏顶部固定一行、手机底栏右侧独立圆钮，「待确认」数挂在它上面。
//
// 模块被关闭时对应入口直接隐藏（常用组少一格），由调用方先按能力过滤再传入。

export type NavGroupKey = "primary" | "manage" | "tools";

/** 可以选进「常用」的入口（一级 + 二级）。 */
export const PRIMARY_NAV_ELIGIBLE = [
  "/overview",
  "/plugins",
  "/interaction",
  "/accounts",
  "/ai",
  "/operations",
  "/ledger",
  "/logs",
] as const;

/** 「管理」组默认顺序；不在常用里的 概览/插件/交互 排在最前。 */
export const MANAGE_NAV_DEFAULT_ORDER = [
  "/overview",
  "/plugins",
  "/interaction",
  "/accounts",
  "/ai",
  "/operations",
  "/ledger",
  "/logs",
] as const;

/** 「工具与设置」组（三级，低频）。 */
export const TOOLS_NAV_DEFAULT_ORDER = ["/webhooks", "/dispatch-debug", "/settings"] as const;

/** Web 默认常用入口：插件打头（落地页仍是插件，见 round19 方案 2.3）。 */
export const DEFAULT_PRIMARY_NAV = ["/plugins", "/interaction", "/overview"];

export const PRIMARY_NAV_MAX = 4;

export const NAV_GROUP_LABELS: Record<NavGroupKey, string> = {
  primary: "常用",
  manage: "管理",
  tools: "工具与设置",
};

const ELIGIBLE = new Set<string>(PRIMARY_NAV_ELIGIBLE);

/** 清洗保存的常用入口：只留可选项、去重、最多 4 个；为空时回到默认。 */
export function normalizePrimaryNav(saved: readonly string[] | null | undefined): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const path of saved ?? []) {
    if (!ELIGIBLE.has(path) || seen.has(path)) continue;
    seen.add(path);
    result.push(path);
    if (result.length >= PRIMARY_NAV_MAX) break;
  }
  return result.length ? result : [...DEFAULT_PRIMARY_NAV];
}

/** 按偏好顺序排序；偏好里没有的保持默认相对顺序排在后面。 */
export function sortByPreference(paths: readonly string[], preferred: readonly string[] | null | undefined): string[] {
  if (!preferred?.length) return [...paths];
  const positions = new Map(preferred.map((path, index) => [path, index]));
  return paths
    .map((path, index) => ({ path, index }))
    .sort((left, right) => {
      const l = positions.get(left.path);
      const r = positions.get(right.path);
      if (l == null && r == null) return left.index - right.index;
      if (l == null) return 1;
      if (r == null) return -1;
      return l - r;
    })
    .map((item) => item.path);
}

export interface NavGroupInput {
  /** 按能力过滤后仍可见的入口路径。 */
  visible: readonly string[];
  /** 常用入口（ui_preferences.primary_nav）。 */
  primaryNav?: readonly string[] | null;
  /** 组内顺序（ui_preferences.sidebar_order），只影响「管理」「工具与设置」组内排序。 */
  sidebarOrder?: readonly string[] | null;
}

export type NavGroups = Record<NavGroupKey, string[]>;

/** 把可见入口分成三组。常用组按常用入口顺序；其余组按 sidebar_order 组内排序。 */
export function groupNavPaths({ visible, primaryNav, sidebarOrder }: NavGroupInput): NavGroups {
  const visibleSet = new Set(visible);
  const primaryAll = normalizePrimaryNav(primaryNav);
  const primarySet = new Set(primaryAll);
  const primary = primaryAll.filter((path) => visibleSet.has(path));
  const manage = sortByPreference(
    MANAGE_NAV_DEFAULT_ORDER.filter((path) => !primarySet.has(path) && visibleSet.has(path)),
    sidebarOrder,
  );
  const tools = sortByPreference(
    TOOLS_NAV_DEFAULT_ORDER.filter((path) => visibleSet.has(path)),
    sidebarOrder,
  );
  return { primary, manage, tools };
}

/** 组内拖动：把 path 放到 target 前面；不同组之间不允许拖动，返回 null。 */
export function reorderWithinGroup(group: readonly string[], path: string, target: string): string[] | null {
  if (path === target) return null;
  if (!group.includes(path) || !group.includes(target)) return null;
  const next = group.filter((item) => item !== path);
  next.splice(next.indexOf(target), 0, path);
  return next;
}

/** 把一组的新顺序合并回完整的 sidebar_order（保留其它组的相对顺序）。 */
export function mergeGroupOrder(
  fullOrder: readonly string[],
  groupOrder: readonly string[],
): string[] {
  const groupSet = new Set(groupOrder);
  const queue = [...groupOrder];
  return fullOrder.map((path) => (groupSet.has(path) ? queue.shift() ?? path : path));
}

/** 判断某路径属于哪个组（未知路径返回 null）。 */
export function navGroupOf(path: string, primaryNav?: readonly string[] | null): NavGroupKey | null {
  if (normalizePrimaryNav(primaryNav).includes(path)) return "primary";
  if ((MANAGE_NAV_DEFAULT_ORDER as readonly string[]).includes(path)) return "manage";
  if ((TOOLS_NAV_DEFAULT_ORDER as readonly string[]).includes(path)) return "tools";
  return null;
}
