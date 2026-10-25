import Foundation

/// 插件配置表单的字段顺序与分组，与 Web `configFieldOrder.ts` / `groupConfigFields` 同一套规则：
/// - 服务器写库前把 `properties` 的声明顺序记在 `x-ui-property-order`（PostgreSQL JSONB 不保留对象键顺序）；
/// - 字段先按声明顺序，再按 `x-ui-order`（未声明视为 0）稳定排序，不按标题或字母排序；
/// - 分组：先各 `x-ui-section`（按首个字段出现的顺序），再是未分组字段，最后是消息模板（`*_template`）。
public enum PluginConfigLayout {
    public static let propertyOrderKey = "x-ui-property-order"

    public struct FieldGroup: Equatable, Sendable {
        public let title: String?
        public let keys: [String]

        public init(title: String?, keys: [String]) {
            self.title = title
            self.keys = keys
        }
    }

    /// 声明顺序：`x-ui-property-order` 列出的字段按列出顺序在前；
    /// 未列出的字段（旧服务器没有记录顺序时就是全部字段）按键名排在后面，保证每次显示一致。
    public static func declaredOrder(schema: JSONValue) -> [String] {
        guard let properties = schema["properties"]?.objectValue else { return [] }
        var seen = Set<String>()
        var ordered: [String] = []
        for key in schema[propertyOrderKey]?.arrayValue?.compactMap(\.stringValue) ?? [] {
            guard properties[key] != nil, !seen.contains(key) else { continue }
            seen.insert(key)
            ordered.append(key)
        }
        ordered += properties.keys.filter { !seen.contains($0) }.sorted()
        return ordered
    }

    public static func uiOrder(_ spec: JSONValue?) -> Double { spec?["x-ui-order"]?.doubleValue ?? 0 }

    /// 先声明顺序，再按 `x-ui-order` 稳定排序；`keys` 用于只取其中一部分字段（例如全局或账号作用域）。
    public static func orderedKeys(schema: JSONValue, keys: Set<String>? = nil) -> [String] {
        let properties = schema["properties"]?.objectValue ?? [:]
        let declared = declaredOrder(schema: schema).filter { keys?.contains($0) ?? true }
        return declared.enumerated().sorted { lhs, rhs in
            let left = uiOrder(properties[lhs.element])
            let right = uiOrder(properties[rhs.element])
            return left == right ? lhs.offset < rhs.offset : left < right
        }.map(\.element)
    }

    public static func isTemplate(_ key: String) -> Bool {
        let lower = key.lowercased()
        return lower == "message_template" || lower.hasSuffix("_template")
    }

    /// 按 Web 的分组规则把已排序的字段分组。存在 `x-ui-section` 分组时，未分组字段归入“其他设置”。
    public static func groups(schema: JSONValue, orderedKeys: [String]) -> [FieldGroup] {
        let properties = schema["properties"]?.objectValue ?? [:]
        var sectionIDs: [String] = []
        var sectionTitles: [String: String] = [:]
        var sectionKeys: [String: [String]] = [:]
        var basic: [String] = []
        var templates: [String] = []
        for key in orderedKeys {
            if isTemplate(key) {
                templates.append(key)
                continue
            }
            let name = (properties[key]?["x-ui-section"]?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else {
                basic.append(key)
                continue
            }
            let id = name.lowercased()
            if sectionKeys[id] == nil {
                sectionIDs.append(id)
                sectionTitles[id] = name
            }
            sectionKeys[id, default: []].append(key)
        }
        var result = sectionIDs.map { FieldGroup(title: sectionTitles[$0], keys: sectionKeys[$0] ?? []) }
        if !basic.isEmpty { result.append(FieldGroup(title: result.isEmpty ? nil : "其他设置", keys: basic)) }
        if !templates.isEmpty { result.append(FieldGroup(title: "消息模板", keys: templates)) }
        return result
    }
}
