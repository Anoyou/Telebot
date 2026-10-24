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
                VStack(spacing: 0) {
                    Picker("插件工作区", selection: $section) {
                        ForEach(PluginWorkspaceSection.allCases, id: \.self) { item in
                            Text(item.title).tag(item)
                                .accessibilityIdentifier(item == .center ? "plugins.center" : "plugins.management")
                        }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(Color(.systemGroupedBackground))
                    switch section {
                    case .center:
                        PluginCenterView(features: features, loading: busy, loadError: error, reload: load)
                    case .management:
                        PluginSourcesView(features: features, installed: installed, loading: busy, loadError: error, reload: load)
                    }
                }
            }
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle(accountID == nil ? "插件" : "插件与规则")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
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
        Array(repeating: GridItem(.flexible(), spacing: 12, alignment: .top), count: horizontalSizeClass == .regular ? 3 : 2)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                accountPicker
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
                        ContentUnavailableView("没有匹配的插件", systemImage: "puzzlepiece.extension", description: Text("调整账号、状态或分类筛选。")).padding(.vertical, 24)
                    } else {
                        LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
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

    private var accountPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            if fixedAccountID == nil {
                Menu {
                    Button("全部账号 · 全局插件状态") { selectedAccountID = nil }
                    if !model.accounts.isEmpty { Divider() }
                    ForEach(model.accounts) { account in Button(ManagementText.accountName(account)) { selectedAccountID = account.id } }
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: selectedAccountID == nil ? "square.stack.3d.up" : "person.crop.circle").frame(width: 30, height: 30).foregroundStyle(Color.accentColor)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(selectedAccount.map(ManagementText.accountName) ?? "全部账号").font(.subheadline.weight(.semibold)).foregroundStyle(.primary)
                            Text(selectedAccountID == nil ? "查看全局配置与运行状态" : "配置此账号的插件与自动化规则").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(12).background(Color(.secondarySystemGroupedBackground), in: .rect(cornerRadius: 12))
                }
                .accessibilityIdentifier("plugins.accountPicker")
            }
            if model.accounts.isEmpty, fixedAccountID == nil {
                Label("还没有账号，可先安装插件，再到账号中启用。", systemImage: "info.circle").font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    private var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(PluginFilter.allCases, id: \.self) { item in
                    Button(item.title) { filter = item }
                        .buttonStyle(.borderedProminent)
                        .tint(filter == item ? Color.accentColor : Color(.secondarySystemGroupedBackground))
                        .foregroundStyle(filter == item ? Color.white : Color.primary)
                        .font(.subheadline)
                        .accessibilityIdentifier("plugins.filter." + item.rawValue)
                }
            }
        }
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
                    LabeledContent("检查更新") { Text(updates > 0 ? "\(updates) 个可更新" : "查看远程版本").foregroundStyle(updates > 0 ? Color.orange : Color.secondary) }
                }
                .accessibilityIdentifier("plugins.checkUpdates")
            } header: { Text("安装与更新") } footer: { Text("这里管理服务器上的插件包，不会改变某个账号的插件开关或规则。") }
            Section("来源") {
                NavigationLink("远程插件与更新") { remotePlugins }
                NavigationLink("插件仓库") { repositories }
                NavigationLink("从文件安装") { FileManagementView(mode: .plugin) }
                NavigationLink("本地参考插件") { ManagementLocalPluginsView() }
            }
            Section("已安装插件") {
                if loading && features.isEmpty { ProgressView("正在读取安装记录…") }
                ForEach(Array(features.enumerated()), id: \.offset) { _, feature in
                    NavigationLink { ManagementPluginDetailView(feature: feature.withInstallState(installed[feature["key"]?.stringValue ?? ""]), initialSection: .maintenance) } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(ManagementText.title(feature)).font(.headline).lineLimit(2)
                                Spacer()
                                if let enabled = installed[feature["key"]?.stringValue ?? ""]?["global_enabled"]?.boolValue {
                                    Text(enabled ? "安装已启用" : "安装已停用")
                                        .font(.caption.weight(.medium))
                                        .foregroundStyle(enabled ? Color.secondary : Color.orange)
                                }
                                if feature["update_available"]?.boolValue == true { Text("可更新").font(.caption.weight(.medium)).foregroundStyle(.orange) }
                            }
                            Text(PluginPresentation.source(feature)).font(.caption).foregroundStyle(.secondary)
                            if let version = feature["version"]?.stringValue {
                                Text("当前 v\(version)" + (feature["latest_version"]?.stringValue.map { " · 最新 v\($0)" } ?? "")).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if !loading, features.isEmpty { Text("暂无已安装插件").foregroundStyle(.secondary) }
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

private enum PluginStatusTone { case running, paused, pending, failed, unknown }

private enum PluginPresentation {
    static func status(feature: JSONValue, accountState: JSONValue?, accountScoped: Bool) -> (title: String, symbol: String, tone: PluginStatusTone) {
        if accountScoped {
            guard accountState?["enabled"]?.boolValue == true else { return ("未启用", "pause.circle", .paused) }
            let state = accountState?["state"]?.stringValue ?? ""
            if accountState?["last_error"]?.stringValue?.isEmpty == false || state == "failed" { return ("运行异常", "exclamationmark.circle.fill", .failed) }
            if state == "active" { return ("运行中", "checkmark.circle.fill", .running) }
            return ("等待 Worker 生效", "clock", .pending)
        }
        if let runtime = feature["runtime_availability"]?.stringValue, !runtime.isEmpty {
            switch runtime {
            case "ready": return ("全局可用", "checkmark.circle.fill", .running)
            case "paused": return ("全局已暂停", "pause.circle", .paused)
            case "partial": return ("部分可用", "exclamationmark.circle", .pending)
            default: return ("等待运行状态", "clock", .pending)
            }
        }
        return ("已安装 · 全局管理", "shippingbox", .unknown)
    }
    static func needsAttention(feature: JSONValue, accountState: JSONValue?) -> Bool {
        feature["orphan"]?.boolValue == true || feature["signature_ok"]?.boolValue == false || feature["update_available"]?.boolValue == true
            || accountState?["state"]?.stringValue == "failed" || accountState?["last_error"]?.stringValue?.isEmpty == false
    }
    static func category(_ value: String) -> String { ["automation": "自动化", "interactive": "交互", "utility": "工具"][value] ?? value }
    static func source(_ feature: JSONValue) -> String {
        var parts: [String] = []
        if feature["is_builtin"]?.boolValue == true { parts.append("内置") }
        else if let label = feature["source_label"]?.stringValue, !label.isEmpty { parts.append(label) }
        else { parts.append(feature["source_type"]?.stringValue ?? "本地") }
        if feature["signature_ok"]?.boolValue == true { parts.append("签名有效") }
        if feature["signature_ok"]?.boolValue == false { parts.append("签名异常") }
        if feature["orphan"]?.boolValue == true { parts.append("来源缺失") }
        return parts.joined(separator: " · ")
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

private struct PluginCard: View {
    let feature: JSONValue
    let accountState: JSONValue?
    let accountScoped: Bool
    let favorite: Bool
    private var key: String { feature["key"]?.stringValue ?? "" }
    var body: some View {
        let status = PluginPresentation.status(feature: feature, accountState: accountState, accountScoped: accountScoped)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Image(systemName: feature["category"]?.stringValue == "interactive" ? "bubble.left.and.text.bubble.right" : "puzzlepiece.extension").font(.body.weight(.medium)).foregroundStyle(Color.accentColor).frame(width: 34, height: 34).background(Color.accentColor.opacity(0.1), in: .rect(cornerRadius: 9))
                Spacer(minLength: 4)
                if feature["update_available"]?.boolValue == true { Image(systemName: "arrow.up.circle.fill").foregroundStyle(.orange).accessibilityLabel("有可用更新") }
                if favorite { Image(systemName: "star.fill").font(.caption).foregroundStyle(.orange).accessibilityLabel("已收藏") }
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(ManagementText.title(feature)).font(.subheadline.weight(.semibold)).lineLimit(2).frame(minHeight: 38, alignment: .topLeading)
                if let usage = feature["usage"]?.stringValue, !usage.isEmpty {
                    Text(usage).font(.caption).foregroundStyle(.secondary).lineLimit(2).frame(minHeight: 32, alignment: .topLeading)
                } else {
                    Text(key).font(.caption).foregroundStyle(.secondary).lineLimit(1).frame(minHeight: 32, alignment: .topLeading)
                }
            }
            Spacer(minLength: 0)
            Label(status.title, systemImage: status.symbol).font(.caption.weight(.medium)).foregroundStyle(PluginPresentation.color(status.tone)).lineLimit(2)
            Text(accountScoped ? "账号作用域" : "v" + (feature["version"]?.stringValue ?? "未标注")).font(.caption2).foregroundStyle(.secondary)
        }
        .padding(12).frame(maxWidth: .infinity, minHeight: 190, alignment: .topLeading)
        .background(Color(.secondarySystemGroupedBackground), in: .rect(cornerRadius: 14))
        .contentShape(.rect(cornerRadius: 14))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("plugin.card." + key)
    }
}

enum PluginDetailSection: String, CaseIterable {
    case overview, configuration, accounts, maintenance
}

struct ManagementPluginDetailView: View {
    let feature: JSONValue
    var accountID: Int?
    var accountState: JSONValue?
    var initialSection: PluginDetailSection = .overview
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    @State private var section: PluginDetailSection = .overview
    @State private var currentAccountState: JSONValue?
    @State private var toggling = false
    @State private var confirmToggle = false
    @State private var actionMessage: String?
    @State private var actionError: String?
    private var key: String { feature["key"]?.stringValue ?? "" }
    private var segment: String { ManagementText.pathSegment(key) }

    var body: some View {
        List {
            Section {
                PluginDetailHeader(feature: feature, accountState: currentAccountState, accountScoped: accountID != nil)
                if accountID != nil { toggleButton }
                if toggling { ProgressView("正在提交并刷新状态…") }
                if let actionMessage { Label(actionMessage, systemImage: "checkmark.circle").foregroundStyle(.green) }
                if let actionError { NoticeView(title: "操作未完成", message: actionError) }
            }
            Picker("插件详情", selection: $section) {
                Text("概况").tag(PluginDetailSection.overview)
                Text(accountID == nil ? "全局配置" : "账号配置").tag(PluginDetailSection.configuration)
                if accountID == nil { Text("账号使用").tag(PluginDetailSection.accounts) }
                Text("维护").tag(PluginDetailSection.maintenance)
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
        .task { section = accountID == nil ? initialSection : .overview; currentAccountState = accountState }
        .confirmationDialog(accountEnabled ? "停用此账号的插件？" : "启用此账号的插件？", isPresented: $confirmToggle, titleVisibility: .visible) {
            Button(accountEnabled ? "确认停用" : "确认启用", role: accountEnabled ? .destructive : nil) { Task { await toggleAccount() } }
            Button("取消", role: .cancel) {}
        } message: { Text("只修改当前账号，不会修改其他账号或服务器上的插件安装状态。") }
    }
    private var accountEnabled: Bool { currentAccountState?["enabled"]?.boolValue == true }
    private var toggleButton: some View {
        Button(accountEnabled ? "停用此账号" : "启用此账号") { confirmToggle = true }
            .buttonStyle(.borderedProminent).tint(accountEnabled ? Color(.secondarySystemGroupedBackground) : Color.accentColor)
            .foregroundStyle(accountEnabled ? Color.primary : Color.white).disabled(toggling).accessibilityIdentifier("plugin.account.toggle")
    }
    @ViewBuilder private var overview: some View {
        Section("插件信息") {
            if let usage = feature["usage"]?.stringValue, !usage.isEmpty { Text(usage) }
            ReadOnlyRow(title: "插件标识", value: key)
            ReadOnlyRow(title: "版本", value: feature["version"]?.stringValue ?? "未标注")
            ReadOnlyRow(title: "分类", value: PluginPresentation.category(feature["category"]?.stringValue ?? "utility"))
            ReadOnlyRow(title: "来源", value: PluginPresentation.source(feature))
        }
        if let accountState = currentAccountState {
            Section("账号运行详情") {
                ReadOnlyRow(title: "账号开关", value: accountState["enabled"]?.boolValue == true ? "已启用" : "未启用")
                ReadOnlyRow(title: "Worker 状态", value: ManagementText.scalar(accountState["state"] ?? "未知"))
                if let error = accountState["last_error"]?.stringValue, !error.isEmpty { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            }
        }
        Section("完整技术信息") {
            NavigationLink("能力、权限与诊断字段") { List { ManagementValueView(value: feature) }.navigationTitle("插件技术信息") }
            if let state = currentAccountState { NavigationLink("账号原始状态") { List { ManagementValueView(value: state) }.navigationTitle("账号插件状态") } }
        }
    }
    @ViewBuilder private var configuration: some View {
        Section {
            NavigationLink(accountID == nil ? "编辑全局配置" : "编辑账号配置") { ManagementPluginConfigView(feature: feature, accountID: accountID) }
        } footer: { Text(accountID == nil ? "全局配置是所有账号的默认值，不包含各账号独立规则。" : "这里使用合并后的账号配置。保存只影响当前账号。") }
        if let accountID {
            let base = "/api/accounts/\(accountID)/features/\(segment)"
            Section("规则与自动化") {
                NavigationLink("自动化规则") {
                    ManagementCollectionView(resource: .init(title: "自动化规则", path: base + "/rules", kind: .rules, create: .init(title: "添加规则", path: base + "/rules", schema: ManagementRuleSchemas.request(feature: feature)), itemPath: base + "/rules/{id}", ruleSchema: ManagementRuleSchemas.request(feature: feature)))
                }
                ManagementActionLink(action: .init(title: "复制规则到其他账号", path: base + "/rules/copy"))
                ManagementActionLink(action: .init(title: "直通开关与优先级", path: base + "/direct-passthrough", method: "PATCH"))
                NavigationLink("配置任务与进度") { ManagementCollectionView(resource: .init(title: "配置任务", path: base + "/config/action-jobs", kind: .jobs, idField: "job_id")) }
            }
        }
    }
    @ViewBuilder private var accounts: some View {
        Section {
            ManagementActionLink(action: .init(title: "分配到账号", path: "/api/plugins/install", explanation: "为选择的账号创建插件配置。账号启用状态请在对应账号中单独管理。", seed: .object(["plugin_key": .string(key)])))
            ForEach(model.accounts) { account in NavigationLink(ManagementText.accountName(account)) { AccountPluginDetailView(accountID: account.id, feature: feature) } }
        } header: { Text("选择账号管理") } footer: { Text("账号使用与全局安装状态分开管理。进入账号后可配置规则、试运行和后台任务。") }
    }
    @ViewBuilder private var maintenance: some View {
        if accountID == nil {
            Section("安装状态与 Worker") {
                ForEach([("全局启用插件", "enable"), ("全局停用插件", "disable")], id: \.1) { label, operation in
                    ManagementActionLink(action: .init(title: label, path: "/api/plugins/install/\(segment)/\(operation)", destructive: operation == "disable",
                        explanation: "修改服务器上的全局安装状态，不会替你修改各账号的独立插件配置。"))
                }
                ManagementActionLink(action: .init(title: "重新加载到所选账号", path: "/api/plugins/\(segment)/reload",
                    explanation: "选择要重载的账号。服务器将重新加载此插件，运行中的任务可能短暂中断。"))
            }
            Section("安装记录") {
                NavigationLink("安装历史") { ManagementCollectionView(resource: .init(title: "安装历史", path: "/api/plugins/install/\(segment)/history")) }
                NavigationLink("更新日志") { ManagementDocumentView(title: "更新日志", path: "/api/plugins/install/\(segment)/changelog") }
                if feature["is_builtin"]?.boolValue != true {
                    ManagementActionLink(action: .init(title: "卸载插件", path: "/api/plugins/install/\(segment)", method: "DELETE", destructive: true, explanation: "将移除服务器上的插件安装记录与文件。请先检查依赖此插件的账号和任务。"))
                }
            }
        } else {
            Section("当前账号") {
                NavigationLink("配置任务与进度") {
                    let base = "/api/accounts/\(accountID!)/features/\(segment)"
                    ManagementCollectionView(resource: .init(title: "配置任务", path: base + "/config/action-jobs", kind: .jobs, idField: "job_id"))
                }
            }
            Section("服务器级维护") { NavigationLink("查看全局插件维护") { ManagementPluginDetailView(feature: feature, initialSection: .maintenance) } }
        }
    }
    private func toggleAccount() async {
        guard let accountID, !toggling else { return }
        toggling = true
        actionMessage = nil
        actionError = nil
        defer { toggling = false }
        do {
            _ = try await model.request(path: "/api/accounts/\(accountID)/features/\(segment)", method: "PATCH", body: ["enabled": .bool(!accountEnabled)])
            let values = try await model.request(path: "/api/accounts/\(accountID)/features").arrayValue ?? []
            currentAccountState = values.first { $0["feature_key"]?.stringValue == key }
            actionMessage = currentAccountState?["state"]?.stringValue == "active" ? "已启用，Worker 正在运行。" : "请求已提交，请留意 Worker 生效状态。"
        } catch { actionError = error.localizedDescription }
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
        .navigationTitle(ManagementText.title(feature))
        .task {
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
    var body: some View {
        let status = PluginPresentation.status(feature: feature, accountState: accountState, accountScoped: accountScoped)
        VStack(alignment: .leading, spacing: 8) {
            Label(status.title, systemImage: status.symbol).foregroundStyle(PluginPresentation.color(status.tone))
            if !accountScoped, let enabled = feature["global_enabled"]?.boolValue {
                Label(enabled ? "服务器安装已启用" : "服务器安装已停用", systemImage: "shippingbox")
                    .font(.footnote).foregroundStyle(enabled ? Color.green : Color.orange)
            }
            Text(PluginPresentation.source(feature)).font(.footnote).foregroundStyle(.secondary)
            if let error = accountState?["last_error"]?.stringValue, !error.isEmpty { Text(error).font(.footnote).foregroundStyle(.red) }
        }.padding(.vertical, 4)
    }
}

struct ManagementPluginConfigView: View {
    let feature: JSONValue
    var accountID: Int?
    @Environment(AppModel.self) private var model
    @State private var current: JSONValue?
    @State private var error: String?
    private var key: String { ManagementText.pathSegment(feature["key"]?.stringValue ?? "") }
    private var path: String { accountID.map { "/api/accounts/\($0)/features/\(key)/config" } ?? "/api/plugins/\(key)/config" }
    private var schema: JSONValue {
        guard let original = feature["config_schema"], let fields = original["properties"]?.objectValue else { return .object(["type": .string("object"), "additionalProperties": .bool(true)]) }
        var root = original.objectValue ?? [:]
        let properties = fields.filter { _, field in accountID == nil ? field["level"]?.stringValue == "global" : field["level"]?.stringValue != "global" }
        root["properties"] = .object(properties)
        root["required"] = .array((original["required"]?.arrayValue ?? []).filter { item in item.stringValue.map { properties[$0] != nil } ?? false })
        return .object(root)
    }
    private var actions: [JSONValue] { feature["config_actions"]?.arrayValue ?? feature["config_schema"]?["x-config-actions"]?.arrayValue ?? [] }
    var body: some View {
        List {
            if let error { NoticeView(title: "配置读取失败", message: error) }
            if let current {
                Section(accountID == nil ? "当前全局配置" : "当前生效配置") { ManagementValueView(value: current) }
                Section { ManagementActionLink(action: .init(title: "编辑配置", path: path, method: accountID == nil ? "PUT" : "PATCH", schema: schema, seed: current, envelope: "config")) }
                if let accountID, !actions.isEmpty {
                    Section("试运行与插件操作") {
                        ForEach(Array(actions.enumerated()), id: \.offset) { _, action in
                            if let actionKey = action["key"]?.stringValue {
                                let title = action["title"]?.stringValue ?? actionKey
                                let base = "/api/accounts/\(accountID)/features/\(key)/config/actions/\(ManagementText.pathSegment(actionKey))"
                                let inputSchema = action["input_schema"] ?? .object(["type": .string("object"), "properties": .object([:])])
                                let requestSchema: JSONValue = .object(["type": .string("object"), "properties": .object(["input": inputSchema]), "required": .array([.string("input")])])
                                ManagementActionLink(action: .init(title: title, path: base, explanation: action["description"]?.stringValue ?? "在服务器上运行此插件操作。", schema: requestSchema, supplementalBody: ["config": current]))
                                ManagementActionLink(action: .init(title: "后台运行：" + title, path: base + "/jobs", schema: requestSchema, supplementalBody: ["config": current]))
                            }
                        }
                    }
                }
            } else if error == nil { ProgressView("正在读取配置…") }
        }
        .navigationTitle(accountID == nil ? "全局配置" : "账号插件配置").navigationBarTitleDisplayMode(.inline)
        .task { await load() }.refreshable { await load() }
    }
    private func load() async {
        do {
            let response = try await model.request(path: path)
            current = accountID == nil ? (response["config"] ?? response) : response
            error = nil
        } catch { self.error = error.localizedDescription }
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
        }.navigationTitle("本地参考插件").task {
            do { items = ManagementText.items(try await model.request(path: "/api/plugin-repos/local/plugins")) }
            catch { self.error = error.localizedDescription }
        }
    }
}
