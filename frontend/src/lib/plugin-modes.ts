import type { FeatureInfo } from "@/api/types";

export type PluginMode = "rules" | "single" | "schema";

export const PLUGIN_MODE_META: Record<PluginMode, { label: string; plain: string }> = {
  rules: {
    label: "模式 A · 规则驱动",
    plain: "像自动化流水线：先建规则，再按匹配条件触发动作，适合多条规则反复运行。",
  },
  single: {
    label: "模式 B · 单配置对象",
    plain: "像一个工具面板：每个账号只保存一份配置，直接用触发指令或固定入口运行。",
  },
  schema: {
    label: "模式 B · 通用配置页",
    plain: "像一个轻量工具面板：插件声明字段，前端在独立页面生成账号配置表单。",
  },
};

// 平台能力（宿主自己的功能，不是插件）。插件的配置形态一律看 config_schema["x-ui-mode"]，
// 不再按插件 key 兜底（W1，0.110.0-beta.2 起；旧版本插件没有声明时按通用配置页处理）。
const PLATFORM_FEATURE_KEYS = new Set(["scheduler"]);

export function isPlatformFeature(featureOrKey: string | Pick<FeatureInfo, "key" | "config_schema">): boolean {
  if (typeof featureOrKey === "string") return PLATFORM_FEATURE_KEYS.has(featureOrKey);
  return featureOrKey.config_schema?.["x-ui-mode"] === "platform" || PLATFORM_FEATURE_KEYS.has(featureOrKey.key);
}

export function pluginMode(feature: Pick<FeatureInfo, "key" | "config_schema">): PluginMode {
  const declared = feature.config_schema?.["x-ui-mode"];
  if (declared === "rules" || declared === "single" || declared === "schema") {
    return declared;
  }
  return "schema";
}

export function isExperimentalFeature(feature: Pick<FeatureInfo, "experimental">): boolean {
  return feature.experimental;
}
