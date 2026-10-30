import SwiftUI
import TelePilotCore

/// 交互中心（iOS 0.16.0，对齐 Web `/interaction`）。
///
/// 按账号管理交互 Bot：交互总闸、交互 Bot Token、互动规则与玩法入口、查询指令与通知模板。
/// 读写与 Web 相同的接口：`GET`／`PUT /api/accounts/{id}/interaction-bot`；玩法入口来自
/// `GET /api/feature-matrix` 的 `interaction_entries`，生效群可从会话白名单（`/ignored-peers`）里选。
///
/// 保存约定（与 Web `buildInteractionPayload` 一致）：
/// - 服务器把没有 `rules` 的 PUT 当作“清空规则”，所以每次保存都带完整的 `rules`，并把第一条启用规则镜像到旧版顶层字段；
/// - 规则以服务器返回的原始对象为底改写已编辑的字段，App 不认识的字段（如插件玩法参数 `module_config`）原样保留；
/// - 写操作只提交一次，不自动重试；失败或结果未知时保留草稿并提示核对。
struct InteractionCenterView: View {
    /// 作为 Dock 一级 Tab 根页面时为真：大标题（与概览、插件、更多一致）。
    var isTabRoot = false
    /// 从插件详情「交互规则 ›」进入：先选中该账号，并只列出启动这个插件的规则（可切换为全部）。
    var initialAccountID: Int? = nil
    var focusPluginKey: String? = nil
    @Environment(AppModel.self) private var model
    @State private var selectedAccountID: Int?
    /// 插件矩阵里的插件（按 key），规则编辑页「插件配置 ›」跳转用。
    @State private var features: [String: JSONValue] = [:]
    @State private var showAllRules = false
    @State private var serverValue: JSONValue?
    @State private var baseline = InteractionDraft()
    @State private var draft = InteractionDraft()
    @State private var loading = false
    @State private var loadError: String?
    @State private var saving = false
    @State private var saveMessage: String?
    @State private var saveError: String?
    @State private var entries: [InteractionEntryOption] = []
    @State private var peers: [InteractionPeer] = []
    @State private var confirmingDiscard = false
    /// 已读取的账号。`.task` 在推入规则编辑页再返回时会重新触发，只在换账号（或尚未读到）时读取，
    /// 否则会用服务器值覆盖刚在编辑页里改的草稿。
    @State private var loadedAccountID: Int?
    /// 每次保存（成功或失败）递增，用来把列表滚回顶部的结果行。
    @State private var resultToken = 0

    private var dirty: Bool { serverValue != nil && draft != baseline }
    private var accountID: Int? { selectedAccountID ?? initialAccountID ?? model.accounts.first?.id }
    /// 只看某个插件的规则（来自插件详情跳转，且用户没切到“全部规则”）。
    private var focusKey: String? { showAllRules ? nil : focusPluginKey }
    private var visibleRuleIDs: [String] {
        draft.rules.filter { rule in focusKey.map { rule.action == "module" && rule.moduleKey == $0 } ?? true }.map(\.id)
    }
    private var focusPluginName: String {
        guard let key = focusPluginKey else { return "" }
        return features[key]?["display_name"]?.stringValue ?? key
    }

    var body: some View {
        ScrollViewReader { proxy in
            content
                .onChange(of: resultToken) { _, _ in
                    // 结果显示在列表顶部：保存时多半停在下方的规则区，滚回顶部让结果可见。
                    withAnimation { proxy.scrollTo("interaction.result", anchor: .top) }
                }
        }
    }

    private var content: some View {
        List {
            if model.accounts.isEmpty {
                Section {
                    NoticeView(title: "暂无账号", message: "交互中心需要先绑定一个 Telegram 账号，再配置对应的交互 Bot 和互动规则。",
                               symbol: "exclamationmark.triangle")
                }
            } else {
                accountSection
                if let saveError {
                    Section {
                        Label(saveError, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
                            .accessibilityIdentifier("interaction.saveError")
                            .id("interaction.result")
                    }
                } else if let saveMessage {
                    Section {
                        Label(saveMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                            .accessibilityIdentifier("interaction.saved")
                            .id("interaction.result")
                    }
                }
                if let serverValue {
                    statusSection(serverValue)
                    switchSection(serverValue)
                    rulesSection
                    advancedSection
                } else if let loadError {
                    Section {
                        NoticeView(title: "交互配置加载失败", message: loadError)
                        Button("重试", systemImage: "arrow.clockwise") { Task { await load() } }
                    }
                } else {
                    Section { ProgressView("正在读取交互配置…").accessibilityIdentifier("interaction.loading") }
                }
            }
        }
        .symbolRenderingMode(.hierarchical)
        // 导航名 = 页面标题：Dock 格与「更多」里都叫「交互」。
        .navigationTitle("交互")
        .navigationBarTitleDisplayMode(isTabRoot ? .large : .inline)
        .accessibilityIdentifier("interaction.screen")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if saving {
                    ProgressView()
                } else {
                    Button("保存") { Task { await save() } }
                        .disabled(!dirty)
                        .accessibilityIdentifier("interaction.save")
                }
            }
        }
        .refreshable { if !dirty { await load() } }
        .task(id: accountID) {
            if entries.isEmpty { await loadEntries() }
            if loadedAccountID != accountID || serverValue == nil { await load() }
        }
    }

    // MARK: 账号

    private var accountSection: some View {
        Section {
            Picker(selection: Binding(get: { accountID ?? 0 }, set: { selectedAccountID = $0 })) {
                ForEach(model.accounts) { account in
                    Text(AccountPresentation.name(account)).tag(account.id)
                }
            } label: {
                Label("选择配置的账号", systemImage: "person.crop.circle.badge.checkmark")
            }
            .pickerStyle(.menu)
            .disabled(dirty || saving)
            .accessibilityIdentifier("interaction.account")
            if dirty {
                Button("放弃修改", systemImage: "arrow.uturn.backward", role: .destructive) { confirmingDiscard = true }
                    .accessibilityIdentifier("interaction.discard")
                    .confirmationDialog("放弃未保存的修改？", isPresented: $confirmingDiscard, titleVisibility: .visible) {
                        Button("放弃修改", role: .destructive) {
                            draft = baseline
                            saveError = nil
                        }
                        Button("继续编辑", role: .cancel) {}
                    }
            }
        } footer: {
            if dirty {
                Text("有未保存的修改：保存或放弃后才能切换账号、下拉刷新。")
            } else if let account = model.accounts.first(where: { $0.id == accountID }) {
                Text("\(AccountPresentation.status(account.status)) · ID \(account.id)")
            }
        }
    }

    // MARK: 运行状态（只读，取服务器最近一次返回）

    private func statusSection(_ value: JSONValue) -> some View {
        let enabled = value["enabled"]?.boolValue ?? false
        let running = value["interaction_running"]?.boolValue ?? false
        let hasToken = value["has_interaction_bot_token"]?.boolValue ?? false
        let username = value["interaction_bot_username"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 }
        let lastError = value["interaction_last_error"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines)
        let rules = value["rules"]?.arrayValue ?? []
        let activeRules = rules.filter { $0["enabled"]?.boolValue ?? true }.count
        let chats = Set(rules.flatMap { ($0["chat_ids"]?.arrayValue ?? []).compactMap(\.intValue) }).count
        let memberships = value["interaction_chat_memberships"]?.arrayValue ?? []
        let dlq = value["polling_dlq_count"]?.intValue ?? 0
        return Section {
            statusRow("交互总闸", enabled ? "已启用" : "未启用", symbol: enabled ? "power.circle.fill" : "power.circle",
                      tint: enabled ? .green : .secondary, id: "gate")
            statusRow("交互 Bot", hasToken ? (username.map { "@\($0)" } ?? "已配置") : "待配置 Token",
                      symbol: hasToken ? "checkmark.circle.fill" : "exclamationmark.triangle.fill",
                      tint: hasToken ? .green : .orange, id: "token")
            statusRow("监听状态", running ? "运行中" : (enabled ? "未运行" : "未启用"),
                      symbol: running ? "checkmark.circle.fill" : (enabled ? "exclamationmark.triangle.fill" : "pause.circle"),
                      tint: running ? .green : (enabled ? .orange : .secondary), id: "runtime")
            statusRow("启用订阅", "\(activeRules)/\(rules.count) 条 · \(chats) 个群", symbol: "bubble.left.and.text.bubble.right",
                      tint: .accentColor, id: "rules")
            if let lastError, !lastError.isEmpty {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("最近运行有错误").font(.subheadline.weight(.semibold))
                        Text(lastError).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                } icon: { Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                .accessibilityIdentifier("interaction.status.error")
            }
            if dlq > 0 {
                statusRow("失败投递", "\(dlq) 条待处理", symbol: "tray.and.arrow.down", tint: .orange, id: "dlq")
            }
            if !memberships.isEmpty {
                DisclosureGroup {
                    ForEach(Array(memberships.enumerated()), id: \.offset) { _, item in
                        membershipRow(item)
                    }
                } label: {
                    let joined = memberships.filter { $0["status"]?.stringValue == "joined" }.count
                    Label("Bot 入群情况 · \(joined)/\(memberships.count) 已加入", systemImage: "person.2")
                        .accessibilityIdentifier("interaction.memberships")
                }
            }
        } header: { Text("运行状态") } footer: {
            Text("状态取自服务器最近一次读取或保存的结果；保存后交互 Bot 会按新配置自动重启。")
        }
    }

    private func statusRow(_ title: String, _ value: String, symbol: String, tint: Color, id: String) -> some View {
        LabeledContent {
            Text(value).foregroundStyle(.secondary).multilineTextAlignment(.trailing)
        } label: {
            Label { Text(title) } icon: { Image(systemName: symbol).foregroundStyle(tint) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("interaction.status.\(id)")
    }

    private func membershipRow(_ item: JSONValue) -> some View {
        let chatID = item["chat_id"]?.intValue ?? 0
        let status = item["status"]?.stringValue ?? "unknown"
        let title = peers.first(where: { $0.id == chatID })?.label ?? "会话 \(chatID)"
        let state: (text: String, symbol: String, tint: Color)
        switch status {
        case "joined": state = ("已加入", "checkmark.circle.fill", .green)
        case "not_joined": state = ("未加入", "exclamationmark.triangle.fill", .orange)
        default: state = ("未知", "questionmark.circle", .secondary)
        }
        return VStack(alignment: .leading, spacing: 2) {
            LabeledContent {
                Label(state.text, systemImage: state.symbol).foregroundStyle(state.tint).labelStyle(.titleAndIcon)
            } label: { Text(title).lineLimit(1) }
            if let detail = item["detail"]?.stringValue, !detail.isEmpty {
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: 总闸与 Bot

    private func switchSection(_ value: JSONValue) -> some View {
        let hasToken = value["has_interaction_bot_token"]?.boolValue ?? false
        return Section {
            Toggle(isOn: $draft.enabled) {
                Label("交互总闸", systemImage: "power.circle")
            }
            .accessibilityIdentifier("interaction.enabled")
            Label {
                SecureField(hasToken ? "已配置，留空保持不变" : "粘贴 BotFather 给的 Token", text: $draft.newToken)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .disabled(draft.clearToken)
                    .accessibilityIdentifier("interaction.token")
            } icon: { Image(systemName: "key") }
            if hasToken {
                Toggle(isOn: $draft.clearToken) {
                    Label("清除已保存的 Token", systemImage: "trash")
                }
                .tint(.red)
                .accessibilityIdentifier("interaction.clearToken")
            }
        } header: { Text("交互总闸与 Bot") } footer: {
            Text("总闸关闭时所有互动规则都不生效。交互 Bot 不能和管理 Bot 使用同一个 Bot；Token 只在保存时提交，App 不保存也不显示。")
        }
    }

    // MARK: 规则

    private func ruleBinding(_ id: String) -> Binding<InteractionRuleDraft>? {
        guard let rule = draft.rules.first(where: { $0.id == id }) else { return nil }
        return Binding(
            get: { draft.rules.first(where: { $0.id == id }) ?? rule },
            set: { value in
                if let index = draft.rules.firstIndex(where: { $0.id == id }) { draft.rules[index] = value }
            })
    }

    private var rulesSection: some View {
        Section {
            if focusPluginKey != nil {
                Toggle(isOn: $showAllRules) {
                    Label("显示全部规则", systemImage: "line.3.horizontal.decrease.circle")
                }
                .accessibilityIdentifier("interaction.showAllRules")
                if focusKey != nil && visibleRuleIDs.isEmpty {
                    Text("还没有启动「\(focusPluginName)」的规则。点下方「添加规则」新建一条，或打开“显示全部规则”查看其它规则。")
                        .font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("interaction.focusEmpty")
                }
            }
            ForEach(visibleRuleIDs, id: \.self) { id in
                if let binding = ruleBinding(id) { ruleRow(binding) }
            }
            if draft.rules.count < 20 {
                Button("添加规则", systemImage: "plus") {
                    var rule = InteractionRuleDraft.new(index: draft.rules.count)
                    // 从插件详情进来时，新规则直接指向这个插件的第一个玩法入口。
                    if let key = focusKey, let entry = entries.first(where: { $0.featureKey == key }) {
                        rule.action = "module"
                        rule.moduleKey = entry.featureKey
                        rule.moduleAction = entry.entryKey
                        if let scope = entry.sessionScope { rule.moduleSessionScope = scope }
                        if let policy = entry.participantPolicy { rule.participantPolicy = policy }
                    }
                    draft.rules.append(rule)
                }
                .accessibilityIdentifier("interaction.addRule")
            }
        } header: {
            Text(focusKey != nil ? "启动「\(focusPluginName)」的规则" : "互动规则与玩法入口")
        } footer: {
            Text("设置别人发什么关键词、在哪些群生效、启动哪个玩法；保存后交互 Bot 会自动监听并交给对应插件处理。左滑可停用或删除，至少保留一条规则。")
        }
    }

    private func ruleRow(_ binding: Binding<InteractionRuleDraft>) -> some View {
        let rule = binding.wrappedValue
        return NavigationLink {
                    InteractionRuleEditor(rule: binding, entries: entries, peers: peers, accountID: accountID, features: features)
                } label: {
                    InteractionRuleRow(rule: rule, entries: entries)
                }
                .accessibilityIdentifier("interaction.rule.\(rule.id)")
                .swipeActions(edge: .trailing) {
                    if draft.rules.count > 1 {
                        Button("删除", systemImage: "trash", role: .destructive) {
                            draft.rules.removeAll { $0.id == rule.id }
                        }
                    }
                    Button(rule.enabled ? "停用" : "启用", systemImage: rule.enabled ? "pause.circle" : "checkmark.circle") {
                        binding.wrappedValue.enabled.toggle()
                    }
                    .tint(rule.enabled ? .orange : .green)
                }
    }

    // MARK: 高级

    private var advancedSection: some View {
        Section {
            NavigationLink {
                InteractionAdvancedView(draft: $draft)
            } label: {
                Label("查询指令、通知模板与可信 Bot", systemImage: "slider.horizontal.3")
            }
            .accessibilityIdentifier("interaction.advanced")
        } footer: {
            Text("插件玩法的专属参数（如奖池、题目难度）请在插件的账号配置或 Web 管理台修改，App 保存规则时原样保留。")
        }
    }

    // MARK: 读写

    private func load() async {
        guard let id = accountID else { return }
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: "/api/accounts/\(id)/interaction-bot")
            guard id == accountID else { return }
            apply(value)
            loadedAccountID = id
            loadError = nil
            saveMessage = nil
            saveError = nil
        } catch is CancellationError {
            return
        } catch {
            guard id == accountID else { return }
            serverValue = nil
            if case .server(let status, _)? = error as? APIError, status == 404 {
                loadError = "服务器没有交互配置接口（可能版本过旧），请先更新服务器。"
            } else {
                loadError = OpsFormat.errorMessage(error)
            }
        }
        if let peerValue = try? await model.request(path: "/api/accounts/\(id)/ignored-peers"), id == accountID {
            peers = (peerValue.arrayValue ?? []).compactMap(InteractionPeer.init)
        }
    }

    private func loadEntries() async {
        guard let value = try? await model.request(path: "/api/feature-matrix") else { return }
        entries = InteractionEntryOption.parse(value)
        features = Dictionary((value["features"]?.arrayValue ?? []).compactMap { item in item["key"]?.stringValue.map { ($0, item) } },
                              uniquingKeysWith: { first, _ in first })
    }

    private func apply(_ value: JSONValue) {
        serverValue = value
        let next = InteractionDraft(value)
        baseline = next
        draft = next
    }

    private func save() async {
        guard let id = accountID, let serverValue, !saving else { return }
        defer { resultToken += 1 }
        let hasToken = (serverValue["has_interaction_bot_token"]?.boolValue ?? false) && !draft.clearToken
        let payload: JSONValue
        do {
            payload = try draft.payload(hasToken: hasToken, entries: entries)
        } catch {
            saveError = (error as? InteractionDraftError)?.message ?? error.localizedDescription
            saveMessage = nil
            return
        }
        saving = true
        defer { saving = false }
        do {
            let value = try await model.request(path: "/api/accounts/\(id)/interaction-bot", method: "PUT", body: payload)
            guard id == accountID else { return }
            apply(value)
            saveError = nil
            saveMessage = "已保存，交互 Bot 已按新配置重启。"
        } catch {
            saveMessage = nil
            saveError = "保存失败或结果未知：\(OpsFormat.errorMessage(error))。不会自动重试，修改仍保留，请核对后再保存。"
        }
    }
}

// MARK: - 规则行

private struct InteractionRuleRow: View {
    let rule: InteractionRuleDraft
    let entries: [InteractionEntryOption]

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(rule.displayName).font(.body.weight(.medium)).lineLimit(1)
                    Spacer(minLength: 4)
                    Label(rule.enabled ? "启用" : "停用", systemImage: rule.enabled ? "checkmark.circle.fill" : "pause.circle")
                        .labelStyle(.titleAndIcon)
                        .font(.caption)
                        .foregroundStyle(rule.enabled ? Color.green : Color.secondary)
                }
                Text(rule.summary(entries: entries)).font(.footnote).foregroundStyle(.secondary).lineLimit(2)
            }
        } icon: {
            Image(systemName: rule.action == "module" ? "puzzlepiece.extension" : (rule.action == "math10" ? "function" : "bell.badge"))
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - 规则编辑

struct InteractionRuleEditor: View {
    @Binding var rule: InteractionRuleDraft
    let entries: [InteractionEntryOption]
    let peers: [InteractionPeer]
    var accountID: Int? = nil
    var features: [String: JSONValue] = [:]

    private var effectiveTrigger: String { rule.action == "notice" ? "payment" : rule.triggerMode }
    private var selectedEntry: InteractionEntryOption? {
        entries.first { $0.featureKey == rule.moduleKey && $0.entryKey == rule.moduleAction }
    }

    var body: some View {
        Form {
            basicSection
            if rule.action == "module" { moduleSection }
            triggerTextSection
            chatSection
            if effectiveTrigger != "keyword" { paymentSection }
            if rule.action != "notice" { limitSection }
            commandSection
            replySection
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle(rule.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("interaction.ruleEditor")
    }

    private var basicSection: some View {
        Section {
            TextField("规则名称", text: $rule.name)
                .accessibilityIdentifier("interaction.rule.name")
            Toggle(isOn: $rule.enabled) { Label("启用这条规则", systemImage: "checkmark.circle") }
                .accessibilityIdentifier("interaction.rule.enabled")
            Picker(selection: $rule.action) {
                Text("只发通知").tag("notice")
                Text("算数题").tag("math10")
                Text("启动插件").tag("module")
            } label: { Label("命中后", systemImage: "play.circle") }
            .accessibilityIdentifier("interaction.rule.action")
            if rule.action != "notice" {
                Picker(selection: $rule.triggerMode) {
                    Text("转账").tag("payment")
                    Text("仅关键词").tag("keyword")
                    Text("转账或关键词").tag("both")
                } label: { Label("触发方式", systemImage: "bolt.horizontal") }
                .accessibilityIdentifier("interaction.rule.trigger")
            }
        } header: { Text("规则") }
    }

    private var triggerTextSection: some View {
        Section {
            multiline(effectiveTrigger == "keyword" ? "触发关键词" : "转账通知里的关键词", text: $rule.triggerTexts, id: "keywords")
            if rule.action == "module" && effectiveTrigger != "payment" {
                multiline("开局关键词（可选）", text: $rule.moduleStartKeywords, id: "startKeywords")
            }
        } header: { Text("触发文本") } footer: {
            Text("每行一项。转账触发时匹配转账通知文字；关键词触发时匹配群成员发的消息。")
        }
    }

    private var paymentSection: some View {
        Section {
            TextField("金额（留空不限）", text: $rule.amount).keyboardType(.numberPad)
            Picker("金额匹配", selection: $rule.amountMatchMode) {
                Text("等于").tag("eq")
                Text("大于等于").tag("gte")
            }
            TextField("指定收款人用户 ID（可选）", text: $rule.receiverUserID).keyboardType(.numberPad)
            TextField("收款人文字（可选）", text: $rule.receiverText)
        } header: { Text("转账过滤") } footer: {
            Text("金额是群内游戏币。启用转账触发前，服务器要求在“高级”里配置可信通知 Bot ID。")
        }
    }

    private var limitSection: some View {
        Section {
            TextField("每用户冷却秒数（可选）", text: $rule.cooldown)
            TextField("每用户每日上限（可选）", text: $rule.dailyLimit).keyboardType(.numberPad)
            TextField("参与有效期（秒）", text: $rule.validSeconds).keyboardType(.numberPad)
            Picker("并发方式", selection: $rule.concurrency) {
                Text("按群").tag("chat")
                Text("按用户").tag("user")
                Text("不并发").tag("none")
            }
            if rule.action == "math10" || selectedEntry?.supportsPrize == true {
                TextField("奖金", text: $rule.prize).keyboardType(.numberPad)
            }
        } header: { Text("限制") }
    }

    private var commandSection: some View {
        Section {
            multiline("开启指令（可选）", text: $rule.openCommands, id: "open")
            multiline("关闭指令（可选）", text: $rule.closeCommands, id: "close")
            multiline("状态指令（可选）", text: $rule.statusCommands, id: "status")
            TextField("暂停时回复（可选）", text: $rule.disabledMessage, axis: .vertical).lineLimit(1...4)
        } header: { Text("管理指令") } footer: { Text("每行一条，管理员在群里发这些指令可开启、关闭或查询本规则。") }
    }

    private var replySection: some View {
        Section {
            TelegramTemplateInlineField(title: "回复模板", text: $rule.responseTemplate,
                                        spec: TelegramTemplateSpec(botName: "交互 Bot", samples: TelegramTemplateSamples.interactionReply,
                                                                   prompt: "（转账 88.00 给 Bob）"),
                                        identifier: "interaction.rule.response")
        } header: { Text("命中回复") } footer: {
            Text("可用变量：{payer_name} 付款人、{receiver_name} 收款人、{amount} 金额、{规则名称}。")
        }
    }

    private var moduleSection: some View {
        Section {
            Picker(selection: Binding(
                get: { rule.moduleKey.isEmpty ? "" : "\(rule.moduleKey):\(rule.moduleAction)" },
                set: { value in
                    let parts = value.split(separator: ":", maxSplits: 1).map(String.init)
                    rule.moduleKey = parts.first ?? ""
                    rule.moduleAction = parts.count > 1 ? parts[1] : ""
                    if let entry = entries.first(where: { $0.featureKey == rule.moduleKey && $0.entryKey == rule.moduleAction }) {
                        if let scope = entry.sessionScope { rule.moduleSessionScope = scope }
                        if let policy = entry.participantPolicy { rule.participantPolicy = policy }
                    }
                })) {
                Text("请选择").tag("")
                ForEach(entries) { entry in Text(entry.label).tag(entry.value) }
                if !rule.moduleKey.isEmpty && selectedEntry == nil {
                    Text("\(rule.moduleKey) / \(rule.moduleAction.isEmpty ? "默认入口" : rule.moduleAction)（未找到入口）")
                        .tag("\(rule.moduleKey):\(rule.moduleAction)")
                }
            } label: { Label("插件入口", systemImage: "puzzlepiece.extension") }
            .accessibilityIdentifier("interaction.rule.entry")
            if let description = selectedEntry?.description, !description.isEmpty {
                Text(description).font(.footnote).foregroundStyle(.secondary)
            }
            // 插件 ↔ 交互双向跳转：直接打开该插件在本账号的配置（玩法参数、开关都在那里）。
            if let feature = features[rule.moduleKey] {
                NavigationLink {
                    ManagementPluginDetailView(feature: feature, accountID: accountID, initialSection: .configuration)
                } label: {
                    Label {
                        LabeledContent("插件配置") { Text(feature["display_name"]?.stringValue ?? rule.moduleKey) }
                    } icon: { Image(systemName: "slider.horizontal.3") }
                }
                .accessibilityIdentifier("interaction.rule.pluginConfig")
            }
            Picker("会话范围", selection: $rule.moduleSessionScope) {
                Text("按群会话").tag("chat")
                Text("按用户会话").tag("user")
                Text("不保存会话").tag("none")
            }
            Picker("参与者", selection: $rule.participantPolicy) {
                Text("所有人抢答").tag("open_race")
                Text("仅付款/开局本人").tag("solo_owner")
                Text("仅付费玩家").tag("paid_pool")
                Text("只通知").tag("notify_only")
            }
            TextField("开局提示（可选）", text: $rule.moduleStartText, axis: .vertical).lineLimit(1...4)
            if rule.moduleConfigCount > 0 {
                LabeledContent("玩法参数", value: "\(rule.moduleConfigCount) 项 · 保存时保留")
            }
        } header: { Text("玩法入口") } footer: {
            Text(entries.isEmpty ? "没有读到插件声明的玩法入口：服务器版本较旧或尚未安装交互类插件。" : "列表来自已安装插件声明的交互入口。")
        }
    }

    private var chatSection: some View {
        Section {
            multiline("生效群 Chat ID（每行一个，留空不限）", text: $rule.chatIDs, id: "chats", numbers: true)
            if !peers.isEmpty {
                NavigationLink {
                    InteractionPeerPicker(chatIDs: $rule.chatIDs, peers: peers)
                } label: {
                    Label("从会话白名单选择", systemImage: "checklist")
                }
                .accessibilityIdentifier("interaction.rule.pickChats")
            }
        } header: { Text("生效群") } footer: {
            Text("最多 20 个群；超级群的 ID 形如 -100…。")
        }
    }

    private func multiline(_ title: String, text: Binding<String>, id: String, numbers: Bool = false) -> some View {
        TextField(title, text: text, axis: .vertical)
            .lineLimit(1...6)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(numbers ? .numbersAndPunctuation : .default)
            .accessibilityIdentifier("interaction.rule.\(id)")
    }
}

private struct InteractionPeerPicker: View {
    @Binding var chatIDs: String
    let peers: [InteractionPeer]

    var body: some View {
        List {
            Section {
                ForEach(peers) { peer in
                    Toggle(isOn: Binding(
                        get: { InteractionDraft.ints(chatIDs).contains(peer.id) },
                        set: { on in
                            var ids = InteractionDraft.ints(chatIDs)
                            if on { if !ids.contains(peer.id) { ids.append(peer.id) } } else { ids.removeAll { $0 == peer.id } }
                            chatIDs = ids.map(String.init).joined(separator: "\n")
                        })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(peer.label)
                            Text(String(peer.id)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        }
                    }
                }
            } footer: { Text("列表是账号的会话白名单（Web「允许会话」）。") }
        }
        .navigationTitle("选择生效群")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 高级

private struct InteractionAdvancedView: View {
    @Binding var draft: InteractionDraft

    var body: some View {
        Form {
            Section {
                field("可信通知 Bot ID（每行一个）", text: $draft.trustedBotIDs, numbers: true)
            } header: { Text("可信通知 Bot") } footer: {
                Text("只接受这些 Bot 发出的转账通知；启用转账触发的规则前必须至少填写一个。")
            }
            Section {
                field("查询指令（每行一条）", text: $draft.queryCommands)
            } header: { Text("玩法查询") } footer: { Text("群成员发查询指令时，交互 Bot 列出当前可用的联动玩法。") }
            Section {
                TelegramTemplateInlineField(title: "查询回复模板", text: $draft.queryResponseTemplate, spec: querySpec(response: true),
                                            identifier: "interaction.query.response")
            } header: { Text("查询回复模板") } footer: { Text("{items} 是下面条目模板逐个渲染后的列表；{count} 可用数量、{closed_count} 已关闭数量、{chat_id} 群 ID。留空保持服务器当前值，预览用默认模板。") }
            Section {
                TelegramTemplateInlineField(title: "每个玩法的条目模板", text: $draft.queryItemTemplate, spec: querySpec(response: false),
                                            identifier: "interaction.query.item", previewInitiallyShown: false)
            } header: { Text("每个玩法的条目模板") } footer: { Text("{index} 序号、{name} 规则名称、{trigger} 触发方式、{kind} 玩法类型、{limit} 限制摘要、{module_key} 插件 key。预览与上面的回复模板组合成整条消息。") }
            Section {
                field("没有可用玩法时回复", text: $draft.queryEmptyMessage)
            }
            Section {
                TelegramTemplateInlineField(title: "收款通知模板", text: $draft.transferNoticeTemplate,
                                            spec: noticeSpec(TelegramTemplateSamples.defaultTransferNoticeTemplate, prompt: "+88"),
                                            identifier: "interaction.notice.transfer")
            } header: { Text("收款通知模板") } footer: { Text("{payer_name} 付款人、{payer_user_id} 付款人 ID、{receiver_name} 收款人、{receiver_user_id} 收款人 ID、{amount} 金额；{payer_user_id_line} / {receiver_user_id_line} 有 ID 时渲染为一行。留空保持服务器当前值。") }
            Section {
                TelegramTemplateInlineField(title: "扣款通知模板", text: $draft.debitNoticeTemplate,
                                            spec: noticeSpec(TelegramTemplateSamples.defaultDebitNoticeTemplate, prompt: "-88"),
                                            identifier: "interaction.notice.debit")
            } header: { Text("扣款通知模板") } footer: { Text("群里回复任意消息发送 +123 / -123 时，测试用的通知 Bot 按这两个模板发出转账 / 扣款通知。留空保持服务器当前值。") }
        }
        .navigationTitle("高级")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// 玩法查询：回复模板与条目模板组合成一条消息（与 Web BotTab 的查询预览相同的示例玩法）。
    private func querySpec(response: Bool) -> TelegramTemplateSpec {
        let responseTemplate = draft.queryResponseTemplate
        let itemTemplate = draft.queryItemTemplate
        var spec = TelegramTemplateSpec(botName: "交互 Bot", samples: ["count": "2", "closed_count": "0", "chat_id": "-1001234567890"])
        spec.prompt = draft.queryCommands.split(whereSeparator: \.isNewline).first.map(String.init) ?? "。玩法"
        if response {
            spec.samples["items"] = "（下方条目模板渲染的列表）"
            spec.compose = { TelegramTemplateSamples.interactionQuery(response: $0, item: itemTemplate) }
            spec.defaultTemplate = TelegramTemplateSamples.defaultQueryResponseTemplate
        } else {
            spec.samples = ["index": "1", "name": "示例玩法", "trigger": "转账或关键词", "kind": "玩法 <code>example_game</code>",
                            "limit": "限时 <code>600</code> 秒", "module_key": "example_game"]
            spec.compose = { TelegramTemplateSamples.interactionQuery(response: responseTemplate, item: $0) }
            spec.defaultTemplate = TelegramTemplateSamples.defaultQueryItemTemplate
        }
        return spec
    }

    private func noticeSpec(_ fallback: String, prompt: String) -> TelegramTemplateSpec {
        TelegramTemplateSpec(botName: "转账通知 Bot", samples: TelegramTemplateSamples.transferNotice, defaultTemplate: fallback, prompt: prompt)
    }

    private func field(_ title: String, text: Binding<String>, numbers: Bool = false) -> some View {
        TextField(title, text: text, axis: .vertical)
            .lineLimit(1...8)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(numbers ? .numbersAndPunctuation : .default)
    }
}

// MARK: - 数据

struct InteractionEntryOption: Identifiable, Hashable {
    let featureKey: String
    let entryKey: String
    let label: String
    let description: String?
    let sessionScope: String?
    let participantPolicy: String?
    let supportsPrize: Bool
    var id: String { value }
    var value: String { "\(featureKey):\(entryKey)" }

    static func parse(_ matrix: JSONValue) -> [InteractionEntryOption] {
        (matrix["features"]?.arrayValue ?? []).flatMap { feature -> [InteractionEntryOption] in
            guard let key = feature["key"]?.stringValue else { return [] }
            let name = feature["display_name"]?.stringValue ?? key
            return (feature["interaction_entries"]?.arrayValue ?? []).compactMap { entry in
                guard let entryKey = entry["key"]?.stringValue, !entryKey.isEmpty else { return nil }
                let title = entry["title"]?.stringValue ?? entry["label"]?.stringValue ?? entryKey
                let scope = entry["session_scope"]?.stringValue
                let policy = entry["participant_policy"]?.stringValue
                return InteractionEntryOption(
                    featureKey: key, entryKey: entryKey, label: "\(name) / \(title)",
                    description: entry["description"]?.stringValue,
                    sessionScope: ["chat", "user", "none"].contains(scope ?? "") ? scope : nil,
                    participantPolicy: ["open_race", "solo_owner", "paid_pool", "notify_only"].contains(policy ?? "") ? policy : nil,
                    supportsPrize: entry["input_schema"]?["properties"]?["prize"] != nil)
            }
        }
    }
}

struct InteractionPeer: Identifiable, Hashable {
    let id: Int
    let label: String
    init?(_ value: JSONValue) {
        guard let peerID = value["peer_id"]?.intValue else { return nil }
        id = peerID
        let raw = value["peer_label"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        label = raw.isEmpty ? "会话 \(peerID)" : raw
    }
}

struct InteractionDraftError: Error { let message: String }

/// 交互配置的可编辑草稿。与服务器值比较判断是否有未保存修改。
struct InteractionDraft: Equatable {
    var enabled = false
    var newToken = ""
    var clearToken = false
    var trustedBotIDs = ""
    var queryCommands = ""
    var queryResponseTemplate = ""
    var queryItemTemplate = ""
    var queryEmptyMessage = ""
    var transferNoticeTemplate = ""
    var debitNoticeTemplate = ""
    var rules: [InteractionRuleDraft] = []

    init() {}

    init(_ value: JSONValue) {
        enabled = value["enabled"]?.boolValue ?? false
        var trusted = (value["trusted_bot_ids"]?.arrayValue ?? []).compactMap(\.intValue)
        if trusted.isEmpty, let single = value["trusted_bot_id"]?.intValue { trusted = [single] }
        trustedBotIDs = trusted.map(String.init).joined(separator: "\n")
        queryCommands = Self.lines(value["query_commands"])
        queryResponseTemplate = value["query_response_template"]?.stringValue ?? ""
        queryItemTemplate = value["query_item_template"]?.stringValue ?? ""
        queryEmptyMessage = value["query_empty_message"]?.stringValue ?? ""
        transferNoticeTemplate = value["transfer_notice_template"]?.stringValue ?? ""
        debitNoticeTemplate = value["debit_notice_template"]?.stringValue ?? ""
        let fallbackChats = (value["chat_ids"]?.arrayValue ?? []).compactMap(\.intValue)
        let rawRules = value["rules"]?.arrayValue ?? []
        if rawRules.isEmpty {
            // 旧版单规则：用顶层字段拼出一条（与 Web legacyRuleFromConfig 同一做法）。
            var legacy = value.objectValue ?? [:]
            legacy["id"] = "default"
            legacy["name"] = "默认规则"
            legacy["enabled"] = true
            legacy["chat_ids"] = .array(fallbackChats.map { JSONValue.integer(Int64($0)) })
            rules = [InteractionRuleDraft(.object(Self.ruleKeys(legacy)))]
        } else {
            rules = rawRules.map(InteractionRuleDraft.init)
        }
    }

    /// 旧版顶层字段里属于规则的部分。
    private static func ruleKeys(_ object: [String: JSONValue]) -> [String: JSONValue] {
        let keys: Set<String> = ["id", "name", "enabled", "chat_ids", "trigger_mode", "trigger_texts", "module_start_keywords",
                                 "receiver_user_id", "receiver_text", "amount", "amount_match_mode", "action", "math_prize",
                                 "module_key", "module_action", "module_session_scope", "participant_policy", "module_prize",
                                 "module_config", "module_start_text", "user_cooldown_seconds", "daily_limit_per_user",
                                 "open_commands", "close_commands", "status_commands", "disabled_message", "valid_seconds",
                                 "concurrency", "response_template"]
        return object.filter { keys.contains($0.key) }
    }

    func payload(hasToken: Bool, entries: [InteractionEntryOption]) throws -> JSONValue {
        guard !rules.isEmpty else { throw InteractionDraftError(message: "至少需要保留一条规则") }
        let token = newToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if enabled && !hasToken && token.isEmpty {
            throw InteractionDraftError(message: "启用交互总闸时必须填写交互 Bot Token")
        }
        let ruleValues = try rules.enumerated().map { try $0.element.json(index: $0.offset, entries: entries) }
        var chats: [Int] = []
        for rule in ruleValues {
            for id in (rule["chat_ids"]?.arrayValue ?? []).compactMap(\.intValue) where !chats.contains(id) { chats.append(id) }
        }
        let trusted = try Self.parseInts(trustedBotIDs, label: "可信通知 Bot ID", positive: true)
        var body: [String: JSONValue] = [
            "enabled": .bool(enabled),
            "chat_id": chats.first.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null,
            "chat_ids": .array(chats.map { JSONValue.integer(Int64($0)) }),
            "trusted_bot_ids": .array(trusted.map { JSONValue.integer(Int64($0)) }),
            "trusted_bot_id": trusted.first.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null,
            "query_commands": .array(Self.parseLines(queryCommands).map { JSONValue.string($0) }),
            "rules": .array(ruleValues)
        ]
        for (key, text) in [("query_response_template", queryResponseTemplate), ("query_item_template", queryItemTemplate),
                            ("query_empty_message", queryEmptyMessage), ("transfer_notice_template", transferNoticeTemplate),
                            ("debit_notice_template", debitNoticeTemplate)] {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { body[key] = .string(trimmed) }
        }
        if clearToken { body["clear_interaction_bot_token"] = true }
        if !token.isEmpty { body["interaction_bot_token"] = .string(token) }
        // 旧版顶层字段镜像第一条启用规则（服务器与旧 Worker 仍会读取）。
        let first = ruleValues.first { $0["enabled"]?.boolValue ?? true } ?? ruleValues[0]
        for (key, value) in first.objectValue ?? [:] where !["id", "name", "enabled", "chat_ids"].contains(key) {
            body[key] = value
        }
        let triggerTexts = (first["trigger_texts"]?.arrayValue ?? []).compactMap(\.stringValue)
        body["trigger_text"] = .string(triggerTexts.first ?? "转账成功")
        return .object(body)
    }

    // MARK: 文本工具

    static func lines(_ value: JSONValue?) -> String {
        (value?.arrayValue ?? []).compactMap { item -> String? in
            if let text = item.stringValue { return text }
            if let number = item.intValue { return String(number) }
            return nil
        }.joined(separator: "\n")
    }

    static func parseLines(_ text: String) -> [String] {
        var out: [String] = []
        for line in text.split(whereSeparator: \.isNewline) {
            let value = line.trimmingCharacters(in: .whitespaces)
            if !value.isEmpty && !out.contains(value) { out.append(value) }
        }
        return out
    }

    /// 宽松读取（选择器用）：忽略无法识别的行。
    static func ints(_ text: String) -> [Int] {
        parseLines(text).compactMap { Int($0) }
    }

    static func parseInts(_ text: String, label: String, positive: Bool = false) throws -> [Int] {
        var out: [Int] = []
        for line in parseLines(text) {
            guard let value = Int(line), !positive || value > 0 else {
                throw InteractionDraftError(message: "\(label)“\(line)”不是有效的数字")
            }
            if !out.contains(value) { out.append(value) }
        }
        return out
    }
}

struct InteractionRuleDraft: Identifiable, Equatable {
    var raw: [String: JSONValue]
    var id: String
    var name: String
    var enabled: Bool
    var chatIDs: String
    var action: String
    var triggerMode: String
    var triggerTexts: String
    var moduleKey: String
    var moduleAction: String
    var moduleStartKeywords: String
    var moduleStartText: String
    var moduleSessionScope: String
    var participantPolicy: String
    var prize: String
    var amount: String
    var amountMatchMode: String
    var receiverUserID: String
    var receiverText: String
    var cooldown: String
    var dailyLimit: String
    var validSeconds: String
    var concurrency: String
    var openCommands: String
    var closeCommands: String
    var statusCommands: String
    var disabledMessage: String
    var responseTemplate: String

    var displayName: String {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "未命名规则" : trimmed
    }
    var moduleConfigCount: Int { raw["module_config"]?.objectValue?.count ?? 0 }

    init(_ value: JSONValue) {
        let o = value.objectValue ?? [:]
        func text(_ key: String) -> String {
            if let s = o[key]?.stringValue { return s }
            if let n = o[key]?.intValue { return String(n) }
            return ""
        }
        raw = o
        id = text("id").isEmpty ? "rule-\(UUID().uuidString.prefix(8))" : text("id")
        name = text("name")
        enabled = o["enabled"]?.boolValue ?? true
        chatIDs = InteractionDraft.lines(o["chat_ids"])
        let rawAction = text("action")
        action = ["module", "math10"].contains(rawAction) ? rawAction : "notice"
        let rawTrigger = text("trigger_mode")
        triggerMode = ["keyword", "both"].contains(rawTrigger) ? rawTrigger : "payment"
        triggerTexts = InteractionDraft.lines(o["trigger_texts"])
        moduleKey = text("module_key")
        moduleAction = text("module_action")
        moduleStartKeywords = InteractionDraft.lines(o["module_start_keywords"])
        moduleStartText = text("module_start_text")
        let scope = text("module_session_scope")
        moduleSessionScope = ["user", "none"].contains(scope) ? scope : "chat"
        let policy = text("participant_policy")
        participantPolicy = ["solo_owner", "paid_pool", "notify_only"].contains(policy) ? policy : "open_race"
        prize = text("math_prize").isEmpty ? "123" : text("math_prize")
        amount = text("amount")
        amountMatchMode = text("amount_match_mode") == "gte" ? "gte" : "eq"
        receiverUserID = text("receiver_user_id")
        receiverText = text("receiver_text")
        cooldown = text("user_cooldown_seconds")
        dailyLimit = text("daily_limit_per_user")
        validSeconds = text("valid_seconds").isEmpty ? "600" : text("valid_seconds")
        let rawConcurrency = text("concurrency")
        concurrency = ["user", "none"].contains(rawConcurrency) ? rawConcurrency : "chat"
        openCommands = InteractionDraft.lines(o["open_commands"])
        closeCommands = InteractionDraft.lines(o["close_commands"])
        statusCommands = InteractionDraft.lines(o["status_commands"])
        disabledMessage = text("disabled_message")
        responseTemplate = text("response_template")
    }

    static func new(index: Int) -> InteractionRuleDraft {
        let suffix = index + 1
        var rule = InteractionRuleDraft(.object([:]))
        rule.id = "rule-\(Int(Date().timeIntervalSince1970 * 1000))-\(suffix)"
        rule.name = suffix == 1 ? "默认规则" : "规则 \(suffix)"
        rule.triggerTexts = "转账成功"
        rule.moduleStartText = "正在启动{规则名称}"
        rule.disabledMessage = "本条互动规则已暂停，暂时不能开启。"
        rule.responseTemplate = "已收到 {payer_name} 给 {receiver_name} 的转账 {amount}，互动流程已准备就绪。"
        return rule
    }

    func summary(entries: [InteractionEntryOption]) -> String {
        var parts: [String] = []
        switch action {
        case "module":
            let entry = entries.first { $0.featureKey == moduleKey && $0.entryKey == moduleAction }
            parts.append(entry?.label ?? (moduleKey.isEmpty ? "启动插件（未选择入口）" : "启动 \(moduleKey)"))
        case "math10": parts.append("算数题")
        default: parts.append("只发通知")
        }
        let trigger = action == "notice" ? "payment" : triggerMode
        parts.append(trigger == "keyword" ? "仅关键词" : (trigger == "both" ? "转账或关键词" : "转账触发"))
        let keywords = InteractionDraft.parseLines(triggerTexts)
        if !keywords.isEmpty { parts.append("“" + keywords.prefix(2).joined(separator: "”“") + "”" + (keywords.count > 2 ? " 等" : "")) }
        let chats = InteractionDraft.ints(chatIDs).count
        parts.append(chats == 0 ? "所有群" : "\(chats) 个群")
        return parts.joined(separator: " · ")
    }

    /// 与 Web `ruleFromForm` 同一规则：以原始对象为底，只改写编辑过的字段。
    func json(index: Int, entries: [InteractionEntryOption]) throws -> JSONValue {
        var o = raw
        let label = displayName
        let trigger = action == "notice" ? "payment" : triggerMode
        let paymentFilters = trigger != "keyword"
        let limits = action != "notice"
        if action == "module" && moduleKey.isEmpty {
            throw InteractionDraftError(message: "\(label)：请先选择要启动的插件入口")
        }
        func optionalInt(_ text: String, _ field: String, max: Int? = nil) throws -> JSONValue {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { return .null }
            guard let value = Int(trimmed), value > 0, max.map({ value <= $0 }) ?? true else {
                throw InteractionDraftError(message: "\(label)：\(field)需要是正整数" + (max.map { "（最大 \($0)）" } ?? ""))
            }
            return .integer(Int64(value))
        }
        func optionalText(_ text: String) -> JSONValue {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? .null : .string(trimmed)
        }
        func list(_ text: String) -> JSONValue { .array(InteractionDraft.parseLines(text).map { JSONValue.string($0) }) }

        let chats = try InteractionDraft.parseInts(chatIDs, label: "\(label) 生效群 Chat ID")
        if chats.count > 20 { throw InteractionDraftError(message: "\(label)：生效群最多 20 个") }
        let keywords = InteractionDraft.parseLines(triggerTexts)
        let entry = entries.first { $0.featureKey == moduleKey && ($0.entryKey == moduleAction || moduleAction.isEmpty) }
        let prizeValue = try optionalInt(prize, "奖金")

        o["id"] = .string(id.isEmpty ? "rule-\(index + 1)" : id)
        o["name"] = .string(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "规则 \(index + 1)" : name.trimmingCharacters(in: .whitespacesAndNewlines))
        o["enabled"] = .bool(enabled)
        o["chat_ids"] = .array(chats.map { JSONValue.integer(Int64($0)) })
        o["trigger_mode"] = .string(trigger)
        o["trigger_texts"] = .array((keywords.isEmpty ? ["转账成功"] : keywords).map { JSONValue.string($0) })
        o["module_start_keywords"] = list(moduleStartKeywords)
        o["receiver_user_id"] = try paymentFilters ? optionalInt(receiverUserID, "指定收款人用户 ID") : JSONValue.null
        o["receiver_text"] = paymentFilters ? optionalText(receiverText) : .null
        o["amount"] = try paymentFilters ? optionalInt(amount, "金额") : JSONValue.null
        o["amount_match_mode"] = .string(amountMatchMode)
        o["action"] = .string(action)
        o["math_prize"] = prizeValue.isNull ? .integer(123) : prizeValue
        if action == "module" {
            let resolvedAction = moduleAction.isEmpty ? (entry?.entryKey ?? "") : moduleAction
            o["module_key"] = .string(moduleKey)
            o["module_action"] = resolvedAction.isEmpty ? .null : .string(resolvedAction)
            o["module_session_scope"] = .string(moduleSessionScope)
            o["participant_policy"] = .string(participantPolicy)
            o["module_start_text"] = optionalText(moduleStartText)
            if let entry {
                o["module_prize"] = entry.supportsPrize ? (prizeValue.isNull ? .integer(123) : prizeValue) : .null
            }
            if o["module_config"] == nil { o["module_config"] = .object([:]) }
        } else {
            o["module_key"] = .null
            o["module_action"] = .null
            o["module_session_scope"] = .null
            o["participant_policy"] = .null
            o["module_prize"] = .null
            o["module_start_text"] = .null
            o["module_config"] = .object([:])
        }
        o["user_cooldown_seconds"] = limits ? optionalText(cooldown) : .null
        o["daily_limit_per_user"] = try limits ? optionalInt(dailyLimit, "每用户每日上限", max: 1000) : JSONValue.null
        o["open_commands"] = list(openCommands)
        o["close_commands"] = list(closeCommands)
        o["status_commands"] = list(statusCommands)
        o["disabled_message"] = optionalText(disabledMessage)
        let valid = try optionalInt(validSeconds, "参与有效期", max: 86400)
        if let seconds = valid.intValue, seconds < 30 { throw InteractionDraftError(message: "\(label)：参与有效期至少 30 秒") }
        o["valid_seconds"] = valid.isNull ? .integer(600) : valid
        o["concurrency"] = .string(limits ? concurrency : "chat")
        let template = responseTemplate.trimmingCharacters(in: .whitespacesAndNewlines)
        o["response_template"] = .string(template.isEmpty ? "已收到 {payer_name} 给 {receiver_name} 的转账 {amount}，互动流程已准备就绪。" : template)
        return .object(o)
    }
}
