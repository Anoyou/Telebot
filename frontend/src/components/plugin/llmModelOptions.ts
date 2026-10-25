/**
 * 插件配置 `llm-model-select` 的统一模型列表：列出所有 Provider 下已启用的模型，
 * 选中后同时写回模型字段与 `x-ui-provider-field` 指向的 Provider 字段。
 *
 * 与 iOS `PluginSelectOptions.unifiedModelOptions` 保持同一套规则（同一份测试用例）。
 */

export interface LLMModelLike {
  id: string;
  label?: string | null;
  enabled?: boolean | null;
}

export interface LLMProviderLike {
  id: number | string;
  name: string;
  provider?: string | null;
  models?: Array<LLMModelLike | string> | null;
}

export interface UnifiedModelOption {
  /** 选项唯一值（Provider id + 模型 id），只在控件内部使用。 */
  key: string;
  label: string;
  /** Provider 分组标题（optgroup / Section）。 */
  group: string;
  providerValue: string;
  modelValue: string;
}

export interface UnifiedModelSelection {
  options: UnifiedModelOption[];
  /** 当前选中的选项 key；`""` 表示自动路由（不指定）。 */
  selectedKey: string;
  /** 当前保存值不在列表里时的“当前值：…”选项。 */
  legacy: UnifiedModelOption | null;
}

export const AUTO_ROUTE_KEY = "";
export const AUTO_ROUTE_LABEL = "自动路由（不指定）";
const KEY_SEPARATOR = "\u001f";

export function modelMatchesModality(modelId: string, modelModality?: string | null): boolean {
  if ((modelModality || "").trim().toLowerCase() !== "text") return true;
  const normalized = modelId.trim().toLowerCase();
  return !(normalized.startsWith("gpt-image-") || normalized.startsWith("dall-e-") || normalized.includes("image"));
}

export function findProviderBySelector<T extends LLMProviderLike>(providers: T[] | undefined | null, selector: string): T | null {
  const raw = selector.trim();
  if (!raw) return null;
  const lowered = raw.toLowerCase();
  return (
    providers?.find(
      (provider) =>
        String(provider.id) === raw ||
        provider.name.toLowerCase() === lowered ||
        String(provider.provider ?? "").toLowerCase() === lowered,
    ) ?? null
  );
}

/** 单个 Provider 下已启用、符合模态的模型（去重，保留原顺序）。 */
export function enabledModelRows(provider: LLMProviderLike, modelModality?: string | null): Array<{ value: string; label: string }> {
  const seen = new Set<string>();
  const rows: Array<{ value: string; label: string }> = [];
  for (const item of provider.models ?? []) {
    // 与后端一致：对象形式的模型必须显式 enabled === true；纯字符串视为已启用。
    const model = typeof item === "string" ? { id: item, enabled: true } : item;
    if (model.enabled !== true) continue;
    const value = String(model.id ?? "").trim();
    if (!value || seen.has(value) || !modelMatchesModality(value, modelModality)) continue;
    seen.add(value);
    const label = model.label && model.label !== model.id ? `${model.label}（${value}）` : value;
    rows.push({ value, label });
  }
  return rows;
}

export function unifiedModelKey(providerValue: string, modelValue: string): string {
  return `${providerValue}${KEY_SEPARATOR}${modelValue}`;
}

/**
 * 构建统一列表：按 Provider（id 升序，与后端列表一致）分组，标签“Provider 名 · 模型”。
 *
 * - 模型与 Provider 都为空 → 选中“自动路由（不指定）”；
 * - Provider 有值、模型在该 Provider 已启用列表里 → 选中对应项；
 * - 其余组合（模型已停用 / Provider 已删除 / 只填了 Provider / 只填了模型）→ 生成
 *   “当前值：…”选项，如实展示，不静默改写。
 */
export function buildUnifiedModelSelection(
  providers: LLMProviderLike[] | undefined | null,
  providerValueRaw: unknown,
  modelValueRaw: unknown,
  modelModality?: string | null,
): UnifiedModelSelection {
  const options: UnifiedModelOption[] = [];
  const sorted = [...(providers ?? [])].sort((a, b) => Number(a.id) - Number(b.id) || String(a.id).localeCompare(String(b.id)));
  for (const provider of sorted) {
    const providerValue = String(provider.id);
    for (const row of enabledModelRows(provider, modelModality)) {
      options.push({
        key: unifiedModelKey(providerValue, row.value),
        label: `${provider.name} · ${row.label}`,
        group: provider.name,
        providerValue,
        modelValue: row.value,
      });
    }
  }
  const providerText = textValue(providerValueRaw);
  const modelText = textValue(modelValueRaw);
  if (!providerText && !modelText) {
    return { options, selectedKey: AUTO_ROUTE_KEY, legacy: null };
  }
  const provider = findProviderBySelector(sorted, providerText);
  if (provider && modelText) {
    const match = options.find((option) => option.providerValue === String(provider.id) && option.modelValue === modelText);
    if (match) return { options, selectedKey: match.key, legacy: null };
  }
  const providerLabel = provider?.name ?? providerText;
  let label: string;
  if (providerLabel && modelText) label = `当前值：${providerLabel} · ${modelText}`;
  else if (providerLabel) label = `当前值：${providerLabel} · 默认模型`;
  else label = `当前值：${modelText}（未固定 Provider）`;
  const legacy: UnifiedModelOption = {
    key: `legacy${KEY_SEPARATOR}${providerText}${KEY_SEPARATOR}${modelText}`,
    label,
    group: "",
    providerValue: providerText,
    modelValue: modelText,
  };
  return { options, selectedKey: legacy.key, legacy };
}

/** 把选中的 key 解析成要写回的（Provider 值, 模型值）。未知 key 返回 null（保持不变）。 */
export function resolveUnifiedModelKey(selection: UnifiedModelSelection, key: string): { provider: string; model: string } | null {
  if (key === AUTO_ROUTE_KEY) return { provider: "", model: "" };
  const option = selection.options.find((item) => item.key === key) ?? (selection.legacy?.key === key ? selection.legacy : null);
  if (!option) return null;
  return { provider: option.providerValue, model: option.modelValue };
}

/** 分组后的选项，供 `<optgroup>` 渲染。 */
export function groupUnifiedOptions(options: UnifiedModelOption[]): Array<{ group: string; options: UnifiedModelOption[] }> {
  const groups: Array<{ group: string; options: UnifiedModelOption[] }> = [];
  for (const option of options) {
    const last = groups[groups.length - 1];
    if (last && last.group === option.group) last.options.push(option);
    else groups.push({ group: option.group, options: [option] });
  }
  return groups;
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}
