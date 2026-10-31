import SwiftUI
import TelePilotCore

// MARK: - 命中调试（第 19 轮，iOS 0.20.0，对齐 Web `/dispatch-debug`）
//
// 按账号模拟一条消息，按服务器的真实分发顺序「直通 → 命令 → 关键词 → 事件订阅」逐项显示命中与否和原因。
// 只调用现有接口 `POST /api/dispatch/simulate`（不发 Telegram、不执行插件，服务器行为不变）；会话从
// 会话白名单（`GET /api/accounts/{id}/ignored-peers`）选，消息可从该账号最近的消息日志（`GET /api/logs/messages`）里挑，
// 也可以全部手动填写。
//
// 可复用的导航目标：`DispatchDebugView(accountID:chatID:chatType:senderID:text:via:)`，参数全部可省略；
// 其它页面（例如运行树底部）直接 `NavigationLink { DispatchDebugView() }`，或放一行 `DispatchDebugEntryLink()`。

/// 一行「命中调试」入口（图标 `scope`，与运行树里的命中调试分支同一个符号）。其它页面可直接复用。
struct DispatchDebugEntryLink: View {
    var accountID: Int? = nil
    var title = "命中调试"
    var detail: String? = "模拟一条消息，逐步看直通、命令、关键词、事件订阅为什么命中或没命中。"
    var identifier = "dispatchDebug.entry"

    var body: some View {
        NavigationLink { DispatchDebugView(accountID: accountID) } label: {
            if let detail {
                LogKindLabel(title: title, symbol: "scope", detail: detail)
            } else {
                Label(title, systemImage: "scope")
            }
        }
        .accessibilityIdentifier(identifier)
    }
}

/// 命中调试的预填参数（第 20 轮，对齐 Web `dispatchDebugHref` / `parseDispatchDebugPrefill`）：
/// 日志消息详情「用命中调试复现」把这条消息的账号、会话、发送者、来源和正文带过去。
/// 规则与 Web 相同：来源只保留命中调试支持的 userbot / interaction_bot / webhook；会话类型按 chat_id 推断
/// （-100… 超级群，其它负数普通群，正数私聊）；正文去掉首尾空白、最多 2000 字。
struct DispatchDebugPrefill: Hashable {
    static let vias: Set<String> = ["userbot", "interaction_bot", "webhook"]
    static let chatTypes: Set<String> = ["group", "supergroup", "private", "channel"]

    var accountID: Int?
    var chatID: Int?
    var chatType: String?
    var senderID: Int?
    var text: String?
    var via: String?

    init(accountID: Int? = nil, chatID: Int? = nil, chatType: String? = nil, senderID: Int? = nil, text: String? = nil, via: String? = nil) {
        self.accountID = accountID
        self.chatID = chatID
        let type = chatType ?? Self.inferChatType(chatID)
        self.chatType = type.flatMap { Self.chatTypes.contains($0) ? $0 : nil }
        self.senderID = senderID
        let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.text = trimmed.isEmpty ? nil : String(trimmed.prefix(2000))
        self.via = via.flatMap { Self.vias.contains($0) ? $0 : nil }
    }

    /// 日志消息流的一条记录（`GET /api/logs/messages`）。旧服务器缺哪个字段就不带哪个。
    init(logMessage raw: JSONValue, accountID: Int? = nil) {
        self.init(accountID: accountID ?? raw["account_id"]?.intValue,
                  chatID: raw["chat_id"]?.intValue,
                  senderID: raw["sender_user_id"]?.intValue,
                  text: raw["text_preview"]?.stringValue,
                  via: raw["source_channel"]?.stringValue)
    }

    /// 至少有正文或会话才值得复现（旧服务器的记录可能两者都没有）。
    var isUseful: Bool { text != nil || chatID != nil }

    static func inferChatType(_ chatID: Int?) -> String? {
        guard let chatID, chatID != 0 else { return nil }
        if chatID <= -1_000_000_000_000 { return "supergroup" }
        return chatID < 0 ? "group" : "private"
    }
}

extension DispatchDebugView {
    init(prefill: DispatchDebugPrefill) {
        self.init(accountID: prefill.accountID, chatID: prefill.chatID, chatType: prefill.chatType,
                  senderID: prefill.senderID, text: prefill.text, via: prefill.via)
    }
}

/// 分发阶段与原因码的中文说明。原因码以服务器为准，没有收录的原样显示。
enum DispatchDebugText {
    static let stageOrder = ["direct_passthrough", "prefix_command", "keyword", "event_subscription"]

    static func stage(_ raw: String) -> String {
        switch raw {
        case "direct_passthrough": "直通"
        case "prefix_command": "命令"
        case "keyword": "关键词"
        case "event_subscription": "事件订阅"
        default: raw
        }
    }

    static func stageExplanation(_ raw: String) -> String {
        switch raw {
        case "direct_passthrough": "插件声明直通后，Userbot 收到的消息先原样交给它（按优先级）。"
        case "prefix_command": "以指令前缀开头、由账号本人发出的消息，交给声明了该命令的插件。"
        case "keyword": "消息全文等于交互规则或插件入口的关键词时命中。"
        case "event_subscription": "插件通过 Event Bus 订阅的消息事件，按来源、范围和过滤条件判断。"
        default: ""
        }
    }

    static func reason(_ code: String?) -> String? {
        guard let code, !code.isEmpty else { return nil }
        let map: [String: String] = [
            "matched": "命中",
            "command_matched": "命令命中",
            "command_not_matched": "没有对应的命令",
            "command_unauthorized": "无权使用该命令",
            "filter_not_matched": "条件不满足",
            "source_not_subscribed": "来源未订阅",
            "event_type_not_subscribed": "事件类型未订阅",
            "subscription_not_matched": "订阅未命中",
            "scope_not_matched": "事件范围不匹配",
            "account_not_matched": "不是这个账号",
            "chat_not_in_allowed_chats": "会话不在允许列表",
            "callback_not_owned": "回调按钮属于其它插件",
            "plugin_not_installed": "插件未安装",
            "plugin_disabled": "插件在该账号未启用（或未开直通）",
            "plugin_load_failed": "插件未加载",
            "manifest_invalid": "插件声明有误",
            "handler_error": "插件没有对应的处理函数",
            "permission_denied": "权限不足（只允许账号本人或 Sudo 用户）",
            "interaction_rule_owned": "已被交互规则接管",
            "event_bus_delivery_disabled": "Event Bus 投递已关闭",
            "runtime_profile_safe_watch": "值守模式下暂停",
            "platform_module_disabled": "平台模块已关闭",
            "channel_disabled": "来源通道已关闭",
            "capability_unavailable": "能力不可用",
            "payment_source_mismatch": "转账来源不匹配",
            "transfer_rule_not_matched": "转账规则不匹配",
            "bot_not_configured": "交互 Bot 未配置",
            "interaction_bot_not_in_chat": "交互 Bot 不在该会话",
            "userbot_offline": "Userbot 不在线",
            "session_not_found": "没有进行中的会话",
            "rate_limited": "触发频率限制",
            "empty_message_text": "消息为空"
        ]
        return map[code]
    }

    static func via(_ raw: String?) -> String {
        switch raw ?? "" {
        case "userbot": "Userbot"
        case "interaction_bot": "交互 Bot"
        case "webhook": "Webhook"
        case "": "—"
        default: raw ?? "—"
        }
    }

    static func chatType(_ raw: String) -> String {
        switch raw {
        case "private": "私聊"
        case "group": "群组"
        case "supergroup": "超级群"
        case "channel": "频道"
        default: raw
        }
    }

    static func direction(_ raw: String?) -> String {
        switch raw ?? "incoming" {
        case "outgoing": "账号发出"
        case "incoming": "收到的消息"
        default: raw ?? "收到的消息"
        }
    }

    /// 候选／命中项的一行标题：插件 · 入口，或命令、关键词。
    static func itemTitle(_ item: JSONValue) -> String {
        var parts: [String] = []
        if let plugin = OpsFormat.text(item["plugin_key"]) { parts.append(plugin) }
        if let entry = OpsFormat.text(item["entry_key"]) { parts.append("入口 " + entry) }
        if let command = OpsFormat.text(item["command"]) { parts.append("命令 " + command) }
        if let keywords = item["keywords"]?.arrayValue?.compactMap({ OpsFormat.text($0) }), !keywords.isEmpty {
            parts.append("关键词 " + keywords.prefix(6).joined(separator: "、") + (keywords.count > 6 ? "…" : ""))
        }
        if parts.isEmpty, let raw = OpsFormat.text(item["source"]) { parts.append(Self.source(raw)) }
        return parts.isEmpty ? "（未命名项）" : parts.joined(separator: " · ")
    }

    static func source(_ raw: String) -> String {
        switch raw {
        case "interaction_text_guard": "交互规则"
        case "manifest_interaction_entry": "插件入口"
        case "plugin_commands": "插件命令"
        default: raw
        }
    }

    /// 除标题字段外的次要信息（优先级、来源、订阅方式等），每项一行。
    static func itemDetails(_ item: JSONValue) -> [String] {
        var lines: [String] = []
        if let source = OpsFormat.text(item["source"]) { lines.append("来源：" + Self.source(source)) }
        if let priority = item["priority"]?.intValue { lines.append("优先级：\(priority)") }
        if let mode = OpsFormat.text(item["dispatch_mode"]) { lines.append("投递方式：" + mode) }
        if let scope = item["scope"], !scope.isNull, let text = compact(scope) { lines.append("范围：" + text) }
        if let filters = item["filters"], !filters.isNull, let text = compact(filters) { lines.append("过滤条件：" + text) }
        return lines
    }

    /// 结构化值压成一行，空对象／空数组返回 nil。
    static func compact(_ value: JSONValue) -> String? {
        if let object = value.objectValue, object.isEmpty { return nil }
        if let array = value.arrayValue, array.isEmpty { return nil }
        if value.objectValue != nil || value.arrayValue != nil {
            guard let data = try? value.data(), let text = String(data: data, encoding: .utf8) else { return nil }
            return AssistantRedaction.text(text)
        }
        return OpsFormat.text(value)
    }
}

struct DispatchDebugPeer: Identifiable, Hashable {
    let id: Int
    let peerID: Int
    let kind: String
    let label: String

    init?(_ value: JSONValue) {
        guard let peerID = value["peer_id"]?.intValue else { return nil }
        self.peerID = peerID
        self.id = value["id"]?.intValue ?? peerID
        self.kind = value["peer_kind"]?.stringValue ?? "group"
        let name = OpsFormat.text(value["peer_label"])
        self.label = name ?? (DispatchDebugText.chatType(kind) + " \(peerID)")
    }
}

/// 最近消息日志里的一条，用来一键填好会话、发送者和文本。
struct DispatchDebugRecentMessage: Identifiable, Hashable {
    let id: String
    let chatID: Int?
    let chatTitle: String?
    let senderID: Int?
    let senderName: String?
    let text: String
    let via: String?
    let date: Date?

    init?(_ value: JSONValue) {
        guard let text = value["text_preview"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        self.text = text
        self.id = OpsFormat.text(value["trace_id"]) ?? OpsFormat.text(value["id"]) ?? UUID().uuidString
        self.chatID = value["chat_id"]?.intValue
        self.chatTitle = OpsFormat.text(value["chat_title"])
        self.senderID = value["sender_user_id"]?.intValue
        self.senderName = OpsFormat.text(value["sender_name"])
        self.via = value["source_channel"]?.stringValue
        self.date = OpsFormat.date(value["started_at"])
    }

    var menuTitle: String {
        let who = [senderName, chatTitle].compactMap { $0 }.joined(separator: " · ")
        let preview = String(text.prefix(40)) + (text.count > 40 ? "…" : "")
        return who.isEmpty ? preview : "\(preview)（\(who)）"
    }
}

struct DispatchDebugView: View {
    var accountID: Int? = nil
    var chatID: Int? = nil
    var chatType: String? = nil
    var senderID: Int? = nil
    var text: String? = nil
    var via: String? = nil

    @Environment(AppModel.self) private var model
    @State private var selectedAccountID: Int?
    @State private var peers: [DispatchDebugPeer] = []
    @State private var peersLoading = false
    @State private var recent: [DispatchDebugRecentMessage] = []
    @State private var draftChatType = "group"
    @State private var draftChatID = ""
    @State private var draftSenderID = ""
    @State private var draftVia = "userbot"
    @State private var draftText = ""
    @State private var commandPrefix: String?
    @State private var trace: JSONValue?
    @State private var running = false
    @State private var error: String?
    @State private var seeded = false
    @State private var loadedAccountID: Int?

    private var currentAccountID: Int? { selectedAccountID ?? model.accounts.first?.id }
    private var stages: [JSONValue] { trace?["stages"]?.arrayValue ?? [] }
    private var matchedCount: Int { stages.filter { $0["matched"]?.boolValue == true }.count }

    var body: some View {
        List {
            Section {
                Text("模拟一条消息会被哪些插件和规则接住：按服务器的真实顺序「直通 → 命令 → 关键词 → 事件订阅」逐项显示命中与否和原因。只做判断，不会发到 Telegram，也不会执行插件。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if model.accounts.isEmpty {
                Section {
                    NoticeView(title: "暂无账号", message: "命中调试需要至少一个 Telegram 账号在运行。", symbol: "exclamationmark.triangle")
                }
            } else {
                inputSections
                actionSection
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
                            .accessibilityIdentifier("dispatchDebug.error")
                    }
                }
                if let trace { resultSections(trace) }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .scrollDismissesKeyboard(.immediately)
        .navigationTitle("命中调试")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("dispatchDebug.screen")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("复制结果", systemImage: "doc.on.doc") { copyResult() }
                    .disabled(trace == nil)
                    .accessibilityIdentifier("dispatchDebug.copy")
            }
        }
        .task {
            if !seeded { seed() }
            if commandPrefix == nil { await loadPrefix() }
        }
        .task(id: currentAccountID) {
            if loadedAccountID != currentAccountID { await loadAccountData() }
        }
    }

    // MARK: 输入

    @ViewBuilder private var inputSections: some View {
        Section {
            Picker(selection: Binding(get: { currentAccountID ?? 0 }, set: { newValue in
                guard newValue != currentAccountID else { return }
                selectedAccountID = newValue
                draftChatID = ""
                trace = nil
                error = nil
            })) {
                ForEach(model.accounts) { account in
                    Text(AccountPresentation.name(account)).tag(account.id)
                }
            } label: {
                Label("账号", systemImage: "person.crop.circle.badge.checkmark")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("dispatchDebug.account")

            Picker(selection: Binding(get: { selectedPeerTag }, set: { applyPeer($0) })) {
                Text(peersLoading ? "正在读取…" : "手动填写").tag("")
                ForEach(peers) { peer in
                    Text("\(peer.label) · \(peer.peerID)").tag(String(peer.peerID))
                }
            } label: {
                Label("会话", systemImage: "bubble.left.and.bubble.right")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("dispatchDebug.peer")

            Menu {
                if recent.isEmpty {
                    Text("这个账号最近没有消息记录")
                } else {
                    ForEach(recent) { message in
                        Button(message.menuTitle) { applyRecent(message) }
                    }
                }
            } label: {
                // 标签撑满整行：Menu 只响应标签区域，不撑满时点行的右半边打不开菜单。
                Label("从最近消息选择", systemImage: "clock.arrow.circlepath")
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .accessibilityIdentifier("dispatchDebug.recent")
        } header: {
            Text("账号与会话")
        } footer: {
            if let account = model.accounts.first(where: { $0.id == currentAccountID }) {
                Text("\(AccountPresentation.status(account.status)) · ID \(account.id)。会话来自该账号的「允许会话」；选中会话或最近消息会自动填好 Chat ID 和会话类型。")
            }
        }

        Section {
            Picker(selection: $draftVia) {
                Text("Userbot").tag("userbot")
                Text("交互 Bot").tag("interaction_bot")
                Text("Webhook").tag("webhook")
            } label: {
                Label("来源", systemImage: "arrow.down.forward.circle")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("dispatchDebug.via")
            Picker(selection: $draftChatType) {
                ForEach(["group", "supergroup", "private", "channel"], id: \.self) { kind in
                    Text(DispatchDebugText.chatType(kind)).tag(kind)
                }
            } label: {
                Label("会话类型", systemImage: "person.2")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("dispatchDebug.chatType")
            LabeledContent {
                TextField("-100123", text: $draftChatID)
                    .keyboardType(.numbersAndPunctuation)
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                    .accessibilityIdentifier("dispatchDebug.chatID")
            } label: {
                Label("Chat ID", systemImage: "number")
            }
            LabeledContent {
                TextField("可选", text: $draftSenderID)
                    .keyboardType(.numbersAndPunctuation)
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                    .accessibilityIdentifier("dispatchDebug.senderID")
            } label: {
                Label("发送者 ID", systemImage: "person.crop.circle")
            }
        } header: {
            Text("消息来源")
        } footer: {
            Text("发送者 ID 留空时按群成员处理；填账号本人或 Sudo 用户的 ID 可以检查“只允许本人”的插件。")
        }

        Section {
            TextField("\(commandPrefix ?? ",")help 或关键词文本", text: $draftText, axis: .vertical)
                .lineLimit(3...8)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("dispatchDebug.text")
        } header: {
            Text("消息文本")
        } footer: {
            if let commandPrefix {
                Text("当前指令前缀是「\(commandPrefix)」。模拟的消息按“收到的消息”处理，前缀命令只在账号本人发出时触发，所以命令阶段通常显示未命中并写明原因。")
            }
        }
    }

    private var actionSection: some View {
        Section {
            Button {
                Task { await simulate() }
            } label: {
                Label(running ? "正在模拟…" : "模拟命中", systemImage: "play.circle")
            }
            .disabled(running || currentAccountID == nil || draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .frame(minHeight: 44)
            .accessibilityIdentifier("dispatchDebug.run")
            if running { ProgressView("正在请求服务器模拟…") }
            if trace != nil || !draftText.isEmpty {
                Button("清空", systemImage: "trash", role: .destructive) {
                    draftText = ""
                    trace = nil
                    error = nil
                }
                .disabled(running)
                .accessibilityIdentifier("dispatchDebug.clear")
            }
        }
    }

    // MARK: 结果

    @ViewBuilder private func resultSections(_ trace: JSONValue) -> some View {
        Section {
            let total = stages.count
            Label {
                Text(matchedCount > 0 ? "有 \(matchedCount)/\(total) 个阶段命中" : "4 个阶段都没有命中")
            } icon: {
                Image(systemName: matchedCount > 0 ? "checkmark.circle.fill" : "xmark.circle")
                    .foregroundStyle(matchedCount > 0 ? Color.green : Color.secondary)
            }
            .font(.headline)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("dispatchDebug.summary")
            LabeledContent("来源", value: DispatchDebugText.via(trace["via"]?.stringValue))
            LabeledContent("方向", value: DispatchDebugText.direction(trace["chat"]?["direction"]?.stringValue))
            if let chat = trace["chat"]?["chat_id"]?.intValue { LabeledContent("Chat ID", value: String(chat)) }
            if let sender = trace["chat"]?["sender_id"]?.intValue { LabeledContent("发送者 ID", value: String(sender)) }
        } header: {
            Text("模拟结果")
        }
        ForEach(Array(stages.enumerated()), id: \.offset) { index, stage in
            DispatchDebugStageSection(index: index, stage: stage)
        }
        Section {
            DisclosureGroup {
                Text((try? trace.prettyText()).map(AssistantRedaction.text) ?? "（无法显示）")
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            } label: {
                Label("原始结果", systemImage: "curlybraces")
            }
            .accessibilityIdentifier("dispatchDebug.raw")
        }
    }

    // MARK: 动作

    private var selectedPeerTag: String {
        let trimmed = draftChatID.trimmingCharacters(in: .whitespaces)
        return peers.contains { String($0.peerID) == trimmed } ? trimmed : ""
    }

    private func applyPeer(_ tag: String) {
        guard !tag.isEmpty else { return }
        draftChatID = tag
        if let peer = peers.first(where: { String($0.peerID) == tag }) { draftChatType = peer.kind }
        trace = nil
    }

    private func applyRecent(_ message: DispatchDebugRecentMessage) {
        draftText = message.text
        if let chat = message.chatID {
            draftChatID = String(chat)
            if let peer = peers.first(where: { $0.peerID == chat }) {
                draftChatType = peer.kind
            } else {
                draftChatType = chat > 0 ? "private" : "group"
            }
        }
        draftSenderID = message.senderID.map(String.init) ?? ""
        if let via = message.via, ["userbot", "interaction_bot", "webhook"].contains(via) { draftVia = via }
        trace = nil
        error = nil
    }

    private func seed() {
        seeded = true
        if let accountID { selectedAccountID = accountID }
        if let chatID { draftChatID = String(chatID) }
        if let chatType { draftChatType = chatType }
        if let senderID { draftSenderID = String(senderID) }
        if let text { draftText = text }
        if let via { draftVia = via }
    }

    private func loadPrefix() async {
        if let value = try? await model.request(path: "/api/system/settings") {
            commandPrefix = value["command_prefix"]?.stringValue ?? ","
        }
    }

    private func loadAccountData() async {
        guard let id = currentAccountID else { return }
        loadedAccountID = id
        peersLoading = true
        defer { peersLoading = false }
        let model = self.model
        async let peerResult = OpsFetch.result(model, "/api/accounts/\(id)/ignored-peers")
        async let recentResult = OpsFetch.result(model, OpsFormat.path("/api/logs/messages", [("account_id", String(id)), ("limit", "20")]))
        let (peerValue, recentValue) = await (peerResult, recentResult)
        guard currentAccountID == id else { return }
        if case .success(let value) = peerValue {
            peers = (value.arrayValue ?? value["items"]?.arrayValue ?? []).compactMap(DispatchDebugPeer.init)
        } else {
            peers = []
        }
        if case .success(let value) = recentValue {
            recent = (value.arrayValue ?? []).compactMap(DispatchDebugRecentMessage.init)
        } else {
            recent = []
        }
    }

    private func simulate() async {
        guard !running, let accountID = currentAccountID else { return }
        let text = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { error = "请输入要模拟的文本"; return }
        let chat: Int?
        let sender: Int?
        do {
            chat = try Self.optionalInteger(draftChatID, field: "Chat ID")
            sender = try Self.optionalInteger(draftSenderID, field: "发送者 ID")
        } catch {
            self.error = error.localizedDescription
            return
        }
        running = true
        error = nil
        defer { running = false }
        let body: JSONValue = [
            "account_id": .integer(Int64(accountID)),
            "chat_type": .string(draftChatType),
            "chat_id": chat.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null,
            "sender_id": sender.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null,
            "text": .string(text),
            "via": .string(draftVia)
        ]
        do {
            let response = try await model.request(path: "/api/dispatch/simulate", method: "POST", body: body)
            guard response["stages"]?.arrayValue != nil else {
                trace = nil
                error = "服务器返回的结果无法识别，请更新服务器后再试。"
                return
            }
            trace = response
        } catch {
            trace = nil
            self.error = Self.message(for: error)
        }
    }

    private func copyResult() {
        guard let trace else { return }
        var lines = ["命中调试：账号 \(trace["account_id"]?.intValue.map(String.init) ?? "—") · 来源 \(DispatchDebugText.via(trace["via"]?.stringValue))",
                     "文本：\(trace["text"]?.stringValue ?? draftText)"]
        for (index, stage) in stages.enumerated() {
            let name = DispatchDebugText.stage(stage["stage"]?.stringValue ?? "")
            let matched = stage["matched"]?.boolValue == true ? "命中" : "未命中"
            let code = stage["reason_code"]?.stringValue ?? ""
            lines.append("#\(index + 1) \(name)：\(matched)，\(stage["message"]?.stringValue ?? "")（\(DispatchDebugText.reason(code) ?? code)）")
        }
        OpsFormat.copyForDiagnosis(lines)
    }

    struct InputError: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func optionalInteger(_ raw: String, field: String) throws -> Int? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard let value = Int(trimmed) else { throw InputError(message: "\(field) 必须是整数") }
        return value
    }

    static func message(for error: Error) -> String {
        if let apiError = error as? APIError, case .server(let status, let code) = apiError {
            if status == 503 || code == "WORKER_OFFLINE" {
                return "这个账号的 worker 未在线或没有返回结果：先在“账号”里确认账号正在运行。"
            }
            if status == 422 { return "填写的内容不符合要求（HTTP 422），请检查 Chat ID、发送者 ID 和文本长度。" }
        }
        return OpsFormat.errorMessage(error)
    }
}

/// 一个分发阶段：命中状态、服务器给的说明与中文原因，以及命中项／候选项／订阅决策。
struct DispatchDebugStageSection: View {
    let index: Int
    let stage: JSONValue

    private var key: String { stage["stage"]?.stringValue ?? "" }
    private var matched: Bool { stage["matched"]?.boolValue == true }
    private var code: String { stage["reason_code"]?.stringValue ?? "" }

    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 4) {
                Label {
                    Text(matched ? "命中" : "未命中").font(.body.weight(.semibold))
                } icon: {
                    Image(systemName: matched ? "checkmark.circle.fill" : "xmark.circle")
                        .foregroundStyle(matched ? Color.green : Color.secondary)
                }
                if let message = OpsFormat.text(stage["message"]) {
                    Text(message).font(.subheadline)
                }
                HStack(spacing: 6) {
                    Text("原因：" + (DispatchDebugText.reason(code) ?? "见原因码"))
                    Text(code).font(.caption.monospaced())
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            .padding(.vertical, 2)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("dispatchDebug.stage.\(key)")
            .accessibilityValue(matched ? "命中" : "未命中")
            if let command = OpsFormat.text(stage["command"]) { LabeledContent("命令", value: command) }
            if let prefix = OpsFormat.text(stage["prefix"]) { LabeledContent("指令前缀", value: prefix) }
            group("命中项", stage["matches"]?.arrayValue, identifier: "matches", open: true)
            group("候选项", stage["candidates"]?.arrayValue, identifier: "candidates", open: false)
            group("订阅决策", stage["decisions"]?.arrayValue, identifier: "decisions", open: false)
        } header: {
            Text("#\(index + 1) \(DispatchDebugText.stage(key))")
        } footer: {
            let explanation = DispatchDebugText.stageExplanation(key)
            if !explanation.isEmpty { Text(explanation) }
        }
    }

    @ViewBuilder private func group(_ title: String, _ items: [JSONValue]?, identifier: String, open: Bool) -> some View {
        if let items, !items.isEmpty {
            DispatchDebugItemGroup(title: title, items: items, initiallyExpanded: open && items.count <= 6)
                .accessibilityIdentifier("dispatchDebug.\(key).\(identifier)")
        }
    }
}

struct DispatchDebugItemGroup: View {
    let title: String
    let items: [JSONValue]
    @State private var expanded: Bool

    init(title: String, items: [JSONValue], initiallyExpanded: Bool) {
        self.title = title
        self.items = items
        _expanded = State(initialValue: initiallyExpanded)
    }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                DispatchDebugItemRow(item: item)
            }
        } label: {
            Label("\(title) \(items.count)", systemImage: "list.bullet.rectangle.portrait")
        }
    }
}

struct DispatchDebugItemRow: View {
    let item: JSONValue

    var body: some View {
        let matched = item["matched"]?.boolValue == true
        let code = item["reason_code"]?.stringValue
        VStack(alignment: .leading, spacing: 3) {
            Label {
                Text(DispatchDebugText.itemTitle(item)).font(.subheadline.weight(.medium))
            } icon: {
                Image(systemName: matched ? "checkmark.circle.fill" : "xmark.circle")
                    .foregroundStyle(matched ? Color.green : Color.secondary)
            }
            let reason = OpsFormat.text(item["message"]) ?? DispatchDebugText.reason(code)
            if matched || reason != nil || code != nil {
                Text([matched ? "命中" : "未命中", reason, code].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            ForEach(DispatchDebugText.itemDetails(item), id: \.self) { line in
                Text(line).font(.caption2).foregroundStyle(.secondary).lineLimit(3)
            }
        }
        .padding(.vertical, 1)
        .accessibilityElement(children: .combine)
    }
}
