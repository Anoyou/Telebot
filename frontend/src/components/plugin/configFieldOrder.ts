/**
 * 插件配置表单的字段顺序与可选字段处理。
 *
 * - 顺序：服务器在写库前把 `properties` 的声明顺序记在 `x-ui-property-order`
 *   （PostgreSQL JSONB 不保留对象键顺序）。字段先按这份顺序排列，再按 `x-ui-order`
 *   稳定排序；都没有声明时保持服务器返回的顺序，不按字母排序。
 * - 可选字段：清空一个不接受 `null` 的字段时不提交它，由服务器按 schema 默认值处理。
 *
 * 与 iOS `PluginConfigLayout` 保持同一套规则。
 */

export const PROPERTY_ORDER_KEY = "x-ui-property-order";

type FieldLike = {
  type?: unknown;
  enum?: unknown;
  anyOf?: unknown;
  oneOf?: unknown;
  const?: unknown;
  nullable?: unknown;
  default?: unknown;
  "x-ui-order"?: unknown;
};

/** 按 `x-ui-property-order` 还原声明顺序；未列出的字段保持原顺序排在后面。 */
export function orderedPropertyEntries<T>(
  properties: Record<string, T> | undefined | null,
  order?: unknown,
): Array<[string, T]> {
  const entries = Object.entries(properties ?? {});
  if (!Array.isArray(order) || order.length === 0) return entries;
  const rank = new Map<string, number>();
  order.forEach((key, index) => {
    if (typeof key === "string" && !rank.has(key)) rank.set(key, index);
  });
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      const ra = rank.get(a.entry[0]) ?? order.length + a.index;
      const rb = rank.get(b.entry[0]) ?? order.length + b.index;
      return ra - rb;
    })
    .map(({ entry }) => entry);
}

export function configFieldOrder(field: FieldLike | undefined): number {
  const value = field?.["x-ui-order"];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** 先按声明顺序，再按 `x-ui-order` 稳定排序（与 Web 分组、iOS 表单一致）。 */
export function sortedConfigEntries<T extends FieldLike>(
  properties: Record<string, T> | undefined | null,
  order?: unknown,
): Array<[string, T]> {
  return orderedPropertyEntries(properties, order)
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => configFieldOrder(a.entry[1]) - configFieldOrder(b.entry[1]) || a.index - b.index)
    .map(({ entry }) => entry);
}

export function fieldAllowsNull(field: FieldLike | undefined): boolean {
  if (!field) return true;
  const hasTypeConstraint = ["type", "enum", "anyOf", "oneOf", "const"].some((key) => key in field);
  if (!hasTypeConstraint) return true;
  const type = field.type;
  if (type === "null" || (Array.isArray(type) && type.includes("null"))) return true;
  if (field.nullable === true) return true;
  if (Array.isArray(field.enum) && field.enum.includes(null)) return true;
  for (const options of [field.anyOf, field.oneOf]) {
    if (Array.isArray(options) && options.some((option) => fieldAllowsNull(option as FieldLike))) return true;
  }
  return false;
}

/** 去掉被清空成 null、但字段不接受 null 的项：不提交，由服务器使用默认值。 */
export function withoutClearedValues(
  values: Record<string, unknown>,
  properties: Record<string, FieldLike> | undefined | null,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    const field = properties?.[key];
    if (value === null && field && !fieldAllowsNull(field)) continue;
    out[key] = value;
  }
  return out;
}
