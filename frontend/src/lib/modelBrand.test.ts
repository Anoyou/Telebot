import assert from "node:assert/strict";
import test from "node:test";

import { resolveModelBrand } from "./modelBrand.ts";

test("按模型 ID 识别主流公司", () => {
  assert.equal(resolveModelBrand("deepseek-v4-flash").id, "deepseek");
  assert.equal(resolveModelBrand("deepseek-ai/deepseek-chat").id, "deepseek");
  assert.equal(resolveModelBrand("claude-sonnet-4-5").id, "anthropic");
  assert.equal(resolveModelBrand("gpt-5.4").id, "openai");
  assert.equal(resolveModelBrand("o3-mini").id, "openai");
  assert.equal(resolveModelBrand("gemini-2.5-pro").id, "google");
  assert.equal(resolveModelBrand("grok-3").id, "xai");
  assert.equal(resolveModelBrand("meta-llama/llama-3.3-70b").id, "meta");
  assert.equal(resolveModelBrand("mistral-large-latest").id, "mistral");
  assert.equal(resolveModelBrand("qwen2.5-72b").id, "qwen");
  assert.equal(resolveModelBrand("kimi-k2").id, "moonshot");
  assert.equal(resolveModelBrand("glm-4.5").id, "zhipu");
  assert.equal(resolveModelBrand("doubao-pro-32k").id, "bytedance");
});

test("模型无法识别时回退 Provider 名", () => {
  assert.equal(resolveModelBrand("custom-router-1", "DeepSeek Official").id, "deepseek");
  assert.equal(resolveModelBrand("proxy-model", "OpenRouter").id, "openrouter");
  assert.equal(resolveModelBrand("local-model", "Ollama Home").id, "ollama");
});

test("自动路由与未知品牌", () => {
  assert.equal(resolveModelBrand(undefined, undefined, { auto: true }).id, "auto");
  assert.equal(resolveModelBrand("totally-custom-xyz").id, "unknown");
  assert.equal(resolveModelBrand("gateway/auto").id, "auto");
});

test("服务器 238 个模型标识全部归到正确家族（与 iOS 共用用例）", async () => {
  const { default: data } = await import("./modelBrandCases.json", { with: { type: "json" } });
  const cases = data.cases as Array<{ model: string; brand: string }>;
  assert.equal(cases.length, 238);
  for (const { model, brand } of cases) {
    assert.equal(resolveModelBrand(model).id, brand, model);
    // 忽略大小写；带 org/ 前缀（OpenRouter 风格）时家族不变（路由类除外：前缀本身就是 openrouter）。
    assert.equal(resolveModelBrand(model.toUpperCase()).id, brand, model.toUpperCase());
    if (brand !== "auto" && brand !== "openrouter") {
      assert.equal(resolveModelBrand(`gateway/${model}`).id, brand, `gateway/${model}`);
    }
  }
  assert.equal(resolveModelBrand("openrouter/pareto-code").id, "openrouter");
  assert.equal(resolveModelBrand("openrouter/auto").id, "openrouter");
  assert.equal(resolveModelBrand("tencent/hy3-preview").id, "tencent");
  assert.equal(resolveModelBrand("custom-model", "腾讯混元").id, "tencent");
});

test("每个家族都有名称；有开源图标的家族都有矢量路径", async () => {
  const { MODEL_BRAND_ICONS } = await import("./modelBrandIcons.ts");
  const { listModelBrands } = await import("./modelBrand.ts");
  const withoutIcon = new Set(["auto", "unknown", "writer", "thinkingmachines", "nexagi"]);
  for (const brand of listModelBrands()) {
    assert.ok(brand.label, brand.id);
    if (!withoutIcon.has(brand.id)) assert.ok(MODEL_BRAND_ICONS[brand.id]?.paths.includes("<path"), brand.id);
  }
});
