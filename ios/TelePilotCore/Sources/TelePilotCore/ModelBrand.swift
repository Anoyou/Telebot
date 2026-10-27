import Foundation

/// 模型家族（公司品牌），与 Web `frontend/src/lib/modelBrand.ts` 同一套规则、同一份数据：
/// `model-brands.json` 由 `ios/TelePilotCore/scripts/sync-model-brands.py` 从 `frontend/src/lib/modelBrands.json` 逐字节复制，
/// iOS 工作流用 `--check` 校验两端一致。
///
/// 识别顺序（与 Web 相同）：整个模型 ID 依次匹配 `modelRules` → `org/model` 的 org 段匹配 `modelRules` 与 `providerRules`
/// → 最后一段匹配 `modelRules` → 供应商名匹配 `providerRules` → `unknown`（“其他”）。
public struct ModelBrand: Sendable, Hashable, Identifiable {
    public let id: String
    public let label: String
    /// 深色背景下可读的品牌色（十六进制），iOS 界面目前用单色图标，保留以便与 Web 一致。
    public let accent: String

    public static let autoID = "auto"
    public static let unknownID = "unknown"

    /// 识别模型所属家族。`auto` 为真时直接返回“自动路由”。
    public static func resolve(model: String?, providerName: String? = nil, auto: Bool = false) -> ModelBrand {
        ModelBrandCatalog.shared.resolve(model: model, providerName: providerName, auto: auto)
    }

    /// 全部家族（含 auto 与 unknown），顺序与数据文件一致。
    public static var all: [ModelBrand] { ModelBrandCatalog.shared.brands }
}

final class ModelBrandCatalog: Sendable {
    struct Rule: @unchecked Sendable {
        let brand: String
        let regex: NSRegularExpression
    }

    static let shared = ModelBrandCatalog()

    let brands: [ModelBrand]
    let byID: [String: ModelBrand]
    let modelRules: [Rule]
    let providerRules: [Rule]

    init(data: Data? = nil) {
        let payload = data ?? Bundle.module.url(forResource: "model-brands", withExtension: "json").flatMap { try? Data(contentsOf: $0) }
        struct File: Decodable {
            struct Brand: Decodable { let id: String; let label: String; let accent: String }
            struct RuleData: Decodable { let brand: String; let pattern: String }
            let brands: [Brand]
            let modelRules: [RuleData]
            let providerRules: [RuleData]
        }
        let file = payload.flatMap { try? JSONDecoder().decode(File.self, from: $0) }
        var brands = (file?.brands ?? []).map { ModelBrand(id: $0.id, label: $0.label, accent: $0.accent) }
        if !brands.contains(where: { $0.id == ModelBrand.unknownID }) {
            brands.append(ModelBrand(id: ModelBrand.unknownID, label: "其他", accent: "#94a3b8"))
        }
        if !brands.contains(where: { $0.id == ModelBrand.autoID }) {
            brands.insert(ModelBrand(id: ModelBrand.autoID, label: "自动路由", accent: "#8b9cff"), at: 0)
        }
        self.brands = brands
        byID = Dictionary(brands.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        func compile(_ rules: [File.RuleData]) -> [Rule] {
            rules.compactMap { rule in
                (try? NSRegularExpression(pattern: rule.pattern, options: [.caseInsensitive])).map { Rule(brand: rule.brand, regex: $0) }
            }
        }
        modelRules = compile(file?.modelRules ?? [])
        providerRules = compile(file?.providerRules ?? [])
    }

    private func match(_ text: String, _ rules: [Rule]) -> String? {
        let range = NSRange(text.startIndex..., in: text)
        return rules.first { $0.regex.firstMatch(in: text, range: range) != nil }?.brand
    }

    private func brand(_ id: String) -> ModelBrand { byID[id] ?? byID[ModelBrand.unknownID]! }

    func resolve(model: String?, providerName: String?, auto: Bool) -> ModelBrand {
        if auto { return brand(ModelBrand.autoID) }
        let rawModel = (model ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let rawProvider = (providerName ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !rawModel.isEmpty {
            if let id = match(rawModel, modelRules) { return brand(id) }
            if let slash = rawModel.firstIndex(of: "/"), slash > rawModel.startIndex {
                let org = rawModel[..<slash].trimmingCharacters(in: .whitespaces)
                if !org.isEmpty {
                    if let id = match(org, modelRules) ?? match(org, providerRules) { return brand(id) }
                    let last = String(rawModel[rawModel.index(after: rawModel.lastIndex(of: "/")!)...])
                    if let id = match(last, modelRules) { return brand(id) }
                }
            }
        }
        if !rawProvider.isEmpty, let id = match(rawProvider, providerRules) { return brand(id) }
        return brand(ModelBrand.unknownID)
    }
}
