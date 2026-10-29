import Foundation
import Testing
@testable import TelePilotCore

/// 遍历插件 UI 契约（schemas/plugin-ui-widgets.json 的副本）：App 实现了全部控件与卡片布局，
/// 每个控件的示例字段解析结果与契约一致（Web `pluginUiContract.test.ts` 做同样的校验）。
struct PluginUIContractTests {
    @Test func bundledContractLoads() {
        #expect(!PluginUIContract.widgets.isEmpty)
        #expect(PluginUIContract.unknownWidgetMessage == "此控件需要更新 App")
        #expect(PluginUIContract.cardFailureMessage == "已停止工作")
    }

    @Test func appImplementsEveryWidgetInContractOrder() {
        #expect(PluginFieldWidget.supported == PluginUIContract.resolutionOrder)
        #expect(PluginUIContract.widgets.compactMap { $0["id"]?.stringValue } == PluginUIContract.resolutionOrder)
    }

    @Test func everyContractExampleResolvesToItsWidget() {
        for widget in PluginUIContract.widgets {
            let id = widget["id"]?.stringValue ?? ""
            let key = widget["example"]?["key"]?.stringValue ?? ""
            let schema = widget["example"]?["schema"] ?? .null
            #expect(PluginFieldWidget.resolve(key: key, spec: schema) == id, "控件 \(id)")
        }
    }

    @Test func iosFirstBatchWidgetsAreNoLongerGeneric() {
        for id in ["config-list", "allowed-peer-multi-select", "multi-select", "list-select", "secret-list"] {
            #expect(PluginFieldWidget.supported.contains(id))
            let since = PluginUIContract.widgets.first { $0["id"]?.stringValue == id }?["ios_since"]?.stringValue
            #expect(since == "0.14.0")
        }
    }

    @Test func unknownWidgetStaysReadOnly() {
        #expect(PluginFieldWidget.resolve(key: "color", spec: ["type": "string", "x-ui-widget": "color-wheel"]) == "unknown")
        #expect(PluginFieldWidget.resolve(key: "rows", spec: ["type": "array", "x-ui-widget": "kanban", "items": ["type": "object"]]) == "unknown")
        #expect(PluginFieldWidget.resolve(key: "color", spec: ["type": "string", "x-ui-widget": "color-wheel", "readOnly": true]) == "readonly")
        let server: JSONValue = ["widgets": [["id": "color-wheel", "x_ui_widget": "color-wheel", "ios_since": "0.15.0"]]]
        #expect(PluginUIContract.requiredIOSVersion(widget: "color-wheel", serverContract: server) == "0.15.0")
    }

    @Test func inferredWidgets() {
        #expect(PluginFieldWidget.resolve(key: "days", spec: ["type": "array", "items": ["type": "string", "enum": ["a"]]]) == "multi-select")
        #expect(PluginFieldWidget.resolve(key: "rows", spec: ["type": "array", "items": ["type": "object"]]) == "json-array")
        #expect(PluginFieldWidget.resolve(key: "api_key", spec: ["anyOf": [["type": "string"], ["type": "null"]]]) == "secret")
    }

    @Test func secretListSpecAndSplit() {
        let spec = PluginSecretListSpec(field: ["type": "array", "x-secret-list": ["secret_field": "token", "id_field": "token_id"]])
        #expect(spec == PluginSecretListSpec(field: ["type": "array", "x-secret-list": ["secret_field": "token", "note_field": "note", "id_field": "token_id"]]))
        #expect(spec.noteField == "note")
        let inferred = PluginSecretListSpec(field: ["type": "array", "x-secret-list": true, "items": ["properties": ["api_key": [:], "note": [:]]]])
        #expect(inferred.secretField == "api_key" && inferred.idField == "secret_id")
        #expect(PluginSecretListSpec.split("a\n b \n\n") == ["a", "b"])
        #expect(PluginSecretListSpec.split("{\"accessToken\": \"x\"}\n") == ["{\"accessToken\": \"x\"}"])
    }

    @Test func appImplementsEveryCardLayout() {
        #expect(PluginCardBlock.supportedLayouts.sorted() == PluginUIContract.cardLayouts.sorted())
        let layouts = PluginUIContract.bundled["cards"]?["layouts"]?.arrayValue ?? []
        for layout in layouts {
            let model = PluginCardBlock.build(layout["example"] ?? .null, data: layout["example_data"] ?? .null)
            if case .unsupported(let type) = model { Issue.record("布局 \(type) 未实现") }
        }
    }

    @Test func cardBlocksMatchWebRules() {
        let stats = PluginCardBlock.build(["type": "stats", "items": [["label": "调用", "path": "calls"], ["label": "失败", "path": "failed", "tone": "danger"]]],
                                          data: ["calls": 12, "failed": 1])
        #expect(stats == .stats(title: nil, items: [PluginCardItem(label: "调用", text: "12", tone: .neutral), PluginCardItem(label: "失败", text: "1", tone: .danger)]))
        let table = PluginCardBlock.build(["type": "table", "rows_path": "rows", "row_key": "id",
                                           "columns": [["key": "id", "label": "ID"], ["key": "rate", "label": "失败率", "format": "percent", "tone_key": "tone"]]],
                                          data: ["rows": [["id": "a", "rate": 0.25, "tone": "danger"]]])
        if case .table(_, let columns, let rows, _) = table {
            #expect(columns == ["ID", "失败率"])
            #expect(rows.first?.key == "a")
            #expect(rows.first?.cells.last == PluginCardCell(text: "25%", tone: .danger))
        } else { Issue.record("应为表格") }
        let list = PluginCardBlock.build(["type": "list", "items_path": "items", "hide_when_empty": true], data: ["items": []])
        if case .list(_, _, _, let hidden) = list { #expect(hidden) } else { Issue.record("应为列表") }
        #expect(PluginCardBlock.build(["type": "chart"], data: [:]) == .unsupported("chart"))
    }

    @Test func cardFormats() {
        #expect(PluginCardFormat.format(1234, format: "number") == "1,234")
        #expect(PluginCardFormat.format(0.0167, format: "percent") == "1.7%")
        #expect(PluginCardFormat.format(1800, format: "duration_ms") == "1.8 秒")
        #expect(PluginCardFormat.format(1000, format: "relative_time", now: Date(timeIntervalSince1970: 2000)) == "16 分钟前")
        #expect(PluginCardFormat.format(nil, format: nil) == "-")
        #expect(PluginCardFormat.value(at: "summary.calls", in: ["summary": ["calls": 3]]) == .integer(3))
    }
}
