// 第 21 轮：服务器参数显示工具（Web「全部服务器参数」用；iOS 同规则在 ServerSettingsView.swift）。
import type { SystemSettingMeta } from "../api/types.ts";

/** 按点号路径取值，例如 log_retention.trace_enabled。 */
export function readSettingValue(settings: unknown, key: string): unknown {
  return key.split(".").reduce<unknown>((current, part) => {
    if (current && typeof current === "object" && !Array.isArray(current)) {
      return (current as Record<string, unknown>)[part];
    }
    return undefined;
  }, settings);
}

/** 显示用的当前值：开关「开启 / 关闭」，数字带单位，0 带含义，选项显示中文，列表显示内容。 */
export function formatSettingValue(value: unknown, meta?: Pick<SystemSettingMeta, "unit" | "zero_means" | "options"> | null): string {
  if (value === undefined || value === null) return "未设置";
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (Array.isArray(value)) return value.length ? value.map(String).join("、") : "（空 = 默认）";
  if (typeof value === "object") {
    const size = Object.keys(value as Record<string, unknown>).length;
    return size ? `${size} 项设置` : "（空）";
  }
  if (typeof value === "number") {
    if (value === 0 && meta?.zero_means) return `0（${meta.zero_means}）`;
    return meta?.unit ? `${value} ${meta.unit}` : String(value);
  }
  const text = String(value);
  const option = meta?.options?.find((item) => item.value === text);
  return option?.label ?? text;
}

/** 旧服务器（没有元数据）：把返回值拍平成「键 → 值」，嵌套对象展开一层。 */
export function flattenSettings(settings: unknown): Array<{ key: string; value: unknown }> {
  if (!settings || typeof settings !== "object") return [];
  const rows: Array<{ key: string; value: unknown }> = [];
  for (const [key, value] of Object.entries(settings as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b))) {
    if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
      for (const [sub, inner] of Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b))) {
        rows.push({ key: `${key}.${sub}`, value: inner });
      }
    } else {
      rows.push({ key, value });
    }
  }
  return rows;
}

export function originalName(key: string): string {
  const parts = key.split(".");
  return parts[parts.length - 1];
}
