import SwiftUI
import UniformTypeIdentifiers
import TelePilotCore

private enum PluginWorkspaceSection: String, CaseIterable {
    case center, discover, installed
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
    /// 仅作为“插件”一级 Tab 根页面时使用大标题；从“管理”等处推入时用行内标题。
    var isTabRoot = true
    @Environment(AppModel.self) private var model
    @State private var section: PluginWorkspaceSection = .center
    @State private var store = PluginStoreModel()
    /// 插件中心所选账号、筛选与本地排序：放在这里，展开／收起搜索或切换分页时不丢失。
    @State private var centerState = PluginCenterState()
    /// 页面级搜索（左上角放大镜 → 系统搜索栏），作用于当前分页。
    @State private var search = ""
    @State private var searching = false

    /// - Parameter initialMode: 打开时显示「插件」卡片还是「运行」（运行树）；服务器设置里的旧入口跳到这里用 `.runtime`。
    init(accountID: Int? = nil, isTabRoot: Bool = true, initialMode: PluginCenterMode = .plugins) {
        self.accountID = accountID
        self.isTabRoot = isTabRoot
        let state = PluginCenterState()
        state.mode = accountID == nil ? initialMode : .plugins
        _centerState = State(initialValue: state)
    }

    var body: some View {
        Group {
            if let accountID {
                PluginCenterView(features: store.features, state: centerState, search: search, fixedAccountID: accountID, loading: store.loading, loadError: store.loadError, reload: load)
            } else {
                // 分段切换作为各自滚动内容的顶部头部，随内容一起滚动。曾挂在 `.safeAreaBar(edge: .top)` 上，
                // 与大标题导航栏的折叠/展开互相改变顶部安全区，滑到顶部时顶栏会持续抖动。
                switch section {
                case .center:
                    if centerState.mode == .runtime {
                        RuntimeTreeView(header: AnyView(sectionPicker), accountID: $centerState.selectedAccountID, mode: $centerState.mode, features: store.features)
                    } else {
                        PluginCenterView(features: store.features, state: centerState, search: search, loading: store.loading, loadError: store.loadError, reload: load, header: AnyView(sectionPicker))
                    }
                case .discover:
                    PluginDiscoverView(store: store, search: search, header: AnyView(sectionPicker))
                case .installed:
                    PluginInstalledView(store: store, search: search, header: AnyView(sectionPicker))
                }
            }
        }
        // 运行树没有可搜索的列表，不显示页面级搜索。
        .pageSearch(text: $search, isActive: $searching, prompt: searchPrompt, identifier: "plugins.search")
        .background(Color(.systemGroupedBackground))
        .navigationTitle(accountID == nil ? "插件" : "插件与规则")
        // 一级 Tab 与概览、AI、更多保持一致使用大标题；账号下的子页面仍用行内标题。
        .navigationBarTitleDisplayMode(accountID == nil && isTabRoot ? .large : .inline)
        .task { await load() }
    }

    /// 插件中心（按账号运行状态）/ 发现（插件仓库）/ 已安装（含待更新，数量为服务器真实的可更新数）。
    private var sectionPicker: some View {
        Picker("插件工作区", selection: $section) {
            Text("插件中心").tag(PluginWorkspaceSection.center)
                .accessibilityIdentifier("plugins.center")
            Text("发现").tag(PluginWorkspaceSection.discover)
                .accessibilityIdentifier("plugins.discover")
            Text(store.updateCount > 0 ? "已安装 · \(store.updateCount)" : "已安装").tag(PluginWorkspaceSection.installed)
                .accessibilityIdentifier("plugins.installed")
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }

    private var searchPrompt: String {
        if accountID != nil { return "搜索插件、标识或使用说明" }
        switch section {
        case .center: return "搜索插件、标识或使用说明"
        case .discover: return "搜索插件名称、简介或标签"
        case .installed: return "搜索已安装的插件"
        }
    }

    private func load() async {
        await store.load(model: model)
        if accountID == nil, !centerState.scopeInitialized, !model.accounts.isEmpty {
            centerState.scopeInitialized = true
            if centerState.selectedAccountID == nil { centerState.selectedAccountID = model.accounts.first?.id }
        }
    }
}

/// 插件中心需要跨视图重建保留的状态（所选账号、该账号的插件状态、筛选、拖动后的本地顺序）。
@MainActor @Observable
private final class PluginCenterState {
    var selectedAccountID: Int?
    var accountFeatures: [String: JSONValue] = [:]
    /// `accountFeatures` 对应的账号。
    var featuresAccountID: Int?
    var category = "all"
    var filter: PluginFilter = .all
    var localOrder: [String]?
    /// 「插件 | 运行」：插件卡片或运行树。
    var mode: PluginCenterMode = .plugins
    /// 首次读取后默认选中第一个账号（之后尊重用户选的「全部账号」）。
    var scopeInitialized = false
}

extension JSONValue {
    /// 把 installed-overview 里的服务器总开关并入 feature-matrix 项，供插件详情使用。
    func withInstallState(_ install: JSONValue?) -> JSONValue {
        guard var object = objectValue else { return self }
        object["global_enabled"] = install?["global_enabled"] ?? .null
        return .object(object)
    }
}

// MARK: - 插件中心

private struct PluginCenterView: View {
    let features: [JSONValue]
    @Bindable var state: PluginCenterState
    /// 页面级搜索内容（来自导航栏左上角的系统搜索栏）。
    let search: String
    var fixedAccountID: Int? = nil
    var loading = false
    var loadError: String?
    let reload: () async -> Void
    /// 可选的顶部头部（插件工作区的分段切换），作为滚动内容第一项显示。
    var header: AnyView? = nil
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var accountError: String?
    @State private var accountLoading = false
    private var selectedAccountID: Int? {
        get { state.selectedAccountID }
        nonmutating set { state.selectedAccountID = newValue }
    }
    private var accountFeatures: [String: JSONValue] {
        get { state.accountFeatures }
        nonmutating set { state.accountFeatures = newValue }
    }
    private var category: String { state.category }
    private var filter: PluginFilter { state.filter }
    // 长按拖动排序：本地顺序在保存成功前覆盖服务器顺序；保存只提交一次，不自动重试。
    private var localOrder: [String]? {
        get { state.localOrder }
        nonmutating set { state.localOrder = newValue }
    }
    @State private var dragging: String?
    @State private var dropTarget: String?
    @State private var savingOrder = false
    @State private var orderMessage: String?
    /// 与系统分组列表相同的左右边距（随容器宽度），让分段控件与 AI、发现、已安装页左右对齐。
    @State private var margin: CGFloat = 16

    /// 服务器顺序（GET /api/feature-matrix 已按保存的展示顺序排好），拖动后暂用本地顺序。
    private var orderedFeatures: [JSONValue] {
        guard let localOrder else { return features }
        let rank = Dictionary(localOrder.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
        return features.enumerated().sorted { lhs, rhs in
            let l = rank[lhs.element["key"]?.stringValue ?? ""] ?? Int.max
            let r = rank[rhs.element["key"]?.stringValue ?? ""] ?? Int.max
            return l == r ? lhs.offset < rhs.offset : l < r
        }.map(\.element)
    }

    private var selectedAccount: Account? { model.accounts.first { $0.id == selectedAccountID } }
    private var categories: [String] { Array(Set(features.compactMap { $0["category"]?.stringValue })).sorted() }
    private var filtered: [JSONValue] {
        orderedFeatures.filter { feature in
            let key = feature["key"]?.stringValue ?? ""
            let accountState = accountFeatures[key]
            let matchesFilter: Bool
            switch filter {
            case .all: matchesFilter = true
            case .running: matchesFilter = PluginPresentation.status(feature: feature, accountState: accountState, accountScoped: selectedAccountID != nil).tone == .running
            case .attention: matchesFilter = PluginPresentation.needsAttention(feature: feature, accountState: accountState)
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
                if let header { header.padding(.top, PageLayout.listHeaderTop) }
                scopePicker
                if fixedAccountID == nil { PluginCenterModePicker(mode: $state.mode) }
                if let loadError { NoticeView(title: "插件目录读取失败", message: loadError) }
                if let accountError { NoticeView(title: "账号插件状态读取失败", message: accountError) }
                if loading && features.isEmpty {
                    ProgressView("正在读取插件与账号状态…").frame(maxWidth: .infinity).padding(.vertical, 48)
                } else {
                    // 状态与分类筛选合并为一个原生筛选菜单（原玻璃胶囊 + 分类菜单）。
                    HStack {
                        filterMenu
                        Spacer()
                        Text("\(filtered.count) / \(features.count) 个插件").font(.footnote).foregroundStyle(.secondary).monospacedDigit()
                    }
                    if accountLoading { ProgressView().frame(maxWidth: .infinity, alignment: .leading) }
                    if let orderMessage {
                        Label(orderMessage, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(.orange)
                            .accessibilityIdentifier("plugins.order.error")
                    }
                    if filtered.isEmpty {
                        ContentUnavailableView("没有匹配的插件", systemImage: "puzzlepiece.extension", description: Text("调整所选账号、状态或分类筛选。")).padding(.vertical, 24)
                    } else {
                        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
                            ForEach(filtered.map { (key: $0["key"]?.stringValue ?? "", feature: $0) }, id: \.key) { item in
                                let key = item.key
                                let feature = item.feature
                                NavigationLink {
                                    ManagementPluginDetailView(feature: feature, accountID: selectedAccountID, accountState: accountFeatures[key])
                                } label: {
                                    PluginCard(feature: feature, accountState: accountFeatures[key], accountScoped: selectedAccountID != nil, favorite: preferences.isFavoritePlugin(key))
                                }
                                .buttonStyle(.plain)
                                .overlay {
                                    if dropTarget == key && dragging != key {
                                        RoundedRectangle(cornerRadius: 12).strokeBorder(Color.accentColor, lineWidth: 2)
                                    }
                                }
                                .opacity(dragging == key ? 0.5 : 1)
                                .contextMenu {
                                    Button(preferences.isFavoritePlugin(key) ? "取消收藏" : "收藏", systemImage: "star") { preferences.togglePlugin(key) }
                                }
                                // 长按后拖动到另一张卡片上，放下即排到该卡片的位置；顺序保存在服务器，Web 与 App 一致。
                                .onDrag {
                                    dragging = key
                                    return NSItemProvider(object: key as NSString)
                                }
                                .dropDestination(for: String.self) { items, _ in
                                    let source = dragging ?? items.first
                                    dragging = nil
                                    dropTarget = nil
                                    guard let source, source != key else { return false }
                                    move(source, before: key)
                                    return true
                                } isTargeted: { targeted in
                                    if targeted { dropTarget = key } else if dropTarget == key { dropTarget = nil }
                                }
                                .accessibilityAction(named: "前移") { moveByOne(key, offset: -1) }
                                .accessibilityAction(named: "后移") { moveByOne(key, offset: 1) }
                            }
                        }
                        Text(savingOrder ? "正在保存排序…" : "长按卡片后拖动可调整顺序，顺序保存在服务器，Web 管理台与 App 一致。")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.horizontal, margin).padding(.bottom, 20)
        }
        .onGeometryChange(for: CGFloat.self) { proxy in proxy.size.width } action: { width in margin = PageLayout.margin(width: width) }
        .background(Color(.systemGroupedBackground))
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
    /// 原生 `Picker(.menu)` 行（系统负责当前值与上下箭头），放在系统分组背景里。
    @ViewBuilder private var scopePicker: some View {
        if fixedAccountID == nil {
            VStack(alignment: .leading, spacing: 6) {
                Picker(selection: $state.selectedAccountID) {
                    Label("全部账号", systemImage: "square.stack.3d.up").tag(Int?.none)
                    ForEach(model.accounts) { account in
                        Label(ManagementText.accountName(account), systemImage: "person.crop.circle").tag(Optional(account.id))
                    }
                } label: {
                    Label("选择配置的账号", systemImage: "person.crop.circle.badge.checkmark")
                }
                .pickerStyle(.menu)
                .frame(minHeight: 44)
                .accessibilityIdentifier("plugins.accountPicker")
                Text(selectedAccountID == nil
                     ? "未选择账号：卡片显示插件在服务器上的整体状态。选择一个账号后，可以查看并配置插件在该账号里的启用情况。"
                     : "正在配置此账号：卡片显示插件在这个账号里是否启用、是否正常运行。点开插件即可为此账号开关或修改配置。")
                    .font(.footnote).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if model.accounts.isEmpty {
                    Label("还没有账号。可以先安装插件，添加账号后再到账号中启用。", systemImage: "info.circle").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(Color(.secondarySystemGroupedBackground), in: .rect(cornerRadius: 12))
            .padding(.top, 4)
        }
    }

    /// 状态与分类筛选：一个原生 `Menu`，内含两个内联 `Picker`（系统勾选当前项），
    /// 取代原先的玻璃胶囊按钮行与单独的分类菜单。
    private var filterMenu: some View {
        Menu {
            Picker("状态", selection: $state.filter) {
                ForEach(PluginFilter.allCases, id: \.self) { item in Text(item.title).tag(item) }
            }
            .pickerStyle(.inline)
            Picker("分类", selection: $state.category) {
                Text("全部分类").tag("all")
                ForEach(categories, id: \.self) { Text(PluginPresentation.category($0)).tag($0) }
            }
            .pickerStyle(.inline)
        } label: {
            Label(filterSummary, systemImage: filter == .all && category == "all" ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
                .font(.subheadline)
                .frame(minHeight: 44)
        }
        .accessibilityLabel("筛选：\(filterSummary)")
        .accessibilityIdentifier("plugins.category")
    }

    private var filterSummary: String {
        let parts = [filter == .all ? nil : filter.title, category == "all" ? nil : PluginPresentation.category(category)].compactMap { $0 }
        return parts.isEmpty ? "全部插件" : parts.joined(separator: " · ")
    }

    /// 把 source 移到 target 的位置（基于完整顺序，不受筛选影响），然后保存。
    private func move(_ source: String, before target: String) {
        var keys = orderedFeatures.compactMap { $0["key"]?.stringValue }
        guard let from = keys.firstIndex(of: source), let to = keys.firstIndex(of: target), from != to else { return }
        keys.remove(at: from)
        keys.insert(source, at: to)
        withAnimation(.snappy) { localOrder = keys }
        Task { await saveOrder(keys) }
    }

    private func moveByOne(_ key: String, offset: Int) {
        let visible = filtered.compactMap { $0["key"]?.stringValue }
        guard let index = visible.firstIndex(of: key) else { return }
        let target = index + offset
        guard visible.indices.contains(target) else { return }
        var keys = orderedFeatures.compactMap { $0["key"]?.stringValue }
        guard let from = keys.firstIndex(of: key), let to = keys.firstIndex(of: visible[target]) else { return }
        keys.swapAt(from, to)
        withAnimation(.snappy) { localOrder = keys }
        Task { await saveOrder(keys) }
    }

    /// 整表覆盖写入展示顺序（PUT /api/feature-matrix/order）。失败时恢复服务器顺序并提示；结果未知时提示刷新核对，不自动重试。
    private func saveOrder(_ keys: [String]) async {
        guard !savingOrder else { return }
        savingOrder = true
        orderMessage = nil
        defer { savingOrder = false }
        do {
            _ = try await model.request(path: "/api/feature-matrix/order", method: "PUT",
                                        body: .object(["keys": .array(keys.map { .string($0) })]))
            await reload()
            localOrder = nil
        } catch {
            if (error as? APIError)?.isOutcomeUncertain == true {
                orderMessage = "未能确认排序是否已保存，下拉刷新查看实际顺序。"
            } else {
                orderMessage = "排序未保存：\(error.localizedDescription)"
                withAnimation(.snappy) { localOrder = nil }
            }
        }
    }

    private func loadAccountFeatures() async {
        accountError = nil
        // 视图重建（展开／收起搜索、切换分页）后重新读取同一账号时保留现有状态，避免卡片闪成空白。
        if state.featuresAccountID != selectedAccountID { accountFeatures = [:] }
        state.featuresAccountID = selectedAccountID
        guard let selectedAccountID else { return }
        accountLoading = true
        defer { accountLoading = false }
        do {
            let response = try await model.request(path: "/api/accounts/\(selectedAccountID)/features")
            accountFeatures = Dictionary((response.arrayValue ?? []).compactMap { item in item["feature_key"]?.stringValue.map { ($0, item) } }, uniquingKeysWith: { _, new in new })
        } catch { accountError = error.localizedDescription }
    }
}

// MARK: - 展示辅助

enum PluginStatusTone { case running, paused, pending, failed, unknown }

enum PluginPresentation {
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
        if isGloballyDisabled(error) { return ("全局已停用", "pause.circle", .paused) }
        if (error?.isEmpty == false) || state == "failed" { return ("运行异常", "exclamationmark.circle.fill", .failed) }
        if state == "active" { return ("运行中", "checkmark.circle.fill", .running) }
        return ("等待生效", "clock", .pending)
    }
    static func needsAttention(feature: JSONValue, accountState: JSONValue?) -> Bool {
        feature["orphan"]?.boolValue == true || feature["signature_ok"]?.boolValue == false || feature["update_available"]?.boolValue == true
            || accountState?["state"]?.stringValue == "failed" || accountState?["last_error"]?.stringValue?.isEmpty == false
    }
    /// Worker 因服务器总开关关闭而跳过加载（loader 写入 `PLUGIN_DISABLED: installed_plugin.enabled=False`）。
    static func isGloballyDisabled(_ error: String?) -> Bool {
        error?.trimmingCharacters(in: .whitespaces).hasPrefix("PLUGIN_DISABLED") == true
    }
    /// 把 Worker 的原始错误码翻译成可理解的中文；未知错误原样展示。
    static func errorText(_ error: String) -> String {
        isGloballyDisabled(error) ? "插件已全局停用，账号开关不会生效。需在服务器总开关中全局启用后才会运行。" : error
    }
    static func category(_ value: String) -> String { ["automation": "自动化", "interactive": "交互", "utility": "工具"][value] ?? value }
    static func symbol(_ feature: JSONValue) -> String { symbol(category: feature["category"]?.stringValue) }
    static func symbol(category: String?) -> String {
        switch category {
        case "interactive": "bubble.left.and.text.bubble.right"
        case "automation": "gearshape.2"
        default: "puzzlepiece.extension"
        }
    }
    /// 列表里的一句话来源：内置、仓库名或安装方式（不含签名等状态）。
    static func sourceShort(_ feature: JSONValue) -> String {
        if feature["is_builtin"]?.boolValue == true { return "内置" }
        if let label = feature["source_label"]?.stringValue, !label.isEmpty, !["local", "remote", "repo", "git", "zip", "core"].contains(label) { return label }
        return sourceType(feature["source_type"]?.stringValue)
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
        "agent_skills": ("读取 Skills", "只读获取管理员对插件启用的 Skill 说明文本，不执行其中脚本"),
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

struct PluginIcon: View {
    let feature: JSONValue
    var size: CGFloat = 28
    var body: some View {
        PluginArtwork(key: feature["key"]?.stringValue ?? "", title: ManagementText.title(feature),
                      category: feature["category"]?.stringValue, size: size)
    }
}

/// 状态徽标：同时使用图标、文字和颜色，不只靠颜色区分。
struct PluginStatusBadge: View {
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

struct PluginInfoRow: View {
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

/// 「规则与自动化」：只显示插件真正支持的入口（iOS 0.15.0）。规则入口要插件声明规则或该账号已有规则，
/// 配置任务入口要插件声明配置操作或该账号已有任务；都没有时整个分组不显示。
private struct PluginAutomationSection: View {
    let feature: JSONValue
    let accountID: Int
    let segment: String
    @Environment(AppModel.self) private var model
    @State private var ruleCount = 0
    @State private var jobCount = 0

    private var base: String { "/api/accounts/\(accountID)/features/\(segment)" }
    private var showsRules: Bool { PluginAutomation.showsRules(feature, existingRuleCount: ruleCount) }
    private var showsJobs: Bool { PluginAutomation.showsConfigJobs(feature, existingJobCount: jobCount) }
    private var actions: [JSONValue] { PluginAutomation.configActions(feature) }
    private var passthrough: Bool { PluginPresentation.supportsDirectPassthrough(feature) }

    var body: some View {
        Group {
            if showsRules || showsJobs || passthrough || !actions.isEmpty {
                Section {
                    if showsRules {
                        NavigationLink {
                            ManagementCollectionView(resource: .init(title: "自动化规则", path: base + "/rules", kind: .rules, create: .init(title: "添加规则", path: base + "/rules", schema: ManagementRuleSchemas.request(feature: feature)), itemPath: base + "/rules/{id}", ruleSchema: ManagementRuleSchemas.request(feature: feature)))
                        } label: { Label("自动化规则", systemImage: "text.badge.checkmark") }
                        .accessibilityIdentifier("plugin.automation.rules")
                        PluginActionRow(action: .init(title: "复制规则到其他账号", path: base + "/rules/copy", explanation: "把此账号的规则复制到选择的账号。"), systemImage: "doc.on.doc")
                            .accessibilityIdentifier("plugin.automation.copyRules")
                    }
                    if passthrough {
                        PluginActionRow(action: .init(title: "直通开关与优先级", path: base + "/direct-passthrough", method: "PATCH", explanation: "裸直通让插件先于正常处理流程收到原始消息；优先级数字越小越先调用。"), systemImage: "arrow.triangle.branch")
                    }
                    if !actions.isEmpty {
                        NavigationLink { PluginConfigActionsView(feature: feature, accountID: accountID) } label: { Label("试运行与插件操作", systemImage: "play.rectangle") }
                    }
                    if showsJobs {
                        NavigationLink { ManagementCollectionView(resource: .init(title: "配置任务", path: base + "/config/action-jobs", kind: .jobs, idField: "job_id")) } label: {
                            Label("配置任务与进度", systemImage: "clock.arrow.circlepath")
                        }
                        .accessibilityIdentifier("plugin.automation.jobs")
                    }
                } header: { Text("规则与自动化") } footer: {
                    if showsRules { Text("规则决定插件在什么消息上触发、做什么，只对当前账号生效。") }
                }
            }
        }
        .task(id: "\(accountID)/\(segment)") { await loadCounts() }
    }

    /// 不支持规则 / 配置操作的插件才需要查询“已有”数量（旧数据仍可管理）；读取失败按 0 处理，不影响其它入口。
    private func loadCounts() async {
        if !PluginAutomation.supportsRules(feature) {
            ruleCount = PluginAutomation.count(try? await model.request(path: base + "/rules"))
        }
        if PluginAutomation.configActions(feature).isEmpty {
            jobCount = PluginAutomation.count(try? await model.request(path: base + "/config/action-jobs"))
        }
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
    @State private var actionWarning: String?
    @State private var actionError: String?
    @State private var globalBusy = false
    @State private var globalUncertain = false
    @State private var confirmGlobal = false
    @State private var globalMessage: String?
    @State private var globalError: String?
    private var key: String { feature["key"]?.stringValue ?? "" }
    private var segment: String { ManagementText.pathSegment(key) }
    private var isBuiltin: Bool { feature["is_builtin"]?.boolValue == true }
    /// 声明了交互入口（经交互 Bot 启动的玩法）的插件。
    private var isInteractionPlugin: Bool { !(feature["interaction_entries"]?.arrayValue ?? []).isEmpty }

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
    /// 服务器总开关关闭：以安装台账为准；台账缺失时以 Worker 回报的 PLUGIN_DISABLED 判断。
    private var globallyDisabled: Bool {
        globalEnabled == false || PluginPresentation.isGloballyDisabled(currentAccountState?["last_error"]?.stringValue)
    }

    var body: some View {
        List {
            Section {
                PluginDetailHeader(feature: feature, accountState: currentAccountState, accountScoped: accountID != nil, globalEnabled: globalEnabled)
            }
            if accountID != nil {
                accountSwitchSection
                if globallyDisabled { globalDisabledSection }
            }
            // 页面级分段：与插件、AI 页同一规则（原生分段控件、透明零边距行、与卡片左右对齐）。
            Section {
                Picker("插件详情", selection: $section) {
                    ForEach(sections, id: \.self) { item in Text(title(item)).tag(item) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .pageHeaderRow()
            }
            .listSectionSeparator(.hidden)
            switch section {
            case .overview: overview
            case .configuration: configuration
            case .accounts: accounts
            case .maintenance: maintenance
            }
        }
        .navigationTitle(ManagementText.title(feature)).navigationBarTitleDisplayMode(.inline)
        .toolbar { Button(preferences.isFavoritePlugin(key) ? "取消收藏" : "收藏", systemImage: preferences.isFavoritePlugin(key) ? "star.fill" : "star") { preferences.togglePlugin(key) } }
        .task {
            await loadInstallRecord()
            // 从交互规则「插件配置」跳来时没有带账号状态：补读一次，账号开关与运行状态才真实。
            if accountID != nil && currentAccountState == nil { await reloadAccountState() }
        }
        .refreshable { await loadInstallRecord(); if accountID != nil { await reloadAccountState() } }
    }

    // MARK: 账号开关（仅账号作用域）

    private var accountEnabled: Bool { currentAccountState?["enabled"]?.boolValue == true }
    private var accountSwitchSection: some View {
        Section {
            LabeledContent {
                if accountEnabled && globallyDisabled {
                    Text("已开启 · 未生效").foregroundStyle(Color.orange)
                } else {
                    Text(accountEnabled ? "已启用" : "未启用").foregroundStyle(accountEnabled ? Color.green : Color.secondary)
                }
            } label: { Label("在此账号中", systemImage: accountEnabled ? "power.circle.fill" : "power.circle") }
            Button(role: accountEnabled ? .destructive : nil) { confirmToggle = true } label: {
                Label(accountEnabled ? "在此账号停用该插件" : "在此账号启用该插件", systemImage: accountEnabled ? "pause.circle" : "play.circle")
                    .frame(minHeight: 44)
            }
            .disabled(toggling || toggleUncertain)
            .accessibilityIdentifier("plugin.account.toggle")
            .confirmationDialog(accountEnabled ? "在此账号停用该插件？" : "在此账号启用该插件？", isPresented: $confirmToggle, titleVisibility: .visible) {
                Button(accountEnabled ? "确认停用" : "确认启用", role: accountEnabled ? .destructive : nil) { Task { await toggleAccount() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text(accountEnabled ? "此账号将不再运行该插件，配置和规则会保留。其他账号与服务器安装不受影响。" : "此账号将按自己的配置和规则运行该插件。其他账号不受影响。")
            }
            if toggling { ProgressView("正在提交并刷新状态…") }
            if let actionMessage { Label(actionMessage, systemImage: "checkmark.circle").foregroundStyle(.green) }
            if let actionWarning { Label(actionWarning, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
            if let actionError { NoticeView(title: toggleUncertain ? "结果待确认" : "操作未完成", message: actionError) }
        } header: { Text("此账号的插件开关") } footer: {
            Text(toggleUncertain
                 ? "网络中断，无法确认是否已生效。请下拉刷新，确认实际状态后再操作。"
                 : accountEnabled
                    ? "停用后此账号不再运行该插件，配置和规则都会保留，随时可以重新启用。不影响其他账号，也不会卸载插件。"
                    : "启用后此账号会按自己的配置和规则运行该插件。不影响其他账号。")
        }
    }

    /// 账号作用域下服务器总开关关闭时的提示与直达“全局启用”入口（复用全局总开关的确认框与提交逻辑）。
    private var globalDisabledSection: some View {
        Section {
            NoticeView(title: "插件已全局停用，账号开关不会生效",
                       message: "服务器总开关处于关闭状态，所有账号都不会运行此插件。全局启用后，已在账号中开启的账号会开始运行它。",
                       symbol: "pause.circle")
            Button { confirmGlobal = true } label: {
                Label("全局启用插件", systemImage: "play.circle").frame(minHeight: 44)
            }
            .disabled(globalBusy || globalUncertain)
            .accessibilityIdentifier("plugin.account.globalEnable")
            .confirmationDialog("全局启用此插件？", isPresented: $confirmGlobal, titleVisibility: .visible) {
                Button("确认全局启用") { Task { await toggleGlobal() } }
                Button("取消", role: .cancel) {}
            } message: {
                Text("启用后，已在账号中开启此插件的账号会重新开始运行它。会影响所有账号。")
            }
            if globalBusy { ProgressView("正在提交并刷新状态…") }
            if let globalError { NoticeView(title: globalUncertain ? "结果待确认" : "操作未完成", message: globalError) }
        } header: { Text("服务器总开关") } footer: {
            Text(globalUncertain
                 ? "网络中断，无法确认是否已生效。请下拉刷新，确认实际状态后再操作。"
                 : "总开关在「服务器安装与维护」中也可以管理。")
        }
    }

    // MARK: 概况

    @ViewBuilder private var overview: some View {
        // plugin.overview：宿主「运行统计」卡（卡片 DSL 的第一个用户）+ 插件自己声明的原生卡片。
        PluginCardSections(slot: "plugin.overview", pluginKey: key, accountID: accountID)
        // 插件自带页面与菜单项（ui.pages / ui.actions，Plugin API 1.4）；没有声明时不显示。
        PluginPageEntriesSection(menu: "plugin.detail", pluginKey: key, accountID: accountID)
        // 交互类插件（声明了交互入口）：直达交互中心里启动它的规则。交互模块关闭时不显示（与 Web 隐藏入口一致）。
        if isInteractionPlugin && model.isModuleEnabled("interaction_bot") {
            Section {
                NavigationLink {
                    InteractionCenterView(initialAccountID: accountID, focusPluginKey: key)
                } label: {
                    Label("交互规则", systemImage: "bubble.left.and.text.bubble.right")
                }
                .accessibilityIdentifier("plugin.detail.interactionRules")
            } footer: {
                Text("群里发什么关键词、转多少游戏币会启动这个插件，在「交互」的规则里设置。")
            }
        }
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
                    Label(PluginPresentation.errorText(error), systemImage: PluginPresentation.isGloballyDisabled(error) ? "pause.circle" : "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(PluginPresentation.isGloballyDisabled(error) ? Color.orange : Color.red).textSelection(.enabled)
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
            PluginAutomationSection(feature: feature, accountID: accountID, segment: segment)
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
                .confirmationDialog(globalEnabled == true ? "全局停用此插件？" : "全局启用此插件？", isPresented: $confirmGlobal, titleVisibility: .visible) {
                    Button(globalEnabled == true ? "确认全局停用" : "确认全局启用", role: globalEnabled == true ? .destructive : nil) { Task { await toggleGlobal() } }
                    Button("取消", role: .cancel) {}
                } message: {
                    Text(globalEnabled == true ? "停用后所有账号都不会运行此插件；各账号的开关、配置和规则会保留，重新启用即可恢复。" : "启用后，已在账号中开启此插件的账号会重新开始运行它。")
                }
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
        actionWarning = nil
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
            else if globallyDisabled { actionWarning = "已在此账号开启，但插件处于全局停用状态，需全局启用后才会运行。" }
            else { actionMessage = currentAccountState?["state"]?.stringValue == "active" ? "已在此账号启用，Worker 正在运行。" : "已提交启用，请留意运行状态。" }
        } catch {
            actionMessage = "已提交，但重新读取状态失败。请下拉刷新确认。"
        }
    }

    private func toggleGlobal() async {
        guard let enabled = globalEnabled ?? (globallyDisabled ? false : nil), !globalBusy, !globalUncertain else { return }
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
        if accountID != nil {
            await reloadAccountState()
            actionWarning = nil
            actionMessage = enabled ? nil : (currentAccountState?["state"]?.stringValue == "active" ? "已全局启用，此账号的 Worker 正在运行。" : "已全局启用，请留意此账号的运行状态。")
        }
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
                if PluginPresentation.isGloballyDisabled(error) {
                    Label(PluginPresentation.errorText(error), systemImage: "pause.circle").font(.footnote).foregroundStyle(.orange).lineLimit(3)
                } else {
                    Label(error, systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.red).lineLimit(3)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - 完整技术信息（全局与账号详情共用）

struct PluginTechnicalInfoView: View {
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
            } header: { Text("版本更新") } footer: { Text("只检查不会自动更新；更新需要在「插件 → 已安装」中手动执行。") }

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
                        Label(PluginPresentation.errorText(error), systemImage: PluginPresentation.isGloballyDisabled(error) ? "pause.circle" : "exclamationmark.triangle")
                            .font(.footnote).foregroundStyle(PluginPresentation.isGloballyDisabled(error) ? Color.orange : Color.red).textSelection(.enabled)
                        if PluginPresentation.isGloballyDisabled(error) {
                            Text(error).font(.caption2.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                        }
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
    /// 契约控件 id（schemas/plugin-ui-widgets.json），与 Web resolveConfigWidget 一致。
    var widget: String { PluginFieldWidget.resolve(key: key, spec: spec) }
    /// 密钥列表（x-secret-list）本身是遮罩后的条目列表，不按单个密钥处理。
    var secret: Bool {
        widget != "secret-list" && (ManagementText.secret(key) || spec["format"]?.stringValue == "password" || spec["writeOnly"]?.boolValue == true)
    }
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
        if PluginSelectWidget(spec: spec) != nil { return true }
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
    /// 顺序与 Web 相同：按插件声明顺序（`x-ui-property-order`），再按 `x-ui-order`；不按标题或字母排序。
    static func fields(feature: JSONValue, global: Bool) -> [PluginConfigField] {
        guard let schema = feature["config_schema"], let properties = schema["properties"]?.objectValue else { return [] }
        // 规则模式（契约 2）且没有 x-rule-schema：config_schema 的字段都是规则字段，在「自动化规则」里编辑，不再当账号配置。
        if PluginRuleForm.configFieldsAreRuleFields(schema) { return [] }
        let required = Set(schema["required"]?.arrayValue?.compactMap(\.stringValue) ?? [])
        return PluginConfigLayout.orderedKeys(schema: schema).compactMap { key -> PluginConfigField? in
            guard let spec = properties[key], !isGuide(key) else { return nil }
            guard (spec["level"]?.stringValue == "global") == global else { return nil }
            return PluginConfigField(key: key, spec: spec, required: required.contains(key))
        }
    }
    /// 按 `x-ui-section` 分组（与 Web 相同：分组 → 未分组 → 消息模板），组内保持 `fields` 的顺序。
    static func groups(feature: JSONValue, fields: [PluginConfigField]) -> [PluginConfigFieldGroup] {
        let schema = feature["config_schema"] ?? .object([:])
        let byKey = Dictionary(fields.map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first })
        return PluginConfigLayout.groups(schema: schema, orderedKeys: fields.map(\.key)).enumerated().map { index, group in
            PluginConfigFieldGroup(id: "\(index)-\(group.title ?? "")", title: group.title, fields: group.keys.compactMap { byKey[$0] })
        }
    }
}

private struct PluginConfigFieldGroup: Identifiable {
    let id: String
    let title: String?
    let fields: [PluginConfigField]
    var core: PluginConfigLayout.FieldGroup { .init(title: title, keys: fields.map(\.key)) }
}

/// 长配置页的拆分：`inline` 留在主页，`pages` 各自成为一行进入子页面（规则见 `PluginConfigLayout.pagePlan`）。
private struct PluginConfigPagePlan {
    let inline: [PluginConfigFieldGroup]
    let pages: [PluginConfigFieldGroup]
}

extension PluginConfigSchema {
    static func pagePlan(feature: JSONValue, groups: [PluginConfigFieldGroup]) -> PluginConfigPagePlan {
        let plan = PluginConfigLayout.pagePlan(schema: feature["config_schema"] ?? .object([:]), groups: groups.map(\.core))
        guard !plan.pages.isEmpty else { return PluginConfigPagePlan(inline: groups, pages: []) }
        return PluginConfigPagePlan(inline: groups.filter { plan.inline.contains($0.core) },
                                    pages: groups.filter { plan.pages.contains($0.core) })
    }
    /// 子页面入口的图标，按分组标题里的关键词选择。
    static func symbol(forGroup title: String) -> String {
        let rules: [(String, String)] = [
            ("模板", "text.bubble"), ("触发", "bolt"), ("关键词", "text.magnifyingglass"), ("模型", "cpu"),
            ("限额", "gauge.with.dots.needle.33percent"), ("使用者", "person.2"), ("护栏", "shield.lefthalf.filled"),
            ("安全", "shield.lefthalf.filled"), ("PT", "server.rack"), ("数据源", "server.rack"), ("站点", "server.rack"),
            ("时限", "timer"), ("超时", "timer"), ("记忆", "brain"), ("回复", "arrowshape.turn.up.left"),
            ("Skill", "puzzlepiece.extension"), ("题库", "books.vertical"), ("红包", "gift"), ("AI", "sparkles")
        ]
        return rules.first { title.localizedCaseInsensitiveContains($0.0) }?.1 ?? "slider.horizontal.3"
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
    @State private var llmProviders: [JSONValue]?
    @State private var llmLoading = false
    @State private var llmError: String?
    @State private var confirmingDiscard = false
    @State private var commandPrefix = ","
    @Environment(\.dismiss) private var dismiss

    private var global: Bool { accountID == nil }
    /// 模板字段的 Telegram 预览上下文：示例值取自当前草稿（指令名、超时等），与 Web ConfigDialog 相同。
    private var telegramContext: TelegramPreviewContext {
        var config = snapshot?.values ?? [:]
        for (key, value) in draft.objectValue ?? [:] { config[key] = value }
        return TelegramPreviewContext(kind: .plugin(featureKey: feature["key"]?.stringValue ?? ""), config: config, prefix: commandPrefix)
    }
    /// `*_preview` / `template_preview` 字段：按模板顺序显示一组消息气泡（Web「预览结果」）。
    private var previewItems: [TelegramPreviewThreadSection.Item] {
        guard let properties = feature["config_schema"]?["properties"]?.objectValue else { return [] }
        let keys = Set(properties.keys)
        let context = telegramContext
        let samples = TelegramTemplateSamples.plugin(config: context.config, prefix: commandPrefix)
        return PluginConfigLayout.orderedKeys(schema: feature["config_schema"] ?? .object([:])).compactMap { (key: String) -> TelegramPreviewThreadSection.Item? in
            guard key == "template_preview" || key.lowercased().hasSuffix("_preview"), key != "usage_preview",
                  let previewSpec = properties[key] else { return nil }
            let templateKey = TelegramTemplateSamples.templateKey(forPreview: key, keys: keys)
            let templateValue = templateKey.flatMap { draft[$0] ?? snapshot?.values[$0] ?? properties[$0]?["default"] } ?? previewSpec["default"]
            let template = TelegramTemplate.normalizeEscapes(TelegramTemplate.format(templateValue))
            guard !template.isEmpty else { return nil }
            let title = previewSpec["title"]?.stringValue ?? key
            return TelegramPreviewThreadSection.Item(id: key, title: title, model: TelegramMessagePreviewModel(template: template, values: samples))
        }
    }
    /// 有未保存的修改（含子页面里改的）：离开前需要确认。
    private var hasUnsavedChanges: Bool { snapshot != nil && !changes.isEmpty && !saving }
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
                    let plan = PluginConfigSchema.pagePlan(feature: feature,
                                                           groups: PluginConfigSchema.groups(feature: feature, fields: fields.filter { !$0.hidden }))
                    ForEach(plan.inline) { group in
                        ForEach(Array(group.fields.enumerated()), id: \.element.id) { index, field in
                            fieldSection(field, groupTitle: index == 0 ? group.title : nil)
                        }
                    }
                    let previews = previewItems
                    if !previews.isEmpty {
                        TelegramPreviewThreadSection(title: "预览结果", items: previews, prompt: "发送指令并参与竞猜。", identifier: "plugin.config.previews")
                    }
                    if !plan.pages.isEmpty {
                        Section {
                            ForEach(plan.pages) { group in pageRow(group) }
                        } header: { Text("更多设置") } footer: {
                            Text("各分组的修改都暂存在这次编辑里，回到本页点“检查修改”一次保存。")
                        }
                    }
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
        // 可选字段只显示一个控件（不渲染“是否提交此项”开关），留空由服务器按默认值处理。
        .environment(\.managementOptionalFieldsInline, true)
        .environment(\.telegramPreviewContext, telegramContext)
        .navigationTitle(global ? "修改全局配置" : "修改账号配置").navigationBarTitleDisplayMode(.inline)
        // 有未保存的修改时用自己的返回按钮（同时停用滑动返回），离开前确认是否放弃。
        .navigationBarBackButtonHidden(hasUnsavedChanges)
        .toolbar {
            if hasUnsavedChanges {
                ToolbarItem(placement: .topBarLeading) {
                    Button("返回", systemImage: "chevron.backward") { confirmingDiscard = true }
                        .accessibilityIdentifier("plugin.config.back")
                        .confirmationDialog("放弃未保存的修改？", isPresented: $confirmingDiscard, titleVisibility: .visible) {
                            Button("放弃修改", role: .destructive) { dismiss() }
                            Button("继续编辑", role: .cancel) {}
                        } message: { Text("还有 \(changes.count) 项修改没有保存，离开后会丢失。") }
                }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("检查修改") { beginReview() }.disabled(snapshot == nil || changes.isEmpty || saving || uncertain)
                    .accessibilityIdentifier("plugin.config.toolbarReview")
            }
        }
        // 只在首次进入时读取：从嵌套的列表/分组编辑页返回时不能覆盖尚未保存的修改。
        .task { if snapshot == nil { await load() } }
        .task {
            if let settings = try? await model.request(path: "/api/system/settings"),
               let prefix = settings["command_prefix"]?.stringValue, !prefix.isEmpty { commandPrefix = prefix }
        }
        .sheet(isPresented: $reviewing) { reviewSheet }
    }

    private func isChanged(_ field: PluginConfigField) -> Bool {
        field.secret ? !(draft[field.key]?.stringValue ?? "").isEmpty : !PluginConfigFormat.same(baseline[field.key], draft[field.key])
    }
    /// 当前草稿里此项是否为空（敏感字段：服务器上也没有保存过）。
    private func isEmptyValue(_ field: PluginConfigField) -> Bool {
        if field.secret {
            return (draft[field.key]?.stringValue ?? "").isEmpty
                && PluginConfigFormat.text(snapshot?.values[field.key], secret: true).hasPrefix("未")
        }
        guard let value = draft[field.key], !value.isNull else { return true }
        if let text = value.stringValue { return text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return false
    }
    /// 此项是否已配置为不同于插件默认值的内容。
    private func isConfigured(_ field: PluginConfigField) -> Bool {
        if field.secret { return !isEmptyValue(field) }
        guard let value = draft[field.key], !value.isNull else { return false }
        if let fallback = field.spec["default"] { return !PluginConfigFormat.same(value, fallback) }
        if let text = value.stringValue { return !text.isEmpty }
        return true
    }
    private func pageSummary(_ group: PluginConfigFieldGroup) -> (text: String, missingRequired: Bool, changed: Int) {
        let editable = group.fields.filter { !$0.readOnly }
        let changed = editable.filter { isChanged($0) }.count
        let missing = editable.contains { $0.required && isEmptyValue($0) }
        var parts: [String] = []
        if missing { parts.append("有必填项未填") }
        if let toggle = editable.first(where: { ManagementSchema.isPlainBoolean($0.spec) && ($0.key == "enabled" || $0.key.hasSuffix("_enabled")) }) {
            parts.append(draft[toggle.key]?.boolValue == true ? "已启用" : "未启用")
        }
        let configured = editable.filter { isConfigured($0) }.count
        parts.append(configured > 0 ? "已配置 \(configured) 项" : "使用默认值")
        parts.append("共 \(editable.count) 项")
        return (parts.joined(separator: " · "), missing, changed)
    }

    /// 子页面入口：显示分组名与摘要；子页面直接编辑同一份草稿，回到主页统一检查并保存。
    @ViewBuilder private func pageRow(_ group: PluginConfigFieldGroup) -> some View {
        let title = group.title ?? "其他设置"
        let summary = pageSummary(group)
        NavigationLink {
            Form {
                Section {
                    Label("这里的修改暂存在本次编辑中，返回上一页后点“检查修改”统一保存。", systemImage: "info.circle")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                ForEach(group.fields) { field in fieldSection(field) }
            }
            .environment(\.managementOptionalFieldsInline, true)
            .environment(\.telegramPreviewContext, telegramContext)
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        } label: {
            HStack(spacing: 12) {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title)
                        Text(summary.text).font(.caption).foregroundStyle(.secondary)
                    }
                } icon: { Image(systemName: PluginConfigSchema.symbol(forGroup: title)) }
                Spacer(minLength: 8)
                if summary.missingRequired {
                    Image(systemName: "circle.fill").font(.caption2).foregroundStyle(.red)
                        .accessibilityLabel("有必填项未填")
                }
                if summary.changed > 0 {
                    Image(systemName: "pencil.circle.fill").foregroundStyle(.orange)
                        .accessibilityLabel("已修改 \(summary.changed) 项")
                }
            }
        }
        .accessibilityIdentifier("plugin.config.page.\(title)")
    }

    /// 字段级 `x-ui-visible-if`（插件 UI 契约 2）：按草稿里同级字段的当前值显示 / 隐藏。
    @ViewBuilder private func fieldSection(_ field: PluginConfigField, groupTitle: String? = nil) -> some View {
        if PluginRuleForm.isVisible(field.spec, values: draft) {
            visibleFieldSection(field, groupTitle: groupTitle)
        }
    }

    @ViewBuilder private func visibleFieldSection(_ field: PluginConfigField, groupTitle: String? = nil) -> some View {
        let changed = isChanged(field)
        Section {
            if field.readOnly {
                LabeledContent {
                    Text(PluginConfigFormat.text(snapshot?.values[field.key], secret: field.secret)).foregroundStyle(.secondary)
                } label: { Label(field.label, systemImage: field.symbol) }
            } else if !field.secret, PluginFieldWidgetView.handles(field.widget) {
                PluginFieldWidgetView(widget: field.widget, title: field.label, key: field.key, spec: field.spec,
                                      value: binding(field.key), accountID: accountID)
            } else if !field.secret, AgentSkillMultiSelectField.matches(field.spec) {
                AgentSkillMultiSelectField(title: field.label, value: binding(field.key))
            } else if !field.secret, let widget = PluginSelectWidget(spec: field.spec) {
                PluginSelectWidgetField(widget: widget, title: field.label, value: binding(field.key), draft: draft,
                                        stored: snapshot?.values ?? [:], providers: llmProviders, loading: llmLoading, loadError: llmError,
                                        editableKeys: Set(fields.filter { !$0.readOnly }.map(\.key)), setField: set)
            } else {
                ManagementFieldView(title: field.secret ? "留空则保持原值" : field.label, key: field.key, schema: field.spec, value: binding(field.key))
                if ManagementSchema.allowsNull(field.spec), let value = draft[field.key], !value.isNull, !field.secret {
                    Button("清空此项", systemImage: "xmark.circle", role: .destructive) { set(field.key, .null) }
                }
            }
        } header: {
            VStack(alignment: .leading, spacing: 8) {
                // 分组标题（x-ui-section）挂在组内第一项上方，与 Web 的分组卡片对应。
                if let groupTitle {
                    Text(groupTitle).font(.headline).foregroundStyle(.primary).textCase(nil)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("plugin.config.group.\(groupTitle)")
                }
                if !field.titledInline || changed || field.required {
                    HStack {
                        if !field.titledInline { Text(field.label) }
                        // 只给 schema required 里的字段标“必填”；可选字段留空时使用插件默认值。
                        if field.required { Text("必填").accessibilityIdentifier("plugin.config.required.\(field.key)") }
                        Spacer()
                        if changed { Label("已修改", systemImage: "pencil.circle.fill").foregroundStyle(.orange) }
                    }
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

    /// 与 Web 插件配置页一致：从 `/api/commands/llm-providers` 读取 TelePilot AI Provider 及其模型。
    private func loadProviders() async {
        guard !llmLoading else { return }
        llmLoading = true
        defer { llmLoading = false }
        do {
            llmProviders = try await model.request(path: "/api/commands/llm-providers").arrayValue ?? []
            llmError = nil
        } catch {
            llmError = error.localizedDescription
        }
    }

    private func load() async {
        do {
            let loaded = try await PluginConfigLoader.load(model: model, feature: feature, accountID: accountID)
            if llmProviders == nil, fields.contains(where: { PluginSelectWidget(spec: $0.spec)?.needsProviders == true }) {
                Task { await loadProviders() }
            }
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

// MARK: - 插件配置的选择类控件（x-ui-widget）

// PluginSelectWidget / PluginSelectOptions 位于 TelePilotCore（便于用真实插件 manifest 做单元测试）。

/// 原生 Picker 呈现插件的 Provider / 模型 / 动态选项字段；保留不在候选里的旧值。
private struct PluginSelectWidgetField: View {
    let widget: PluginSelectWidget
    let title: String
    @Binding var value: JSONValue
    let draft: JSONValue
    let stored: [String: JSONValue]
    let providers: [JSONValue]?
    let loading: Bool
    let loadError: String?
    /// 当前表单可写的字段：Provider 字段在另一作用域（全局/账号）时不跨域写入。
    let editableKeys: Set<String>
    let setField: (String, JSONValue) -> Void

    var body: some View {
        if case .llmModel(let providerField, let modality) = widget {
            unifiedModelPicker(providerField: providerField, modality: modality)
        } else {
            plainPicker
        }
    }

    /// 统一模型列表：所有 Provider 下已启用的模型按 Provider 分组；选中时同时写回 Provider 字段（与 Web 一致）。
    @ViewBuilder private func unifiedModelPicker(providerField: String?, modality: String?) -> some View {
        let providerValue = providerField.flatMap { draft[$0] ?? stored[$0] }
        let selection = PluginSelectOptions.unifiedModelSelection(providers, providerValue: providerValue, modelValue: value, modality: modality)
        let waiting = loading && providers == nil
        Picker(selection: Binding(get: { selection.selectedKey }, set: { key in
            guard let next = selection.resolve(key) else { return }
            value = .string(next.model)
            if let providerField, editableKeys.contains(providerField) { setField(providerField, .string(next.provider)) }
        })) {
            Text(waiting ? "正在加载供应商…" : PluginSelectOptions.autoRouteLabel).tag(PluginSelectOptions.autoRouteKey)
            // 用序号作 id：不同 Provider 可能同名。
            ForEach(Array(selection.groups.enumerated()), id: \.offset) { _, group in
                Section(group.group) {
                    ForEach(group.options, id: \.key) { option in Text(option.label).tag(option.key) }
                }
            }
            if let legacy = selection.legacy { Text(legacy.label).tag(legacy.key) }
        } label: { Text(title) }
        .pickerStyle(.menu)
        .disabled(waiting)
        .accessibilityIdentifier("plugin.config.select." + title)
        if let loadError, providers == nil {
            Text("供应商列表读取失败：\(loadError)").font(.footnote).foregroundStyle(.secondary)
        } else if !waiting, selection.options.isEmpty {
            Text("还没有已启用的模型；请先在 AI 页添加供应商并启用模型。").font(.footnote).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var plainPicker: some View {
        let selected = PluginSelectOptions.text(value)
        let content = choices()
        Picker(selection: Binding(get: { selected }, set: { value = .string($0) })) {
            Text(content.empty).tag("")
            ForEach(content.options, id: \.value) { option in Text(option.label).tag(option.value) }
            if !selected.isEmpty, !content.options.contains(where: { $0.value == selected }) {
                Text("当前值：\(selected)").tag(selected)
            }
        } label: { Text(title) }
        .pickerStyle(.menu)
        .disabled(content.disabled && selected.isEmpty)
        .accessibilityIdentifier("plugin.config.select." + title)
        if let note = content.note { Text(note).font(.footnote).foregroundStyle(.secondary) }
    }

    private func choices() -> (options: [PluginSelectOption], empty: String, disabled: Bool, note: String?) {
        switch widget {
        case .llmProvider:
            if loading && providers == nil { return ([], "正在加载供应商…", true, nil) }
            if let loadError, providers == nil { return ([], "自动路由", false, "供应商列表读取失败：\(loadError)") }
            let options = PluginSelectOptions.providerOptions(providers ?? [])
            return (options, "自动路由", false, options.isEmpty ? "尚未配置 AI 供应商；请先在 AI 页添加。"
                                                              : "随模型自动填写；只想固定供应商、使用其默认模型时才需要单独选择。")
        case .llmModel:
            // 由 unifiedModelPicker 渲染，不会走到这里。
            return ([], PluginSelectOptions.autoRouteLabel, false, nil)
        case .dynamic(let optionsField):
            let source = optionsField.flatMap { draft[$0] ?? stored[$0] }
            let options = PluginSelectOptions.dynamicOptions(source)
            return (options, "未设置", false, options.isEmpty ? "暂无可选项。" : nil)
        }
    }
}

// MARK: - 插件配置控件契约（schemas/plugin-ui-widgets.json，iOS 0.14.0 补齐）

/// 契约里由 App 专门实现、不走通用表单的控件：config-list、allowed-peer-multi-select、multi-select、
/// list-select、secret-list，以及不认识的控件（只读并提示更新 App）。
struct PluginFieldWidgetView: View {
    static let handled: Set<String> = [PluginFieldWidget.unknown, "config-list", "allowed-peer-multi-select", "multi-select", "list-select", "secret-list"]
    static func handles(_ widget: String) -> Bool { handled.contains(widget) }

    let widget: String
    let title: String
    let key: String
    let spec: JSONValue
    @Binding var value: JSONValue
    var accountID: Int?

    var body: some View {
        switch widget {
        case "config-list": PluginConfigListField(title: title, spec: spec, value: $value)
        case "allowed-peer-multi-select": PluginAllowedPeerField(title: title, value: $value, accountID: accountID)
        case "multi-select": PluginMultiSelectField(title: title, spec: spec, value: $value)
        case "list-select": PluginListSelectField(title: title, spec: spec, value: $value)
        case "secret-list": PluginSecretListField(title: title, spec: spec, value: $value)
        default: PluginUnknownWidgetRow(title: title, widget: spec["x-ui-widget"]?.stringValue ?? "", value: value)
        }
    }
}

/// 不认识的控件：只读显示当前值并提示更新 App（版本要求来自服务器契约）。
private struct PluginUnknownWidgetRow: View {
    let title: String
    let widget: String
    let value: JSONValue
    @Environment(AppModel.self) private var model
    @State private var required: String?

    var body: some View {
        LabeledContent {
            Text(PluginConfigFormat.text(value, secret: false)).foregroundStyle(.secondary).multilineTextAlignment(.trailing)
        } label: { Label(title, systemImage: "lock") }
        Label(PluginUIContract.unknownWidgetMessage + (required.map { "（需 iOS \($0)）" } ?? ""), systemImage: "arrow.down.app")
            .font(.footnote).foregroundStyle(.orange)
            .accessibilityIdentifier("plugin.config.unknownWidget")
            .task {
                guard required == nil, !widget.isEmpty else { return }
                let contract = try? await model.request(path: "/api/plugins/ui-contract")
                required = PluginUIContract.requiredIOSVersion(widget: widget, serverContract: contract)
            }
        Text("控件「\(widget)」由插件声明，这个版本的 App 只能查看、不能修改，避免把数据改坏。可在 Web 管理台修改。")
            .font(.caption).foregroundStyle(.secondary)
    }
}

private enum PluginChoice {
    /// 选项与显示名：enum + enumNames（数组字段取 items 上的声明）。
    static func options(_ raw: JSONValue, array: Bool) -> [(value: JSONValue, label: String, detail: String?)] {
        let spec = ManagementSchema.normalized(raw)
        let source = array ? ManagementSchema.normalized(spec["items"] ?? .object([:])) : spec
        let choices = source["enum"]?.arrayValue ?? spec["enum"]?.arrayValue ?? []
        let names = (source["enumNames"]?.arrayValue ?? spec["enumNames"]?.arrayValue ?? []).compactMap(\.stringValue)
        let details = (source["enumDescriptions"]?.arrayValue ?? spec["enumDescriptions"]?.arrayValue ?? []).compactMap(\.stringValue)
        return choices.enumerated().map { index, choice in
            (choice, names.indices.contains(index) && !names[index].isEmpty ? names[index] : ManagementText.scalar(choice),
             details.indices.contains(index) && !details[index].isEmpty ? details[index] : nil)
        }
    }
}

/// multi-select：固定选项多选（系统 Toggle 行）。
private struct PluginMultiSelectField: View {
    let title: String
    let spec: JSONValue
    @Binding var value: JSONValue

    var body: some View {
        let options = PluginChoice.options(spec, array: true)
        let selected = value.arrayValue ?? []
        NavigationLink {
            List {
                Section {
                    ForEach(Array(options.enumerated()), id: \.offset) { _, option in
                        Toggle(option.label, isOn: Binding(get: { selected.contains(option.value) }, set: { on in
                            var next = (value.arrayValue ?? []).filter { $0 != option.value }
                            if on {
                                // 保持声明顺序，方便与 Web 比对。
                                next.append(option.value)
                                let order = options.map { $0.value }
                                next.sort { (order.firstIndex(of: $0) ?? .max) < (order.firstIndex(of: $1) ?? .max) }
                            }
                            value = .array(next)
                        }))
                    }
                    ForEach(Array(selected.filter { item in !options.contains { $0.value == item } }.enumerated()), id: \.offset) { _, stale in
                        Toggle("当前值：\(ManagementText.scalar(stale))", isOn: Binding(get: { true }, set: { on in
                            if !on { value = .array((value.arrayValue ?? []).filter { $0 != stale }) }
                        }))
                    }
                } footer: { Text("可以选择多项；保存前会列出修改。") }
            }
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        } label: {
            LabeledContent { Text(selected.isEmpty ? "未选择" : "已选 \(selected.count) 项") } label: { Label(title, systemImage: "checklist") }
        }
        .accessibilityIdentifier("plugin.config.multiSelect." + title)
    }
}

/// list-select：单选，选项平铺并显示说明（原生 inline Picker）。
private struct PluginListSelectField: View {
    let title: String
    let spec: JSONValue
    @Binding var value: JSONValue

    var body: some View {
        let options = PluginChoice.options(spec, array: false)
        Picker(title, selection: $value) {
            if value.isNull { Text("未选择").tag(JSONValue.null) }
            ForEach(Array(options.enumerated()), id: \.offset) { _, option in
                VStack(alignment: .leading, spacing: 2) {
                    Text(option.label)
                    if let detail = option.detail { Text(detail).font(.caption).foregroundStyle(.secondary) }
                }
                .tag(option.value)
            }
            if !value.isNull, !options.contains(where: { $0.value == value }) {
                Text("当前值：\(ManagementText.scalar(value))").tag(value)
            }
        }
        .pickerStyle(.inline)
        .accessibilityIdentifier("plugin.config.listSelect." + title)
    }
}

/// allowed-peer-multi-select：从账号「允许会话」里多选。
private struct PluginAllowedPeerField: View {
    let title: String
    @Binding var value: JSONValue
    var accountID: Int?

    var body: some View {
        let selected = value.arrayValue ?? []
        NavigationLink {
            PluginAllowedPeerPicker(title: title, value: $value, accountID: accountID)
        } label: {
            LabeledContent { Text(selected.isEmpty ? "未选择" : "已选 \(selected.count) 个会话") } label: { Label(title, systemImage: "checklist") }
        }
        .accessibilityIdentifier("plugin.config.allowedPeers." + title)
    }
}

private struct PluginAllowedPeerPicker: View {
    let title: String
    @Binding var value: JSONValue
    var accountID: Int?
    @Environment(AppModel.self) private var model
    @State private var peers: [JSONValue]?
    @State private var error: String?

    private func ids(_ raw: JSONValue) -> [Int64] {
        (raw.arrayValue ?? []).compactMap { item in
            if case .integer(let id) = item { return id }
            if let double = item.doubleValue { return Int64(double) }
            return item.stringValue.flatMap { Int64($0.trimmingCharacters(in: .whitespaces)) }
        }
    }

    var body: some View {
        let selected = ids(value)
        List {
            if accountID == nil {
                Section {
                    Label("全局配置不属于某个账号，不能从允许会话里选择；请在账号配置里修改。", systemImage: "info.circle")
                        .font(.subheadline).foregroundStyle(.secondary)
                    ForEach(selected, id: \.self) { id in Text(String(id)).monospacedDigit() }
                }
            } else if let error, peers == nil {
                Section { NoticeView(title: "允许会话读取失败", message: error) }
            } else if let peers {
                Section {
                    if peers.isEmpty { Text("这个账号的「允许会话」为空（全部会话都允许）。先在账号的会话白名单里添加会话。").foregroundStyle(.secondary) }
                    ForEach(Array(peers.enumerated()), id: \.offset) { _, peer in
                        let id = Int64(peer["peer_id"]?.intValue ?? 0)
                        let label = peer["peer_label"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } ?? "会话"
                        Toggle(isOn: Binding(get: { selected.contains(id) }, set: { on in
                            var next = selected.filter { $0 != id }
                            if on { next.append(id) }
                            value = .array(next.map { .integer($0) })
                        })) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(label)
                                Text(String(id)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                            }
                        }
                    }
                    ForEach(selected.filter { id in !peers.contains { Int64($0["peer_id"]?.intValue ?? 0) == id } }, id: \.self) { id in
                        Toggle("\(id)（已不在允许会话里）", isOn: Binding(get: { true }, set: { on in
                            if !on { value = .array(selected.filter { $0 != id }.map { .integer($0) }) }
                        }))
                    }
                } footer: { Text("只能从账号的「允许会话」里选择。") }
            } else {
                Section { ProgressView("正在读取允许会话…") }
            }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .task {
            guard let accountID, peers == nil else { return }
            do { peers = try await model.request(path: "/api/accounts/\(accountID)/ignored-peers").arrayValue ?? []; error = nil }
            catch { self.error = error.localizedDescription }
        }
    }
}

/// config-list：对象列表，每项一行摘要，点开编辑；可增删、排序。
private struct PluginConfigListField: View {
    let title: String
    let spec: JSONValue
    @Binding var value: JSONValue

    var body: some View {
        let count = value.arrayValue?.count ?? 0
        NavigationLink {
            PluginConfigListEditor(title: title, spec: spec, value: $value)
        } label: {
            LabeledContent { Text(count == 0 ? "空" : "\(count) 项") } label: { Label(title, systemImage: "list.bullet.rectangle") }
        }
        .accessibilityIdentifier("plugin.config.configList." + title)
    }
}

private struct PluginConfigListEditor: View {
    let title: String
    let spec: JSONValue
    @Binding var value: JSONValue
    @Environment(\.managementOptionalFieldsInline) private var optionalInline

    private var schema: JSONValue { ManagementSchema.normalized(spec) }
    private var itemSchema: JSONValue { ManagementSchema.normalized(schema["items"] ?? .object(["type": .string("object")])) }
    private var enabledField: String {
        if let field = schema["x-ui-enabled-field"]?.stringValue, !field.isEmpty { return field }
        return itemSchema["properties"]?["enabled"] != nil ? "enabled" : ""
    }
    private var reorderable: Bool { schema["x-ui-reorderable"]?.boolValue != false }
    private var maxItems: Int { schema["maxItems"]?.intValue ?? .max }
    private var minItems: Int { schema["minItems"]?.intValue ?? 0 }

    private func itemTitle(_ item: JSONValue, index: Int) -> String {
        if let field = schema["x-ui-title-field"]?.stringValue, let text = item[field].map({ ManagementText.scalar($0) }), !text.isEmpty, text != "-" { return text }
        for field in ["name", "title", "label", "keyword"] {
            if let text = item[field]?.stringValue, !text.isEmpty { return text }
        }
        return "第 \(index + 1) 项"
    }
    private func itemSubtitle(_ item: JSONValue) -> String {
        if let field = schema["x-ui-description-field"]?.stringValue, let text = item[field]?.stringValue, !text.isEmpty { return text }
        guard let template = schema["x-ui-summary"]?.stringValue, !template.isEmpty else { return "" }
        var result = template
        for match in template.matches(of: #/\{([A-Za-z0-9_.]+)\}/#) {
            let path = String(match.1)
            let text = PluginCardFormat.value(at: path, in: item).map { ManagementText.scalar($0) } ?? ""
            result = result.replacingOccurrences(of: "{\(path)}", with: text)
        }
        return result
    }
    private func item(_ index: Int) -> Binding<JSONValue> {
        Binding(get: { (value.arrayValue ?? []).indices.contains(index) ? value.arrayValue![index] : .null }, set: { new in
            var array = value.arrayValue ?? []; guard array.indices.contains(index) else { return }; array[index] = new; value = .array(array)
        })
    }

    var body: some View {
        let items = value.arrayValue ?? []
        List {
            Section {
                ForEach(Array(items.enumerated()), id: \.offset) { index, entry in
                    NavigationLink {
                        Form { ManagementFieldsView(schema: itemSchema, value: item(index)) }
                            .environment(\.managementOptionalFieldsInline, optionalInline)
                            .navigationTitle(itemTitle(entry, index: index)).navigationBarTitleDisplayMode(.inline)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(itemTitle(entry, index: index))
                                if !enabledField.isEmpty {
                                    let on = entry[enabledField]?.boolValue != false
                                    Text(on ? "已启用" : "已停用").font(.caption).foregroundStyle(on ? Color.green : Color.secondary)
                                }
                            }
                            let subtitle = itemSubtitle(entry)
                            if !subtitle.isEmpty { Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                        }
                    }
                    .accessibilityIdentifier("plugin.config.configList.item.\(index)")
                }
                .onDelete { offsets in
                    var array = value.arrayValue ?? []
                    guard array.count - offsets.count >= minItems else { return }
                    array.remove(atOffsets: offsets); value = .array(array)
                }
                .onMove { offsets, destination in
                    guard reorderable else { return }
                    var array = value.arrayValue ?? []; array.move(fromOffsets: offsets, toOffset: destination); value = .array(array)
                }
                if items.isEmpty { Text("还没有内容。").foregroundStyle(.secondary) }
            } footer: { Text("左滑删除；点右上角“编辑”调整顺序。修改暂存在本次编辑里，回到配置页统一检查并保存。") }
            Section {
                Button(schema["x-ui-add-label"]?.stringValue ?? "添加一组", systemImage: "plus") {
                    var array = value.arrayValue ?? []
                    array.append(ManagementSchema.initial(itemSchema))
                    value = .array(array)
                }
                .disabled(items.count >= maxItems)
                .accessibilityIdentifier("plugin.config.configList.add")
            }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .toolbar { if reorderable { EditButton() } }
    }
}

/// secret-list（x-secret-list）：已保存的密钥只显示服务器给的遮罩，可改备注、删除、排序；新增填明文，保存后服务器加密。
private struct PluginSecretListField: View {
    let title: String
    let spec: JSONValue
    @Binding var value: JSONValue

    var body: some View {
        let count = value.arrayValue?.count ?? 0
        NavigationLink {
            PluginSecretListEditor(title: title, spec: PluginSecretListSpec(field: spec), value: $value)
        } label: {
            LabeledContent { Text(count == 0 ? "未保存" : "\(count) 个（内容隐藏）") } label: { Label(title, systemImage: "key") }
        }
        .accessibilityIdentifier("plugin.config.secretList." + title)
    }
}

private struct PluginSecretListEditor: View {
    let title: String
    let spec: PluginSecretListSpec
    @Binding var value: JSONValue
    @State private var draft = ""
    @State private var draftNote = ""

    private func field(_ index: Int, _ name: String) -> Binding<String> {
        Binding(get: { (value.arrayValue ?? []).indices.contains(index) ? (value.arrayValue![index][name]?.stringValue ?? "") : "" }, set: { new in
            var array = value.arrayValue ?? []
            guard array.indices.contains(index) else { return }
            var object = array[index].objectValue ?? [:]
            object[name] = .string(new)
            array[index] = .object(object)
            value = .array(array)
        })
    }

    var body: some View {
        let items = value.arrayValue ?? []
        List {
            Section {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    let saved = item[spec.idField] != nil
                    let secret = item[spec.secretField]?.stringValue ?? ""
                    VStack(alignment: .leading, spacing: 6) {
                        Label(saved ? secret : String(secret.prefix(4)) + "…（新增，保存后加密）", systemImage: saved ? "key" : "key.fill")
                            .font(.callout.monospaced()).lineLimit(1).truncationMode(.middle).privacySensitive()
                        TextField("备注（可选）", text: field(index, spec.noteField))
                            .font(.subheadline).textInputAutocapitalization(.never).autocorrectionDisabled()
                    }
                    .accessibilityIdentifier("plugin.config.secretList.item.\(index)")
                }
                .onDelete { offsets in var array = value.arrayValue ?? []; array.remove(atOffsets: offsets); value = .array(array) }
                .onMove { offsets, destination in var array = value.arrayValue ?? []; array.move(fromOffsets: offsets, toOffset: destination); value = .array(array) }
                if items.isEmpty { Text("还没有保存任何\(title)。").foregroundStyle(.secondary) }
            } header: { Text("已保存 \(items.count) 个") } footer: {
                Text("出于安全只显示遮罩，不会显示完整内容。左滑删除，点“编辑”调整顺序；回到配置页检查并保存后生效。")
            }
            Section {
                TextField("每行一个；也可粘贴插件支持的导入格式（如 JSON）", text: $draft, axis: .vertical)
                    .lineLimit(2...6).textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
                    .accessibilityIdentifier("plugin.config.secretList.input")
                TextField("备注（可选）", text: $draftNote).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button("添加", systemImage: "plus") {
                    let secrets = PluginSecretListSpec.split(draft)
                    guard !secrets.isEmpty else { return }
                    var array = value.arrayValue ?? []
                    let note = draftNote.trimmingCharacters(in: .whitespacesAndNewlines)
                    array.append(contentsOf: secrets.map { JSONValue.object([spec.secretField: .string($0), spec.noteField: .string(note)]) })
                    value = .array(array)
                    draft = ""; draftNote = ""
                }
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("plugin.config.secretList.add")
            } header: { Text("新增") }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .toolbar { EditButton() }
    }
}

// MARK: - 插件原生卡片（ui.cards：stats / table / kv / list / buttons）

/// 某个位置（plugin.overview / account.detail / dashboard）的插件卡片，作为 List 里的若干 Section 插入。
/// 声明一次读取，数据逐卡读取：一张卡失败只在这张卡上显示「已停止工作 · 重试」。
struct PluginCardSections: View {
    let slot: String
    var pluginKey: String?
    var accountID: Int?
    @Environment(AppModel.self) private var model
    @State private var cards: [JSONValue] = []
    @State private var loaded = false
    @State private var error: String?

    var body: some View {
        if !loaded {
            Section {
                if pluginKey != nil { ProgressView("正在读取插件卡片…") }
                else { Color.clear.frame(height: 1).listRowBackground(Color.clear).accessibilityHidden(true) }
            }
            .listSectionSpacing(pluginKey != nil ? ListSectionSpacing.default : ListSectionSpacing.compact)
            .task { await load() }
        } else {
            if let error, pluginKey != nil {
                Section {
                    Label("插件卡片读取失败：\(error)", systemImage: "exclamationmark.triangle").font(.footnote).foregroundStyle(.secondary)
                    Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            }
            ForEach(Array(cards.enumerated()), id: \.offset) { _, card in
                PluginCardSection(card: card, accountID: accountID, showPluginName: pluginKey == nil)
            }
        }
    }

    private func load() async {
        var path = "/api/plugin-ui/cards?slot=\(slot)"
        if let pluginKey { path += "&plugin_key=" + (pluginKey.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? pluginKey) }
        if let accountID { path += "&account_id=\(accountID)" }
        do {
            cards = try await model.request(path: path)["cards"]?.arrayValue ?? []
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loaded = true
    }
}

private struct PluginCardSection: View {
    let card: JSONValue
    var accountID: Int?
    var showPluginName = false
    @Environment(AppModel.self) private var model
    @State private var result: JSONValue?
    @State private var loading = true
    @State private var requestError: String?
    @State private var busyAction: String?
    @State private var confirming: String?
    @State private var actionMessage: String?
    @State private var actionError: String?

    private var cardID: String { card["card_id"]?.stringValue ?? "" }
    private var pluginKey: String { card["plugin_key"]?.stringValue ?? "" }
    private var basePath: String {
        let plugin = pluginKey.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? pluginKey
        let id = cardID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? cardID
        return "/api/plugin-ui/cards/\(plugin)/\(id)"
    }
    private var requestBody: JSONValue {
        .object(["account_id": accountID.map { JSONValue.integer(Int64($0)) } ?? JSONValue.null, "slot": card["slot"] ?? JSONValue.null])
    }
    private var failed: Bool { requestError != nil || (result != nil && result?["status"]?.stringValue != "ok") }

    var body: some View {
        Section {
            if loading, result == nil, requestError == nil {
                ProgressView("正在读取…")
            } else if failed {
                VStack(alignment: .leading, spacing: 6) {
                    Label("\(PluginUIContract.cardFailureMessage) · \(PluginUIContract.cardRetryLabel)", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                    if let detail = requestError ?? result?["message"]?.stringValue { Text(detail).font(.caption).foregroundStyle(.secondary) }
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("plugin.uicard.failed")
                Button("重试", systemImage: "arrow.clockwise") { Task { await load() } }
                    .disabled(loading)
                    .accessibilityIdentifier("plugin.uicard.retry.\(cardID)")
            } else if let result {
                let blocks = (card["blocks"]?.arrayValue ?? []).map { PluginCardBlock.build($0, data: result["data"] ?? .object([:])) }
                ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in blockView(block) }
            }
            if let actionMessage { Label(actionMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.footnote) }
            if let actionError { Label(actionError, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).font(.footnote) }
        } header: {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(card["title"]?.stringValue ?? cardID).font(.headline).foregroundStyle(.primary).textCase(nil)
                    if showPluginName, let name = card["plugin_name"]?.stringValue { Text(name).font(.caption).textCase(nil) }
                }
                Spacer()
                Button("刷新", systemImage: "arrow.clockwise") { Task { await load() } }
                    .labelStyle(.iconOnly).disabled(loading)
                    .accessibilityIdentifier("plugin.uicard.refresh.\(cardID)")
            }
            // 只挂在分组标题上：挂在 Section 上会被逐行套用，导致重复读取。
            .task(id: "\(pluginKey)|\(cardID)|\(accountID ?? 0)") {
                let interval = card["refresh_seconds"]?.intValue
                repeat {
                    await load()
                    guard let interval, interval > 0 else { break }
                    try? await Task.sleep(for: .seconds(interval))
                } while !Task.isCancelled
            }
        } footer: {
            if let subtitle = card["subtitle"]?.stringValue, !subtitle.isEmpty { Text(subtitle) }
        }
    }

    private func tone(_ tone: PluginCardTone) -> Color {
        switch tone {
        case .neutral: .primary
        case .success: .green
        case .warning: .orange
        case .danger: .red
        case .accent: .accentColor
        }
    }

    @ViewBuilder private func blockView(_ block: PluginCardBlock) -> some View {
        switch block {
        case .stats(_, let items):
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 92), alignment: .leading)], alignment: .leading, spacing: 10) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.label).font(.caption).foregroundStyle(.secondary)
                        Text(item.text).font(.title3.weight(.semibold)).monospacedDigit().foregroundStyle(tone(item.tone))
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(.vertical, 4)
        case .kv(_, let items):
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                LabeledContent(item.label) { Text(item.text).monospacedDigit().foregroundStyle(tone(item.tone)) }
            }
        case .table(let title, let columns, let rows, let empty):
            if let title { Text(title).font(.subheadline.weight(.semibold)) }
            if rows.isEmpty { Text(empty).font(.footnote).foregroundStyle(.secondary) }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                VStack(alignment: .leading, spacing: 4) {
                    if let first = row.cells.first { Text(first.text).font(.subheadline.weight(.semibold)).foregroundStyle(tone(first.tone)) }
                    ForEach(Array(row.cells.enumerated().dropFirst()), id: \.offset) { index, cell in
                        if cell.text != "-", !cell.text.isEmpty {
                            HStack(alignment: .firstTextBaseline) {
                                Text(columns.indices.contains(index) ? columns[index] : "").font(.caption).foregroundStyle(.secondary)
                                Spacer(minLength: 8)
                                Text(cell.text).font(.caption).monospacedDigit().foregroundStyle(tone(cell.tone))
                                    .multilineTextAlignment(.trailing).lineLimit(3)
                            }
                        }
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("plugin.uicard.row.\(row.key)")
            }
        case .list(let title, let items, let empty, let hidden):
            if !hidden {
                if let title { Text(title).font(.subheadline.weight(.semibold)) }
                if items.isEmpty { Text(empty).font(.footnote).foregroundStyle(.secondary) }
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.title).font(.callout.monospaced())
                            if !item.subtitle.isEmpty { Text(item.subtitle).font(.caption).foregroundStyle(.secondary) }
                        }
                        Spacer(minLength: 8)
                        if !item.trailing.isEmpty { Text(item.trailing).font(.caption.weight(.medium)).foregroundStyle(tone(item.tone)) }
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        case .buttons(let buttons):
            ForEach(buttons, id: \.id) { button in
                Button(role: button.destructive ? .destructive : nil) {
                    if button.confirm.isEmpty { Task { await run(button.id) } } else { confirming = button.id }
                } label: {
                    Label(button.label, systemImage: button.destructive ? "exclamationmark.triangle" : "play.circle").frame(minHeight: 44)
                }
                .disabled(busyAction != nil)
                .accessibilityIdentifier("plugin.uicard.action.\(button.id)")
                .confirmationDialog(button.confirm, isPresented: Binding(get: { confirming == button.id }, set: { if !$0 { confirming = nil } }),
                                    titleVisibility: .visible) {
                    Button(button.label, role: button.destructive ? .destructive : nil) { Task { await run(button.id) } }
                    Button("取消", role: .cancel) {}
                }
            }
        case .unsupported(let layout):
            Label("这张卡片的布局（\(layout)）需要更新 App 才能显示。", systemImage: "arrow.down.app").font(.footnote).foregroundStyle(.orange)
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            result = try await model.request(path: basePath + "/data", method: "POST", body: requestBody)
            requestError = nil
        } catch {
            requestError = error.localizedDescription
        }
    }

    /// 按钮只提交一次、不自动重试；网络中断时如实提示结果未知。
    private func run(_ actionID: String) async {
        guard busyAction == nil else { return }
        busyAction = actionID
        actionMessage = nil; actionError = nil
        defer { busyAction = nil }
        do {
            let id = actionID.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? actionID
            let response = try await model.request(path: basePath + "/actions/\(id)", method: "POST", body: requestBody)
            actionMessage = response["message"]?.stringValue ?? "已完成"
            if response["refresh"]?.boolValue != false { await load() }
        } catch {
            actionError = (error as? APIError)?.isOutcomeUncertain == true
                ? "网络中断，结果未知。请刷新这张卡确认，不要重复点。" : "操作失败：\(error.localizedDescription)"
        }
    }
}
