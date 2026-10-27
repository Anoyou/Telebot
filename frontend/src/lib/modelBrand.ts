/**
 * 从模型 ID / Provider 名称推断所属模型家族（公司品牌），用于 UI logo。
 * 映射数据与 iOS 共用：modelBrands.json（iOS 副本由 ios/TelePilotCore/scripts/sync-model-brands.py 同步校验）。
 * 识别顺序：整个模型 ID 匹配 modelRules → OpenRouter 风格 `org/model` 的 org 段 → providerName → unknown（“其他”）。
 */
import brandData from "./modelBrands.json" with { type: "json" };

export type ModelBrandId =
  | "auto" | "openai" | "anthropic" | "deepseek" | "google" | "xai" | "meta" | "mistral" | "qwen" | "moonshot"
  | "zhipu" | "bytedance" | "microsoft" | "amazon" | "cohere" | "perplexity" | "nvidia" | "minimax" | "baichuan"
  | "yi" | "tencent" | "xiaomi" | "stepfun" | "meituan" | "antgroup" | "kuaishou" | "ibm" | "liquid" | "upstage"
  | "writer" | "reka" | "inception" | "rednote" | "arcee" | "sakana" | "poolside" | "thinkingmachines" | "typesafe"
  | "agnes" | "aionlabs" | "nexagi" | "perceptron" | "ollama" | "openrouter" | "unknown";

export type ModelBrand = {
  id: ModelBrandId;
  label: string;
  /** 深色背景下可读的品牌色 */
  accent: string;
};

type Rule = { brand: ModelBrandId; pattern: RegExp };

const BRANDS = Object.fromEntries(
  (brandData.brands as ModelBrand[]).map((brand) => [brand.id, brand]),
) as Record<ModelBrandId, ModelBrand>;

function compile(rules: Array<{ brand: string; pattern: string }>): Rule[] {
  return rules.map((rule) => ({ brand: rule.brand as ModelBrandId, pattern: new RegExp(rule.pattern, "i") }));
}

/** 模型 ID 片段匹配（按优先级从前到后） */
const MODEL_RULES = compile(brandData.modelRules);
/** Provider 名称匹配（ollama 需在 meta/llama 前，避免 Ollama 被 llama 子串误伤） */
const PROVIDER_RULES = compile(brandData.providerRules);

function matchRules(text: string, rules: Rule[]): ModelBrandId | null {
  for (const rule of rules) {
    if (rule.pattern.test(text)) return rule.brand;
  }
  return null;
}

/** OpenRouter / 网关风格 `org/model` 取 org 段 */
function orgSegment(model: string): string | null {
  const slash = model.indexOf("/");
  if (slash <= 0) return null;
  return model.slice(0, slash).trim() || null;
}

export function resolveModelBrand(
  model?: string | null,
  providerName?: string | null,
  opts?: { auto?: boolean },
): ModelBrand {
  if (opts?.auto) return BRANDS.auto;

  const rawModel = (model || "").trim();
  const rawProvider = (providerName || "").trim();

  if (rawModel) {
    const fromModel = matchRules(rawModel, MODEL_RULES);
    if (fromModel) return BRANDS[fromModel];

    const org = orgSegment(rawModel);
    if (org) {
      const fromOrg = matchRules(org, MODEL_RULES) || matchRules(org, PROVIDER_RULES);
      if (fromOrg) return BRANDS[fromOrg];
      // `org/model` 的 org 无法识别时再看最后一段（例如自建网关 `my-gw/gpt-5`）。
      const last = rawModel.slice(rawModel.lastIndexOf("/") + 1);
      const fromLast = matchRules(last, MODEL_RULES);
      if (fromLast) return BRANDS[fromLast];
    }
  }

  if (rawProvider) {
    const fromProvider = matchRules(rawProvider, PROVIDER_RULES);
    if (fromProvider) return BRANDS[fromProvider];
  }

  return BRANDS.unknown;
}

export function modelBrandLabel(brand: ModelBrandId): string {
  return BRANDS[brand]?.label || BRANDS.unknown.label;
}

export function listModelBrands(): ModelBrand[] {
  return Object.values(BRANDS);
}
