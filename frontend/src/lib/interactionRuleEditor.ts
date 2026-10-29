/**
 * 交互规则编辑器的插件参数区（W4，0.112.0 起）。
 *
 * 规则编辑器不再写死任何插件的默认值、启动词或示例：参数区的字段、默认值、启动词提示都来自
 * 插件自己的声明 —— 入口 `input_schema`、`entry_policy.default_start_keywords`，以及 0.112.0 新增的
 * `plugin.json` `ui.rule_editor.entries[入口 key]`（schema / defaults / 启动词 / 提示）。
 * 旧服务器或没声明 `ui.rule_editor` 的旧插件：只用 `input_schema`，行为与之前相同。
 */
import type { FeatureInteractionEntry, FeatureRuleEditorEntry } from "../api/types";

export type RuleEditorSpecMap = Record<string, FeatureRuleEditorEntry> | undefined | null;

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function ruleEditorSpecFor(
  ruleEditor: { entries?: Record<string, FeatureRuleEditorEntry> } | null | undefined,
  entryKey: string,
): FeatureRuleEditorEntry | undefined {
  const entries = ruleEditor?.entries;
  if (!isObject(entries)) return undefined;
  const spec = (entries as Record<string, FeatureRuleEditorEntry>)[entryKey];
  return isObject(spec) ? spec : undefined;
}

/**
 * 把 `ui.rule_editor` 的 schema / defaults 合并进入口的 `input_schema`，得到编辑器实际使用的入口。
 * - 没有 spec：原样返回（旧插件 / 旧服务器路径）；
 * - spec.schema.properties 追加或覆盖同名字段的展示定义（不删除 input_schema 已有字段，奖金等宿主字段识别不变）；
 * - spec.defaults 写进对应字段的 `default`。
 */
export function effectiveInteractionEntry(
  entry: FeatureInteractionEntry,
  spec?: FeatureRuleEditorEntry,
): FeatureInteractionEntry {
  if (!spec) return entry;
  const base = isObject(entry.input_schema) ? entry.input_schema : null;
  const baseProps = base && isObject(base.properties) ? (base.properties as Record<string, unknown>) : {};
  const extraProps = isObject(spec.schema) && isObject(spec.schema.properties)
    ? (spec.schema.properties as Record<string, unknown>)
    : {};
  const defaults = isObject(spec.defaults) ? spec.defaults : {};
  if (!Object.keys(extraProps).length && !Object.keys(defaults).length) return entry;
  const properties: Record<string, unknown> = { ...baseProps };
  for (const [key, field] of Object.entries(extraProps)) {
    properties[key] = isObject(baseProps[key]) && isObject(field)
      ? { ...(baseProps[key] as Record<string, unknown>), ...field }
      : field;
  }
  for (const [key, value] of Object.entries(defaults)) {
    if (isObject(properties[key])) {
      properties[key] = { ...(properties[key] as Record<string, unknown>), default: value };
    }
  }
  return {
    ...entry,
    input_schema: { ...(base ?? {}), type: "object", properties },
  };
}

/** 插件声明的默认启动词：`ui.rule_editor` 优先，其次 `entry_policy.default_start_keywords`。 */
export function declaredStartKeywords(entry?: FeatureInteractionEntry, spec?: FeatureRuleEditorEntry): string[] {
  const fromSpec = Array.isArray(spec?.default_start_keywords) ? spec.default_start_keywords : [];
  const fromPolicy = Array.isArray(entry?.entry_policy?.default_start_keywords)
    ? entry.entry_policy.default_start_keywords
    : [];
  const list = fromSpec.length ? fromSpec : fromPolicy;
  return list.map((item) => String(item).trim()).filter(Boolean);
}

export const GENERIC_START_KEYWORDS_PLACEHOLDER = "每行一个，例如：开局";

/** 启动关键词输入框的占位提示：插件声明 → 插件默认启动词 → 通用提示（不再写死任何插件的启动词）。 */
export function startKeywordsPlaceholder(entry?: FeatureInteractionEntry, spec?: FeatureRuleEditorEntry): string {
  const explicit = String(spec?.start_keywords_placeholder || "").trim();
  if (explicit) return explicit;
  const keywords = declaredStartKeywords(entry, spec);
  return keywords.length ? keywords.join("\n") : GENERIC_START_KEYWORDS_PLACEHOLDER;
}
