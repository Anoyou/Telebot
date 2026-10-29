import type { FeatureInfo } from "@/api/types";
import { pluginSupportsDirectPassthrough } from "@/types/pluginContract";

// 宿主自己的平台能力（不是插件）才有专属配置页；插件一律走通用声明式配置页。
export const FEATURE_CONFIG_PAGE_KEYS = new Set(["scheduler"]);

// W1（0.110.0-beta.2）：这五个插件的旧专属页改为通用声明式页面，旧页面只保留一个版本做对照，
// 下一版连同本名单一起删除。插件还是没有 config_schema 的旧版本（例如 auto_reply 1.0.4）时，
// 配置入口自动落到旧页面，保证功能不缺。
export const LEGACY_CONFIG_PAGE_KEYS = new Set([
  "auto_reply",
  "autorepeat",
  "chatgpt_image",
  "codex_image",
  "game24",
]);

export function isLegacyConfigPageKey(key: string): boolean {
  return LEGACY_CONFIG_PAGE_KEYS.has(key);
}

/** 旧版插件没有声明 config_schema，通用页无从渲染，只能用对照页。 */
export function needsLegacyConfigPage(
  key: string,
  feature?: Pick<FeatureInfo, "config_schema"> | null,
): boolean {
  return isLegacyConfigPageKey(key) && !feature?.config_schema;
}

export function legacyConfigPagePath(aid: number, key: string, search = ""): string {
  return `/accounts/${aid}/features/${key}/legacy${search}`;
}

export type FeatureConfigSource = "account" | "plugins";

export function featureConfigPath(
  aid: number | null | undefined,
  key: string,
  feature?: Pick<FeatureInfo, "config_schema" | "capabilities"> | null,
  options?: { source?: FeatureConfigSource },
): string | null {
  if (!aid || !key) return null;
  const legacy = needsLegacyConfigPage(key, feature);
  if (
    !legacy &&
    !FEATURE_CONFIG_PAGE_KEYS.has(key) &&
    !feature?.config_schema &&
    !pluginSupportsDirectPassthrough(feature?.capabilities)
  ) return null;
  const path = legacy ? legacyConfigPagePath(aid, key) : `/accounts/${aid}/features/${key}`;
  if (!options?.source || options.source === "account") return path;
  const params = new URLSearchParams({ from: options.source });
  return `${path}?${params.toString()}`;
}

export function featureConfigBackTarget(
  aid: number,
  search = "",
): { backLabel: string; backHref: string } {
  const source = new URLSearchParams(search).get("from");
  if (source === "plugins") {
    const params = new URLSearchParams({ account: String(aid) });
    return {
      backLabel: "返回插件中心",
      backHref: `/plugins?${params.toString()}`,
    };
  }
  return {
    backLabel: "返回账号",
    backHref: `/accounts/${aid}?tab=features`,
  };
}

export function formatFeatureVersion(version?: string | null): string {
  const value = (version || "").trim();
  if (!value) return "版本未知";
  return value.startsWith("v") ? value : `v${value}`;
}
