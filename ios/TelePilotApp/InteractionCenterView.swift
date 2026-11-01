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
    /// 规则列表筛选（与 Web 规则列表「全部 / 启用 / 暂停」一致）。
    @State private var ruleFilter = "all"
    /// 规则区底部「查询回复预览」是否展开（默认收起，省地方）。
    @State private var showingQueryPreview = false
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
        draft.rules.filter { rule in
            switch ruleFilter {
            case "enabled" where !rule.enabled: return false
            case "paused" where rule.enabled: return false
            default: break
            }
            return focusKey.map { rule.action == "module" && rule.moduleKey == $0 } ?? true
        }.map(\.id)
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
                    Motion.perform(fadeOnly: false) { proxy.scrollTo("interaction.result", anchor: .top) }
                }
        }
    }

    private var content: some View {
        List {
            if model.accounts.isEmpty {
                Section {
                    NoticeView(title: "暂无账号", message: "交互需要先绑定一个 Telegram 账号，再配置对应的交互 Bot 和互动规则。",
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
                    notifyBotsSection(serverValue)
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

    // MARK: 运行状态（第 21 轮：一行紧凑卡片，细节点进去看）

    private func statusSection(_ value: JSONValue) -> some View {
        let status = InteractionRuntimeStatus(value)
        return Section {
            NavigationLink {
                InteractionStatusDetailView(status: status)
            } label: {
                InteractionStatusCard(status: status)
            }
            .accessibilityIdentifier("interaction.statusCard")
        }
    }

    // MARK: 总闸与 Bot

    private func switchSection(_ value: JSONValue) -> some View {
        let hasToken = value["has_interaction_bot_token"]?.boolValue ?? false
        return Section {
            Toggle(isOn: $draft.enabled) {
                Label("交互总闸", systemImage: "power.circle")
            }
            .accessibilityIdentifier("interaction.enabled")
            SecretField(
                title: "交互 Bot Token",
                text: $draft.newToken,
                configured: hasToken && !draft.clearToken,
                reveal: accountID.map { SecretRevealTarget(kind: "interaction_bot_token", accountID: $0) },
                placeholder: "粘贴 BotFather 给的 Token",
                identifier: "interaction.token",
                disabled: draft.clearToken)
            if hasToken {
                Toggle(isOn: $draft.clearToken) {
                    Label("清除已保存的 Token", systemImage: "trash")
                }
                .tint(.red)
                .accessibilityIdentifier("interaction.clearToken")
            }
            InteractionMembershipGroup(title: "监听群成员状态", memberships: value["interaction_chat_memberships"]?.arrayValue ?? [], peers: peers, id: "interaction.memberships")
        } header: { Text("交互总闸与 Bot") } footer: {
            Text("总闸关闭时所有互动规则都不生效。交互 Bot 不能和管理 Bot 使用同一个 Bot；新 Token 只在保存时提交。点眼睛可查看已保存的 Token（服务器会记录这次查看），离开页面即隐藏，App 不保存。群成员状态服务器缓存 5 分钟。")
        }
    }

    // MARK: 通知 Bot（第 21 轮收进二级页「通知 Bot 设置」）

    private func notifyBotsSection(_ value: JSONValue) -> some View {
        let supportsTest = value["has_transfer_bot_token"] != nil
        let summary = InteractionNotifyBotsView.summary(draft: draft, value: value)
        let unsaved = InteractionNotifyBotsView.isDirty(draft, baseline)
        return Section {
            NavigationLink {
                InteractionNotifyBotsView(draft: $draft, baseline: baseline, value: value, peers: peers, accountID: accountID)
            } label: {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("通知 Bot 设置")
                        Text(unsaved ? "\(summary) · 未保存" : summary)
                            .font(.caption).foregroundStyle(unsaved ? AnyShapeStyle(.orange) : AnyShapeStyle(.secondary))
                            .monospacedDigit()
                            .lineLimit(2)
                    }
                } icon: { Image(systemName: "bell.badge") }
            }
            .accessibilityIdentifier("interaction.notifyBots")
        } footer: {
            Text(supportsTest ? "真实转账结果通知 Bot 与测试模拟通知 Bot。启用转账触发的规则前，至少要填一个真实通知 Bot ID。"
                              : "真实转账结果通知 Bot。启用转账触发的规则前，至少要填一个真实通知 Bot ID。")
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
            if draft.rules.count > 1 {
                Picker("规则筛选", selection: $ruleFilter) {
                    Text("全部").tag("all")
                    Text("启用").tag("enabled")
                    Text("暂停").tag("paused")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .accessibilityIdentifier("interaction.ruleFilter")
                if ruleFilter != "all" && visibleRuleIDs.isEmpty {
                    Text("没有符合当前筛选的规则").font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("interaction.filterEmpty")
                }
            }
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
            queryPreviewRows
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
                    // 第 24 轮：列表增删统一动效（减弱动态效果时只淡变）。
                    Motion.perform { draft.rules.append(rule) }
                }
                .accessibilityIdentifier("interaction.addRule")
            }
        } header: {
            Text(focusKey != nil ? "启动「\(focusPluginName)」的规则" : "互动规则与玩法入口")
        } footer: {
            Text("设置别人发什么关键词、在哪些群生效、启动哪个玩法；保存后交互 Bot 会自动监听并交给对应插件处理。「查询里显示」是群成员发查询指令时这条玩法的样子（按「每个玩法的条目模板」）。左滑可停用或删除，长按可上移、下移或复制，至少保留一条规则。")
        }
    }

    private func ruleRow(_ binding: Binding<InteractionRuleDraft>) -> some View {
        let rule = binding.wrappedValue
        return NavigationLink {
                    InteractionRuleEditor(rule: binding, entries: entries, peers: peers, accountID: accountID, features: features,
                                          query: queryContext)
                } label: {
                    InteractionRuleRow(rule: rule, entries: entries, queryLine: queryContext.itemLine(for: rule.id))
                }
                .accessibilityIdentifier("interaction.rule.\(rule.id)")
                .swipeActions(edge: .trailing) {
                    if draft.rules.count > 1 {
                        Button("删除", systemImage: "trash", role: .destructive) {
                            Motion.perform { draft.rules.removeAll { $0.id == rule.id } }
                        }
                    }
                    Button(rule.enabled ? "停用" : "启用", systemImage: rule.enabled ? "pause.circle" : "checkmark.circle") {
                        binding.wrappedValue.enabled.toggle()
                    }
                    .tint(rule.enabled ? .orange : .green)
                }
                .contextMenu {
                    let index = draft.rules.firstIndex(where: { $0.id == rule.id }) ?? 0
                    Button("上移", systemImage: "arrow.up") { moveRule(rule.id, by: -1) }
                        .disabled(index == 0)
                    Button("下移", systemImage: "arrow.down") { moveRule(rule.id, by: 1) }
                        .disabled(index >= draft.rules.count - 1)
                    if draft.rules.count < 20 {
                        Button("复制", systemImage: "doc.on.doc") { copyRule(rule.id) }
                    }
                    Button(rule.enabled ? "停用" : "启用", systemImage: rule.enabled ? "pause.circle" : "checkmark.circle") {
                        binding.wrappedValue.enabled.toggle()
                    }
                    if draft.rules.count > 1 {
                        Button("删除", systemImage: "trash", role: .destructive) {
                            Motion.perform { draft.rules.removeAll { $0.id == rule.id } }
                        }
                    }
                }
    }

    /// 玩法查询预览的上下文（当前草稿的规则与模板）。
    private var queryContext: InteractionQueryContext { InteractionQueryContext(draft: draft, entries: entries) }

    /// 规则区底部：群成员发查询指令时交互 Bot 的整条回复（按当前草稿，未保存也实时更新）。默认收起。
    @ViewBuilder
    private var queryPreviewRows: some View {
        let context = queryContext
        Button {
            Motion.perform(Motion.smooth) { showingQueryPreview.toggle() }
        } label: {
            Label {
                LabeledContent(showingQueryPreview ? "收起查询回复预览" : "查询回复预览") {
                    Text(context.listedCount > 0 ? "列出 \(context.listedCount) 个玩法" : "暂无可列出的玩法")
                }
            } icon: { Image(systemName: showingQueryPreview ? "eye.slash" : "eye") }
        }
        .accessibilityIdentifier("interaction.queryPreview.toggle")
        if showingQueryPreview {
            TelegramChatPreview(model: TelegramMessagePreviewModel(template: context.full(), values: [:]), botName: "交互 Bot",
                                prompt: context.prompt, identifier: "interaction.queryPreview")
                .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
        }
    }

    /// 上移／下移（与 Web 规则列表的上下箭头相同：和相邻规则交换位置；顺序决定旧版顶层字段镜像哪条启用规则）。
    private func moveRule(_ id: String, by offset: Int) {
        guard let index = draft.rules.firstIndex(where: { $0.id == id }) else { return }
        let target = index + offset
        guard draft.rules.indices.contains(target) else { return }
        Motion.perform(fadeOnly: false) { draft.rules.swapAt(index, target) }
    }

    /// 复制规则（与 Web `copyInteractionRule` 相同：新 ID、名称加「副本」、插在原规则后面，玩法参数一并复制）。
    private func copyRule(_ id: String) {
        guard draft.rules.count < 20, let index = draft.rules.firstIndex(where: { $0.id == id }) else { return }
        var copy = draft.rules[index]
        copy.id = "rule-\(Int(Date().timeIntervalSince1970 * 1000))-\(index + 2)"
        copy.raw["id"] = .string(copy.id)
        copy.name = "\(draft.rules[index].displayName) 副本"
        Motion.perform { draft.rules.insert(copy, at: index + 1) }
    }

    // MARK: 高级

    private var advancedSection: some View {
        Section {
            NavigationLink {
                InteractionAdvancedView(draft: $draft, entries: entries)
            } label: {
                Label("查询指令与通知模板", systemImage: "slider.horizontal.3")
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
    /// 这条玩法在查询回复里的样子（条目模板渲染后的纯文本，一行）；不会被列出的规则为 nil。
    var queryLine: String? = nil

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
                if let queryLine {
                    Text("查询里显示：\(queryLine)")
                        .font(.caption).foregroundStyle(.tertiary).lineLimit(1).truncationMode(.tail)
                }
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
    /// 玩法查询（条目模板 + 回复模板）；用来预览这条玩法在查询回复里的样子。
    var query: InteractionQueryContext? = nil

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
            querySection
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle(rule.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("interaction.ruleEditor")
    }

    private var basicSection: some View {
        Section {
            LabeledTextField(title: "规则名称", text: $rule.name, prompt: "例如：猜数字", capitalization: .sentences,
                             identifier: "interaction.rule.name")
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
            multiline(effectiveTrigger == "keyword" ? "触发关键词" : "转账通知里的关键词", prompt: "每行一项", text: $rule.triggerTexts, id: "keywords")
            if rule.action == "module" && effectiveTrigger != "payment" {
                multiline("开局关键词", optional: true, prompt: "每行一项", text: $rule.moduleStartKeywords, id: "startKeywords")
            }
        } header: { Text("触发文本") } footer: {
            Text("每行一项。转账触发时匹配转账通知文字；关键词触发时匹配群成员发的消息。")
        }
    }

    private var paymentSection: some View {
        Section {
            LabeledTextField(title: "金额", text: $rule.amount, prompt: "不限", keyboard: .numberPad,
                             identifier: "interaction.rule.amount")
            Picker("金额匹配", selection: $rule.amountMatchMode) {
                Text("等于").tag("eq")
                Text("大于等于").tag("gte")
            }
            LabeledTextField(title: "收款人用户 ID", text: $rule.receiverUserID, optional: true, keyboard: .numberPad,
                             identifier: "interaction.rule.receiverUserID")
            LabeledTextField(title: "收款人文字", text: $rule.receiverText, optional: true,
                             identifier: "interaction.rule.receiverText")
        } header: { Text("转账过滤") } footer: {
            Text("金额是群内游戏币。启用转账触发前，服务器要求在交互页「真实转账结果通知 Bot」里填写至少一个 Bot ID。")
        }
    }

    private var limitSection: some View {
        Section {
            // 冷却允许写「15」或「15s」「2m」：纯数字时补「秒」，带单位的原样显示。
            LabeledTextField(title: "每用户冷却", text: $rule.cooldown, prompt: "可选，如 15 或 15s",
                             unit: FieldUnit.ifNumeric(rule.cooldown, "秒"), identifier: "interaction.rule.cooldown")
            LabeledTextField(title: "每用户每日上限", text: $rule.dailyLimit, unit: "次", optional: true, keyboard: .numberPad,
                             identifier: "interaction.rule.dailyLimit")
            LabeledTextField(title: "参与有效期", text: $rule.validSeconds, prompt: "可选", unit: "秒", keyboard: .numberPad,
                             identifier: "interaction.rule.validSeconds")
            Picker("并发方式", selection: $rule.concurrency) {
                Text("按群").tag("chat")
                Text("按用户").tag("user")
                Text("不并发").tag("none")
            }
            if rule.action == "math10" || selectedEntry?.supportsPrize == true {
                LabeledTextField(title: "奖金", text: $rule.prize, prompt: "默认 123", keyboard: .numberPad,
                                 identifier: "interaction.rule.prize")
            }
        } header: { Text("限制") }
    }

    private var commandSection: some View {
        Section {
            multiline("开启指令", optional: true, prompt: "每行一条", text: $rule.openCommands, id: "open")
            multiline("关闭指令", optional: true, prompt: "每行一条", text: $rule.closeCommands, id: "close")
            multiline("状态指令", optional: true, prompt: "每行一条", text: $rule.statusCommands, id: "status")
            StackedTextField(title: "暂停时回复", text: $rule.disabledMessage, prompt: "规则关闭时群成员触发会收到这句话",
                             optional: true, lines: 1...4, capitalization: .sentences, identifier: "interaction.rule.disabledMessage")
        } header: { Text("管理指令") } footer: { Text("每行一条，管理员在群里发这些指令可开启、关闭或查询本规则。") }
    }

    private var replySection: some View {
        Section {
            TelegramTemplateInlineField(title: "回复模板", showsTitle: true, text: $rule.responseTemplate,
                                        spec: TelegramTemplateSpec(botName: "交互 Bot", samples: TelegramTemplateSamples.interactionReply,
                                                                   prompt: "（转账 88.00 给 Bob）"),
                                        identifier: "interaction.rule.response")
        } header: { Text("命中回复") } footer: {
            Text("可用变量：{payer_name} 付款人、{receiver_name} 收款人、{amount} 金额、{规则名称}。")
        }
    }

    /// 这条玩法在群成员查询时显示成什么样（第 21 轮）：用账号的「每个玩法的条目模板」按本规则当前填写的内容渲染。
    @ViewBuilder
    private var querySection: some View {
        if let query, rule.action != "notice" {
            Section {
                TelegramChatPreview(model: TelegramMessagePreviewModel(template: query.single(rule), values: [:]), botName: "交互 Bot",
                                    prompt: query.prompt, identifier: "interaction.rule.queryPreview")
                    .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
            } header: { Text("在玩法查询里显示为") } footer: {
                Text(rule.enabled
                     ? "群成员发查询指令时，这条玩法按「每个玩法的条目模板」列出（模板在交互页「查询指令与通知模板」里改）；被管理指令临时关闭时不列出。"
                     : "这条规则已停用，查询时不会列出；上面是启用后的样子。模板在交互页「查询指令与通知模板」里改。")
            }
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
            StackedTextField(title: "开局提示", text: $rule.moduleStartText, optional: true, lines: 1...4,
                             capitalization: .sentences, identifier: "interaction.rule.moduleStartText")
            if rule.moduleConfigCount > 0 {
                LabeledContent("玩法参数", value: "\(rule.moduleConfigCount) 项 · 保存时保留")
            }
        } header: { Text("玩法入口") } footer: {
            Text(entries.isEmpty ? "没有读到插件声明的玩法入口：服务器版本较旧或尚未安装交互类插件。" : "列表来自已安装插件声明的交互入口。")
        }
    }

    private var chatSection: some View {
        Section {
            multiline("生效群 Chat ID", prompt: "每行一个，留空不限", text: $rule.chatIDs, id: "chats", numbers: true)
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

    private func multiline(_ title: String, optional: Bool = false, prompt: String, text: Binding<String>, id: String,
                           numbers: Bool = false) -> some View {
        StackedTextField(title: title, text: text, prompt: prompt, optional: optional,
                         keyboard: numbers ? .numbersAndPunctuation : .default, monospaced: numbers,
                         identifier: "interaction.rule.\(id)")
    }
}

/// 模拟测试群（Web `AllowedPeerMultiSelect`，`emptyMeansAll = false`）：从已允许会话多选 + 手动填写 Chat ID。
/// 留空 = 不启用任何测试群（不是“全部”）。最多 20 个。
private struct InteractionTestChatPicker: View {
    @Binding var chatIDs: String
    let peers: [InteractionPeer]

    private var selectedItems: [String] { InteractionDraft.parseLines(chatIDs) }
    private var unknownSelected: [String] {
        let known = Set(peers.map { String($0.id) })
        return selectedItems.filter { !known.contains($0) }
    }

    var body: some View {
        List {
            Section {
                if peers.isEmpty {
                    Text("暂无已允许会话可点选。可以在下方手填 Chat ID，或到账号详情的「会话白名单」（Web「允许会话」）添加。")
                        .font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("interaction.testChats.empty")
                }
                ForEach(peers) { peer in
                    Toggle(isOn: Binding(
                        get: { selectedItems.contains(String(peer.id)) },
                        set: { on in
                            var items = selectedItems
                            let id = String(peer.id)
                            if on { if !items.contains(id) { items.append(id) } } else { items.removeAll { $0 == id } }
                            chatIDs = items.joined(separator: "\n")
                        })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(peer.label)
                            Text("\(peer.kindLabel) · \(String(peer.id))").font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        }
                    }
                    .accessibilityIdentifier("interaction.testChats.peer.\(peer.id)")
                }
            } header: {
                HStack {
                    Text(peers.isEmpty ? "已允许会话" : "从已允许会话选择")
                    Spacer()
                    Text(selectedItems.isEmpty ? "未选择" : "\(selectedItems.count) 个已选")
                        .accessibilityIdentifier("interaction.testChats.count")
                }
            }
            if !unknownSelected.isEmpty {
                Section {
                    ForEach(unknownSelected, id: \.self) { id in
                        Button(role: .destructive) {
                            chatIDs = selectedItems.filter { $0 != id }.joined(separator: "\n")
                        } label: {
                            LabeledContent {
                                Image(systemName: "minus.circle.fill")
                            } label: {
                                Text(id).monospacedDigit()
                            }
                        }
                        .accessibilityLabel("移除 \(id)")
                    }
                } header: { Text("不在已允许会话里") } footer: { Text("点按从模拟测试群移除。") }
            }
            Section {
                TextField("例如：-1001234567890", text: $chatIDs, axis: .vertical)
                    .lineLimit(2...8)
                    .font(.body.monospacedDigit())
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.numbersAndPunctuation)
                    .accessibilityIdentifier("interaction.testChats.manual")
            } header: { Text("手动填写 Chat ID（可选，一行一个）") } footer: {
                Text("没有找到想选的会话时，可手填 Chat ID，或到账号详情的「会话白名单」添加快捷选项。只有明确选择的群会响应 +金额、-金额模拟命令；留空不会启用任何测试群。最多 20 个，点交互页右上角「保存」后生效。")
            }
        }
        .navigationTitle("模拟测试群")
        .navigationBarTitleDisplayMode(.inline)
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
    var entries: [InteractionEntryOption] = []

    var body: some View {
        Form {
            Section {
                field("查询指令", prompt: "每行一条", text: $draft.queryCommands, id: "interaction.query.commands")
            } header: { Text("玩法查询") } footer: { Text("群成员发查询指令时，交互 Bot 列出当前可用的联动玩法。") }
            Section {
                TelegramTemplateInlineField(title: "查询回复模板", text: $draft.queryResponseTemplate, spec: querySpec(response: true),
                                            identifier: "interaction.query.response")
            } header: { Text("查询回复模板") } footer: { Text("{items} 是下面条目模板逐个渲染后的列表；{count} 可用数量、{closed_count} 已关闭数量、{chat_id} 群 ID。留空保持服务器当前值，预览用默认模板。") }
            Section {
                TelegramTemplateInlineField(title: "每个玩法的条目模板", text: $draft.queryItemTemplate, spec: querySpec(response: false),
                                            identifier: "interaction.query.item")
            } header: { Text("每个玩法的条目模板") } footer: { Text("{index} 序号、{name} 规则名称、{trigger} 触发方式、{kind} 玩法类型、{limit} 限制摘要、{module_key} 插件 key。预览用本账号当前启用的玩法规则逐条代入（没有时用两条示例玩法），与上面的回复模板组合成整条消息。") }
            Section {
                field("没有可用玩法时回复", prompt: "留空保持服务器当前值", text: $draft.queryEmptyMessage, id: "interaction.query.empty")
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

    /// 玩法查询：回复模板与条目模板组合成一条消息。第 21 轮起用本账号当前（草稿）的玩法规则逐条代入；
    /// 一条可列出的规则都没有时，沿用 Web BotTab 的两条示例玩法。
    private func querySpec(response: Bool) -> TelegramTemplateSpec {
        let context = InteractionQueryContext(draft: draft, entries: entries)
        let responseTemplate = draft.queryResponseTemplate
        let itemTemplate = draft.queryItemTemplate
        let useRules = context.listedCount > 0
        var spec = TelegramTemplateSpec(botName: "交互 Bot",
                                        samples: ["count": useRules ? String(context.listedCount) : "2", "closed_count": "0",
                                                  "chat_id": InteractionQueryPreview.sampleChatID])
        spec.prompt = context.prompt
        if response {
            spec.samples["items"] = "（下方条目模板渲染的列表）"
            spec.compose = { useRules ? context.full(response: $0) : TelegramTemplateSamples.interactionQuery(response: $0, item: itemTemplate) }
            spec.defaultTemplate = TelegramTemplateSamples.defaultQueryResponseTemplate
        } else {
            spec.samples = context.firstItemValues ?? ["index": "1", "name": "示例玩法", "trigger": "转账或关键词", "kind": "玩法 <code>example_game</code>",
                                                       "limit": "限时 <code>600</code> 秒", "module_key": "example_game"]
            spec.compose = { useRules ? context.full(item: $0) : TelegramTemplateSamples.interactionQuery(response: responseTemplate, item: $0) }
            spec.defaultTemplate = TelegramTemplateSamples.defaultQueryItemTemplate
        }
        return spec
    }

    private func noticeSpec(_ fallback: String, prompt: String) -> TelegramTemplateSpec {
        TelegramTemplateSpec(botName: "转账通知 Bot", samples: TelegramTemplateSamples.transferNotice, defaultTemplate: fallback, prompt: prompt)
    }

    private func field(_ title: String, prompt: String, text: Binding<String>, id: String) -> some View {
        StackedTextField(title: title, text: text, prompt: prompt, lines: 1...8, identifier: id)
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
    /// 会话类型（与 Web `peerKindLabel` 相同：私聊／普通群／超级群／频道）。
    var kindLabel: String = "会话"
    init?(_ value: JSONValue) {
        guard let peerID = value["peer_id"]?.intValue else { return nil }
        id = peerID
        let kind = value["peer_kind"]?.stringValue ?? ""
        kindLabel = ["private": "私聊", "group": "普通群", "supergroup": "超级群", "channel": "频道"][kind] ?? (kind.isEmpty ? "会话" : kind)
        let raw = value["peer_label"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        label = raw.isEmpty ? "\(kindLabel) \(peerID)" : raw
    }
}

struct InteractionDraftError: Error { let message: String }

/// 交互配置的可编辑草稿。与服务器值比较判断是否有未保存修改。
struct InteractionDraft: Equatable {
    var enabled = false
    var newToken = ""
    var clearToken = false
    var trustedBotIDs = ""
    /// 测试模拟通知 Bot（Web「身份配置 › 真实转账结果通知 Bot」）：新 Token 只在保存时提交。
    var newTransferToken = ""
    var clearTransferToken = false
    /// 模拟测试群（`transfer_test_chat_ids`），每行一个 Chat ID；只有明确选择的群响应 +金额／-金额。
    var transferTestChatIDs = ""
    /// 服务器返回里有 `transfer_test_chat_ids` 字段（旧服务器没有时不显示、也不提交这一项）。
    var supportsTransferTestChats = false
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
        supportsTransferTestChats = value["transfer_test_chat_ids"] != nil
        transferTestChatIDs = Self.lines(value["transfer_test_chat_ids"])
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
        let trusted = try Self.parseInts(trustedBotIDs, label: "真实通知 Bot ID", positive: true)
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
        // 测试模拟通知 Bot：与 Web buildInteractionPayload 相同字段；旧服务器没有模拟测试群字段时不提交（服务器按未设置保留原值）。
        let transferToken = newTransferToken.trimmingCharacters(in: .whitespacesAndNewlines)
        if clearTransferToken { body["clear_transfer_bot_token"] = true }
        if !transferToken.isEmpty { body["transfer_bot_token"] = .string(transferToken) }
        if supportsTransferTestChats {
            let testChats = try Self.parseInts(transferTestChatIDs, label: "模拟测试群 Chat ID")
            guard testChats.count <= 20 else { throw InteractionDraftError(message: "模拟测试群最多选择 20 个") }
            body["transfer_test_chat_ids"] = .array(testChats.map { JSONValue.integer(Int64($0)) })
        }
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

    /// 预览用的服务器形状 JSON：能通过校验时与保存提交的完全相同；填写有误（如数字不合法）时退回按当前填写拼出的近似值，
    /// 只用于玩法查询预览，不提交。
    func previewJSON(index: Int, entries: [InteractionEntryOption]) -> JSONValue {
        if let value = try? json(index: index, entries: entries) { return value }
        var o = raw
        func number(_ text: String) -> JSONValue {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { return .null }
            return Int(trimmed).map { JSONValue.integer(Int64($0)) } ?? .string(trimmed)
        }
        o["id"] = .string(id)
        o["name"] = .string(displayName)
        o["enabled"] = .bool(enabled)
        o["action"] = .string(action)
        o["trigger_mode"] = .string(action == "notice" ? "payment" : triggerMode)
        o["module_start_keywords"] = .array(InteractionDraft.parseLines(moduleStartKeywords).map { JSONValue.string($0) })
        o["module_key"] = .string(moduleKey)
        o["module_action"] = .string(moduleAction)
        o["chat_ids"] = .array(InteractionDraft.ints(chatIDs).map { JSONValue.integer(Int64($0)) })
        o["valid_seconds"] = number(validSeconds)
        o["user_cooldown_seconds"] = .string(cooldown)
        o["daily_limit_per_user"] = number(dailyLimit)
        o["math_prize"] = number(prize)
        o["module_prize"] = action == "module" ? number(prize) : .null
        return .object(o)
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

// MARK: - 通知 Bot 设置（第 21 轮：从交互页主列表收进二级页）

/// 「真实转账结果通知 Bot」与「测试模拟通知 Bot」（Web BotTab「身份配置」右栏）。改的是交互页同一份草稿，
/// 和规则编辑页一样回到交互页点右上角「保存」一次提交。
struct InteractionNotifyBotsView: View {
    @Binding var draft: InteractionDraft
    let baseline: InteractionDraft
    let value: JSONValue
    let peers: [InteractionPeer]
    let accountID: Int?
    @State private var confirmingClearNotify = false

    /// 交互页那一行上的状态摘要。
    static func summary(draft: InteractionDraft, value: JSONValue) -> String {
        let trusted = InteractionDraft.parseLines(draft.trustedBotIDs).count
        var parts = [trusted > 0 ? "真实 \(trusted) 个" : "真实未填写"]
        if value["has_transfer_bot_token"] != nil {
            let saved = (value["has_transfer_bot_token"]?.boolValue ?? false) && !draft.clearTransferToken
            let typed = !draft.newTransferToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            parts.append(saved || typed ? "测试 Bot 已配置" : "测试 Bot 未配置")
            if draft.supportsTransferTestChats {
                let chats = InteractionDraft.parseLines(draft.transferTestChatIDs).count
                parts.append(chats > 0 ? "\(chats) 个测试群" : "未选测试群")
            }
        }
        return parts.joined(separator: " · ")
    }

    /// 这一页的字段有没有未保存的修改。
    static func isDirty(_ draft: InteractionDraft, _ baseline: InteractionDraft) -> Bool {
        draft.trustedBotIDs != baseline.trustedBotIDs || draft.newTransferToken != baseline.newTransferToken
            || draft.clearTransferToken != baseline.clearTransferToken || draft.transferTestChatIDs != baseline.transferTestChatIDs
    }

    var body: some View {
        List {
            if Self.isDirty(draft, baseline) {
                Section {
                    Label("有未保存的修改：返回交互页点右上角「保存」后生效。", systemImage: "exclamationmark.circle")
                        .font(.footnote).foregroundStyle(.orange)
                        .accessibilityIdentifier("interaction.notifyBots.unsaved")
                }
            }
            trustedNotifySection
            if value["has_transfer_bot_token"] != nil {
                testNotifySection(value)
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("通知 Bot 设置")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("interaction.notifyBots.screen")
    }

    private var trustedNotifySection: some View {
        Section {
            StackedTextField(title: "真实通知 Bot ID", text: $draft.trustedBotIDs, prompt: "每行一个数字 ID，不是 @ 用户名",
                             keyboard: .numbersAndPunctuation, monospaced: true, identifier: "interaction.trustedBotIDs")
        } header: { Text("真实转账结果通知 Bot") } footer: {
            Text("填群里实际发送到账结果的 Bot ID（参考转账结果消息的发送者 ID），多个群用不同通知 Bot 时可以全部加入；保存时自动去重，测试模拟 Token 识别出的 Bot 会额外加入信任。启用转账触发的规则前必须至少有一个。")
        }
    }

    private func testNotifySection(_ value: JSONValue) -> some View {
        let hasTransferToken = value["has_transfer_bot_token"]?.boolValue ?? false
        let botID = value["transfer_bot_id"]?.intValue
        let selected = InteractionDraft.parseLines(draft.transferTestChatIDs).count
        return Section {
            SecretField(
                title: "测试模拟通知 Bot Token",
                text: $draft.newTransferToken,
                configured: hasTransferToken && !draft.clearTransferToken,
                reveal: accountID.map { SecretRevealTarget(kind: "transfer_bot_token", accountID: $0) },
                placeholder: "测试模拟通知时填写",
                identifier: "interaction.transferToken",
                disabled: draft.clearTransferToken)
            LabeledContent {
                Text(botID.map(String.init) ?? "未识别").foregroundStyle(.secondary).monospacedDigit()
            } label: {
                Label("当前测试 Bot ID", systemImage: "number")
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("interaction.transferBotID")
            if hasTransferToken {
                Toggle(isOn: $draft.clearTransferToken) {
                    Label("清除已保存的测试 Token", systemImage: "trash")
                }
                .tint(.red)
                .accessibilityIdentifier("interaction.clearTransferToken")
            }
            if draft.supportsTransferTestChats {
                NavigationLink {
                    InteractionTestChatPicker(chatIDs: $draft.transferTestChatIDs, peers: peers)
                } label: {
                    Label {
                        LabeledContent("模拟测试群") { Text(selected > 0 ? "\(selected) 个已选" : "未选择") }
                    } icon: { Image(systemName: "checklist") }
                }
                .accessibilityIdentifier("interaction.testChats")
            }
            InteractionMembershipGroup(title: "测试 Bot 群成员状态", memberships: value["transfer_test_chat_memberships"]?.arrayValue ?? [],
                                       peers: peers, id: "interaction.testMemberships")
            Button("清空通知 Bot", systemImage: "trash", role: .destructive) { confirmingClearNotify = true }
                .disabled(draft.trustedBotIDs.isEmpty && draft.newTransferToken.isEmpty && (!hasTransferToken || draft.clearTransferToken))
                .accessibilityIdentifier("interaction.clearNotify")
                .confirmationDialog("清空转账结果通知 Bot？", isPresented: $confirmingClearNotify, titleVisibility: .visible) {
                    Button("清空", role: .destructive) {
                        draft.trustedBotIDs = ""
                        draft.newTransferToken = ""
                        draft.clearTransferToken = hasTransferToken
                    }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text("清空真实通知 Bot ID 和测试模拟 Token（模拟测试群保留），点右上角「保存」后生效。")
                }
        } header: { Text("测试模拟通知 Bot") } footer: {
            let trust = botID.map { "系统会同时信任真实通知 Bot ID 和这个测试 Bot ID（\($0)）。" } ?? "没有测试模拟 Token 时，系统只监听群里真实出现的转账结果通知。"
            Text("\(trust)只有明确选择的模拟测试群会响应 +金额、-金额模拟命令；留空不会启用任何测试群，真实转账规则群也不会自动启用测试 Bot。")
        }
    }
}

/// 群成员状态（Web `BotMembershipList`）：交互 Bot「监听群成员状态」、测试 Bot「测试 Bot 群成员状态」。
/// 服务器缓存 5 分钟；没有数据时不显示。
private struct InteractionMembershipGroup: View {
    let title: String
    let memberships: [JSONValue]
    let peers: [InteractionPeer]
    let id: String

    var body: some View {
        if !memberships.isEmpty {
            DisclosureGroup {
                ForEach(Array(memberships.enumerated()), id: \.offset) { _, item in
                    row(item)
                }
            } label: {
                let joined = memberships.filter { $0["status"]?.stringValue == "joined" }.count
                Label("\(title) · \(joined)/\(memberships.count) 已加入", systemImage: "person.2")
                    .accessibilityIdentifier(id)
            }
        }
    }

    private func row(_ item: JSONValue) -> some View {
        let chatID = item["chat_id"]?.intValue ?? 0
        let status = item["status"]?.stringValue ?? "unknown"
        let title = peers.first(where: { $0.id == chatID })?.label ?? "会话 \(chatID)"
        let state: (text: String, symbol: String, tint: Color)
        switch status {
        case "joined": state = ("已加入", "checkmark.circle.fill", .green)
        case "not_joined": state = ("未加入", "exclamationmark.triangle.fill", .orange)
        default: state = ("无法确认", "questionmark.circle", .secondary)
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
}

// MARK: - 玩法查询预览上下文（第 21 轮）

/// 用当前草稿的规则与模板预览「群成员发查询指令时交互 Bot 的回复」。渲染规则见 `TelePilotCore.InteractionQueryPreview`（移植服务器）。
struct InteractionQueryContext {
    let draft: InteractionDraft
    let entries: [InteractionEntryOption]
    let rules: [(id: String, json: JSONValue)]

    init(draft: InteractionDraft, entries: [InteractionEntryOption]) {
        self.draft = draft
        self.entries = entries
        rules = draft.rules.enumerated().map { (id: $0.element.id, json: $0.element.previewJSON(index: $0.offset, entries: entries)) }
    }

    /// 用户消息示例：第一条查询指令（没有时用服务器默认「。玩法」）。
    var prompt: String {
        draft.queryCommands.split(whereSeparator: \.isNewline).first.map { $0.trimmingCharacters(in: .whitespaces) } ?? "。玩法"
    }

    private var listed: [(id: String, json: JSONValue)] {
        let kept = Set(InteractionQueryPreview.listedRules(rules.map { $0.json }).compactMap { $0["id"]?.stringValue })
        return rules.filter { kept.contains($0.id) }
    }

    var listedCount: Int { listed.count }

    func prizeDeclared(_ rule: JSONValue) -> Bool? {
        guard rule["action"]?.stringValue == "module" else { return nil }
        let key = rule["module_key"]?.stringValue ?? ""
        let action = rule["module_action"]?.stringValue ?? ""
        return entries.first { $0.featureKey == key && $0.entryKey == action }?.supportsPrize
    }

    /// 整条查询回复（HTML）。`response`／`item` 传入时代替草稿里的模板（编辑框实时内容）。
    func full(response: String? = nil, item: String? = nil) -> String {
        InteractionQueryPreview.compose(response: response ?? draft.queryResponseTemplate, item: item ?? draft.queryItemTemplate,
                                        empty: draft.queryEmptyMessage, rules: rules.map { $0.json }, prizeDeclared: prizeDeclared)
    }

    /// 某条规则在查询回复里的那一项（纯文本，用在规则行上）；不会被列出时为 nil。
    func itemLine(for ruleID: String) -> String? {
        guard let index = listed.firstIndex(where: { $0.id == ruleID }) else { return nil }
        let rule = listed[index].json
        let html = InteractionQueryPreview.renderItem(template: draft.queryItemTemplate, rule: rule, index: index + 1,
                                                      prizeDeclared: prizeDeclared(rule))
        let text = TelegramHTML.parse(html).plainText.split(whereSeparator: \.isNewline).joined(separator: " · ")
        return text.isEmpty ? nil : text
    }

    /// 只含这一条规则的查询回复（规则编辑页预览；停用的规则按启用后的样子显示）。
    func single(_ rule: InteractionRuleDraft) -> String {
        let index = draft.rules.firstIndex(where: { $0.id == rule.id }) ?? 0
        var json = rule.previewJSON(index: index, entries: entries).objectValue ?? [:]
        json["enabled"] = .bool(true)
        return InteractionQueryPreview.compose(response: draft.queryResponseTemplate, item: draft.queryItemTemplate,
                                               empty: draft.queryEmptyMessage, rules: [.object(json)], prizeDeclared: prizeDeclared)
    }

    /// 第一条可列出规则的变量值（条目模板完整编辑页的「示例值」）。
    var firstItemValues: [String: String]? {
        guard let first = listed.first?.json else { return nil }
        return InteractionQueryPreview.itemValues(rule: first, index: 1, prizeDeclared: prizeDeclared(first))
    }
}

// MARK: - 运行状态（第 21 轮：紧凑卡片 + 详情页）

struct InteractionRuntimeStatus {
    var enabled: Bool
    var running: Bool
    var hasToken: Bool
    var username: String?
    var lastError: String?
    var totalRules: Int
    var activeRules: Int
    var chats: Int
    var dlq: Int

    init(_ value: JSONValue) {
        enabled = value["enabled"]?.boolValue ?? false
        running = value["interaction_running"]?.boolValue ?? false
        hasToken = value["has_interaction_bot_token"]?.boolValue ?? false
        username = value["interaction_bot_username"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 }
        let error = value["interaction_last_error"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines)
        lastError = (error?.isEmpty ?? true) ? nil : error
        let rules = value["rules"]?.arrayValue ?? []
        totalRules = rules.count
        activeRules = rules.filter { $0["enabled"]?.boolValue ?? true }.count
        chats = Set(rules.flatMap { ($0["chat_ids"]?.arrayValue ?? []).compactMap(\.intValue) }).count
        dlq = value["polling_dlq_count"]?.intValue ?? 0
    }

    /// 一句话状态（卡片标题）。
    var headline: String {
        if !enabled { return "交互未启用" }
        if !hasToken { return "交互 Bot 待配置" }
        return running ? "交互运行中" : "交互未运行"
    }

    var tint: Color {
        if lastError != nil && enabled { return .red }
        if !enabled { return .secondary }
        return running && hasToken ? .green : .orange
    }

    /// 关键数字：Bot · 启用规则 · 群数。
    var numbers: String {
        let bot = hasToken ? (username.map { "@\($0)" } ?? "Bot 已配置") : "未配置 Bot"
        return "\(bot) · \(activeRules)/\(totalRules) 条规则启用 · \(chats) 个群"
    }
}

/// 交互页顶部的一行运行状态：状态点 + 一句话 + 关键数字；有错误、失败投递时右侧小标记。
private struct InteractionStatusCard: View {
    let status: InteractionRuntimeStatus

    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(status.tint).frame(width: 10, height: 10)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(status.headline).font(.subheadline.weight(.semibold))
                Text(status.numbers).font(.caption).foregroundStyle(.secondary).monospacedDigit().lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
            Spacer(minLength: 4)
            if status.lastError != nil {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red).imageScale(.small)
                    .accessibilityLabel("最近运行有错误")
            }
            if status.dlq > 0 {
                Label("\(status.dlq)", systemImage: "tray.and.arrow.down")
                    .labelStyle(.titleAndIcon).font(.caption.weight(.medium)).foregroundStyle(.orange)
                    .accessibilityLabel("失败投递 \(status.dlq) 条")
            }
        }
    }
}

/// 运行状态详情（原交互页「运行状态」分组的全部内容）。
private struct InteractionStatusDetailView: View {
    let status: InteractionRuntimeStatus

    var body: some View {
        List {
            Section {
                row("交互总闸", status.enabled ? "已启用" : "未启用", symbol: status.enabled ? "power.circle.fill" : "power.circle",
                    tint: status.enabled ? .green : .secondary, id: "gate")
                row("交互 Bot", status.hasToken ? (status.username.map { "@\($0)" } ?? "已配置") : "待配置 Token",
                    symbol: status.hasToken ? "checkmark.circle.fill" : "exclamationmark.triangle.fill",
                    tint: status.hasToken ? .green : .orange, id: "token")
                row("监听状态", status.running ? "运行中" : (status.enabled ? "未运行" : "未启用"),
                    symbol: status.running ? "checkmark.circle.fill" : (status.enabled ? "exclamationmark.triangle.fill" : "pause.circle"),
                    tint: status.running ? .green : (status.enabled ? .orange : .secondary), id: "runtime")
                row("启用订阅", "\(status.activeRules)/\(status.totalRules) 条 · \(status.chats) 个群", symbol: "bubble.left.and.text.bubble.right",
                    tint: .accentColor, id: "rules")
                if status.dlq > 0 {
                    row("失败投递", "\(status.dlq) 条待处理", symbol: "tray.and.arrow.down", tint: .orange, id: "dlq")
                }
            } footer: {
                Text("状态取自服务器最近一次读取或保存的结果；保存后交互 Bot 会按新配置自动重启。失败投递可在账号详情的「失败投递」里重试或丢弃。")
            }
            if let lastError = status.lastError {
                Section {
                    Label {
                        Text(lastError).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                    } icon: { Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    .accessibilityIdentifier("interaction.status.error")
                } header: { Text("最近运行有错误") }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("运行状态")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("interaction.statusDetail")
    }

    private func row(_ title: String, _ value: String, symbol: String, tint: Color, id: String) -> some View {
        LabeledContent {
            Text(value).foregroundStyle(.secondary).multilineTextAlignment(.trailing)
        } label: {
            Label { Text(title) } icon: { Image(systemName: symbol).foregroundStyle(tint) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("interaction.status.\(id)")
    }
}
