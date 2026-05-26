import type { AccountFeatureItem } from "@/api/types";

type FeatureStatusLike =
  | Pick<AccountFeatureItem, "enabled" | "state" | "last_error">
  | null
  | undefined;

export function featureSwitchText(feature: FeatureStatusLike) {
  return feature?.enabled ? "已启用" : "未启用";
}

export function featureRuntimeText(feature: FeatureStatusLike) {
  if (!feature) return "未登记";
  if (!feature.enabled) return "已停用";
  if (feature.state === "active") return "运行中";
  if (feature.state === "failed") return "异常";
  return "等待 worker 生效";
}

