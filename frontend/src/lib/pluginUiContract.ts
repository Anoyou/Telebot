// 插件声明式 UI 契约（规范来源 schemas/plugin-ui-widgets.json，本目录 pluginUiWidgets.json 是同步副本）。
// ConfigDialog 用 resolveConfigWidget 决定每个字段渲染哪个控件；单测遍历契约里的每个控件，
// 确认解析结果一致、且 Web 实现了全部控件。iOS 的 PluginFieldWidget 用同一份契约做同样的校验。
import contract from "./pluginUiWidgets.json" with { type: "json" };

export const PLUGIN_UI_CONTRACT = contract;

/** Web 已实现的控件（与契约 widgets[].id 一一对应，单测校验）。 */
export const CONFIG_WIDGET_IDS = [
  "hidden",
  "preview",
  "placeholders",
  "readonly",
  "llm-provider-select",
  "llm-model-select",
  "dynamic-select",
  "config-list",
  "agent-skill-multi-select",
  "allowed-peer-multi-select",
  "multi-select",
  "list-select",
  "secret-list",
  "select",
  "switch",
  "number",
  "object",
  "json-object",
  "json-array",
  "string-list",
  "secret",
  "textarea",
  "text",
] as const;

export type ConfigWidgetId = (typeof CONFIG_WIDGET_IDS)[number] | "unknown";

/** 契约列出的显式 x-ui-widget 取值。 */
export const KNOWN_EXPLICIT_WIDGETS: ReadonlySet<string> = new Set(
  contract.widgets.map((item) => item.x_ui_widget).filter((value): value is string => Boolean(value)),
);

export interface ContractFieldSchema {
  type?: string | string[];
  format?: string;
  enum?: unknown[];
  items?: ContractFieldSchema;
  properties?: Record<string, ContractFieldSchema>;
  readOnly?: boolean;
  writeOnly?: boolean;
  [key: string]: unknown;
}

const SENSITIVE_KEY = /(^|_)(api_key|access_token|auth_token|bot_token|token|tokens|secret|password|passwd|pwd)$/i;
const MULTILINE_KEY = /message|text|prompt|content/i;

export function isSensitiveFieldKey(key: string): boolean {
  return SENSITIVE_KEY.test(key);
}

export function isPreviewFieldKey(key: string): boolean {
  return key === "template_preview" || /_preview$/i.test(key);
}

export function isPlaceholderFieldKey(key: string): boolean {
  return key === "template_placeholders";
}

export function isTemplateFieldKey(key: string): boolean {
  return key === "message_template" || /_template$/i.test(key);
}

function primaryType(field: ContractFieldSchema): string {
  const raw = field.type;
  if (Array.isArray(raw)) return String(raw.find((item) => item !== "null") ?? "");
  return String(raw ?? "");
}

/**
 * 按契约的 resolution_order 解析字段控件。顺序与 iOS PluginFieldWidget.resolve 一致：
 * 显式 x-ui-widget 不认识时返回 "unknown"（只读并提示），不退化成通用编辑器。
 */
export function resolveConfigWidget(key: string, field: ContractFieldSchema): ConfigWidgetId {
  const widget = typeof field["x-ui-widget"] === "string" ? String(field["x-ui-widget"]).trim() : "";
  const type = primaryType(field);
  if (field["x-ui-hidden"] === true) return "hidden";
  if (isPreviewFieldKey(key)) return "preview";
  if (isPlaceholderFieldKey(key)) return "placeholders";
  if (field.readOnly === true) return "readonly";
  if (widget && !KNOWN_EXPLICIT_WIDGETS.has(widget)) return "unknown";
  if (widget === "llm-provider-select") return "llm-provider-select";
  if (widget === "llm-model-select") return "llm-model-select";
  if (widget === "dynamic-select") return "dynamic-select";
  if (type === "array" && widget === "config-list") return "config-list";
  if (type === "array" && widget === "agent-skill-multi-select") return "agent-skill-multi-select";
  if (type === "array" && widget === "allowed-peer-multi-select") return "allowed-peer-multi-select";
  if (type === "array" && (widget === "multi-select" || (field.items?.enum?.length ?? 0) > 0 || (field.enum?.length ?? 0) > 0)) {
    return "multi-select";
  }
  if (type === "array" && field["x-secret-list"]) return "secret-list";
  if (Array.isArray(field.enum) && field.enum.length > 0) return widget === "list-select" ? "list-select" : "select";
  if (type === "boolean") return "switch";
  if (type === "integer" || type === "number") return "number";
  if (type === "object") return field.properties && Object.keys(field.properties).length > 0 ? "object" : "json-object";
  if (type === "array") return primaryType(field.items ?? {}) === "object" ? "json-array" : "string-list";
  if (isSensitiveFieldKey(key) || field.format === "password" || field.writeOnly === true) return "secret";
  if (field.format === "textarea" || widget === "textarea" || isTemplateFieldKey(key) || MULTILINE_KEY.test(key)) {
    return "textarea";
  }
  return "text";
}

export const UNKNOWN_WIDGET_MESSAGE = contract.unknown_widget.message;
