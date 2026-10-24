import SwiftUI
import TelePilotCore

enum ManagementRuleSchemas {
    static func request(feature: JSONValue) -> JSONValue {
        let config: JSONValue
        if feature["key"]?.stringValue == "scheduler" { config = scheduler }
        else { config = feature["config_schema"] ?? .object(["type": .string("object"), "additionalProperties": .bool(true)]) }
        return .object([
            "type": .string("object"), "required": .array([.string("name"), .string("config")]),
            "properties": .object([
                "name": .object(["type": .string("string"), "minLength": .integer(1)]),
                "enabled": .object(["type": .string("boolean"), "default": .bool(true)]),
                "priority": .object(["type": .string("integer"), "default": .integer(100)]),
                "config": config
            ])
        ])
    }
    // Scheduler's rule shape is owned by scheduler_runtime, separate from its account whitelist schema.
    static var scheduler: JSONValue {
        .object([
            "type": .string("object"), "x-telepilot-kind": .string("scheduler"),
            "required": .array([.string("kind"), .string("action")]),
            "properties": .object([
                "kind": enumeration(["cron", "once", "interval"], defaultValue: "cron"),
                "cron": .object(["type": .string("string"), "default": .string("*/5 * * * *"), "description": .string("cron 模式使用服务器时区，例如 */5 * * * * 表示每五分钟。")]),
                "fire_at": .object(["type": .string("string"), "format": .string("date-time"), "description": .string("once 模式的触发时间，须包含时区。")]),
                "interval_sec": .object(["type": .string("integer"), "minimum": .integer(1), "default": .integer(300)]),
                "action": .object([
                    "type": .string("object"), "required": .array([.string("type")]),
                    "properties": .object([
                        "type": enumeration(["send_message", "run_command", "call_llm", "agent_prompt"], defaultValue: "send_message"),
                        "target_chat_id": .object(["type": .string("string"), "description": .string("支持 Telegram 会话 ID、用户名或会话链接。")]),
                        "text": .object(["type": .string("string")]),
                        "command": .object(["type": .string("string"), "description": .string("自动执行的命令必须已加入当前账号的自动指令白名单。")]),
                        "provider_id": .object(["type": .string("integer")]), "model": .object(["type": .string("string")]),
                        "prompt": .object(["type": .string("string")]), "system_prompt": .object(["type": .string("string")]),
                        "max_tokens": .object(["type": .string("integer"), "minimum": .integer(1), "default": .integer(256)]),
                        "account_id": .object(["type": .string("integer")]),
                        "delete_after": .object(["type": .string("integer"), "minimum": .integer(0), "maximum": .integer(3600)])
                    ])
                ])
            ])
        ])
    }
    private static func enumeration(_ options: [String], defaultValue: String) -> JSONValue {
        .object(["type": .string("string"), "enum": .array(options.map(JSONValue.string)), "default": .string(defaultValue)])
    }
}

struct AccountManagementSchedulerView: View {
    let id: Int
    @Environment(AppModel.self) private var model
    @State private var feature: JSONValue?
    @State private var config: JSONValue?
    @State private var error: String?
    var body: some View {
        List {
            if let error { NoticeView(title: "定时配置读取失败", message: error) }
            if let feature {
                let schema = ManagementRuleSchemas.request(feature: feature)
                NavigationLink("定时任务规则") {
                    ManagementCollectionView(resource: .init(title: "定时任务", path: base + "/rules", kind: .rules,
                        create: .init(title: "添加定时任务", path: base + "/rules", schema: schema), itemPath: base + "/rules/{id}", ruleSchema: schema))
                }
                if let config {
                    let whitelist: JSONValue = .object(["type": .string("object"), "properties": .object([
                        "allowed_command_whitelist": .object(["type": .string("array"), "items": .object(["type": .string("string")]),
                            "description": .string("仅列出的命令 key 允许自动执行，不带系统指令前缀。")])
                    ])])
                    ManagementActionLink(action: .init(title: "编辑自动指令白名单", path: base + "/config", method: "PATCH", schema: whitelist, seed: config, envelope: "config"))
                    NavigationLink("当前白名单") { List { ManagementValueView(value: config["allowed_command_whitelist"] ?? .array([])) }.navigationTitle("自动指令白名单") }
                }
                ManagementActionLink(action: .init(title: "启用定时任务", path: base, method: "PATCH", seed: .object(["enabled": .bool(true)])))
            } else if error == nil { ProgressView("正在读取定时配置…") }
        }
        .navigationTitle("定时任务与白名单").navigationBarTitleDisplayMode(.inline)
        .task { await load() }.refreshable { await load() }
    }
    private var base: String { "/api/accounts/\(id)/features/scheduler" }
    private func load() async {
        do {
            let matrix = try await model.request(path: "/api/feature-matrix")
            feature = matrix["features"]?.arrayValue?.first { $0["key"]?.stringValue == "scheduler" }
            if feature == nil { error = "服务器尚未提供定时任务插件。"; return }
            config = try await model.request(path: base + "/config"); error = nil
        } catch { self.error = error.localizedDescription }
    }
}
