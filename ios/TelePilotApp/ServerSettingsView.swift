import SwiftUI
import TelePilotCore

// MARK: - 服务器参数（第 19 轮，iOS 0.20.0，对齐 Web「系统设置 › 能力与通知」）
//
// 常用键（指令前缀、时区、运行日志与 Trace／Event Bus 设置等）用中文名称和说明的原生表单编辑；
// 其余键在「其它参数」里列出，仍可进入原始编辑（原 `ManagementDocumentView` + PATCH 表单）。
// 读写现有接口 `GET`／`PATCH /api/system/settings`：服务器按“未传字段保持不变”局部合并（`log_retention` 也只合并传入的子键），
// 所以保存时只提交改过的键；写操作只提交一次、不自动重试，结果未知时提示下拉核对。

/// 一个可原生编辑的设置项。`group` 为空表示顶层键，否则是 `log_retention` 这类嵌套对象里的子键。
struct ServerSettingSpec: Identifiable {
    enum Kind: Equatable {
        case text(maxLength: Int)
        case toggle
        case integer(ClosedRange<Int>)
        case logLevel
        case timezone
    }

    let key: String
    var group: String? = nil
    let title: String
    let detail: String
    let kind: Kind
    let symbol: String
    let fallback: JSONValue

    var id: String { group.map { "\($0).\(key)" } ?? key }
}

enum ServerSettingsCatalog {
    struct SettingsSection: Identifiable {
        let title: String
        let footer: String?
        let specs: [ServerSettingSpec]
        var id: String { title }
    }

    static let logGroup = "log_retention"

    static let sections: [SettingsSection] = [
        SettingsSection(title: "指令", footer: "修改后各账号的 worker 自动热加载，不需要重启。", specs: [
            ServerSettingSpec(key: "command_prefix", title: "指令前缀",
                              detail: "Telegram 里指令的开头字符，默认“,”，最多 3 个字符。",
                              kind: .text(maxLength: 3), symbol: "terminal", fallback: ","),
            ServerSettingSpec(key: "command_prefix_required", title: "账号本人必须带前缀",
                              detail: "关闭后，只有当前 userbot 账号本人发出的消息可以直接用命令名触发；群成员仍不会因为裸命令或系统前缀触发 userbot 命令。",
                              kind: .toggle, symbol: "textformat", fallback: true),
            ServerSettingSpec(key: "ai_context_messages", title: "AI 指令上下文条数",
                              detail: "0 = 不带上下文；大于 0 时，AI 指令会携带最近 N 条聊天记录（0～20）。",
                              kind: .integer(0...20), symbol: "text.bubble", fallback: 0),
            ServerSettingSpec(key: "command_echo_guard_previous_messages", title: "命令回声防误触",
                              detail: "群聊里检查前 N 条消息，避免纯命令的回声误触发；0 = 关闭（0～50）。",
                              kind: .integer(0...50), symbol: "arrow.uturn.left.circle", fallback: 8)
        ]),
        SettingsSection(title: "时区", footer: nil, specs: [
            ServerSettingSpec(key: "timezone", title: "服务器时区",
                              detail: "IANA 时区标识，例如 Asia/Shanghai。影响定时任务“下次触发／上次触发”等时间的计算与显示。",
                              kind: .timezone, symbol: "globe.asia.australia", fallback: "Asia/Shanghai")
        ]),
        SettingsSection(title: "运行日志", footer: "日志等级保存后立即影响新日志落库；保留天数填 0 表示不自动删除。", specs: [
            ServerSettingSpec(key: "runtime_log_min_level", group: logGroup, title: "运行日志等级",
                              detail: "低于这个等级的运行日志不保存。排查问题时可临时调到“调试”。",
                              kind: .logLevel, symbol: "line.3.horizontal.decrease.circle", fallback: "info"),
            ServerSettingSpec(key: "runtime_log_retention_days", group: logGroup, title: "运行日志保留天数",
                              detail: "0 = 不自动删除（0～3650）。", kind: .integer(0...3650), symbol: "calendar.badge.clock", fallback: 30),
            ServerSettingSpec(key: "runtime_log_max_message_chars", group: logGroup, title: "消息正文最多字符",
                              detail: "单条运行日志正文超出部分截断后保存（200～20000）。", kind: .integer(200...20000), symbol: "text.alignleft", fallback: 2000),
            ServerSettingSpec(key: "runtime_log_max_detail_chars", group: logGroup, title: "结构化详情最多字符",
                              detail: "运行日志附带的结构化详情最多保存多少字符（0～50000）。", kind: .integer(0...50000), symbol: "curlybraces", fallback: 8000)
        ]),
        SettingsSection(title: "Trace 与 Event Bus", footer: "Trace 记录每条消息在系统内部的处理时间线；Event Bus 把消息事件投递给订阅它的插件。", specs: [
            ServerSettingSpec(key: "trace_enabled", group: logGroup, title: "写入 Trace",
                              detail: "关闭后保留旧运行日志；仅用于排查 Trace 存储异常。",
                              kind: .toggle, symbol: "point.topleft.down.to.point.bottomright.curvepath", fallback: true),
            ServerSettingSpec(key: "event_bus_delivery_enabled", group: logGroup, title: "Event Bus 投递",
                              detail: "关闭后回退旧交互规则链路，适合部署回滚观察。",
                              kind: .toggle, symbol: "arrow.triangle.branch", fallback: true),
            ServerSettingSpec(key: "inline_updates_enabled", group: logGroup, title: "Inline 更新",
                              detail: "关闭后不拉取 inline_query / chosen_inline_result。",
                              kind: .toggle, symbol: "rectangle.and.text.magnifyingglass", fallback: true),
            ServerSettingSpec(key: "guest_updates_enabled", group: logGroup, title: "访客消息（Guest Mode）",
                              detail: "关闭后不拉取 guest_message：Bot 在未加入的会话被 @提及时不再回复。",
                              kind: .toggle, symbol: "person.crop.circle.badge.questionmark", fallback: true),
            ServerSettingSpec(key: "trace_retention_days", group: logGroup, title: "Trace 保留天数",
                              detail: "0 = 不自动删除（0～3650）。", kind: .integer(0...3650), symbol: "calendar", fallback: 30),
            ServerSettingSpec(key: "trace_payload_snapshot_retention_days", group: logGroup, title: "Payload 快照保留天数",
                              detail: "Trace 里消息内容快照的保留天数，0 = 不自动删除（0～3650）。",
                              kind: .integer(0...3650), symbol: "doc.text.magnifyingglass", fallback: 7),
            ServerSettingSpec(key: "native_raw_persist_enabled", group: logGroup, title: "保存完整 native_raw",
                              detail: "保存 Telegram 原始更新，便于深度排查，会占用更多存储。",
                              kind: .toggle, symbol: "externaldrive", fallback: false),
            ServerSettingSpec(key: "native_raw_retention_days", group: logGroup, title: "native_raw 保留天数",
                              detail: "0～30 天。", kind: .integer(0...30), symbol: "calendar", fallback: 1)
        ]),
        SettingsSection(title: "其它保留期限", footer: nil, specs: [
            ServerSettingSpec(key: "llm_usage_retention_days", group: logGroup, title: "AI 用量记录保留天数",
                              detail: "0 = 不自动删除（0～3650）。", kind: .integer(0...3650), symbol: "chart.bar", fallback: 30),
            ServerSettingSpec(key: "rate_limit_event_retention_days", group: logGroup, title: "风控事件保留天数",
                              detail: "0 = 不自动删除（0～3650）。", kind: .integer(0...3650), symbol: "gauge.with.dots.needle.33percent", fallback: 30)
        ])
    ]

    static var allSpecs: [ServerSettingSpec] { sections.flatMap(\.specs) }

    /// 原生表单已覆盖（或只读展示）的顶层键；其余键进「其它参数」。
    static var coveredTopLevelKeys: Set<String> {
        Set(allSpecs.filter { $0.group == nil }.map(\.key)).union([logGroup])
    }

    /// 只读展示、不在 App 里改的 `log_retention` 子键（涉及转账通知判定）。
    static let readOnlyLogKeys: Set<String> = ["payment_source_plugin_authoritative"]

    /// 其它顶层键的中文名；没有收录的显示原始键名。
    static func otherTitle(_ key: String) -> String {
        let names: [String: String] = [
            "kill_switch": "全局紧急停止",
            "ai_enabled": "AI 总开关",
            "sudo_enabled": "Sudo 功能",
            "login_security": "登录安全",
            "llm_limits": "AI 调用限额",
            "payout_limits": "出款上限",
            "remote_plugin_update_check": "远程插件更新检查",
            "app_update_target": "服务器更新来源",
            "ui_preferences": "Web 界面偏好"
        ]
        return names[key] ?? key
    }

    static func levelTitle(_ raw: String) -> String {
        switch raw {
        case "debug": "调试"
        case "info": "信息"
        case "warn": "警告"
        case "error": "错误"
        default: raw
        }
    }

    /// 从服务器返回值取出可编辑键的当前值（缺失时用与服务器相同的默认值）。
    static func values(from settings: JSONValue) -> [String: JSONValue] {
        var result: [String: JSONValue] = [:]
        for spec in allSpecs {
            let raw = spec.group.map { settings[$0]?[spec.key] } ?? settings[spec.key]
            if let raw, !raw.isNull { result[spec.id] = raw } else { result[spec.id] = spec.fallback }
        }
        return result
    }

    /// 只含改动键的 PATCH 请求体；嵌套对象只带改动的子键（服务器局部合并）。
    static func patch(baseline: [String: JSONValue], draft: [String: JSONValue]) -> JSONValue? {
        var body: [String: JSONValue] = [:]
        var nested: [String: [String: JSONValue]] = [:]
        for spec in allSpecs {
            guard let value = draft[spec.id], value != baseline[spec.id] else { continue }
            var sent = value
            if case .text = spec.kind, let text = value.stringValue { sent = .string(text.trimmingCharacters(in: .whitespacesAndNewlines)) }
            if spec.kind == .timezone, let text = value.stringValue { sent = .string(text.trimmingCharacters(in: .whitespacesAndNewlines)) }
            if let group = spec.group { nested[group, default: [:]][spec.key] = sent } else { body[spec.key] = sent }
        }
        for (group, values) in nested { body[group] = .object(values) }
        return body.isEmpty ? nil : .object(body)
    }

    /// 提交前的本地校验，与服务器规则一致；返回第一条错误。
    static func validate(_ draft: [String: JSONValue]) -> String? {
        for spec in allSpecs {
            let value = draft[spec.id]
            switch spec.kind {
            case .text(let maxLength):
                let text = value?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if text.isEmpty { return "\(spec.title)不能为空" }
                if text.count > maxLength { return "\(spec.title)最多 \(maxLength) 个字符" }
            case .integer(let range):
                guard let number = value?.intValue, range.contains(number) else {
                    return "\(spec.title)必须在 \(range.lowerBound)～\(range.upperBound) 之间"
                }
            case .timezone:
                let text = value?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if !text.isEmpty, TimeZone(identifier: text) == nil { return "无效时区：\(text)" }
            case .toggle, .logLevel:
                break
            }
        }
        return nil
    }
}

struct ServerSettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var settings: JSONValue?
    @State private var baseline: [String: JSONValue] = [:]
    @State private var draft: [String: JSONValue] = [:]
    @State private var loading = false
    @State private var loadError: String?
    @State private var saving = false
    @State private var saveMessage: String?
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var confirmingDiscard = false

    private var dirty: Bool { settings != nil && draft != baseline }

    var body: some View {
        List {
            Section {
                Text("常用的服务器参数，与 Web「系统设置 › 能力与通知」一致。改完点右上角「保存」，只提交改过的项；保存结果显示在页面底部。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if let settings {
                ForEach(ServerSettingsCatalog.sections) { section in
                    Section {
                        ForEach(section.specs) { spec in
                            ServerSettingRow(spec: spec, value: binding(spec))
                        }
                        if section.title == "Trace 与 Event Bus" { paymentRow(settings) }
                    } header: {
                        Text(section.title)
                    } footer: {
                        if let footer = section.footer { Text(footer) }
                    }
                }
                otherSection(settings)
            } else if let loadError {
                Section {
                    NoticeView(title: "服务器参数读取失败", message: loadError)
                    Button("重试", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            } else {
                Section { ProgressView("正在读取服务器参数…") }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .scrollDismissesKeyboard(.immediately)
        .navigationTitle("服务器参数")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("serverSettings.screen")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if saving {
                    ProgressView()
                } else {
                    Button("保存") { Task { await save() } }
                        .disabled(!dirty || uncertain)
                        .accessibilityIdentifier("serverSettings.save")
                }
            }
        }
        // 保存结果与未保存提示固定在底部（随键盘上移），不论滚到哪里、键盘是否弹出都看得到，
        // 不用为了确认「保存成功没有」再滚回页面顶部。
        .safeAreaInset(edge: .bottom, spacing: 0) { statusBar }
        .animation(.default, value: saveMessage)
        .animation(.default, value: saveError)
        .animation(.default, value: dirty)
        .refreshable { if !dirty { await load() } }
        .task { if settings == nil { await load() } }
    }

    /// 底部状态条：保存失败 / 保存成功 / 有未保存修改（可放弃），没有内容时不占位置。
    @ViewBuilder private var statusBar: some View {
        if saveError != nil || saveMessage != nil || dirty {
            VStack(alignment: .leading, spacing: 8) {
                if let saveError {
                    Label(saveError, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("serverSettings.saveError")
                } else if let saveMessage {
                    Label(saveMessage, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("serverSettings.saved")
                }
                if dirty {
                    HStack {
                        Text("有未保存的修改，点右上角「保存」提交。")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer(minLength: 8)
                        Button("放弃修改", systemImage: "arrow.uturn.backward", role: .destructive) { confirmingDiscard = true }
                            .font(.caption.weight(.semibold))
                            .buttonStyle(.borderless)
                            .accessibilityIdentifier("serverSettings.discard")
                            .confirmationDialog("放弃未保存的修改？", isPresented: $confirmingDiscard, titleVisibility: .visible) {
                                Button("放弃修改", role: .destructive) {
                                    draft = baseline
                                    saveError = nil
                                }
                                Button("继续编辑", role: .cancel) {}
                            }
                    }
                }
            }
            .font(.subheadline)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.bar)
            .overlay(alignment: .top) { Divider() }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("serverSettings.statusBar")
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    private func binding(_ spec: ServerSettingSpec) -> Binding<JSONValue> {
        Binding(get: { draft[spec.id] ?? spec.fallback }, set: { newValue in
            draft[spec.id] = newValue
            saveMessage = nil
        })
    }

    /// 转账通知判定开关：只读，切换请到 Web（涉及群内游戏币转账通知的判定口径）。
    @ViewBuilder private func paymentRow(_ settings: JSONValue) -> some View {
        if let value = settings[ServerSettingsCatalog.logGroup]?["payment_source_plugin_authoritative"]?.boolValue {
            VStack(alignment: .leading, spacing: 3) {
                LabeledContent {
                    Text(value ? "已开启" : "已关闭").foregroundStyle(.secondary)
                } label: {
                    Label("转账通知以插件结果为准", systemImage: "lock")
                }
                Text("默认关闭：以宿主旧解析器为准，插件只做对照。涉及转账通知判定，App 里只读；需要切换请在 Web「系统设置 › 能力与通知」里修改。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("serverSettings.paymentSource")
        }
    }

    @ViewBuilder private func otherSection(_ settings: JSONValue) -> some View {
        let others = (settings.objectValue ?? [:])
            .filter { !ServerSettingsCatalog.coveredTopLevelKeys.contains($0.key) }
            .sorted { $0.key < $1.key }
        Section {
            ForEach(others, id: \.key) { entry in
                VStack(alignment: .leading, spacing: 2) {
                    Text(ServerSettingsCatalog.otherTitle(entry.key))
                    Text(entry.key + " · " + Self.summary(entry.value))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                .accessibilityElement(children: .combine)
            }
            NavigationLink {
                ManagementDocumentView(title: "全部服务器参数", path: "/api/system/settings",
                                       actions: [.init(title: "编辑服务器参数", path: "/api/system/settings", method: "PATCH",
                                                       explanation: "按键名直接编辑服务器参数（原始编辑）。只提交填写的项，未填的保持不变。")])
            } label: {
                Label("原始编辑全部参数", systemImage: "slider.horizontal.3")
            }
            .accessibilityIdentifier("serverSettings.raw")
        } header: {
            Text("其它参数")
        } footer: {
            Text("紧急停止、AI 总开关、Sudo、限额等在各自页面里修改；这里列出服务器返回的其余参数，App 不认识的键也可以在「原始编辑」里改。")
        }
    }

    static func summary(_ value: JSONValue) -> String {
        switch value {
        case .bool(let flag): return flag ? "开启" : "关闭"
        case .object(let object): return object.isEmpty ? "（空）" : "\(object.count) 项设置"
        case .array(let array): return array.isEmpty ? "（空）" : "\(array.count) 项"
        default: return OpsFormat.text(value) ?? "（空）"
        }
    }

    private func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: "/api/system/settings")
            apply(value)
            loadError = nil
            uncertain = false
        } catch {
            loadError = OpsFormat.errorMessage(error)
        }
    }

    private func apply(_ value: JSONValue) {
        settings = value
        let values = ServerSettingsCatalog.values(from: value)
        baseline = values
        draft = values
    }

    private func save() async {
        guard !saving, !uncertain else { return }
        if let problem = ServerSettingsCatalog.validate(draft) {
            saveError = problem
            saveMessage = nil
            return
        }
        guard let body = ServerSettingsCatalog.patch(baseline: baseline, draft: draft) else { return }
        saving = true
        saveError = nil
        saveMessage = nil
        defer { saving = false }
        do {
            let response = try await model.request(path: "/api/system/settings", method: "PATCH", body: body)
            if response.objectValue != nil, response["command_prefix"] != nil {
                apply(response)
            } else {
                baseline = draft
            }
            saveMessage = "已保存到服务器"
        } catch {
            if (error as? APIError)?.isOutcomeUncertain == true {
                uncertain = true
                saveError = "保存结果未知：请下拉刷新，确认服务器上的实际值后再修改。"
            } else {
                saveError = "保存失败：" + error.localizedDescription
            }
        }
    }
}

/// 一行设置：标题、说明与对应的原生控件。
struct ServerSettingRow: View {
    let spec: ServerSettingSpec
    @Binding var value: JSONValue

    private var isTimezone: Bool { spec.kind == .timezone }
    private var localZone: String { TimeZone.current.identifier }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            control
            Text(spec.detail).font(.caption).foregroundStyle(.secondary)
            if isTimezone && value.stringValue != localZone {
                Button("使用本机时区（\(localZone)）", systemImage: "location") { value = .string(localZone) }
                    .font(.caption)
                    .buttonStyle(.borderless)
                    .accessibilityIdentifier("serverSettings.useLocalTimezone")
            }
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder private var control: some View {
        switch spec.kind {
        case .toggle:
            Toggle(isOn: Binding(get: { value.boolValue ?? (spec.fallback.boolValue ?? false) }, set: { value = .bool($0) })) {
                Label(spec.title, systemImage: spec.symbol)
            }
            .accessibilityIdentifier("serverSettings.toggle.\(spec.key)")
        case .integer:
            LabeledContent {
                // 用字符串绑定而不是 `TextField(value:format:)`：后者要按回车提交才写回，数字键盘没有回车，
                // 「保存」会一直不可用。输入非数字或清空时暂存为字符串，保存前的校验会提示范围。
                TextField("0", text: Binding(get: { value.intValue.map(String.init) ?? (value.stringValue ?? "") },
                                             set: { text in
                                                 let digits = text.filter(\.isNumber)
                                                 value = Int(digits).map { JSONValue.integer(Int64($0)) } ?? .string(digits)
                                             }))
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                    .frame(maxWidth: 120)
                    .accessibilityIdentifier("serverSettings.field.\(spec.key)")
            } label: {
                Label(spec.title, systemImage: spec.symbol)
            }
        case .text:
            LabeledContent {
                TextField(spec.fallback.stringValue ?? "", text: Binding(get: { value.stringValue ?? "" }, set: { value = .string($0) }))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .multilineTextAlignment(.trailing)
                    .font(.body.monospaced())
                    .frame(maxWidth: 120)
                    .accessibilityIdentifier("serverSettings.field.\(spec.key)")
            } label: {
                Label(spec.title, systemImage: spec.symbol)
            }
        case .timezone:
            LabeledContent {
                TextField("Asia/Shanghai", text: Binding(get: { value.stringValue ?? "" }, set: { value = .string($0) }))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.asciiCapable)
                    .multilineTextAlignment(.trailing)
                    .accessibilityIdentifier("serverSettings.field.\(spec.key)")
            } label: {
                Label(spec.title, systemImage: spec.symbol)
            }
        case .logLevel:
            Picker(selection: Binding(get: { value.stringValue ?? "info" }, set: { value = .string($0) })) {
                ForEach(["debug", "info", "warn", "error"], id: \.self) { level in
                    Text(ServerSettingsCatalog.levelTitle(level)).tag(level)
                }
            } label: {
                Label(spec.title, systemImage: spec.symbol)
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("serverSettings.picker.\(spec.key)")
        }
    }
}
