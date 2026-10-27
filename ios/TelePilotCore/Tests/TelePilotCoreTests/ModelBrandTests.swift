import Foundation
import Testing
@testable import TelePilotCore

/// 与 Web `frontend/src/lib/modelBrand.test.ts` 共用同一份用例（`model-brand-cases.json`，由 sync-model-brands.py 复制）。
@Suite struct ModelBrandTests {
    struct Cases: Decodable {
        struct Case: Decodable { let model: String; let brand: String }
        let cases: [Case]
    }

    @Test func serverModelIdentifiersResolveToExpectedFamilies() throws {
        let url = try #require(Bundle.module.url(forResource: "model-brand-cases", withExtension: "json"))
        let cases = try JSONDecoder().decode(Cases.self, from: Data(contentsOf: url)).cases
        #expect(cases.count == 238)
        for item in cases {
            #expect(ModelBrand.resolve(model: item.model).id == item.brand, "\(item.model)")
            #expect(ModelBrand.resolve(model: item.model.uppercased()).id == item.brand, "\(item.model.uppercased())")
            if item.brand != "auto" && item.brand != "openrouter" {
                #expect(ModelBrand.resolve(model: "gateway/\(item.model)").id == item.brand, "gateway/\(item.model)")
            }
        }
    }

    @Test func mainstreamProviderFallbackAndAuto() {
        #expect(ModelBrand.resolve(model: "deepseek-ai/deepseek-chat").id == "deepseek")
        #expect(ModelBrand.resolve(model: "meta-llama/llama-3.3-70b").id == "meta")
        #expect(ModelBrand.resolve(model: "o3-mini").id == "openai")
        #expect(ModelBrand.resolve(model: "openrouter/pareto-code").id == "openrouter")
        #expect(ModelBrand.resolve(model: "openrouter/auto").id == "openrouter")
        #expect(ModelBrand.resolve(model: "gateway/auto").id == "auto")
        #expect(ModelBrand.resolve(model: "tencent/hy3-preview").id == "tencent")
        #expect(ModelBrand.resolve(model: "custom-router-1", providerName: "DeepSeek Official").id == "deepseek")
        #expect(ModelBrand.resolve(model: "custom-model", providerName: "腾讯混元").id == "tencent")
        #expect(ModelBrand.resolve(model: "local-model", providerName: "Ollama Home").id == "ollama")
        #expect(ModelBrand.resolve(model: nil, auto: true).id == "auto")
        #expect(ModelBrand.resolve(model: "totally-custom-xyz").id == "unknown")
        #expect(ModelBrand.resolve(model: "totally-custom-xyz").label == "其他")
    }

    @Test func everyBrandHasALabel() {
        #expect(ModelBrand.all.count >= 45)
        for brand in ModelBrand.all { #expect(!brand.label.isEmpty, "\(brand.id)") }
    }
}
