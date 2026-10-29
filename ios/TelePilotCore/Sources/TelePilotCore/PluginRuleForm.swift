import Foundation

/// 通用规则表单（插件 UI 契约 2，`config_schema.x-ui-mode == "rules"`），与 Web
/// `frontend/src/lib/pluginRules.ts` 同一套规则：
/// - `x-rule-schema`：每条规则 `rule.config` 的 schema；省略时整个 `config_schema` 都是规则字段（没有账号级配置表单）；
/// - `x-rule-summary`：规则列表每行显示的摘要字段（最多 3 个），省略时取规则 schema 的前两个可见字段；
/// - `x-dry-run`：试运行卡片 `{sample_fields, title, description}`，`false` 关闭，省略时默认「样例消息 + 会话 ID」；
/// - 字段级 `x-ui-visible-if`：`{field, equals | in | not_in}`，按同级字段的当前值显示 / 隐藏；声明不合法时一律显示。
public enum PluginRuleForm {
    public static let sampleFieldNames = ["message", "chat_type", "chat_id"]
    public static let defaultSampleFields = ["message", "chat_id"]

    public static func isRulesMode(_ configSchema: JSONValue?) -> Bool {
        configSchema?["x-ui-mode"]?.stringValue == "rules"
    }

    /// 是否显式声明了带 `properties` 的 `x-rule-schema`。
    public static func hasExplicitRuleSchema(_ configSchema: JSONValue?) -> Bool {
        isRulesMode(configSchema) && configSchema?["x-rule-schema"]?["properties"]?.objectValue != nil
    }

    /// 每条规则 config 的 schema；不是规则模式时返回 nil（沿用旧行为：整个 config_schema）。
    public static func ruleSchema(_ configSchema: JSONValue?) -> JSONValue? {
        guard let configSchema, isRulesMode(configSchema) else { return nil }
        if hasExplicitRuleSchema(configSchema), let declared = configSchema["x-rule-schema"] { return declared }
        return configSchema
    }

    /// 规则模式且没有 `x-rule-schema`：config_schema 的字段全是规则字段，不再渲染成账号配置表单。
    public static func configFieldsAreRuleFields(_ configSchema: JSONValue?) -> Bool {
        isRulesMode(configSchema) && !hasExplicitRuleSchema(configSchema)
    }

    // MARK: 字段显隐

    /// `x-ui-visible-if`：`values` 是同级字段的当前值（对象）。
    public static func isVisible(_ field: JSONValue?, values: JSONValue?) -> Bool {
        guard let condition = field?["x-ui-visible-if"], let name = condition["field"]?.stringValue else { return true }
        let actual = values?[name]
        if let expected = condition["equals"] { return same(actual, expected) }
        if let options = condition["in"]?.arrayValue { return options.contains { same(actual, $0) } }
        if let options = condition["not_in"]?.arrayValue { return !options.contains { same(actual, $0) } }
        return true
    }

    /// 与 Web 一致：严格相等，或两边都非空时按文本比较（"1" 与 1 视为相同）。
    static func same(_ actual: JSONValue?, _ expected: JSONValue) -> Bool {
        guard let actual else { return false }
        if actual == expected { return true }
        guard !actual.isNull, !expected.isNull, let left = text(actual), let right = text(expected) else { return false }
        return left == right
    }

    static func text(_ value: JSONValue) -> String? {
        switch value {
        case .string(let v): return v
        case .integer(let v): return String(v)
        case .number(let v):
            if v.isFinite, v == v.rounded(), abs(v) < 1e15 { return String(Int64(v)) }
            return String(v)
        case .bool(let v): return v ? "true" : "false"
        default: return nil
        }
    }

    // MARK: 列表摘要

    /// 列表摘要字段：`x-rule-summary`（只取规则 schema 里存在的，最多 3 个），否则前两个可见字段。
    public static func summaryKeys(ruleSchema: JSONValue, declared: JSONValue?) -> [String] {
        let properties = ruleSchema["properties"]?.objectValue ?? [:]
        let listed = (declared?.arrayValue ?? []).compactMap(\.stringValue).filter { properties[$0] != nil }
        if !listed.isEmpty { return Array(listed.prefix(3)) }
        return Array(PluginConfigLayout.orderedKeys(schema: ruleSchema).filter { key in
            guard let spec = properties[key] else { return false }
            return spec["x-ui-hidden"]?.boolValue != true && spec["x-ui-widget"]?.stringValue != "hidden"
        }.prefix(2))
    }

    /// 摘要里的值：数组用顿号连接、枚举显示 `enumNames`、布尔显示是 / 否，过长截断。
    public static func formatValue(_ field: JSONValue?, _ value: JSONValue?) -> String {
        guard let value, !value.isNull else { return "—" }
        if let items = value.arrayValue {
            if items.isEmpty { return "—" }
            let joined = items.map { item in text(item) ?? compactJSON(item) }.joined(separator: "、")
            return joined.count > 60 ? String(joined.prefix(57)) + "…（\(items.count) 项）" : joined
        }
        if let flag = value.boolValue { return flag ? "是" : "否" }
        if let choices = field?["enum"]?.arrayValue, let names = field?["enumNames"]?.arrayValue,
           let index = choices.firstIndex(where: { same(value, $0) }), index < names.count, let name = names[index].stringValue, !name.isEmpty {
            return name
        }
        if value.objectValue != nil { return compactJSON(value) }
        let raw = text(value) ?? ""
        if raw.isEmpty { return "—" }
        return raw.count > 60 ? String(raw.prefix(57)) + "…" : raw
    }

    static func compactJSON(_ value: JSONValue) -> String {
        guard let data = try? value.data() else { return "" }
        return String(decoding: data, as: UTF8.self)
    }

    // MARK: 试运行

    public struct DryRunSpec: Equatable, Sendable {
        public let enabled: Bool
        public let sampleFields: [String]
        public let title: String
        public let description: String
    }

    public static func dryRunSpec(_ raw: JSONValue?) -> DryRunSpec {
        let fallback = DryRunSpec(enabled: true, sampleFields: defaultSampleFields, title: "试运行",
                                  description: "输入一条样例消息，验证规则是否命中；不会真的发送消息。")
        if raw == .bool(false) {
            return DryRunSpec(enabled: false, sampleFields: fallback.sampleFields, title: fallback.title, description: fallback.description)
        }
        guard let raw, raw.objectValue != nil else { return fallback }
        let fields = (raw["sample_fields"]?.arrayValue ?? []).compactMap(\.stringValue).filter { sampleFieldNames.contains($0) }
        let title = raw["title"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let description = raw["description"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return DryRunSpec(enabled: true, sampleFields: fields.isEmpty ? fallback.sampleFields : fields,
                          title: title.isEmpty ? fallback.title : title,
                          description: description.isEmpty ? fallback.description : description)
    }

    /// 会话 ID 输入：只接受整数（可带开头负号）；空串或不合法返回 nil。
    public static func parseChatID(_ text: String) -> Int64? {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard cleaned.range(of: #"^-?\d+$"#, options: .regularExpression) != nil else { return nil }
        return Int64(cleaned)
    }

    // MARK: 每行一项

    /// 数组字段写 `x-ui-widget: textarea` 时每行一项（项里可含逗号，适合正则 / 关键词）。
    public static func isLineList(_ field: JSONValue) -> Bool {
        field["type"]?.stringValue == "array" && field["x-ui-widget"]?.stringValue == "textarea"
            && field["items"]?["type"]?.stringValue != "object"
    }

    public static func joinLines(_ value: JSONValue?) -> String {
        (value?.arrayValue ?? []).compactMap { text($0) }.joined(separator: "\n")
    }

    /// 按行拆分并去掉空行；`items.type` 为 integer / number 时能解析的转成数字。
    public static func splitLines(_ raw: String, itemType: String?) -> JSONValue {
        let lines = raw.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return .array(lines.map { line in
            if itemType == "integer", let value = Int64(line) { return .integer(value) }
            if itemType == "number", let value = Double(line), value.isFinite { return .number(value) }
            return .string(line)
        })
    }
}
