export const FEATURE_CONFIG_PAGE_KEYS = new Set([
  "auto_reply",
  "autorepeat",
  "codex_image",
  "forward",
  "scheduler",
  "game24",
]);

export function featureConfigPath(
  aid: number | null | undefined,
  key: string,
): string | null {
  if (!aid || !FEATURE_CONFIG_PAGE_KEYS.has(key)) return null;
  return `/accounts/${aid}/features/${key}`;
}
