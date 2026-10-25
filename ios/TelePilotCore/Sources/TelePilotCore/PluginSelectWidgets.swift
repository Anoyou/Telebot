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

/// 统一模型列表中的一项：选中后同时写回模型字段与 `x-ui-provider-field` 指向的 Provider 字段。
public struct PluginUnifiedModelOption: Hashable, Sendable {
    /// 选项唯一值（Provider id + 模型 id），只在控件内部使用。
    public let key: String
    public let label: String
    /// Provider 分组标题。
    public let group: String
    public let providerValue: String
    public let modelValue: String
    public init(key: String, label: String, group: String, providerValue: String, modelValue: String) {
        self.key = key; self.label = label; self.group = group; self.providerValue = providerValue; self.modelValue = modelValue
    }
}

/// 统一模型列表的当前状态，与 Web `buildUnifiedModelSelection` 同一套规则。
public struct PluginUnifiedModelSelection: Equatable, Sendable {
    public let options: [PluginUnifiedModelOption]
    /// 当前选中项的 key；`""` 表示自动路由（不指定）。
    public let selectedKey: String
    /// 保存值不在列表里时的“当前值：…”选项。
    public let legacy: PluginUnifiedModelOption?

    /// 按 Provider 分组（保持列表顺序），供 Picker 的 Section 渲染。
    public var groups: [(group: String, options: [PluginUnifiedModelOption])] {
        var result: [(group: String, options: [PluginUnifiedModelOption])] = []
        for option in options {
            if let last = result.indices.last, result[last].group == option.group { result[last].options.append(option) }
            else { result.append((option.group, [option])) }
        }
        return result
    }

    /// 把选中的 key 解析成要写回的（Provider 值, 模型值）；未知 key 返回 nil（保持不变）。
    public func resolve(_ key: String) -> (provider: String, model: String)? {
        if key == PluginSelectOptions.autoRouteKey { return ("", "") }
        guard let option = options.first(where: { $0.key == key }) ?? (legacy?.key == key ? legacy : nil) else { return nil }
        return (option.providerValue, option.modelValue)
    }
}

public enum PluginSelectOptions {
    public static let autoRouteKey = ""
    public static let autoRouteLabel = "自动路由（不指定）"
    private static let keySeparator = "\u{1F}"

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

    /// 单个 Provider 下显式 `enabled == true`（或纯字符串）且符合模态的模型，去重保序。与 Web `enabledModelRows` 一致。
    public static func enabledModelRows(_ provider: JSONValue, modality: String?) -> [PluginSelectOption] {
        var seen = Set<String>()
        var rows: [PluginSelectOption] = []
        for item in provider["models"]?.arrayValue ?? [] {
            let id: String
            var label: String?
            if let plain = item.stringValue {
                id = plain.trimmingCharacters(in: .whitespacesAndNewlines)
            } else {
                guard item["enabled"]?.boolValue == true else { continue }
                id = text(item["id"]); label = item["label"]?.stringValue
            }
            guard !id.isEmpty, matches(id, modality: modality), seen.insert(id).inserted else { continue }
            rows.append(PluginSelectOption(value: id, label: label.flatMap { $0.isEmpty || $0 == id ? nil : "\($0)（\(id)）" } ?? id))
        }
        return rows
    }

    public static func unifiedModelKey(provider: String, model: String) -> String { provider + keySeparator + model }

    /// 统一模型列表：所有 Provider（id 升序）下已启用的模型，标签“Provider 名 · 模型”。
    ///
    /// - 模型与 Provider 都为空 → 自动路由（不指定）；
    /// - Provider 能匹配且模型在其已启用列表里 → 选中该项；
    /// - 其余组合（模型停用、Provider 已删除、只填其一）→ “当前值：…”，如实展示不静默改写。
    public static func unifiedModelSelection(_ providers: [JSONValue]?, providerValue: JSONValue?, modelValue: JSONValue?,
                                             modality: String?) -> PluginUnifiedModelSelection {
        let sorted = (providers ?? []).sorted { lhs, rhs in
            let l = text(lhs["id"]), r = text(rhs["id"])
            if let li = Double(l), let ri = Double(r), li != ri { return li < ri }
            return l < r
        }
        var options: [PluginUnifiedModelOption] = []
        for provider in sorted {
            let providerID = text(provider["id"])
            guard !providerID.isEmpty else { continue }
            let name = provider["name"]?.stringValue ?? providerID
            for row in enabledModelRows(provider, modality: modality) {
                options.append(PluginUnifiedModelOption(key: unifiedModelKey(provider: providerID, model: row.value), label: "\(name) · \(row.label)",
                                                        group: name, providerValue: providerID, modelValue: row.value))
            }
        }
        let providerText = text(providerValue)
        let modelText = text(modelValue)
        if providerText.isEmpty && modelText.isEmpty {
            return PluginUnifiedModelSelection(options: options, selectedKey: autoRouteKey, legacy: nil)
        }
        let matched = provider(sorted, selector: providerText)
        if let matched, !modelText.isEmpty {
            let id = text(matched["id"])
            if let hit = options.first(where: { $0.providerValue == id && $0.modelValue == modelText }) {
                return PluginUnifiedModelSelection(options: options, selectedKey: hit.key, legacy: nil)
            }
        }
        let providerLabel = matched?["name"]?.stringValue ?? providerText
        let label: String
        if !providerLabel.isEmpty && !modelText.isEmpty { label = "当前值：\(providerLabel) · \(modelText)" }
        else if !providerLabel.isEmpty { label = "当前值：\(providerLabel) · 默认模型" }
        else { label = "当前值：\(modelText)（未固定 Provider）" }
        let legacy = PluginUnifiedModelOption(key: "legacy" + keySeparator + providerText + keySeparator + modelText, label: label,
                                              group: "", providerValue: providerText, modelValue: modelText)
        return PluginUnifiedModelSelection(options: options, selectedKey: legacy.key, legacy: legacy)
    }
}
