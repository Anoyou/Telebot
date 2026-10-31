// 第 21 轮：运行树只在插件页「运行树」视图（/plugins?view=tree），服务器设置里不再保留。
//
// 纯函数（不依赖别名导入与 React），便于 node --test 直接测试。旧书签、旧深链、iOS 推送里的运行树地址
// 统一跳到新位置：
//  - /settings?tab=tree | runtime | runtime-tree | platform-tree | capability-tree（早期「看树」在服务器设置里）
//  - /platform-tree、/runtime-tree、/runtime、/plugins/tree、/plugins/runtime

export const RUNTIME_TREE_PATH = "/plugins?view=tree";

/** 早期服务器设置里运行树分页用过的 ?tab= 值。 */
export const LEGACY_SETTINGS_TREE_TABS = [
  "tree",
  "runtime",
  "runtime-tree",
  "platform-tree",
  "capability-tree",
] as const;

/** 不再存在的运行树独立路由（相对根路径，不带开头斜杠，供 <Route path> 使用）。 */
export const LEGACY_RUNTIME_TREE_ROUTES = [
  "platform-tree",
  "runtime-tree",
  "runtime",
  "plugins/tree",
  "plugins/runtime",
] as const;

const SETTINGS_TREE_TABS = new Set<string>(LEGACY_SETTINGS_TREE_TABS);

/** 服务器设置的 ?tab= 指向旧运行树时，返回新地址；否则 null（照常打开服务器设置）。 */
export function legacySettingsTreeRedirect(search: string): string | null {
  const tab = new URLSearchParams(search).get("tab");
  return tab && SETTINGS_TREE_TABS.has(tab) ? RUNTIME_TREE_PATH : null;
}

/** 插件页 ?view= → 视图；只有 tree 打开运行树，其它（含缺省）是卡片。 */
export function pluginViewFromSearch(search: string): "cards" | "tree" {
  return new URLSearchParams(search).get("view") === "tree" ? "tree" : "cards";
}
