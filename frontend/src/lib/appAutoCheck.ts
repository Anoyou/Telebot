// 应用自动检查更新的纯逻辑层：预设、间隔换算与角标判定。
// UpdateDialog / TopBar 复用，保证 UI 与规则不会各自漂移。

import type { AppUpdateStatus } from "@/api/types";

/** 预设频率（分钟）。自定义走输入框，超范围由后端 clamp 到 60..10080。 */
export const AUTO_CHECK_PRESETS = [
  { label: "每 6 小时", minutes: 360 },
  { label: "每 12 小时", minutes: 720 },
  { label: "每天", minutes: 1440 },
  { label: "每 3 天", minutes: 4320 },
  { label: "每周", minutes: 10080 },
] as const;

export const AUTO_CHECK_MIN_MINUTES = 60;
export const AUTO_CHECK_MAX_MINUTES = 10080;

/** 分钟 → 小时（最多两位小数，避免浮点尾巴）。 */
export function minutesToHours(minutes: number): number {
  return Math.round((minutes / 60) * 100) / 100;
}

/** 判断某个间隔是否命中预设；未命中时 UI 显示"自定义"。 */
export function isAutoCheckPreset(minutes: number | null | undefined): boolean {
  if (typeof minutes !== "number") return false;
  return AUTO_CHECK_PRESETS.some((preset) => preset.minutes === minutes);
}

/** 自定义小时输入是否可用：正数且折算后在允许区间内。 */
export function resolveCustomIntervalMinutes(
  raw: string,
): { ok: true; minutes: number } | { ok: false; error: string } {
  const hours = Number(raw.trim());
  if (raw.trim() === "" || !Number.isFinite(hours) || hours <= 0) {
    return { ok: false, error: "请输入大于 0 的小时数。" };
  }
  const minutes = Math.round(hours * 60);
  if (minutes < AUTO_CHECK_MIN_MINUTES || minutes > AUTO_CHECK_MAX_MINUTES) {
    return {
      ok: false,
      error: `间隔需在 ${AUTO_CHECK_MIN_MINUTES / 60} 小时到 ${AUTO_CHECK_MAX_MINUTES / 60} 小时之间。`,
    };
  }
  return { ok: true, minutes };
}

/** 顶栏检查更新按钮的角标条件：最近一次检查真的发现了新版本。 */
export function shouldShowUpdateBadge(
  status: Pick<AppUpdateStatus, "last_check"> | null | undefined,
): boolean {
  if (!status?.last_check) return false;
  if (status.last_check.can_check === false) return false;
  return status.last_check.has_update === true;
}

/** 角标标题文案。 */
export function updateBadgeLabel(hasUpdate: boolean): string {
  return hasUpdate ? "检查更新（发现新版本）" : "检查更新";
}

/** 最近检查时间展示（本地时区，24 小时制）；非法时间返回 null。 */
export function formatLastCheck(iso: string | undefined | null): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}
