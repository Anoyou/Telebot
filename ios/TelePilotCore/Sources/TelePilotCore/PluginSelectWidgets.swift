import Foundation

/// 插件 schema 声明的选择控件，与 Web `ConfigDialog` 的 `x-ui-widget` 对齐。
public enum PluginSelectWidget: Equatable, Sendable {
    case llmProvider
    case llmModel(providerField: String?, modality: String?)
    case dynamic(optionsField: String?)

    public init?(spec: JSONValue) {
        let schema = PluginSelectOptions.normalized(spec)
        switch schema["x-ui-widget"]?.stringValue {
        case "llm-provider-select": self = .llmProvider
        case "llm-model-select":
            self = .llmModel(providerField: schema["x-ui-provider-field"]?.stringValue, modality: schema["x-ui-model-modality"]?.stringValue)
        case "dynamic-select": self = .dynamic(optionsField: schema["x-ui-options-field"]?.stringValue)
        default: return nil
        }
    }

    public var needsProviders: Bool {
        switch self {
        case .llmProvider, .llmModel: true
        case .dynamic: false
        }
    }
}

public struct PluginSelectOption: Hashable, Sendable {
    public let value: String
    public let label: String
    public init(value: String, label: String) { self.value = value; self.label = label }
}

public enum PluginSelectOptions {
    /// 与 App 的 ManagementSchema.normalized 一致：合并 anyOf/oneOf 中第一个非 null 分支。
    public static func normalized(_ schema: JSONValue) -> JSONValue {
        guard let branches = schema["anyOf"]?.arrayValue ?? schema["oneOf"]?.arrayValue,
              let branch = branches.first(where: { $0["type"]?.stringValue != "null" }) else { return schema }
        return .object((schema.objectValue ?? [:]).merging(branch.objectValue ?? [:]) { _, new in new })
    }

    public static func text(_ value: JSONValue?) -> String {
        guard let value, !value.isNull else { return "" }
        if let string = value.stringValue { return string.trimmingCharacters(in: .whitespacesAndNewlines) }
        if let int = value.intValue { return String(int) }
        if let number = value.doubleValue { return String(number) }
        if let flag = value.boolValue { return flag ? "true" : "false" }
        return ""
    }

    /// 按 id、名称或 provider 类型匹配（与 Web `findLLMProviderBySelector` 一致）。
    public static func provider(_ providers: [JSONValue]?, selector: String) -> JSONValue? {
        let raw = selector.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        let lowered = raw.lowercased()
        return providers?.first { item in
            text(item["id"]) == raw
                || (item["name"]?.stringValue?.lowercased() == lowered)
                || (item["provider"]?.stringValue?.lowercased() == lowered)
        }
    }

    public static func providerOptions(_ providers: [JSONValue]) -> [PluginSelectOption] {
        providers.compactMap { item in
            let id = text(item["id"])
            guard !id.isEmpty else { return nil }
            let name = item["name"]?.stringValue ?? id
            var details = [item["provider"]?.stringValue, item["default_model"]?.stringValue].compactMap { $0 }.filter { !$0.isEmpty }
            if let tags = item["tags"]?.arrayValue?.compactMap(\.stringValue), !tags.isEmpty { details.append(tags.joined(separator: ",")) }
            if item["has_effective_key"]?.boolValue != true, item["provider"]?.stringValue != "ollama" { details.append("未配置 API Key") }
            return PluginSelectOption(value: id, label: details.isEmpty ? name : "\(name)（\(details.joined(separator: " · "))）")
        }
    }

    /// 所选 Provider 下已启用的模型；`modality == "text"` 时排除图片模型（与 Web `modelMatchesModality` 一致）。
    public static func modelOptions(_ provider: JSONValue, modality: String?) -> [PluginSelectOption] {
        var seen = Set<String>()
        var rows: [PluginSelectOption] = []
        for item in provider["models"]?.arrayValue ?? [] {
            let id: String
            let label: String?
            if let plain = item.stringValue {
                id = plain.trimmingCharacters(in: .whitespacesAndNewlines); label = nil
            } else {
                guard item["enabled"]?.boolValue != false else { continue }
                id = text(item["id"]); label = item["label"]?.stringValue
            }
            guard !id.isEmpty, matches(id, modality: modality), seen.insert(id).inserted else { continue }
            rows.append(PluginSelectOption(value: id, label: label.flatMap { $0.isEmpty || $0 == id ? nil : "\($0)（\(id)）" } ?? id))
        }
        return rows
    }

    public static func matches(_ modelID: String, modality: String?) -> Bool {
        guard modality?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "text" else { return true }
        let normalized = modelID.lowercased()
        return !(normalized.hasPrefix("gpt-image-") || normalized.hasPrefix("dall-e-") || normalized.contains("image"))
    }

    /// `dynamic-select` 的候选来自另一个配置字段（对象数组，取 value/id 与 label/title）。
    public static func dynamicOptions(_ value: JSONValue?) -> [PluginSelectOption] {
        var seen = Set<String>()
        return (value?.arrayValue ?? []).compactMap { item in
            guard item.objectValue != nil else { return nil }
            let raw = text(item["value"]).isEmpty ? text(item["id"]) : text(item["value"])
            guard !raw.isEmpty, seen.insert(raw).inserted else { return nil }
            let label = item["label"]?.stringValue ?? item["title"]?.stringValue ?? raw
            return PluginSelectOption(value: raw, label: label.isEmpty ? raw : label)
        }
    }
}
