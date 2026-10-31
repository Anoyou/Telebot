import SwiftUI
import TelePilotCore

enum ManagementRuleSchemas {
    /// 规则请求体 schema。插件声明 `x-ui-mode: rules`（契约 2）时 config 用 `x-rule-schema`（省略时整个 config_schema），
    /// 并把 `x-rule-summary` / `x-dry-run` 带在顶层，供规则列表摘要与试运行页读取。
    static func request(feature: JSONValue) -> JSONValue {
        let configSchema = feature["config_schema"]
        var config: JSONValue
        if feature["key"]?.stringValue == "scheduler" { config = scheduler }
        else if let rule = PluginRuleForm.ruleSchema(configSchema) { config = rule }
        else { config = configSchema ?? .object(["type": .string("object"), "additionalProperties": .bool(true)]) }
        let pluginRule = PluginRuleForm.isRulesMode(configSchema)
        if pluginRule, var object = config.objectValue {
            object["x-telepilot-kind"] = .string(pluginRuleKind)
            config = .object(object)
        }
        var root: [String: JSONValue] = [
            "type": .string("object"), "required": .array([.string("name"), .string("config")]),
            "properties": .object([
                "name": .object(["type": .string("string"), "minLength": .integer(1)]),
                "enabled": .object(["type": .string("boolean"), "default": .bool(true)]),
                "priority": .object(["type": .string("integer"), "default": .integer(100)]),
                "config": config
            ])
        ]
        if pluginRule {
            if let summary = configSchema?["x-rule-summary"] { root["x-rule-summary"] = summary }
            root["x-dry-run"] = configSchema?["x-dry-run"] ?? .object([:])
        }
        return .object(root)
    }
    /// 规则模式插件的 config 子表单标记：可选字段直接显示控件（与插件配置页一致），按 `x-ui-visible-if` 显隐。
    static let pluginRuleKind = "plugin-rule"
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
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "定时任务", path: base + "/rules", kind: .rules,
                        create: .init(title: "添加定时任务", path: base + "/rules", schema: schema), itemPath: base + "/rules/{id}", ruleSchema: schema))
                } label: { Label("定时任务规则", systemImage: "calendar.badge.clock") }
                if let config {
                    let whitelist: JSONValue = .object(["type": .string("object"), "properties": .object([
                        "allowed_command_whitelist": .object(["type": .string("array"), "items": .object(["type": .string("string")]),
                            "description": .string("仅列出的命令 key 允许自动执行，不带系统指令前缀。")])
                    ])])
                    ManagementActionLink(action: .init(title: "编辑自动指令白名单", path: base + "/config", method: "PATCH", schema: whitelist, seed: config, envelope: "config"), symbol: "checklist")
                    NavigationLink { List { ManagementValueView(value: config["allowed_command_whitelist"] ?? .array([])) }.navigationTitle("自动指令白名单").navigationBarTitleDisplayMode(.inline) } label: {
                        Label("当前白名单", systemImage: "list.bullet")
                    }
                }
                ManagementActionLink(action: .init(title: "启用定时任务", path: base, method: "PATCH", seed: .object(["enabled": .bool(true)])), symbol: "power")
            } else if error == nil { ProgressView("正在读取定时配置…") }
        }
        .symbolRenderingMode(.hierarchical)
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

// MARK: - 通用规则页（插件 UI 契约 2：x-rule-summary / x-dry-run）

/// 规则列表的一行：名称、启用状态与 `x-rule-summary` 声明的摘要字段（与 Web 规则区同一套摘要）。
struct PluginRuleRow: View {
    let item: JSONValue
    let requestSchema: JSONValue

    var body: some View {
        let config = requestSchema["properties"]?["config"] ?? .null
        let properties = config["properties"]?.objectValue ?? [:]
        let keys = PluginRuleForm.summaryKeys(ruleSchema: config, declared: requestSchema["x-rule-summary"])
        let enabled = item["enabled"]?.boolValue ?? true
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 8) {
                Text(item["name"]?.stringValue ?? "未命名规则").font(.body.weight(.medium)).lineLimit(1)
                Spacer(minLength: 4)
                Label(enabled ? "启用" : "停用", systemImage: enabled ? "checkmark.circle.fill" : "pause.circle")
                    .labelStyle(.titleAndIcon).font(.caption).foregroundStyle(enabled ? Color.green : Color.secondary)
            }
            ForEach(keys, id: \.self) { key in
                let spec = properties[key]
                Text(ManagementText.fieldLabel(key, schema: spec ?? .object([:])) + "：" + PluginRuleForm.formatValue(spec, item["config"]?[key]))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("plugin.rule.row")
    }
}

/// 规则试运行（`POST …/rules/{id}/dry-run`）：样例字段由插件 `x-dry-run.sample_fields` 声明，不会真的发送。
struct PluginRuleDryRunView: View {
    let path: String
    let spec: PluginRuleForm.DryRunSpec
    @Environment(AppModel.self) private var model
    @State private var message = ""
    @State private var chatType = "private"
    @State private var chatID = ""
    @State private var running = false
    @State private var result: JSONValue?
    @State private var error: String?

    private var chatIDInvalid: Bool {
        let text = chatID.trimmingCharacters(in: .whitespacesAndNewlines)
        return !text.isEmpty && PluginRuleForm.parseChatID(text) == nil
    }

    var body: some View {
        Form {
            Section { Text(spec.description).font(.subheadline).foregroundStyle(.secondary) }
            Section("样例") {
                if spec.sampleFields.contains("message") {
                    StackedTextField(title: "样例消息", text: $message, prompt: "模拟群里收到的一条消息",
                                     identifier: "plugin.rule.dryRun.message")
                }
                if spec.sampleFields.contains("chat_type") {
                    Picker("会话类型", selection: $chatType) {
                        Text("私聊").tag("private")
                        Text("群组").tag("group")
                        Text("频道").tag("channel")
                    }
                    .accessibilityIdentifier("plugin.rule.dryRun.chatType")
                }
                if spec.sampleFields.contains("chat_id") {
                    LabeledTextField(title: "会话 ID", text: $chatID, optional: true, keyboard: .numbersAndPunctuation,
                                     identifier: "plugin.rule.dryRun.chatID")
                    if chatIDInvalid { Text("会话 ID 只能是整数，例如 -1001234567890。").font(.caption).foregroundStyle(.red) }
                }
            }
            Section {
                Button { Task { await run() } } label: {
                    Label(running ? "正在试运行…" : "开始试运行", systemImage: "play.circle")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .disabled(running || chatIDInvalid)
                .listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
                .accessibilityIdentifier("plugin.rule.dryRun.submit")
            } footer: { Text("只验证是否命中和将输出什么，不会真的发送消息。") }
            if let error { Section { NoticeView(title: "试运行未完成", message: error) } }
            if let result {
                let matched = result["matched"]?.boolValue == true
                Section("结果") {
                    Label(matched ? "命中：是" : "命中：否", systemImage: matched ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(matched ? Color.green : Color.secondary)
                        .accessibilityIdentifier("plugin.rule.dryRun.matched")
                    if let output = result["output"]?.stringValue, !output.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("将输出").font(.caption).foregroundStyle(.secondary)
                            Text(output).textSelection(.enabled)
                        }
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("plugin.rule.dryRun.output")
                    }
                }
                let logs = result["detail"]?["logs"]?.arrayValue ?? []
                if !logs.isEmpty {
                    Section("判断过程") {
                        ForEach(Array(logs.enumerated()), id: \.offset) { _, log in
                            Text(log["msg"]?.stringValue ?? ManagementText.scalar(log)).font(.footnote)
                        }
                    }
                }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle(spec.title).navigationBarTitleDisplayMode(.inline)
    }

    private func run() async {
        running = true
        defer { running = false }
        var body: [String: JSONValue] = ["sample_message": .string(message)]
        if spec.sampleFields.contains("chat_type") { body["sample_chat_type"] = .string(chatType) }
        if let id = PluginRuleForm.parseChatID(chatID) { body["sample_chat_id"] = .integer(id) }
        do {
            result = try await model.request(path: path, method: "POST", body: .object(body))
            error = nil
        } catch {
            self.error = error.localizedDescription
            result = nil
        }
    }
}
