import Foundation

/// 插件声明式 UI 契约。`plugin-ui-widgets.json` 由仓库根 `scripts/sync-plugin-ui-widgets.py`
/// 从 `schemas/plugin-ui-widgets.json` 逐字节复制（CI `--check`），与 Web `pluginUiContract.ts` 同一份规则：
/// 字段解析出的控件 id 两端一致；App 必须实现契约里的全部控件，遇到不认识的 `x-ui-widget`
/// 显示「此控件需要更新 App」并保持只读，不再静默退化成通用数组编辑器。
public enum PluginUIContract {
    /// 打包进 App 的契约（读取失败时为空对象）。
    public static let bundled: JSONValue = {
        guard let url = Bundle.module.url(forResource: "plugin-ui-widgets", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let value = try? JSONValue(data: data) else { return .object([:]) }
        return value
    }()

    public static var resolutionOrder: [String] {
        bundled["resolution_order"]?.arrayValue?.compactMap(\.stringValue) ?? []
    }

    public static var widgets: [JSONValue] { bundled["widgets"]?.arrayValue ?? [] }

    /// 契约列出的显式 `x-ui-widget` 取值。
    public static var explicitWidgets: Set<String> {
        Set(widgets.compactMap { $0["x_ui_widget"]?.stringValue })
    }

    public static var unknownWidgetMessage: String {
        bundled["unknown_widget"]?["message"]?.stringValue ?? "此控件需要更新 App"
    }

    public static var cardLayouts: [String] {
        bundled["cards"]?["layouts"]?.arrayValue?.compactMap { $0["type"]?.stringValue } ?? []
    }

    public static var cardFailureMessage: String { bundled["cards"]?["failure"]?["message"]?.stringValue ?? "已停止工作" }
    public static var cardRetryLabel: String { bundled["cards"]?["failure"]?["retry"]?.stringValue ?? "重试" }

    /// 服务器契约（`GET /api/plugins/ui-contract`）里某个控件要求的 iOS 版本，用于“需 iOS x.y”提示。
    public static func requiredIOSVersion(widget: String, serverContract: JSONValue?) -> String? {
        let list = serverContract?["widgets"]?.arrayValue ?? []
        return list.first { $0["x_ui_widget"]?.stringValue == widget || $0["id"]?.stringValue == widget }?["ios_since"]?.stringValue
    }
}

/// 插件配置字段的控件（契约 `widgets[].id`）。
public enum PluginFieldWidget {
    /// App 已实现的控件；单测校验与契约 `resolution_order` 完全一致。
    public static let supported: [String] = [
        "hidden", "preview", "placeholders", "readonly",
        "llm-provider-select", "llm-model-select", "dynamic-select",
        "config-list", "agent-skill-multi-select", "allowed-peer-multi-select", "multi-select", "list-select",
        "secret-list", "select", "switch", "number", "object", "json-object", "json-array", "string-list",
        "secret", "textarea", "text",
    ]
    public static let unknown = "unknown"

    public static func isSensitiveKey(_ key: String) -> Bool {
        key.range(of: #"(^|_)(api_key|access_token|auth_token|bot_token|token|tokens|secret|password|passwd|pwd)$"#,
                  options: [.regularExpression, .caseInsensitive]) != nil
    }

    public static func isPreviewKey(_ key: String) -> Bool {
        key == "template_preview" || key.lowercased().hasSuffix("_preview")
    }

    public static func isTemplateKey(_ key: String) -> Bool {
        key == "message_template" || key.lowercased().hasSuffix("_template")
    }

    /// 与 ManagementSchema.normalized 一致：合并 anyOf/oneOf 中第一个非 null 分支。
    static func normalized(_ schema: JSONValue) -> JSONValue {
        guard let branches = schema["anyOf"]?.arrayValue ?? schema["oneOf"]?.arrayValue,
              let branch = branches.first(where: { $0["type"]?.stringValue != "null" }) else { return schema }
        return .object((schema.objectValue ?? [:]).merging(branch.objectValue ?? [:]) { _, new in new })
    }

    static func primaryType(_ schema: JSONValue) -> String {
        if let type = schema["type"]?.stringValue { return type }
        return schema["type"]?.arrayValue?.compactMap(\.stringValue).first { $0 != "null" } ?? ""
    }

    /// 按契约 `resolution_order` 解析控件 id；顺序与 Web `resolveConfigWidget` 一致。
    public static func resolve(key: String, spec raw: JSONValue) -> String {
        let spec = normalized(raw)
        let widget = spec["x-ui-widget"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let type = primaryType(spec)
        if spec["x-ui-hidden"]?.boolValue == true { return "hidden" }
        if isPreviewKey(key) { return "preview" }
        if key == "template_placeholders" { return "placeholders" }
        if spec["readOnly"]?.boolValue == true { return "readonly" }
        if !widget.isEmpty, !PluginUIContract.explicitWidgets.contains(widget) { return unknown }
        if widget == "llm-provider-select" { return "llm-provider-select" }
        if widget == "llm-model-select" { return "llm-model-select" }
        if widget == "dynamic-select" { return "dynamic-select" }
        if type == "array", widget == "config-list" { return "config-list" }
        if type == "array", widget == "agent-skill-multi-select" { return "agent-skill-multi-select" }
        if type == "array", widget == "allowed-peer-multi-select" { return "allowed-peer-multi-select" }
        if type == "array", widget == "multi-select" || !(spec["items"]?["enum"]?.arrayValue ?? []).isEmpty || !(spec["enum"]?.arrayValue ?? []).isEmpty {
            return "multi-select"
        }
        if type == "array", let declared = spec["x-secret-list"], declared != .bool(false), !declared.isNull { return "secret-list" }
        if let choices = spec["enum"]?.arrayValue, !choices.isEmpty { return widget == "list-select" ? "list-select" : "select" }
        if type == "boolean" { return "switch" }
        if type == "integer" || type == "number" { return "number" }
        if type == "object" { return (spec["properties"]?.objectValue?.isEmpty == false) ? "object" : "json-object" }
        if type == "array" { return primaryType(spec["items"] ?? .null) == "object" ? "json-array" : "string-list" }
        if isSensitiveKey(key) || spec["format"]?.stringValue == "password" || spec["writeOnly"]?.boolValue == true { return "secret" }
        if spec["format"]?.stringValue == "textarea" || widget == "textarea" || isTemplateKey(key)
            || key.range(of: "message|text|prompt|content", options: [.regularExpression, .caseInsensitive]) != nil {
            return "textarea"
        }
        return "text"
    }
}

/// `x-secret-list` 声明（与后端 services/plugin_secret_list.py 一致）。
public struct PluginSecretListSpec: Equatable, Sendable {
    public let secretField: String
    public let noteField: String
    public let idField: String

    public init(field raw: JSONValue) {
        let field = PluginFieldWidget.normalized(raw)
        let declared = field["x-secret-list"]?.objectValue ?? [:]
        var secret = declared["secret_field"]?.stringValue ?? ""
        if secret.isEmpty {
            let props = field["items"]?["properties"]?.objectValue ?? [:]
            secret = ["token", "secret", "api_key", "key", "value"].first { props[$0] != nil } ?? "token"
        }
        secretField = secret
        let note = declared["note_field"]?.stringValue ?? ""
        let id = declared["id_field"]?.stringValue ?? ""
        noteField = note.isEmpty ? "note" : note
        idField = id.isEmpty ? "secret_id" : id
    }

    /// 新增输入：JSON（插件导入格式）整体作为一项，其余按行拆分。
    public static func split(_ raw: String) -> [String] {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return [] }
        if text.hasPrefix("{") || text.hasPrefix("[") { return [text] }
        return text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}
