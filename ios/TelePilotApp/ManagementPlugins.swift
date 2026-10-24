import SwiftUI
import TelePilotCore

private enum PluginWorkspaceSection: String, CaseIterable {
    case center, management
    var title: String { self == .center ? "插件中心" : "插件管理" }
}

private enum PluginFilter: String, CaseIterable {
    case all, running, attention, updates, favorites
    var title: String {
        switch self {
        case .all: "全部"
        case .running: "运行中"
        case .attention: "需处理"
        case .updates: "可更新"
        case .favorites: "收藏"
        }
    }
}

struct PluginManagementView: View {
    var accountID: Int? = nil
    @Environment(AppModel.self) private var model
    @State private var section: PluginWorkspaceSection = .center
    @State private var features: [JSONValue] = []
    @State private var installed: [String: JSONValue] = [:]
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        Group {
            if let accountID {
                PluginCenterView(features: features, fixedAccountID: accountID, loading: busy, loadError: error, reload: load)
            } else {
                // 分段切换挂在各自滚动视图的系统安全区栏上（iOS 26 safeAreaBar），
                // 内容滚过时由系统滚动边缘效果承托，不再使用实色背景条。
                switch section {
                case .center:
                    PluginCenterView(features: features, loading: busy, loadError: error, reload: load)
                        .safeAreaBar(edge: .top) { sectionPicker }
                case .management:
                    PluginSourcesView(features: features, installed: installed, loading: busy, loadError: error, reload: load)
                        .safeAreaBar(edge: .top) { sectionPicker }
                }
            }
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle(accountID == nil ? "插件" : "插件与规则")
        // 一级 Tab 与概览、AI、更多保持一致使用大标题；账号下的子页面仍用行内标题。
        .navigationBarTitleDisplayMode(accountID == nil ? .large : .inline)
        .task { await load() }
    }

    private var sectionPicker: some View {
        Picker("插件工作区", selection: $section) {
            ForEach(PluginWorkspaceSection.allCases, id: \.self) { item in
                Text(item.title).tag(item)
                    .accessibilityIdentifier(item == .center ? "plugins.center" : "plugins.management")
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .controlSize(.large)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private func load() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            let response = try await model.request(path: "/api/feature-matrix")
            features = response["features"]?.arrayValue ?? []
            if let packages = try? await model.request(path: "/api/plugins/installed-overview").arrayValue {
                installed = Dictionary(packages.compactMap { item in item["key"]?.stringValue.map { ($0, item) } }, uniquingKeysWith: { _, new in new })
            }
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

private extension JSONValue {
    func withInstallState(_ install: JSONValue?) -> JSONValue {
        guard var object = objectValue else { return self }
        object["global_enabled"] = install?["global_enabled"] ?? .null
        return .object(object)
    }
}

// MARK: - 插件中心

private struct PluginCenterView: View {
    let features: [JSONValue]
    var fixedAccountID: Int? = nil
    var loading = false
    var loadError: String?
    let reload: () async -> Void
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedAccountID: Int?
    @State private var accountFeatures: [String: JSONValue] = [:]
    @State private var accountError: String?
    @State private var accountLoading = false
    @State private var search = ""
    @State private var category = "all"
    @State private var filter: PluginFilter = .all

    private var selectedAccount: Account? { model.accounts.first { $0.id == selectedAccountID } }
    private var categories: [String] { Array(Set(features.compactMap { $0["category"]?.stringValue })).sorted() }
    private var filtered: [JSONValue] {
        features.filter { feature in
            let key = feature["key"]?.stringValue ?? ""
            let state = accountFeatures[key]
            let matchesFilter: Bool
            switch filter {
            case .all: matchesFilter = true
            case .running: matchesFilter = PluginPresentation.status(feature: feature, accountState: state, accountScoped: selectedAccountID != nil).tone == .running
            case .attention: matchesFilter = PluginPresentation.needsAttention(feature: feature, accountState: state)
            case .updates: matchesFilter = feature["update_available"]?.boolValue == true
            case .favorites: matchesFilter = preferences.isFavoritePlugin(key)
            }
            return matchesFilter
                && (category == "all" || feature["category"]?.stringValue == category)
                && (search.isEmpty || ManagementText.title(feature).localizedCaseInsensitiveContains(search)
                    || key.localizedCaseInsensitiveContains(search)
                    || (feature["usage"]?.stringValue?.localizedCaseInsensitiveContains(search) ?? false))
        }
    }
    private var columns: [GridItem] {
        Array(repeating: GridItem(.flexible(), spacing: 10, alignment: .top), count: horizontalSizeClass == .regular ? 3 : 2)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                scopePicker
                if let loadError { NoticeView(title: "插件目录读取失败", message: loadError) }
                if let accountError { NoticeView(title: "账号插件状态读取失败", message: accountError) }
                if loading && features.isEmpty {
                    ProgressView("正在读取插件与账号状态…").frame(maxWidth: .infinity).padding(.vertical, 48)
                } else {
                    filterBar
                    HStack {
                        Menu {
                            Picker("分类", selection: $category) {
                                Text("全部分类").tag("all")
                                ForEach(categories, id: \.self) { Text(PluginPresentation.category($0)).tag($0) }
                            }
                        } label: {
                            Label(category == "all" ? "全部分类" : PluginPresentation.category(category), systemImage: "line.3.horizontal.decrease.circle")
                                .font(.subheadline)
                        }
                        .accessibilityIdentifier("plugins.category")
                        Spacer()
                        Text("\(filtered.count) / \(features.count) 个插件").font(.footnote).foregroundStyle(.secondary).monospacedDigit()
                    }
                    if accountLoading { ProgressView().frame(maxWidth: .infinity, alignment: .leading) }
                    if filtered.isEmpty {
                        ContentUnavailableView("没有匹配的插件", systemImage: "puzzlepiece.extension", description: Text("调整所选账号、状态或分类筛选。")).padding(.vertical, 24)
                    } else {
                        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
                            ForEach(Array(filtered.enumerated()), id: \.offset) { _, feature in
                                let key = feature["key"]?.stringValue ?? ""
                                NavigationLink {
                                    ManagementPluginDetailView(feature: feature, accountID: selectedAccountID, accountState: accountFeatures[key])
                                } label: {
                                    PluginCard(feature: feature, accountState: accountFeatures[key], accountScoped: selectedAccountID != nil, favorite: preferences.isFavoritePlugin(key))
                                }
                                .buttonStyle(.plain)
                                .contextMenu {
                                    Button(preferences.isFavoritePlugin(key) ? "取消收藏" : "收藏", systemImage: "star") { preferences.togglePlugin(key) }
                                }
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 20)
        }
        .background(Color(.systemGroupedBackground))
        .searchable(text: $search, prompt: "搜索插件、标识或使用说明")
        .refreshable { await reload(); await loadAccountFeatures() }
        .task {
            if fixedAccountID == nil, selectedAccountID == nil { selectedAccountID = model.accounts.first?.id }
            if selectedAccountID == nil { selectedAccountID = fixedAccountID }
            await loadAccountFeatures()
        }
        .onChange(of: selectedAccountID) { _, _ in Task { await loadAccountFeatures() } }
        .onChange(of: model.accounts.map(\.id)) { _, ids in
            if fixedAccountID == nil, selectedAccountID == nil { selectedAccountID = ids.first }
        }
    }

    /// 选择配置的账号：决定卡片上显示“服务器整体状态”还是“某个账号里的启用与运行状态”。
    @ViewBuilder private var scopePicker: some View {
        if fixedAccountID == nil {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 10) {
                    Label("选择配置的账号：", systemImage: "person.crop.circle.badge.checkmark")
                        .font(.subheadline.weight(.semibold))
                    Spacer(minLength: 8)
                    Menu {
                        Picker("选择配置的账号", selection: $selectedAccountID) {
                            Label("全部账号", systemImage: "square.stack.3d.up").tag(Int?.none)
                            ForEach(model.accounts) { account in
                                Label(ManagementText.accountName(account), systemImage: "person.crop.circle").tag(Optional(account.id))
                            }
                        }
                    } label: {
                        HStack(spacing: 4) {
                            Text(selectedAccount.map(ManagementText.accountName) ?? "全部账号").lineLimit(1)
                            Image(systemName: "chevron.up.chevron.down").font(.caption2.weight(.semibold))
                        }
                        .font(.subheadline.weight(.medium))
                        .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("plugins.accountPicker")
                }
                Text(selectedAccountID == nil
                     ? "未选择账号：卡片显示插件在服务器上的整体状态。选择一个账号后，可以查看并配置插件在该账号里的启用情况。"
                     : "正在配置此账号：卡片显示插件在这个账号里是否启用、是否正常运行。点开插件即可为此账号开关或修改配置。")
                    .font(.footnote).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if model.accounts.isEmpty {
                    Label("还没有账号。可以先安装插件，添加账号后再到账号中启用。", systemImage: "info.circle").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Color(.secondarySystemGroupedBackground), in: .rect(cornerRadius: 12))
            .padding(.top, 4)
        }
    }

    /// 状态筛选：iOS 26 原生玻璃胶囊按钮，选中项用 glassProminent（强调色），其余为普通玻璃。
    /// 同一个 GlassEffectContainer 让相邻胶囊在切换时由系统统一渲染与融合。
    private var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            GlassEffectContainer(spacing: 8) {
                HStack(spacing: 8) {
                    ForEach(PluginFilter.allCases, id: \.self) { item in
                        filterButton(item)
                    }
                }
                .padding(.vertical, 4)
            }
        }
        .scrollClipDisabled()
    }

    @ViewBuilder private func filterButton(_ item: PluginFilter) -> some View {
        let selected = filter == item
        Group {
            if selected {
                Button(item.title) { filter = item }.buttonStyle(.glassProminent)
            } else {
                Button(item.title) { filter = item }.buttonStyle(.glass)
            }
        }
        .font(.subheadline)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("plugins.filter." + item.rawValue)
    }

    private func loadAccountFeatures() async {
        accountFeatures = [:]
        accountError = nil
        guard let selectedAccountID else { return }
        accountLoading = true
        defer { accountLoading = false }
        do {
            let response = try await model.request(path: "/api/accounts/\(selectedAccountID)/features")
            accountFeatures = Dictionary((response.arrayValue ?? []).compactMap { item in item["feature_key"]?.stringValue.map { ($0, item) } }, uniquingKeysWith: { _, new in new })
        } catch { accountError = error.localizedDescription }
    }
}

// MARK: - 插件管理（安装与来源）

private struct PluginSourcesView: View {
    let features: [JSONValue]
    let installed: [String: JSONValue]
    let loading: Bool
    let loadError: String?
    let reload: () async -> Void
    private var updates: Int { features.filter { $0["update_available"]?.boolValue == true }.count }

    var body: some View {
        List {
            if let loadError { NoticeView(title: "插件安装状态读取失败", message: loadError) }
            Section {
                NavigationLink {
                    ManagementFormView(action: .init(title: "检查插件更新", path: "/api/remote-plugins/check-updates", explanation: "只检查远程仓库版本，不会自动安装或更新插件。"))
                } label: {
                    LabeledContent {
                        Text(updates > 0 ? "\(updates) 个可更新" : "查看远程版本").foregroundStyle(updates > 0 ? Color.orange : Color.secondary)
                    } label: { Label("检查更新", systemImage: "arrow.triangle.2.circlepath") }
                }
                .accessibilityIdentifier("plugins.checkUpdates")
            } header: { Text("安装与更新") } footer: { Text("这里管理服务器上的插件包，不会改变某个账号的插件开关或规则。") }
            Section("来源") {
                NavigationLink { remotePlugins } label: { Label("远程插件与更新", systemImage: "icloud.and.arrow.down") }
                NavigationLink { repositories } label: { Label("插件仓库", systemImage: "books.vertical") }
                NavigationLink { FileManagementView(mode: .plugin) } label: { Label("从文件安装", systemImage: "doc.badge.plus") }
                NavigationLink { ManagementLocalPluginsView() } label: { Label("本地参考插件", systemImage: "folder") }
            }
            Section("已安装插件") {
                if loading && features.isEmpty { ProgressView("正在读取安装记录…") }
                ForEach(Array(features.enumerated()), id: \.offset) { _, feature in
                    NavigationLink { ManagementPluginDetailView(feature: feature.withInstallState(installed[feature["key"]?.stringValue ?? ""]), initialSection: .maintenance) } label: {
                        HStack(alignment: .top, spacing: 12) {
                            PluginIcon(feature: feature, size: 30)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(ManagementText.title(feature)).font(.headline).lineLimit(2)
                                Text(PluginPresentation.source(feature)).font(.caption).foregroundStyle(.secondary)
                                HStack(spacing: 6) {
                                    if let enabled = installed[feature["key"]?.stringValue ?? ""]?["global_enabled"]?.boolValue {
                                        PluginStatusBadge(title: enabled ? "服务器已启用" : "服务器已停用", symbol: enabled ? "checkmark.circle.fill" : "pause.circle", tone: enabled ? .running : .pending)
                                    }
                                    if feature["update_available"]?.boolValue == true {
                                        PluginStatusBadge(title: "可更新", symbol: "arrow.up.circle", tone: .pending)
                                    }
                                }
                                if let version = feature["version"]?.stringValue {
                                    Text("当前 v\(version)" + (feature["latest_version"]?.stringValue.map { " · 最新 v\($0)" } ?? "")).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
                if !loading, features.isEmpty { Label("暂无已安装插件", systemImage: "shippingbox").foregroundStyle(.secondary) }
            }
        }
        .refreshable { await reload() }
        .accessibilityIdentifier("plugins.management.screen")
    }
    private var remotePlugins: some View {
        ManagementCollectionView(resource: .init(title: "远程插件", path: "/api/remote-plugins", kind: .remotePlugins, create: .init(title: "从地址安装插件", path: "/api/remote-plugins/install"), itemPath: "/api/remote-plugins/{id}", idField: "name"))
    }
    private var repositories: some View {
        ManagementCollectionView(resource: .init(title: "插件仓库", path: "/api/plugin-repos", kind: .repos, create: .init(title: "添加仓库", path: "/api/plugin-repos"), itemPath: "/api/plugin-repos/{id}"))
    }
}

// MARK: - 展示辅助

private enum PluginStatusTone { case running, paused, pending, failed, unknown }

private enum PluginPresentation {
    static func status(feature: JSONValue, accountState: JSONValue?, accountScoped: Bool) -> (title: String, symbol: String, tone: PluginStatusTone) {
        if accountScoped {
            return accountStatus(enabled: accountState?["enabled"]?.boolValue == true,
                                 state: accountState?["state"]?.stringValue,
                                 error: accountState?["last_error"]?.stringValue)
        }
        if let runtime = feature["runtime_availability"]?.stringValue, !runtime.isEmpty {
            switch runtime {
            case "ready": return ("全局可用", "checkmark.circle.fill", .running)
            case "paused": return ("全局已暂停", "pause.circle", .paused)
            case "partial": return ("部分可用", "exclamationmark.circle", .pending)
            default: return ("等待运行状态", "clock", .pending)
            }
        }
        return ("已安装", "shippingbox", .unknown)
    }
    static func accountStatus(enabled: Bool, state: String?, error: String?) -> (title: String, symbol: String, tone: PluginStatusTone) {
        guard enabled else { return ("未启用", "pause.circle", .paused) }
        if (error?.isEmpty == false) || state == "failed" { return ("运行异常", "exclamationmark.circle.fill", .failed) }
        if state == "active" { return ("运行中", "checkmark.circle.fill", .running) }
        return ("等待生效", "clock", .pending)
    }
    static func needsAttention(feature: JSONValue, accountState: JSONValue?) -> Bool {
        feature["orphan"]?.boolValue == true || feature["signature_ok"]?.boolValue == false || feature["update_available"]?.boolValue == true
            || accountState?["state"]?.stringValue == "failed" || accountState?["last_error"]?.stringValue?.isEmpty == false
    }
    static func category(_ value: String) -> String { ["automation": "自动化", "interactive": "交互", "utility": "工具"][value] ?? value }
    static func symbol(_ feature: JSONValue) -> String {
        switch feature["category"]?.stringValue {
        case "interactive": "bubble.left.and.text.bubble.right"
        case "automation": "gearshape.2"
        default: "puzzlepiece.extension"
        }
    }
    static func source(_ feature: JSONValue) -> String {
        var parts: [String] = []
        if feature["is_builtin"]?.boolValue == true { parts.append("内置") }
        else if let label = feature["source_label"]?.stringValue, !label.isEmpty { parts.append(label) }
        else { parts.append(sourceType(feature["source_type"]?.stringValue)) }
        if feature["signature_ok"]?.boolValue == true { parts.append("签名有效") }
        if feature["signature_ok"]?.boolValue == false { parts.append("签名异常") }
        if feature["orphan"]?.boolValue == true { parts.append("来源缺失") }
        return parts.joined(separator: " · ")
    }
    static func sourceType(_ value: String?) -> String {
        switch value ?? "" {
        case "builtin": "TelePilot 内置"
        case "remote": "远程仓库"
        case "git": "Git 仓库"
        case "repo": "插件仓库"
        case "zip": "文件上传"
        case "local", "": "本地安装"
        default: value ?? "本地安装"
        }
    }
    static func signature(_ value: JSONValue?) -> (String, Color) {
        switch value?.boolValue {
        case true: ("校验通过", .green)
        case false: ("校验失败", .red)
        default: ("未签名", .secondary)
        }
    }
    static func runtime(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return ["ready": "就绪：所有入口可用", "partial": "部分可用：部分入口受限", "paused": "已暂停：依赖的平台能力未开启",
                "transitioning": "切换中"][value] ?? value
    }
    static func workerState(_ state: String?) -> String {
        switch state ?? "" {
        case "active", "running": "运行中"
        case "pending", "starting", "loading": "等待生效"
        case "stopped", "": "未运行"
        case "paused": "已暂停"
        case "failed", "error": "运行失败"
        case "disabled": "未启用"
        default: state ?? "未知"
        }
    }
    static func trustTier(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return ["official": "官方", "trusted": "受信任", "community": "社区", "untrusted": "未受信任", "unknown": "未知"][value] ?? value
    }
    static func supportsDirectPassthrough(_ feature: JSONValue) -> Bool {
        let value = feature["capabilities"]?["telegram_direct_passthrough"]
        return value?.boolValue == true || value?["enabled"]?.boolValue == true
    }
    static func date(_ value: JSONValue?) -> String? {
        guard let text = value?.stringValue, !text.isEmpty else { return nil }
        let precise = ISO8601DateFormatter()
        precise.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = precise.date(from: text) ?? ISO8601DateFormatter().date(from: text) else { return text }
        return date.formatted(date: .abbreviated, time: .shortened)
    }

    static let permissionLabels: [String: (String, String)] = [
        "send_message": ("发送消息", "以账号身份发送文字消息"),
        "send_file": ("发送文件", "以账号身份发送图片、文件等"),
        "edit_message": ("编辑消息", "修改账号已发出的消息"),
        "delete_message": ("删除消息", "删除消息"),
        "read_chat": ("读取消息", "读取会话中的消息内容"),
        "resolve_entity": ("解析会话", "根据用户名或链接查找用户、群组"),
        "click_bot_button": ("点击 Bot 按钮", "代替账号点击 Bot 消息上的按钮"),
        "external_http": ("访问外部网络", "向服务器以外的网址发送请求"),
        "payout": ("派奖", "执行转账或派奖动作"),
        "ai": ("调用 AI", "使用已配置的模型供应商"),
        "ai_text": ("调用 AI 文本", "使用已配置的模型生成文字"),
        "call_llm": ("调用 AI", "使用已配置的模型供应商"),
        "llm": ("调用 AI", "使用已配置的模型供应商")
    ]
    static let eventLabels: [String: String] = [
        "all_events": "全部事件", "all_messages": "全部消息", "callback_query": "按钮回调", "chosen_inline_result": "Inline 选择结果",
        "command": "指令", "external_payment_notice": "外部付款通知", "inline_query": "Inline 查询", "keyword": "关键词",
        "message": "普通消息", "message_edited": "消息被编辑", "payment_confirmed": "付款确认", "session_close": "会话关闭",
        "session_expired": "会话过期"
    ]
    static let capabilityLabels: [String: String] = [
        "answer_callback": "回应按钮点击", "answer_inline_query": "回答 Inline 查询", "ai": "调用 AI", "ai_text": "AI 文本",
        "llm": "调用 AI", "llm_text": "AI 文本", "inline_all": "处理全部 Inline 查询", "settlement": "结算动作",
        "telegram_direct_passthrough": "裸直通（直接接收原始消息）", "telegram_native_raw": "读取 Telegram 原生事件",
        "native_raw": "读取 Telegram 原生事件", "userbot_reply": "以账号身份回复"
    ]
    static func eventNames(_ feature: JSONValue) -> [String] {
        var result: [String] = []
        func add(_ name: String) { if !name.isEmpty, !result.contains(name) { result.append(name) } }
        for item in feature["event_subscriptions"]?.arrayValue ?? [] {
            for key in ["event_types", "events", "types"] {
                for event in item[key]?.arrayValue ?? [] { if let text = event.stringValue { add(eventLabels[text] ?? text) } }
            }
            for key in ["event_type", "type", "event"] {
                if let text = item[key]?.stringValue { add(eventLabels[text] ?? text) }
            }
        }
        return result
    }
    static func capabilities(_ feature: JSONValue) -> [String] {
        let object = feature["capabilities"]?.objectValue ?? [:]
        return object.keys.sorted().compactMap { key -> String? in
            guard let value = object[key] else { return nil }
            let enabled: Bool
            switch value {
            case .bool(let flag): enabled = flag
            case .null: enabled = false
            case .array(let items): enabled = !items.isEmpty
            case .object(let fields): enabled = key == "telegram_direct_passthrough" ? fields["enabled"]?.boolValue == true : !fields.isEmpty
            default: enabled = true
            }
            return enabled ? (capabilityLabels[key] ?? key) : nil
        }
    }
    static func riskWarnings(_ feature: JSONValue) -> [String] {
        var text = ""
        for key in ["capabilities", "event_subscriptions", "lint_warnings"] {
            if let data = try? feature[key]?.data() { text += String(decoding: data, as: UTF8.self).lowercased() }
        }
        var warnings: [String] = []
        if supportsDirectPassthrough(feature) { warnings.append("插件声明了裸直通：会先于正常处理流程收到原始消息，仅在信任插件作者时开启。") }
        if text.contains("native_raw") { warnings.append("插件可读取 Telegram 原生事件摘要，仅安装可信来源。") }
        if text.contains("inline_all") { warnings.append("插件可能处理全部 Inline 查询。") }
        return warnings
    }
    static func color(_ tone: PluginStatusTone) -> Color {
        switch tone {
        case .running: .green
        case .paused, .unknown: .secondary
        case .pending: .orange
        case .failed: .red
        }
    }
}

private struct PluginIcon: View {
    let feature: JSONValue
    var size: CGFloat = 28
    var body: some View {
        Image(systemName: PluginPresentation.symbol(feature))
            .font(.system(size: size * 0.5, weight: .medium))
            .foregroundStyle(Color.accentColor)
            .frame(width: size, height: size)
            .background(Color.accentColor.opacity(0.12), in: .rect(cornerRadius: size * 0.26))
            .accessibilityHidden(true)
    }
}

/// 状态徽标：同时使用图标、文字和颜色，不只靠颜色区分。
private struct PluginStatusBadge: View {
    let title: String
    let symbol: String
    let tone: PluginStatusTone
    var body: some View {
        let color = PluginPresentation.color(tone)
        Label(title, systemImage: symbol)
            .labelStyle(.titleAndIcon)
            .font(.caption2.weight(.semibold))
            .lineLimit(1)
            .foregroundStyle(color)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(color.opacity(0.12), in: .capsule)
    }
}

private struct PluginInfoRow: View {
    let title: String
    let value: String
    let symbol: String
    var monospaced = false
    var tint: Color? = nil
    var body: some View {
        LabeledContent {
            Text(value)
                .font(monospaced ? .callout.monospaced() : .callout)
                .foregroundStyle(tint ?? Color.secondary)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        } label: { Label(title, systemImage: symbol) }
    }
}

private struct PluginActionRow: View {
    let action: ManagementAction
    let systemImage: String
    var body: some View {
        NavigationLink { ManagementFormView(action: action) } label: {
            Label {
                Text(action.title).foregroundStyle(action.destructive ? Color.red : Color.primary)
            } icon: {
                Image(systemName: systemImage).foregroundStyle(action.destructive ? Color.red : Color.accentColor)
            }
        }
    }
}

private struct PluginCard: View {
    let feature: JSONValue
    let accountState: JSONValue?
    let accountScoped: Bool
    let favorite: Bool
    private var key: String { feature["key"]?.stringValue ?? "" }
    var body: some View {
        let status = PluginPresentation.status(feature: feature, accountState: accountState, accountScoped: accountScoped)
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                PluginIcon(feature: feature, size: 28)
                Text(ManagementText.title(feature))
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if favorite { Image(systemName: "star.fill").font(.caption2).foregroundStyle(.orange).accessibilityLabel("已收藏") }
            }
            Text(feature["usage"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } ?? key)
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(2, reservesSpace: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: 0)
            HStack(spacing: 6) {
                PluginStatusBadge(title: status.title, symbol: status.symbol, tone: status.tone)
                Spacer(minLength: 0)
                if feature["update_available"]?.boolValue == true {
                    Image(systemName: "arrow.up.circle.fill").font(.caption).foregroundStyle(.orange).accessibilityLabel("有可用更新")
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color(.secondarySystemGroupedBackground), in: .rect(cornerRadius: 12))
        .contentShape(.rect(cornerRadius: 12))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("plugin.card." + key)
    }
}

// MARK: - 插件详情

enum PluginDetailSection: String, CaseIterable {
    case overview, configuration, accounts, maintenance
}

private struct PluginAccountUsage: Identifiable {
    let id: Int
    let name: String
    let enabled: Bool?
    let state: String?
    let error: String?
    var status: (title: String, symbol: String, tone: PluginStatusTone) {
        guard let enabled else { return ("查看状态", "questionmark.circle", .unknown) }
        return PluginPresentation.accountStatus(enabled: enabled, state: state, error: error)
    }
}

struct ManagementPluginDetailView: View {
    let feature: JSONValue
    var accountID: Int?
    var accountState: JSONValue?
    var initialSection: PluginDetailSection
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    // 选中的分段只在 init 时确定一次：从子页面返回时不能被 .task 重置（修复右滑返回跳回“概况”）。
    @State private var section: PluginDetailSection
    @State private var currentAccountState: JSONValue?
    @State private var installRecord: JSONValue?
    @State private var installLoaded = false
    @State private var matrixAccounts: [JSONValue] = []
    @State private var installError: String?
    @State private var toggling = false
    @State private var toggleUncertain = false
    @State private var confirmToggle = false
    @State private var actionMessage: String?
    @State private var actionError: String?
    @State private var globalBusy = false
    @State private var globalUncertain = false
    @State private var confirmGlobal = false
    @State private var globalMessage: String?
    @State private var globalError: String?
    private var key: String { feature["key"]?.stringValue ?? "" }
    private var segment: String { ManagementText.pathSegment(key) }
    private var isBuiltin: Bool { feature["is_builtin"]?.boolValue == true }

    init(feature: JSONValue, accountID: Int? = nil, accountState: JSONValue? = nil, initialSection: PluginDetailSection = .overview) {
        self.feature = feature
        self.accountID = accountID
        self.accountState = accountState
        self.initialSection = initialSection
        let start: PluginDetailSection
        if accountID == nil { start = initialSection }
        else { start = initialSection == .configuration ? .configuration : .overview }
        _section = State(initialValue: start)
        _currentAccountState = State(initialValue: accountState)
    }

    private var sections: [PluginDetailSection] { accountID == nil ? PluginDetailSection.allCases : [.overview, .configuration] }
    private func title(_ item: PluginDetailSection) -> String {
        switch item {
        case .overview: "概况"
        case .configuration: accountID == nil ? "全局配置" : "账号配置"
        case .accounts: "账号使用"
        case .maintenance: "安装维护"
        }
    }
    private var globalEnabled: Bool? {
        installRecord?["global_enabled"]?.boolValue ?? feature["global_enabled"]?.boolValue
    }

    var body: some View {
        List {
            Section {
                PluginDetailHeader(feature: feature, accountState: currentAccountState, accountScoped: accountID != nil, globalEnabled: globalEnabled)
            }
            if accountID != nil { accountSwitchSection }
            Picker("插件详情", selection: $section) {
                ForEach(sections, id: \.self) { item in Text(title(item)).tag(item) }
            }
            .pickerStyle(.segmented).listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
            switch section {
            case .overview: overview
            case .configuration: configuration
            case .accounts: accounts
            case .maintenance: maintenance
            }
        }
        .navigationTitle(ManagementText.title(feature)).navigationBarTitleDisplayMode(.inline)
        .toolbar { Button(preferences.isFavoritePlugin(key) ? "取消收藏" : "收藏", systemImage: preferences.isFavoritePlugin(key) ? "star.fill" : "star") { preferences.togglePlugin(key) } }
        .task { if accountID == nil { await loadInstallRecord() } }
        .refreshable { if accountID == nil { await loadInstallRecord() } else { await reloadAccountState() } }
        .confirmationDialog(accountEnabled ? "在此账号停用该插件？" : "在此账号启用该插件？", isPresented: $confirmToggle, titleVisibility: .visible) {
            Button(accountEnabled ? "确认停用" : "确认启用", role: accountEnabled ? .destructive : nil) { Task { await toggleAccount() } }
            Button("取消", role: .cancel) {}
        } message: {
            Text(accountEnabled ? "此账号将不再运行该插件，配置和规则会保留。其他账号与服务器安装不受影响。" : "此账号将按自己的配置和规则运行该插件。其他账号不受影响。")
        }
        .confirmationDialog(globalEnabled == true ? "全局停用此插件？" : "全局启用此插件？", isPresented: $confirmGlobal, titleVisibility: .visible) {
            Button(globalEnabled == true ? "确认全局停用" : "确认全局启用", role: globalEnabled == true ? .destructive : nil) { Task { await toggleGlobal() } }
            Button("取消", role: .cancel) {}
        } message: {
            Text(globalEnabled == true ? "停用后所有账号都不会运行此插件；各账号的开关、配置和规则会保留，重新启用即可恢复。" : "启用后，已在账号中开启此插件的账号会重新开始运行它。")
        }
    }

    // MARK: 账号开关（仅账号作用域）

    private var accountEnabled: Bool { currentAccountState?["enabled"]?.boolValue == true }
    private var accountSwitchSection: some View {
        Section {
            LabeledContent {
                Text(accountEnabled ? "已启用" : "未启用").foregroundStyle(accountEnabled ? Color.green : Color.secondary)
            } label: { Label("在此账号中", systemImage: accountEnabled ? "power.circle.fill" : "power.circle") }
            Button(role: accountEnabled ? .destructive : nil) { confirmToggle = true } label: {
                Label(accountEnabled ? "在此账号停用该插件" : "在此账号启用该插件", systemImage: accountEnabled ? "pause.circle" : "play.circle")
                    .frame(minHeight: 44)
            }
            .disabled(toggling || toggleUncertain)
            .accessibilityIdentifier("plugin.account.toggle")
            if toggling { ProgressView("正在提交并刷新状态…") }
            if let actionMessage { Label(actionMessage, systemImage: "checkmark.circle").foregroundStyle(.green) }
            if let actionError { NoticeView(title: toggleUncertain ? "结果待确认" : "操作未完成", message: actionError) }
        } header: { Text("此账号的插件开关") } footer: {
            Text(toggleUncertain
                 ? "网络中断，无法确认是否已生效。请下拉刷新，确认实际状态后再操作。"
                 : accountEnabled
                    ? "停用后此账号不再运行该插件，配置和规则都会保留，随时可以重新启用。不影响其他账号，也不会卸载插件。"
                    : "启用后此账号会按自己的配置和规则运行该插件。不影响其他账号。")
        }
    }

    // MARK: 概况

    @ViewBuilder private var overview: some View {
        Section("插件信息") {
            if let usage = feature["usage"]?.stringValue, !usage.isEmpty { Text(usage).font(.subheadline) }
            PluginInfoRow(title: "版本", value: feature["version"]?.stringValue.map { "v" + $0 } ?? "未标注", symbol: "number")
            PluginInfoRow(title: "分类", value: PluginPresentation.category(feature["category"]?.stringValue ?? "utility"), symbol: "square.grid.2x2")
            PluginInfoRow(title: "来源", value: PluginPresentation.source(feature), symbol: "shippingbox")
        }
        if let accountState = currentAccountState {
            Section {
                PluginInfoRow(title: "运行状态", value: PluginPresentation.workerState(accountState["state"]?.stringValue), symbol: "gearshape.2")
                if let error = accountState["last_error"]?.stringValue, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red).textSelection(.enabled)
                }
            } header: { Text("账号运行详情") } footer: {
                Text("上面的开关是你的设置；“运行状态”是 Worker 实际加载的结果，刚切换时两者可能短暂不一致。")
            }
        } else if accountID == nil {
            Section {
                PluginInfoRow(title: "服务器安装", value: globalEnabled.map { $0 ? "已启用" : "已停用" } ?? (installLoaded ? (isBuiltin ? "内置，随服务器提供" : "无安装记录") : "读取中…"),
                              symbol: "server.rack", tint: globalEnabled == false ? .orange : nil)
                if let runtime = PluginPresentation.runtime(feature["runtime_availability"]?.stringValue) {
                    PluginInfoRow(title: "运行可用性", value: runtime, symbol: "bolt.horizontal.circle")
                }
                if !usageRows.isEmpty, usageRows.contains(where: { $0.enabled != nil }) {
                    PluginInfoRow(title: "使用账号", value: "\(usageRows.filter { $0.enabled == true }.count) / \(usageRows.count) 个已启用", symbol: "person.2")
                }
            } header: { Text("运行概况") }
        }
        Section {
            NavigationLink { PluginTechnicalInfoView(feature: feature, accountState: currentAccountState, installRecord: installRecord) } label: {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("完整技术信息")
                        Text("版本来源、签名、权限、订阅事件与诊断").font(.caption).foregroundStyle(.secondary)
                    }
                } icon: { Image(systemName: "info.circle") }
            }
            .accessibilityIdentifier("plugin.detail.technical")
        } footer: { Text("排查问题或判断插件是否可信时查看，日常使用无需关注。") }
        if accountID != nil {
            Section {
                NavigationLink { ManagementPluginDetailView(feature: feature, initialSection: .maintenance) } label: {
                    Label("服务器安装与维护", systemImage: "server.rack")
                }
                .accessibilityIdentifier("plugin.detail.serverMaintenance")
            } header: { Text("服务器安装") } footer: { Text("全局总开关、重新加载、版本记录和卸载会影响所有账号，在这里单独管理。") }
        }
    }

    // MARK: 配置

    @ViewBuilder private var configuration: some View {
        let global = accountID == nil
        let fields = PluginConfigSchema.fields(feature: feature, global: global)
        let editable = !fields.isEmpty || (!global && !PluginConfigSchema.hasSchema(feature))
        if editable {
            Section {
                NavigationLink { ManagementPluginConfigView(feature: feature, accountID: accountID) } label: {
                    Label(global ? "修改全局配置" : "修改账号配置", systemImage: "slider.horizontal.3")
                }
                .accessibilityIdentifier("plugin.config.edit")
            } footer: {
                Text(global ? "全局配置由所有账号共享，例如统一的上限或通知开关。每个账号自己的设置在账号中修改。"
                            : "修改只保存到当前账号。没有单独设置的项沿用全局配置或插件默认值。")
            }
            PluginConfigSummarySections(feature: feature, accountID: accountID)
        } else {
            Section {
                Label(global ? "此插件没有全局配置项" : "此插件没有可修改的账号配置项", systemImage: "slider.horizontal.3")
                    .foregroundStyle(.secondary)
            } footer: {
                Text(global ? "它的设置都按账号分别保存。请到「账号使用」选择账号后修改。" : "可以在下方管理此账号的规则与自动化。")
            }
        }
        if let guide = feature["config_schema"]?["x-usage-guide"]?.stringValue, !guide.isEmpty {
            Section { Label { Text(guide).font(.subheadline) } icon: { Image(systemName: "lightbulb") } } header: { Text("使用说明") }
        }
        if let accountID {
            let base = "/api/accounts/\(accountID)/features/\(segment)"
            let actions = feature["config_actions"]?.arrayValue ?? feature["config_schema"]?["x-config-actions"]?.arrayValue ?? []
            Section {
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "自动化规则", path: base + "/rules", kind: .rules, create: .init(title: "添加规则", path: base + "/rules", schema: ManagementRuleSchemas.request(feature: feature)), itemPath: base + "/rules/{id}", ruleSchema: ManagementRuleSchemas.request(feature: feature)))
                } label: { Label("自动化规则", systemImage: "text.badge.checkmark") }
                PluginActionRow(action: .init(title: "复制规则到其他账号", path: base + "/rules/copy", explanation: "把此账号的规则复制到选择的账号。"), systemImage: "doc.on.doc")
                if PluginPresentation.supportsDirectPassthrough(feature) {
                    PluginActionRow(action: .init(title: "直通开关与优先级", path: base + "/direct-passthrough", method: "PATCH", explanation: "裸直通让插件先于正常处理流程收到原始消息；优先级数字越小越先调用。"), systemImage: "arrow.triangle.branch")
                }
                if !actions.isEmpty {
                    NavigationLink { PluginConfigActionsView(feature: feature, accountID: accountID) } label: { Label("试运行与插件操作", systemImage: "play.rectangle") }
                }
                NavigationLink { ManagementCollectionView(resource: .init(title: "配置任务", path: base + "/config/action-jobs", kind: .jobs, idField: "job_id")) } label: {
                    Label("配置任务与进度", systemImage: "clock.arrow.circlepath")
                }
            } header: { Text("规则与自动化") } footer: { Text("规则决定插件在什么消息上触发、做什么，只对当前账号生效。") }
        }
    }

    // MARK: 账号使用（全局作用域）

    private var usageRows: [PluginAccountUsage] {
        var rows: [PluginAccountUsage] = []
        for item in installRecord?["accounts"]?.arrayValue ?? [] {
            guard let id = item["account_id"]?.intValue else { continue }
            let fallbackName = model.accounts.first { $0.id == id }.map(ManagementText.accountName) ?? "账号 \(id)"
            let errors = [item["last_error"]?.stringValue, item["last_load_error"]?.stringValue].compactMap { $0 }.filter { !$0.isEmpty }
            let enabled = item["enabled"]?.boolValue ?? false
            rows.append(PluginAccountUsage(id: id, name: item["account_name"]?.stringValue ?? fallbackName,
                                           enabled: enabled, state: item["state"]?.stringValue, error: enabled ? errors.first : nil))
        }
        // 内置插件不在安装台账里时，改用 /api/feature-matrix 的账号开关与运行状态。
        if rows.isEmpty {
            for item in matrixAccounts {
                guard let id = item["id"]?.intValue else { continue }
                let fallbackName = model.accounts.first { $0.id == id }.map(ManagementText.accountName) ?? "账号 \(id)"
                rows.append(PluginAccountUsage(id: id, name: item["name"]?.stringValue ?? fallbackName,
                                               enabled: item["feature_enabled"]?[key]?.boolValue ?? false,
                                               state: item["features"]?[key]?.stringValue, error: nil))
            }
        }
        let known = Set(rows.map(\.id))
        let overviewLoaded = installRecord != nil || !matrixAccounts.isEmpty
        for account in model.accounts where !known.contains(account.id) {
            rows.append(PluginAccountUsage(id: account.id, name: ManagementText.accountName(account),
                                           enabled: overviewLoaded ? false : nil, state: nil, error: nil))
        }
        return rows
    }

    @ViewBuilder private var accounts: some View {
        let rows = usageRows
        let known = rows.contains { $0.enabled != nil }
        Section {
            if known {
                HStack(spacing: 0) {
                    PluginStatView(title: "已启用", value: "\(rows.filter { $0.enabled == true }.count)/\(rows.count)", symbol: "power", tint: .primary)
                    PluginStatView(title: "运行中", value: "\(rows.filter { $0.status.tone == .running }.count)", symbol: "checkmark.circle", tint: .green)
                    PluginStatView(title: "异常", value: "\(rows.filter { $0.status.tone == .failed }.count)", symbol: "exclamationmark.triangle",
                                   tint: rows.contains { $0.status.tone == .failed } ? .red : .secondary)
                }
                .padding(.vertical, 4)
            } else if let installError {
                NoticeView(title: "各账号状态读取失败", message: installError)
            } else {
                Label("点开账号查看它的启用与运行状态。", systemImage: "info.circle").foregroundStyle(.secondary)
            }
        } header: { Text("使用概况") } footer: {
            Text("插件在每个账号上单独开关、单独配置。点下方账号可以启用或停用、修改配置、管理规则。")
        }
        Section("各账号") {
            if rows.isEmpty { Label("还没有账号", systemImage: "person.crop.circle.badge.questionmark").foregroundStyle(.secondary) }
            ForEach(rows) { row in
                NavigationLink { AccountPluginDetailView(accountID: row.id, feature: feature) } label: { PluginAccountUsageRow(row: row) }
            }
        }
        if isBuiltin {
            Section {
                PluginActionRow(action: .init(title: "批量启用到多个账号", path: "/api/plugins/install",
                                              explanation: "选择账号，一次性为它们启用此插件。之后仍可在各账号中单独停用。",
                                              seed: .object(["plugin_key": .string(key)])), systemImage: "person.badge.plus")
            } footer: { Text("只会打开所选账号的开关，不会修改它们已有的配置和规则。") }
        }
    }

    // MARK: 安装维护（全局作用域）

    @ViewBuilder private var maintenance: some View {
        Section {
            Label {
                Text("管理插件在服务器上的安装包本身：总开关、重新加载、版本记录和卸载。各账号的开关、配置和规则请到「账号使用」中管理。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } icon: { Image(systemName: "server.rack") }
        }
        Section {
            if let enabled = globalEnabled {
                LabeledContent {
                    Text(enabled ? "已启用" : "已停用").foregroundStyle(enabled ? Color.green : Color.orange)
                } label: { Label("服务器总开关", systemImage: enabled ? "power.circle.fill" : "power.circle") }
                Button(role: enabled ? .destructive : nil) { confirmGlobal = true } label: {
                    Label(enabled ? "全局停用插件" : "全局启用插件", systemImage: enabled ? "pause.circle" : "play.circle").frame(minHeight: 44)
                }
                .disabled(globalBusy || globalUncertain)
                .accessibilityIdentifier("plugin.global.toggle")
            } else if installLoaded {
                Label(isBuiltin ? "内置插件随服务器提供，没有单独的总开关。" : "没有找到此插件的安装记录。", systemImage: "info.circle")
                    .foregroundStyle(.secondary)
            } else if let installError {
                NoticeView(title: "安装状态读取失败", message: installError)
                Button("重新读取", systemImage: "arrow.clockwise") { Task { await loadInstallRecord() } }
            } else {
                ProgressView("正在读取安装状态…")
            }
            if globalBusy { ProgressView("正在提交并刷新状态…") }
            if let globalMessage { Label(globalMessage, systemImage: "checkmark.circle").foregroundStyle(.green) }
            if let globalError { NoticeView(title: globalUncertain ? "结果待确认" : "操作未完成", message: globalError) }
        } header: { Text("全局总开关") } footer: {
            Text(globalUncertain
                 ? "网络中断，无法确认是否已生效。请下拉刷新，确认实际状态后再操作。"
                 : "总开关关闭后，所有账号都不会运行此插件；各账号的开关、配置和规则都会保留，重新开启即可恢复。")
        }
        if isBuiltin {
            Section {
                PluginActionRow(action: .init(title: "重新加载到所选账号", path: "/api/plugins/\(segment)/reload",
                                              explanation: "选择要重新加载的账号。对应 Worker 会重新载入此插件，运行中的任务可能短暂中断。"), systemImage: "arrow.clockwise.circle")
            } header: { Text("重新加载") } footer: { Text("插件行为异常或刚修改过文件时使用，不会改变任何配置。") }
        }
        Section {
            NavigationLink { ManagementCollectionView(resource: .init(title: "安装历史", path: "/api/plugins/install/\(segment)/history")) } label: {
                Label("安装历史", systemImage: "clock")
            }
            NavigationLink { ManagementDocumentView(title: "更新日志", path: "/api/plugins/install/\(segment)/changelog") } label: {
                Label("更新日志", systemImage: "doc.text")
            }
        } header: { Text("版本记录") } footer: { Text("安装历史记录每次安装、更新和总开关操作；更新日志由插件作者提供。") }
        if !isBuiltin {
            Section {
                PluginActionRow(action: .init(title: "卸载插件", path: "/api/plugins/install/\(segment)", method: "DELETE", destructive: true,
                                              explanation: "将移除服务器上的插件安装记录与文件。请先检查依赖此插件的账号和任务。"), systemImage: "trash")
            } header: { Text("卸载") } footer: { Text("卸载后所有账号都无法再使用此插件，需要重新安装才能恢复。") }
        }
    }

    // MARK: 数据与操作

    private func loadInstallRecord() async {
        do {
            let items = try await model.request(path: "/api/plugins/installed-overview").arrayValue ?? []
            installRecord = items.first { $0["key"]?.stringValue == key }
            if installRecord?["accounts"]?.arrayValue?.isEmpty != false,
               let matrix = try? await model.request(path: "/api/feature-matrix") {
                matrixAccounts = matrix["accounts"]?.arrayValue ?? []
            }
            installError = nil
            installLoaded = true
            // 重新读取到服务器真实状态后，才允许再次操作（写操作从不自动重试）。
            globalUncertain = false
        } catch { installError = error.localizedDescription }
    }

    private func reloadAccountState() async {
        guard let accountID else { return }
        if let values = try? await model.request(path: "/api/accounts/\(accountID)/features").arrayValue {
            currentAccountState = values.first { $0["feature_key"]?.stringValue == key }
            toggleUncertain = false
        }
    }

    private func toggleAccount() async {
        guard let accountID, !toggling, !toggleUncertain else { return }
        toggling = true
        actionMessage = nil
        actionError = nil
        defer { toggling = false }
        let target = !accountEnabled
        do {
            _ = try await model.request(path: "/api/accounts/\(accountID)/features/\(segment)", method: "PATCH", body: ["enabled": .bool(target)])
        } catch {
            actionError = error.localizedDescription
            toggleUncertain = (error as? APIError)?.isOutcomeUncertain == true
            return
        }
        do {
            let values = try await model.request(path: "/api/accounts/\(accountID)/features").arrayValue ?? []
            currentAccountState = values.first { $0["feature_key"]?.stringValue == key }
            if !target { actionMessage = "已在此账号停用。" }
            else { actionMessage = currentAccountState?["state"]?.stringValue == "active" ? "已在此账号启用，Worker 正在运行。" : "已提交启用，请留意运行状态。" }
        } catch {
            actionMessage = "已提交，但重新读取状态失败。请下拉刷新确认。"
        }
    }

    private func toggleGlobal() async {
        guard let enabled = globalEnabled, !globalBusy, !globalUncertain else { return }
        globalBusy = true
        globalMessage = nil
        globalError = nil
        defer { globalBusy = false }
        do {
            _ = try await model.request(path: "/api/plugins/install/\(segment)/\(enabled ? "disable" : "enable")", method: "POST")
        } catch {
            globalError = error.localizedDescription
            globalUncertain = (error as? APIError)?.isOutcomeUncertain == true
            return
        }
        await loadInstallRecord()
        globalMessage = enabled ? "已全局停用，所有账号都不会再运行此插件。" : "已全局启用。"
    }
}

private struct PluginStatView: View {
    let title: String
    let value: String
    let symbol: String
    let tint: Color
    var body: some View {
        VStack(spacing: 4) {
            Text(value).font(.title3.weight(.semibold)).monospacedDigit().foregroundStyle(tint)
            Label(title, systemImage: symbol).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}

private struct PluginAccountUsageRow: View {
    let row: PluginAccountUsage
    var body: some View {
        let status = row.status
        HStack(spacing: 12) {
            Image(systemName: "person.crop.circle").font(.title3).foregroundStyle(Color.accentColor).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(row.name).lineLimit(1)
                if let error = row.error { Text(error).font(.caption).foregroundStyle(.red).lineLimit(2) }
            }
            Spacer(minLength: 8)
            PluginStatusBadge(title: status.title, symbol: status.symbol, tone: status.tone)
        }
    }
}

private struct AccountPluginDetailView: View {
    let accountID: Int
    let feature: JSONValue
    @Environment(AppModel.self) private var model
    @State private var state: JSONValue?
    @State private var loaded = false
    @State private var error: String?
    var body: some View {
        Group {
            if loaded {
                ManagementPluginDetailView(feature: feature, accountID: accountID, accountState: state)
            } else {
                List {
                    if let error { NoticeView(title: "账号插件状态读取失败", message: error) }
                    else { ProgressView("正在读取账号插件状态…") }
                }
            }
        }
        .navigationTitle(ManagementText.title(feature)).navigationBarTitleDisplayMode(.inline)
        .task {
            guard !loaded else { return }
            let key = feature["key"]?.stringValue ?? ""
            do {
                state = try await model.request(path: "/api/accounts/\(accountID)/features").arrayValue?.first { $0["feature_key"]?.stringValue == key }
                loaded = true
            } catch { self.error = error.localizedDescription }
        }
    }
}

private struct PluginDetailHeader: View {
    let feature: JSONValue
    let accountState: JSONValue?
    let accountScoped: Bool
    let globalEnabled: Bool?
    var body: some View {
        let status = PluginPresentation.status(feature: feature, accountState: accountState, accountScoped: accountScoped)
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                PluginIcon(feature: feature, size: 40)
                VStack(alignment: .leading, spacing: 3) {
                    Text(ManagementText.title(feature)).font(.headline).lineLimit(2)
                    Text(PluginPresentation.source(feature)).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            HStack(spacing: 6) {
                PluginStatusBadge(title: accountScoped ? "此账号：" + status.title : status.title, symbol: status.symbol, tone: status.tone)
                if !accountScoped, let globalEnabled {
                    PluginStatusBadge(title: globalEnabled ? "服务器已启用" : "服务器已停用", symbol: "server.rack", tone: globalEnabled ? .running : .pending)
                }
                if feature["update_available"]?.boolValue == true {
                    PluginStatusBadge(title: "可更新", symbol: "arrow.up.circle", tone: .pending)
                }
            }
            if let error = accountState?["last_error"]?.stringValue, !error.isEmpty {
                Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red).lineLimit(3)
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - 完整技术信息（全局与账号详情共用）

private struct PluginTechnicalInfoView: View {
    let feature: JSONValue
    let accountState: JSONValue?
    let installRecord: JSONValue?

    private var permissions: [String] { (feature["permissions"]?.arrayValue ?? []).compactMap(\.stringValue) }
    private var platformCapabilities: [String] { (feature["requires_platform_capabilities"]?.arrayValue ?? []).compactMap(\.stringValue) }
    private var lintWarnings: [String] { (feature["lint_warnings"]?.arrayValue ?? installRecord?["lint_warnings"]?.arrayValue ?? []).compactMap(\.stringValue) }
    private var configProperties: [String: JSONValue] { feature["config_schema"]?["properties"]?.objectValue ?? [:] }

    var body: some View {
        List {
            Section {
                PluginInfoRow(title: "插件标识", value: feature["key"]?.stringValue ?? "未知", symbol: "tag", monospaced: true)
                PluginInfoRow(title: "显示名称", value: ManagementText.title(feature), symbol: "textformat")
                PluginInfoRow(title: "当前版本", value: feature["version"]?.stringValue.map { "v" + $0 } ?? "未标注", symbol: "number")
                PluginInfoRow(title: "分类", value: PluginPresentation.category(feature["category"]?.stringValue ?? "utility"), symbol: "square.grid.2x2")
                if let profile = feature["interaction_profile"]?.stringValue, !profile.isEmpty {
                    PluginInfoRow(title: "交互方式", value: profile, symbol: "bubble.left.and.bubble.right", monospaced: true)
                }
                if feature["experimental"]?.boolValue == true {
                    PluginInfoRow(title: "实验功能", value: "是，行为可能变化", symbol: "testtube.2", tint: .orange)
                }
            } header: { Text("基本信息") } footer: {
                Text("插件标识是插件在服务器上的唯一名称，规则、日志和接口都用它指代这个插件。")
            }

            Section {
                PluginInfoRow(title: "来源类型", value: feature["is_builtin"]?.boolValue == true ? "TelePilot 内置" : PluginPresentation.sourceType(feature["source_type"]?.stringValue), symbol: "shippingbox")
                if let label = feature["source_label"]?.stringValue, !label.isEmpty { PluginInfoRow(title: "来源名称", value: label, symbol: "books.vertical") }
                if let url = installRecord?["source_url"]?.stringValue, !url.isEmpty { PluginInfoRow(title: "来源地址", value: url, symbol: "link", monospaced: true) }
                let signature = PluginPresentation.signature(feature["signature_ok"])
                PluginInfoRow(title: "签名校验", value: signature.0, symbol: "checkmark.seal", tint: signature.1)
                if let tier = PluginPresentation.trustTier(installRecord?["trust_tier"]?.stringValue) { PluginInfoRow(title: "信任等级", value: tier, symbol: "lock.shield") }
                if feature["orphan"]?.boolValue == true { PluginInfoRow(title: "来源状态", value: "来源已缺失", symbol: "exclamationmark.triangle", tint: .orange) }
            } header: { Text("来源与可信度") } footer: {
                Text("签名校验通过表示插件包没有被改动过。“来源已缺失”表示安装时的仓库或目录已不存在：插件仍可运行，但无法再检查更新。")
            }

            Section {
                PluginInfoRow(title: "有可用更新", value: feature["update_available"]?.boolValue == true ? "是" : "否", symbol: "arrow.up.circle",
                              tint: feature["update_available"]?.boolValue == true ? .orange : nil)
                if let latest = feature["latest_version"]?.stringValue { PluginInfoRow(title: "最新版本", value: "v" + latest, symbol: "sparkles") }
                if let checked = PluginPresentation.date(feature["last_update_check_at"]) { PluginInfoRow(title: "上次检查", value: checked, symbol: "calendar") }
                if let error = feature["last_update_check_error"]?.stringValue, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red).textSelection(.enabled)
                }
            } header: { Text("版本更新") } footer: { Text("只检查不会自动更新；更新需要在「插件管理」中手动执行。") }

            let channels = (feature["available_channels"]?.arrayValue ?? []).compactMap(\.stringValue)
            let blocked = feature["blocked_entries"]?.arrayValue ?? []
            if PluginPresentation.runtime(feature["runtime_availability"]?.stringValue) != nil || !channels.isEmpty || !blocked.isEmpty {
                Section {
                    if let runtime = PluginPresentation.runtime(feature["runtime_availability"]?.stringValue) {
                        PluginInfoRow(title: "运行可用性", value: runtime, symbol: "bolt.horizontal.circle")
                    }
                    if !channels.isEmpty { PluginInfoRow(title: "可用渠道", value: channels.joined(separator: "、"), symbol: "antenna.radiowaves.left.and.right") }
                    if !blocked.isEmpty { PluginInfoRow(title: "受限入口", value: "\(blocked.count) 个", symbol: "nosign", tint: .orange) }
                    if let reason = feature["blocked_reason_code"]?.stringValue, !reason.isEmpty {
                        PluginInfoRow(title: "受限原因代码", value: reason, symbol: "questionmark.circle", monospaced: true)
                    }
                } header: { Text("运行可用性") } footer: {
                    Text("插件依赖的平台能力（例如 Bot、Inline）没有开启时，对应入口会暂停，其余功能照常运行。")
                }
            }

            Section {
                if permissions.isEmpty { Label("未声明任何权限", systemImage: "lock.open").foregroundStyle(.secondary) }
                ForEach(permissions, id: \.self) { permission in
                    let info = PluginPresentation.permissionLabels[permission]
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(info?.0 ?? permission)
                            Text(info.map { $0.1 + " · " + permission } ?? "未收录的权限名称").font(.caption).foregroundStyle(.secondary)
                        }
                    } icon: { Image(systemName: "lock") }
                }
            } header: { Text("申请的权限") } footer: { Text("插件声明它需要以账号身份做的事情。请只安装你信任的插件。") }

            let events = PluginPresentation.eventNames(feature)
            Section {
                if events.isEmpty { Label("未声明订阅事件", systemImage: "bell.slash").foregroundStyle(.secondary) }
                ForEach(events, id: \.self) { event in Label(event, systemImage: "bell") }
            } header: { Text("订阅的事件") } footer: { Text("插件会收到这些类型的消息或事件，并据此触发规则或自动回复。") }

            let capabilities = PluginPresentation.capabilities(feature)
            if !capabilities.isEmpty || !platformCapabilities.isEmpty {
                Section {
                    ForEach(capabilities, id: \.self) { item in Label(item, systemImage: "wrench.and.screwdriver") }
                    ForEach(platformCapabilities, id: \.self) { item in
                        Label("依赖平台能力：\(item)", systemImage: "puzzlepiece")
                    }
                } header: { Text("能力声明") } footer: { Text("插件额外具备的处理能力，以及它需要服务器开启的平台功能。") }
            }

            let warnings = PluginPresentation.riskWarnings(feature)
            if !warnings.isEmpty || !lintWarnings.isEmpty {
                Section {
                    ForEach(warnings, id: \.self) { item in Label(item, systemImage: "exclamationmark.shield").foregroundStyle(.red) }
                    ForEach(lintWarnings, id: \.self) { item in Label(item, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).textSelection(.enabled) }
                } header: { Text("安全与检查提示") } footer: { Text("红色为高风险能力提醒；橙色是安装时自动检查发现的问题，不一定影响使用。") }
            }

            Section {
                let globalCount = configProperties.values.filter { $0["level"]?.stringValue == "global" }.count
                PluginInfoRow(title: "配置项", value: configProperties.isEmpty ? "未声明" : "\(configProperties.count) 项", symbol: "slider.horizontal.3")
                if !configProperties.isEmpty {
                    PluginInfoRow(title: "其中全局配置", value: "\(globalCount) 项", symbol: "globe")
                    PluginInfoRow(title: "其中账号配置", value: "\(configProperties.count - globalCount) 项", symbol: "person.crop.circle")
                }
                let actionCount = (feature["config_actions"]?.arrayValue ?? feature["config_schema"]?["x-config-actions"]?.arrayValue ?? []).count
                if actionCount > 0 { PluginInfoRow(title: "试运行与操作", value: "\(actionCount) 个", symbol: "play.rectangle") }
            } header: { Text("配置结构") } footer: { Text("全局配置所有账号共享；账号配置每个账号单独保存。") }

            if let accountState {
                Section {
                    PluginInfoRow(title: "账号开关", value: accountState["enabled"]?.boolValue == true ? "已启用" : "未启用", symbol: "power")
                    PluginInfoRow(title: "运行状态", value: PluginPresentation.workerState(accountState["state"]?.stringValue), symbol: "gearshape.2")
                    PluginInfoRow(title: "已单独保存的配置", value: "\(accountState["config"]?.objectValue?.count ?? 0) 项", symbol: "tray.full")
                    if let error = accountState["last_error"]?.stringValue, !error.isEmpty {
                        Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red).textSelection(.enabled)
                    }
                } header: { Text("此账号的运行记录") }
            }

            Section {
                NavigationLink { List { ManagementValueView(value: feature) }.navigationTitle("插件原始字段").navigationBarTitleDisplayMode(.inline) } label: {
                    Label("插件原始字段", systemImage: "curlybraces")
                }
                if let accountState {
                    NavigationLink { List { ManagementValueView(value: accountState) }.navigationTitle("账号插件原始字段").navigationBarTitleDisplayMode(.inline) } label: {
                        Label("账号插件原始字段", systemImage: "curlybraces.square")
                    }
                }
            } header: { Text("开发者") } footer: { Text("服务器返回的完整字段，供排查问题使用；字段名保持原样。") }
        }
        .navigationTitle("完整技术信息").navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 插件配置（读取、展示、编辑）

private struct PluginConfigField: Identifiable {
    let key: String
    let spec: JSONValue
    let required: Bool
    var id: String { key }
    var label: String { ManagementText.fieldLabel(key, schema: spec) }
    var secret: Bool { ManagementText.secret(key) || spec["format"]?.stringValue == "password" || spec["writeOnly"]?.boolValue == true }
    var readOnly: Bool { spec["readOnly"]?.boolValue == true }
    var hidden: Bool { spec["x-ui-hidden"]?.boolValue == true }
    var description: String? { spec["description"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
    var symbol: String {
        if secret { return "key" }
        let schema = ManagementSchema.normalized(spec)
        if schema["enum"] != nil { return "list.bullet.circle" }
        switch schema["type"]?.stringValue {
        case "boolean": return "switch.2"
        case "integer", "number": return "number"
        case "array": return "list.bullet"
        case "object": return "curlybraces"
        default: return "textformat"
        }
    }
    /// 开关、选项与分组控件自带标题；文本与数字输入框需要在分组标题里显示字段名。
    var titledInline: Bool {
        let schema = ManagementSchema.normalized(spec)
        return schema["enum"] != nil || ["boolean", "object", "array"].contains(schema["type"]?.stringValue ?? "")
    }
}

private enum PluginConfigSchema {
    static func isGuide(_ key: String) -> Bool {
        ["usage_preview", "usage_guide", "usage_instructions", "ai_usage_guide", "template_placeholders", "template_preview"].contains(key)
            || key.lowercased().hasSuffix("_preview")
    }
    static func hasSchema(_ feature: JSONValue) -> Bool { feature["config_schema"]?["properties"]?.objectValue != nil }
    /// 与 Web 一致：level == "global" 的字段属于全局配置，其余属于账号配置；说明/预览类字段不参与保存。
    static func fields(feature: JSONValue, global: Bool) -> [PluginConfigField] {
        guard let schema = feature["config_schema"], let properties = schema["properties"]?.objectValue else { return [] }
        let required = Set(schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? [])
        return properties.compactMap { entry -> PluginConfigField? in
            guard !isGuide(entry.key) else { return nil }
            guard (entry.value["level"]?.stringValue == "global") == global else { return nil }
            return PluginConfigField(key: entry.key, spec: entry.value, required: required.contains(entry.key))
        }
        .sorted { lhs, rhs in
            if lhs.required != rhs.required { return lhs.required }
            return lhs.label.localizedStandardCompare(rhs.label) == .orderedAscending
        }
    }
}

private enum PluginConfigFormat {
    static func text(_ value: JSONValue?, secret: Bool) -> String {
        guard let value, !value.isNull else { return "未设置" }
        if secret { return (value.stringValue ?? "x").isEmpty ? "未设置" : "已设置（内容隐藏）" }
        if let flag = value.boolValue { return flag ? "开启" : "关闭" }
        if let string = value.stringValue { return string.isEmpty ? "未填写" : ManagementText.scalar(value) }
        if let items = value.arrayValue {
            if items.isEmpty { return "空列表" }
            let scalars = items.compactMap { $0.objectValue == nil && $0.arrayValue == nil ? ManagementText.scalar($0) : nil }
            if scalars.count == items.count {
                let head = scalars.prefix(3).joined(separator: "、")
                return items.count > 3 ? head + " 等 \(items.count) 项" : head
            }
            return "\(items.count) 项"
        }
        if let object = value.objectValue { return object.isEmpty ? "未设置" : "\(object.count) 项设置" }
        return ManagementText.scalar(value)
    }
    /// 比较两个配置值；数字按数值比较（输入框会把 30 写回为 30.0 之类），避免误报“已修改”。
    static func same(_ lhs: JSONValue?, _ rhs: JSONValue?) -> Bool {
        let left = lhs ?? .null, right = rhs ?? .null
        if let x = left.doubleValue, let y = right.doubleValue { return x == y }
        switch (left, right) {
        case (.array(let x), .array(let y)): return x.count == y.count && zip(x, y).allSatisfy { same($0, $1) }
        case (.object(let x), .object(let y)): return Set(x.keys) == Set(y.keys) && x.allSatisfy { same($0.value, y[$0.key]) }
        default: return left == right
        }
    }
}

/// 读取配置所需的全部数据：显示值、在当前层级单独保存的值、继承自全局的值。
private struct PluginConfigSnapshot: Sendable {
    var values: [String: JSONValue]
    var stored: [String: JSONValue]
    var inherited: [String: JSONValue]
}

@MainActor
private enum PluginConfigLoader {
    /// 全局：`GET /api/plugins/{key}/config` 返回 `{plugin_key, config, global_config}`，其中只有“已经保存过”的全局字段；
    /// 未保存的字段使用 schema 默认值（与 Web 的 buildScopedConfigValues 一致）。
    /// 账号：`GET /api/accounts/{id}/features/{key}/config` 返回合并后的生效配置（默认值 < 全局 < 账号），
    /// 账号自己保存的原始配置来自 `GET /api/accounts/{id}/features`。
    static func load(model: AppModel, feature: JSONValue, accountID: Int?) async throws -> PluginConfigSnapshot {
        let rawKey = feature["key"]?.stringValue ?? ""
        let key = ManagementText.pathSegment(rawKey)
        if let accountID {
            let effective = try await model.request(path: "/api/accounts/\(accountID)/features/\(key)/config")
            let list = try await model.request(path: "/api/accounts/\(accountID)/features")
            let stored = list.arrayValue?.first { $0["feature_key"]?.stringValue == rawKey }?["config"]?.objectValue ?? [:]
            var inherited: [String: JSONValue] = [:]
            if let response = try? await model.request(path: "/api/plugins/\(key)/config") { inherited = globalStored(response) }
            return PluginConfigSnapshot(values: effective.objectValue ?? [:], stored: stored, inherited: inherited)
        }
        let stored = globalStored(try await model.request(path: "/api/plugins/\(key)/config"))
        return PluginConfigSnapshot(values: stored, stored: stored, inherited: [:])
    }
    static func globalStored(_ response: JSONValue) -> [String: JSONValue] {
        response["global_config"]?.objectValue ?? response["config"]?.objectValue ?? [:]
    }
}

private struct PluginConfigValueRow: View {
    let field: PluginConfigField
    let value: JSONValue?
    let source: (String, String)?
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            LabeledContent {
                Text(PluginConfigFormat.text(value, secret: field.secret))
                    .foregroundStyle(.secondary).multilineTextAlignment(.trailing).lineLimit(3).textSelection(.enabled)
            } label: { Label(field.label, systemImage: field.symbol) }
            if let source {
                Label(source.0, systemImage: source.1).font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }
}

/// 配置分段里直接显示当前值（可读的字段名、单位与来源），不再需要先进入一层只读页。
private struct PluginConfigSummarySections: View {
    let feature: JSONValue
    let accountID: Int?
    @Environment(AppModel.self) private var model
    @State private var snapshot: PluginConfigSnapshot?
    @State private var error: String?
    private var global: Bool { accountID == nil }

    var body: some View {
        Group {
            if let error, snapshot == nil {
                Section { NoticeView(title: "当前配置读取失败", message: error) }
            } else if let snapshot {
                let fields = PluginConfigSchema.fields(feature: feature, global: global).filter { !$0.hidden }
                if !global && !PluginConfigSchema.hasSchema(feature) {
                    Section("此账号当前生效的配置") {
                        if snapshot.values.isEmpty { Label("尚未保存任何配置", systemImage: "tray").foregroundStyle(.secondary) }
                        ManagementValueView(value: .object(snapshot.values))
                    }
                } else {
                    Section {
                        ForEach(fields) { field in
                            PluginConfigValueRow(field: field, value: snapshot.values[field.key] ?? field.spec["default"], source: source(field, snapshot))
                        }
                    } header: { Text(global ? "当前全局配置" : "此账号当前生效的配置") } footer: {
                        Text(global ? "“插件默认值”表示还没有保存过，正在使用插件自带的默认设置。" : "每项下方注明了值的来源：此账号单独设置、沿用全局配置，或插件默认值。")
                    }
                    if !global {
                        let globalFields = PluginConfigSchema.fields(feature: feature, global: true).filter { !$0.hidden }
                        if !globalFields.isEmpty {
                            Section {
                                ForEach(globalFields) { field in
                                    PluginConfigValueRow(field: field, value: snapshot.values[field.key] ?? field.spec["default"], source: nil)
                                }
                            } header: { Text("全局共享项（只读）") } footer: {
                                Text("这些项所有账号共用，需要到插件的「全局配置」中修改。")
                            }
                        }
                    }
                }
            } else {
                Section { ProgressView("正在读取当前配置…") }
            }
        }
        .task { await load() }
    }

    private func source(_ field: PluginConfigField, _ snapshot: PluginConfigSnapshot) -> (String, String) {
        if global {
            if snapshot.stored[field.key] != nil { return ("已保存", "checkmark.circle") }
            return field.spec["default"] != nil ? ("插件默认值", "shippingbox") : ("未设置", "circle.dashed")
        }
        if snapshot.stored[field.key] != nil { return ("此账号单独设置", "person.crop.circle") }
        if snapshot.inherited[field.key] != nil { return ("沿用全局配置", "globe") }
        return field.spec["default"] != nil ? ("插件默认值", "shippingbox") : ("未设置", "circle.dashed")
    }

    private func load() async {
        do {
            snapshot = try await PluginConfigLoader.load(model: model, feature: feature, accountID: accountID)
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

private struct PluginConfigChange: Identifiable {
    let key: String
    let label: String
    let before: String
    let after: String
    var id: String { key }
}

/// 一步进入的配置编辑表单：显示当前值，修改后先对比差异再保存。
struct ManagementPluginConfigView: View {
    let feature: JSONValue
    var accountID: Int?
    @Environment(AppModel.self) private var model
    @State private var snapshot: PluginConfigSnapshot?
    @State private var baseline: JSONValue = .object([:])
    @State private var draft: JSONValue = .object([:])
    @State private var loadError: String?
    @State private var validationError: String?
    @State private var reviewing = false
    @State private var saving = false
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var savedMessage: String?

    private var global: Bool { accountID == nil }
    private var segment: String { ManagementText.pathSegment(feature["key"]?.stringValue ?? "") }
    private var path: String { accountID.map { "/api/accounts/\($0)/features/\(segment)/config" } ?? "/api/plugins/\(segment)/config" }
    private var freeForm: Bool { !global && !PluginConfigSchema.hasSchema(feature) }
    private var fields: [PluginConfigField] { PluginConfigSchema.fields(feature: feature, global: global) }

    private var changes: [PluginConfigChange] {
        if freeForm {
            let keys = Set((baseline.objectValue ?? [:]).keys).union((draft.objectValue ?? [:]).keys).sorted()
            return keys.compactMap { key -> PluginConfigChange? in
                guard !PluginConfigFormat.same(baseline[key], draft[key]) else { return nil }
                let secret = ManagementText.secret(key)
                return PluginConfigChange(key: key, label: ManagementText.label(key),
                                          before: PluginConfigFormat.text(baseline[key], secret: secret),
                                          after: draft[key] == nil ? "移除此项" : PluginConfigFormat.text(draft[key], secret: secret))
            }
        }
        return fields.compactMap { field -> PluginConfigChange? in
            guard !field.readOnly else { return nil }
            if field.secret {
                guard let typed = draft[field.key]?.stringValue, !typed.isEmpty else { return nil }
                return PluginConfigChange(key: field.key, label: field.label,
                                          before: PluginConfigFormat.text(snapshot?.values[field.key], secret: true), after: "替换为新内容（已隐藏）")
            }
            guard !PluginConfigFormat.same(baseline[field.key], draft[field.key]) else { return nil }
            return PluginConfigChange(key: field.key, label: field.label,
                                      before: PluginConfigFormat.text(baseline[field.key], secret: false),
                                      after: PluginConfigFormat.text(draft[field.key], secret: false))
        }
    }

    var body: some View {
        Form {
            if let loadError, snapshot == nil {
                Section {
                    NoticeView(title: "配置读取失败", message: loadError)
                    Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            } else if snapshot == nil {
                Section { ProgressView("正在读取当前配置…") }
            } else {
                Section {
                    Label(global ? "全局配置所有账号共享，保存后立即对所有账号生效。" : "修改只保存到当前账号，不影响其他账号。",
                          systemImage: global ? "globe" : "person.crop.circle")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if let savedMessage { Section { Label(savedMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green) } }
                if freeForm {
                    ManagementDictionaryView(value: $draft)
                } else {
                    ForEach(fields.filter { !$0.hidden }) { field in fieldSection(field) }
                }
                Section {
                    Button { beginReview() } label: {
                        Label(changes.isEmpty ? "尚未修改" : "检查 \(changes.count) 项修改", systemImage: "list.bullet.clipboard")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(changes.isEmpty || saving || uncertain)
                    .listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
                    .accessibilityIdentifier("plugin.config.review")
                } footer: {
                    if let validationError { Text(validationError).foregroundStyle(.red) }
                    else if uncertain { Text("上次保存结果待确认。请返回后重新进入，确认服务器上的实际配置后再修改。") }
                    else { Text("保存前会列出每一项的原值和新值供你确认。") }
                }
            }
        }
        .navigationTitle(global ? "修改全局配置" : "修改账号配置").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("检查修改") { beginReview() }.disabled(snapshot == nil || changes.isEmpty || saving || uncertain)
                    .accessibilityIdentifier("plugin.config.toolbarReview")
            }
        }
        // 只在首次进入时读取：从嵌套的列表/分组编辑页返回时不能覆盖尚未保存的修改。
        .task { if snapshot == nil { await load() } }
        .sheet(isPresented: $reviewing) { reviewSheet }
    }

    @ViewBuilder private func fieldSection(_ field: PluginConfigField) -> some View {
        let changed = field.secret ? !(draft[field.key]?.stringValue ?? "").isEmpty : !PluginConfigFormat.same(baseline[field.key], draft[field.key])
        Section {
            if field.readOnly {
                LabeledContent {
                    Text(PluginConfigFormat.text(snapshot?.values[field.key], secret: field.secret)).foregroundStyle(.secondary)
                } label: { Label(field.label, systemImage: field.symbol) }
            } else {
                ManagementFieldView(title: field.secret ? "留空则保持原值" : field.label, key: field.key, schema: field.spec, value: binding(field.key))
                if ManagementSchema.allowsNull(field.spec), let value = draft[field.key], !value.isNull, !field.secret {
                    Button("清空此项", systemImage: "xmark.circle", role: .destructive) { set(field.key, .null) }
                }
            }
        } header: {
            if !field.titledInline || changed || field.required {
                HStack {
                    if !field.titledInline { Text(field.label) }
                    if field.required { Text("必填") }
                    Spacer()
                    if changed { Label("已修改", systemImage: "pencil.circle.fill").foregroundStyle(.orange) }
                }
            }
        } footer: {
            VStack(alignment: .leading, spacing: 2) {
                if let description = field.description { Text(description) }
                if field.secret { Text("出于安全不显示已保存的内容（当前：\(PluginConfigFormat.text(snapshot?.values[field.key], secret: true))）。留空表示保持原值。") }
                if field.readOnly { Text("此项由插件自动维护，不能手动修改。") }
                if let fallback = field.spec["default"], !fallback.isNull, !field.secret, !field.readOnly {
                    Text("插件默认值：\(PluginConfigFormat.text(fallback, secret: false))")
                }
            }
        }
    }

    private var reviewSheet: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(changes) { change in
                        VStack(alignment: .leading, spacing: 6) {
                            Label(change.label, systemImage: "pencil").font(.subheadline.weight(.semibold))
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("原值").font(.caption).foregroundStyle(.secondary).frame(width: 34, alignment: .leading)
                                Text(change.before).strikethrough().font(.callout).foregroundStyle(.secondary)
                            }
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("新值").font(.caption).foregroundStyle(.secondary).frame(width: 34, alignment: .leading)
                                Text(change.after).font(.callout.weight(.medium)).foregroundStyle(Color.accentColor)
                            }
                        }
                        .padding(.vertical, 2)
                        .accessibilityElement(children: .combine)
                    }
                } header: { Text("将保存 \(changes.count) 项修改") } footer: {
                    Text(global ? "保存到全局配置，所有账号共享。" : "只保存到当前账号。")
                }
                if let saveError {
                    Section { NoticeView(title: uncertain ? "结果待确认" : "保存失败", message: saveError) }
                }
                Section {
                    Button { Task { await save() } } label: {
                        Label("保存修改", systemImage: "checkmark.circle.fill").frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(saving || uncertain || changes.isEmpty)
                    .listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
                    .accessibilityIdentifier("plugin.config.save")
                    if saving { ProgressView("正在保存到服务器…").frame(maxWidth: .infinity) }
                } footer: {
                    Text(uncertain ? "网络中断，无法确认是否已保存。请关闭此页并返回刷新，确认服务器上的实际配置后再操作。"
                                   : "保存后 Worker 会自动加载新配置，无需重启。")
                }
            }
            .navigationTitle("确认修改").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("继续编辑") { reviewing = false }.disabled(saving) }
            }
        }
        .presentationDetents([.medium, .large])
        .interactiveDismissDisabled(saving)
    }

    private func binding(_ key: String) -> Binding<JSONValue> {
        Binding(get: { draft[key] ?? .null }, set: { set(key, $0) })
    }
    private func set(_ key: String, _ value: JSONValue) {
        var object = draft.objectValue ?? [:]
        object[key] = value
        draft = .object(object)
    }

    private var validationSchema: JSONValue {
        var properties: [String: JSONValue] = [:]
        var required: [JSONValue] = []
        for field in fields where !field.readOnly {
            properties[field.key] = field.spec
            let alreadySet = field.secret && !PluginConfigFormat.text(snapshot?.values[field.key], secret: true).hasPrefix("未")
            if field.required, !alreadySet, !field.hidden { required.append(.string(field.key)) }
        }
        return .object(["type": .string("object"), "properties": .object(properties), "required": .array(required)])
    }

    private func cleanedConfig() -> [String: JSONValue] {
        if freeForm { return draft.objectValue ?? [:] }
        // 全局 PUT 不会像账号 PATCH 一样保留只读值，必须带回其原值。
        var result: [String: JSONValue] = global ? (snapshot?.stored ?? [:]).filter { key, _ in
            fields.contains { $0.key == key && $0.readOnly }
        } : [:]
        for field in fields where !field.readOnly {
            guard let value = draft[field.key] else { continue }
            if value.isNull, !ManagementSchema.allowsNull(field.spec) { continue }
            if field.secret, (value.stringValue ?? "").isEmpty { continue }
            result[field.key] = value
        }
        // 与 Web 一致：账号配置保存是整体覆盖，需带上独立管理的裸直通设置，避免被清掉。
        if !global, result["direct_passthrough"] == nil, let passthrough = snapshot?.stored["direct_passthrough"] {
            result["direct_passthrough"] = passthrough
        }
        return result
    }

    private func beginReview() {
        validationError = nil
        saveError = nil
        guard !changes.isEmpty else { return }
        if !freeForm, let message = ManagementSchema.validate(.object(cleanedConfig()), schema: validationSchema) {
            validationError = message
            return
        }
        reviewing = true
    }

    private func load() async {
        do {
            let loaded = try await PluginConfigLoader.load(model: model, feature: feature, accountID: accountID)
            var seed: [String: JSONValue] = [:]
            if freeForm {
                seed = loaded.stored.filter { !ManagementText.secret($0.key) }
            } else {
                for field in fields where !field.secret && !field.readOnly {
                    if let value = loaded.values[field.key], !value.isNull || ManagementSchema.allowsNull(field.spec) { seed[field.key] = value }
                    else if let fallback = field.spec["default"], !fallback.isNull { seed[field.key] = fallback }
                }
            }
            snapshot = loaded
            baseline = .object(seed)
            draft = .object(seed)
            loadError = nil
        } catch { loadError = error.localizedDescription }
    }

    private func save() async {
        guard !saving, !uncertain else { return }
        saving = true
        saveError = nil
        defer { saving = false }
        let count = changes.count
        do {
            _ = try await model.request(path: path, method: global ? "PUT" : "PATCH", body: .object(["config": .object(cleanedConfig())]))
        } catch {
            saveError = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
            return
        }
        reviewing = false
        baseline = draft
        savedMessage = "已保存 \(count) 项修改，Worker 会自动加载新配置。"
        await load()
    }
}

/// 试运行与插件操作：使用此账号当前生效的配置运行插件声明的动作。
private struct PluginConfigActionsView: View {
    let feature: JSONValue
    let accountID: Int
    @Environment(AppModel.self) private var model
    @State private var current: JSONValue?
    @State private var error: String?
    private var segment: String { ManagementText.pathSegment(feature["key"]?.stringValue ?? "") }
    private var actions: [JSONValue] { feature["config_actions"]?.arrayValue ?? feature["config_schema"]?["x-config-actions"]?.arrayValue ?? [] }

    var body: some View {
        List {
            if let error { NoticeView(title: "配置读取失败", message: error) }
            if let current {
                Section {
                    ForEach(Array(actions.enumerated()), id: \.offset) { _, action in
                        if let actionKey = action["key"]?.stringValue {
                            let title = action["title"]?.stringValue ?? actionKey
                            let base = "/api/accounts/\(accountID)/features/\(segment)/config/actions/\(ManagementText.pathSegment(actionKey))"
                            let inputSchema = action["input_schema"] ?? .object(["type": .string("object"), "properties": .object([:])])
                            let requestSchema: JSONValue = .object(["type": .string("object"), "properties": .object(["input": inputSchema]), "required": .array([.string("input")])])
                            PluginActionRow(action: .init(title: title, path: base, explanation: action["description"]?.stringValue ?? "在服务器上运行此插件操作。", schema: requestSchema, supplementalBody: ["config": current]), systemImage: "play.circle")
                            PluginActionRow(action: .init(title: "后台运行：" + title, path: base + "/jobs", explanation: "创建后台任务运行，可在「配置任务与进度」查看结果。", schema: requestSchema, supplementalBody: ["config": current]), systemImage: "clock.badge.checkmark")
                        }
                    }
                } footer: { Text("操作使用此账号当前保存的配置运行，可能会真实发送消息或调用外部服务。") }
            } else if error == nil { ProgressView("正在读取配置…") }
        }
        .navigationTitle("试运行与插件操作").navigationBarTitleDisplayMode(.inline)
        .task {
            guard current == nil else { return }
            do { current = try await model.request(path: "/api/accounts/\(accountID)/features/\(segment)/config"); error = nil }
            catch { self.error = error.localizedDescription }
        }
    }
}

struct ManagementLocalPluginsView: View {
    @Environment(AppModel.self) private var model
    @State private var items: [JSONValue] = []
    @State private var error: String?
    var body: some View {
        List {
            if let error { NoticeView(title: "读取失败", message: error) }
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                if let name = item["name"]?.stringValue ?? item["key"]?.stringValue {
                    NavigationLink { ManagementFormView(action: .init(title: "安装 " + name, path: "/api/plugin-repos/local/plugins/\(ManagementText.pathSegment(name))/install")) } label: { ManagementRecordRow(value: item) }
                }
            }
        }.navigationTitle("本地参考插件").navigationBarTitleDisplayMode(.inline).task {
            do { items = ManagementText.items(try await model.request(path: "/api/plugin-repos/local/plugins")) }
            catch { self.error = error.localizedDescription }
        }
    }
}
