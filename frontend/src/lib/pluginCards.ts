// 插件原生卡片 DSL（stats / table / kv / list / buttons）的数据整理，与 iOS PluginCardModel 同一套规则。
// 声明见 schemas/plugin-ui-widgets.json 的 cards；组件只负责把这里整理好的模型画出来。
import contract from "./pluginUiWidgets.json" with { type: "json" };

export const CARD_LAYOUT_TYPES = ["stats", "kv", "table", "list", "buttons"] as const;
export type CardLayoutType = (typeof CARD_LAYOUT_TYPES)[number];
export type CardTone = "neutral" | "success" | "warning" | "danger" | "accent";

export const CARD_FAILURE_MESSAGE = contract.cards.failure.message;
export const CARD_RETRY_LABEL = contract.cards.failure.retry;
export const CARD_LAYOUTS_IN_CONTRACT: string[] = contract.cards.layouts.map((item) => item.type);

export type CardBlock = Record<string, unknown> & { type?: string };

export interface CardCell {
  text: string;
  tone: CardTone;
}

export type CardBlockModel =
  | { type: "stats" | "kv"; title?: string; items: Array<{ label: string; text: string; tone: CardTone }> }
  | { type: "table"; title?: string; columns: Array<{ key: string; label: string }>; rows: Array<{ key: string; cells: CardCell[] }>; empty: string }
  | { type: "list"; title?: string; items: Array<{ key: string; title: string; subtitle: string; trailing: string; tone: CardTone }>; empty: string; hidden: boolean }
  | { type: "buttons"; items: Array<{ id: string; label: string; destructive: boolean; confirm: string }> }
  | { type: "unsupported"; layout: string };

const TONES: ReadonlySet<string> = new Set(["neutral", "success", "warning", "danger", "accent"]);

export function valueAtPath(data: unknown, path: unknown): unknown {
  if (typeof path !== "string" || !path) return data;
  let current: unknown = data;
  for (const part of path.split(".")) {
    if (current == null) return undefined;
    if (Array.isArray(current)) {
      const index = Number(part);
      current = Number.isInteger(index) ? current[index] : undefined;
    } else if (typeof current === "object") {
      current = (current as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return current;
}

export function toTone(value: unknown): CardTone {
  return typeof value === "string" && TONES.has(value) ? (value as CardTone) : "neutral";
}

function formatDuration(ms: number): string {
  if (ms >= 10_000) return `${Math.round(ms / 1000)} 秒`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} 秒`;
  return `${Math.round(ms)} 毫秒`;
}

function formatRelative(seconds: number, nowMs: number): string {
  const diff = Math.max(0, Math.round(nowMs / 1000 - seconds));
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86_400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86_400)} 天前`;
}

/** 按契约 formats 格式化；空值显示 "-"。 */
export function formatCardValue(value: unknown, format?: unknown, nowMs: number = Date.now()): string {
  if (value === null || value === undefined || value === "") return "-";
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : Number.NaN;
  switch (format) {
    case "number":
      return Number.isFinite(number) ? number.toLocaleString("zh-CN") : String(value);
    case "percent": {
      if (!Number.isFinite(number)) return String(value);
      const percent = number * 100;
      if (percent === 0) return "0%";
      return percent >= 10 ? `${Math.round(percent)}%` : `${percent.toFixed(1)}%`;
    }
    case "duration_ms":
      return Number.isFinite(number) ? formatDuration(number) : String(value);
    case "datetime":
      if (Number.isFinite(number)) return new Date(number * 1000).toLocaleString("zh-CN", { hour12: false });
      return String(value);
    case "relative_time":
      return Number.isFinite(number) && number > 0 ? formatRelative(number, nowMs) : "-";
    default:
      if (typeof value === "boolean") return value ? "是" : "否";
      if (typeof value === "object") return JSON.stringify(value);
      return String(value);
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : value == null ? fallback : String(value);
}

/** 把一个块声明 + 卡片数据整理成可直接渲染的模型。 */
export function buildCardBlock(block: CardBlock, data: unknown, nowMs: number = Date.now()): CardBlockModel {
  const type = text(block.type);
  const title = typeof block.title === "string" ? block.title : undefined;
  if (type === "stats" || type === "kv") {
    const declared = asArray(block.items);
    const source = declared.length > 0 ? declared : asArray(valueAtPath(data, block.items_path ?? "items"));
    return {
      type,
      title,
      items: source.map((raw) => {
        const item = asRecord(raw);
        const value = "value" in item ? item.value : valueAtPath(data, item.path);
        const unit = typeof item.unit === "string" && item.unit ? ` ${item.unit}` : "";
        const tone = item.tone_path ? valueAtPath(data, item.tone_path) : item.tone;
        return { label: text(item.label), text: `${formatCardValue(value, item.format, nowMs)}${value == null ? "" : unit}`, tone: toTone(tone) };
      }),
    };
  }
  if (type === "table") {
    const rowsRaw = asArray(valueAtPath(data, block.rows_path ?? "rows"));
    let columns = asArray(block.columns).map((raw) => asRecord(raw));
    if (columns.length === 0 && rowsRaw.length > 0) {
      columns = Object.keys(asRecord(rowsRaw[0])).map((key) => ({ key, label: key }));
    }
    const rowKey = text(block.row_key);
    return {
      type,
      title,
      columns: columns.map((column) => ({ key: text(column.key), label: text(column.label, text(column.key)) })),
      rows: rowsRaw.map((raw, index) => {
        const row = asRecord(raw);
        return {
          key: rowKey && row[rowKey] != null ? `${text(row[rowKey])}` : String(index),
          cells: columns.map((column) => ({
            text: formatCardValue(valueAtPath(row, column.key), column.format, nowMs),
            tone: toTone(column.tone_key ? row[text(column.tone_key)] : undefined),
          })),
        };
      }),
      empty: text(block.empty, "暂无数据"),
    };
  }
  if (type === "list") {
    const itemsRaw = asArray(valueAtPath(data, block.items_path ?? "items"));
    const titleKey = text(block.title_key, "title");
    const subtitleKey = text(block.subtitle_key, "subtitle");
    const trailingKey = text(block.trailing_key, "trailing");
    const toneKey = text(block.tone_key, "tone");
    return {
      type,
      title,
      items: itemsRaw.map((raw, index) => {
        const item = asRecord(raw);
        return {
          key: `${index}-${text(item[titleKey])}`,
          title: text(item[titleKey]),
          subtitle: text(item[subtitleKey]),
          trailing: text(item[trailingKey]),
          tone: toTone(item[toneKey]),
        };
      }),
      empty: text(block.empty, "暂无内容"),
      hidden: block.hide_when_empty === true && itemsRaw.length === 0,
    };
  }
  if (type === "buttons") {
    return {
      type,
      items: asArray(block.items).map((raw) => {
        const item = asRecord(raw);
        return {
          id: text(item.id),
          label: text(item.label, text(item.id)),
          destructive: item.style === "destructive",
          confirm: text(item.confirm),
        };
      }).filter((item) => item.id),
    };
  }
  return { type: "unsupported", layout: type };
}
