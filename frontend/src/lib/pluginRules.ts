// 通用规则页（x-ui-mode=rules）的纯函数：规则 schema、字段显隐、列表摘要、试运行声明。
// 契约见 schemas/plugin-ui-widgets.json（contract_version 2）：x-rule-schema / x-rule-summary /
// x-dry-run / x-ui-visible-if。宿主不再按插件 key 写死规则页。

export interface RuleFieldLike {
  type?: string;
  title?: string;
  default?: unknown;
  enum?: Array<string | number | boolean>;
  enumNames?: string[];
  readOnly?: boolean;
  level?: string;
  "x-ui-hidden"?: boolean;
  "x-ui-widget"?: string;
  "x-ui-visible-if"?: unknown;
  [key: string]: unknown;
}

export interface RuleSchemaLike {
  type?: string;
  properties?: Record<string, RuleFieldLike>;
  required?: string[];
  "x-ui-property-order"?: string[];
  [key: string]: unknown;
}

export interface PluginRulesLayout {
  /** 每条规则 rule.config 的 schema */
  ruleSchema: RuleSchemaLike;
  /** 是否显式声明了 x-rule-schema；未声明时整个 config_schema 都是规则字段（没有账号级表单） */
  explicit: boolean;
}

export type DryRunSampleField = "message" | "chat_type" | "chat_id";

export interface DryRunSpec {
  enabled: boolean;
  sampleFields: DryRunSampleField[];
  title: string;
  description: string;
}

const SAMPLE_FIELDS: DryRunSampleField[] = ["message", "chat_type", "chat_id"];
const DEFAULT_SAMPLE_FIELDS: DryRunSampleField[] = ["message", "chat_id"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function isRulesMode(schema: unknown): boolean {
  return isRecord(schema) && schema["x-ui-mode"] === "rules";
}

export function pluginRulesLayout(schema: unknown): PluginRulesLayout | null {
  if (!isRulesMode(schema)) return null;
  const record = schema as Record<string, unknown>;
  const declared = record["x-rule-schema"];
  if (isRecord(declared) && isRecord(declared.properties)) {
    return { ruleSchema: declared as RuleSchemaLike, explicit: true };
  }
  return { ruleSchema: record as RuleSchemaLike, explicit: false };
}

/** x-ui-visible-if：{field, equals?|in?|not_in?}；声明不合法时一律显示。 */
export function isFieldVisible(field: object | undefined, values: Record<string, unknown>): boolean {
  const cond = (field as { "x-ui-visible-if"?: unknown } | undefined)?.["x-ui-visible-if"];
  if (!isRecord(cond) || typeof cond.field !== "string") return true;
  const actual = values[cond.field];
  const same = (a: unknown, b: unknown) => a === b || (a != null && b != null && String(a) === String(b));
  if ("equals" in cond) return same(actual, cond.equals);
  if (Array.isArray(cond.in)) return cond.in.some((item) => same(actual, item));
  if (Array.isArray(cond.not_in)) return !cond.not_in.some((item) => same(actual, item));
  return true;
}

export function visibleEntries<T extends object>(
  entries: Array<[string, T]>,
  values: Record<string, unknown>,
): Array<[string, T]> {
  return entries.filter(([, field]) => isFieldVisible(field, values));
}

function cloneValue<T>(value: T): T {
  if (value === undefined || value === null || typeof value !== "object") return value;
  return JSON.parse(JSON.stringify(value)) as T;
}

/** 新建规则时的初始值：schema default。 */
export function ruleDefaults(ruleSchema: RuleSchemaLike): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(ruleSchema.properties ?? {})) {
    if (field && "default" in field && field.default !== undefined) out[key] = cloneValue(field.default);
  }
  return out;
}

/** 编辑已有规则：schema default 打底，已保存的值覆盖（保留 schema 之外的旧字段）。 */
export function ruleFormValues(ruleSchema: RuleSchemaLike, config: Record<string, unknown> | null | undefined) {
  return { ...ruleDefaults(ruleSchema), ...cloneValue(config ?? {}) };
}

function isEmptyValue(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/** 必填但为空的可见字段（返回字段标题）。 */
export function missingRequiredFields(ruleSchema: RuleSchemaLike, values: Record<string, unknown>): string[] {
  const props = ruleSchema.properties ?? {};
  return (ruleSchema.required ?? [])
    .filter((key) => props[key] && isFieldVisible(props[key], values) && isEmptyValue(values[key]))
    .map((key) => props[key]?.title || key);
}

/** 列表摘要列：x-rule-summary（最多 3 个），否则规则 schema 的前两个可见字段。 */
export function ruleSummaryKeys(
  schema: unknown,
  layout: PluginRulesLayout,
  orderedKeys: string[],
): string[] {
  const props = layout.ruleSchema.properties ?? {};
  const declared = isRecord(schema) ? schema["x-rule-summary"] : undefined;
  if (Array.isArray(declared)) {
    const keys = declared.filter((key): key is string => typeof key === "string" && key in props);
    if (keys.length > 0) return keys.slice(0, 3);
  }
  return orderedKeys
    .filter((key) => props[key] && !props[key]["x-ui-hidden"] && props[key]["x-ui-widget"] !== "hidden")
    .slice(0, 2);
}

export function formatRuleValue(field: RuleFieldLike | undefined, value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    const text = value.map((item) => (typeof item === "object" ? JSON.stringify(item) : String(item))).join("、");
    return text.length > 60 ? `${text.slice(0, 57)}…（${value.length} 项）` : text;
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (field?.enum && field.enumNames) {
    const index = field.enum.findIndex((item) => String(item) === String(value));
    if (index >= 0 && field.enumNames[index]) return field.enumNames[index];
  }
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  return text.length > 60 ? `${text.slice(0, 57)}…` : text;
}

/** x-dry-run：省略时默认开启（样本消息 + 会话 ID）；false 关闭。 */
export function dryRunSpec(schema: unknown): DryRunSpec {
  const raw = isRecord(schema) ? schema["x-dry-run"] : undefined;
  const base: DryRunSpec = {
    enabled: true,
    sampleFields: DEFAULT_SAMPLE_FIELDS,
    title: "试运行",
    description: "输入一条样例消息，验证规则是否命中；不会真的发送消息。",
  };
  if (raw === false) return { ...base, enabled: false };
  if (!isRecord(raw)) return base;
  const fields = Array.isArray(raw.sample_fields)
    ? raw.sample_fields.filter((item): item is DryRunSampleField => SAMPLE_FIELDS.includes(item as DryRunSampleField))
    : [];
  return {
    enabled: true,
    sampleFields: fields.length > 0 ? fields : base.sampleFields,
    title: typeof raw.title === "string" && raw.title.trim() ? raw.title.trim() : base.title,
    description: typeof raw.description === "string" && raw.description.trim() ? raw.description.trim() : base.description,
  };
}

/** 会话 ID 输入：只保留数字与开头的负号；空串返回 null。 */
export function parseChatId(text: string): number | null {
  const cleaned = text.trim();
  if (!/^-?\d+$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isSafeInteger(value) ? value : null;
}
