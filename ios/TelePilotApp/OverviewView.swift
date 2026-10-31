import SwiftUI
import TelePilotCore

struct OverviewView: View {
    /// 被移出 Dock 后从「更多」推入时为真：不再自带 NavigationStack，使用行内标题。
    var embedded = false
    @Environment(AppModel.self) private var model
    @Environment(OverviewLayout.self) private var layout
    @Environment(WorkspacePreferences.self) private var preferences
    @State private var signals = OverviewSignals()
    @State private var destination: OverviewDestination?
    @State private var showCheckup = false
    /// 全局搜索：左上角放大镜展开系统搜索栏，列表换成搜索结果（与“更多”、插件、AI 页同一写法）。
    @State private var searchText = ""
    @State private var searching = false

    var body: some View {
        if embedded {
            page.navigationBarTitleDisplayMode(.inline)
        } else {
            NavigationStack { page }
        }
    }

    private var page: some View {
            List {
                if searching {
                    GlobalSearchResults(query: searchText)
                } else {
                    overviewContent
                }
            }
            .pageSearch(text: $searchText, isActive: $searching, prompt: "管理模块、账号或插件", identifier: "overview.search")
            .navigationTitle("概览")
            .navigationDestination(item: $destination) { destination in
                destinationView(destination)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await refreshAll() } } label: {
                        Label("刷新状态", systemImage: "arrow.clockwise")
                    }
                    .disabled(model.isRefreshing)
                    .accessibilityIdentifier("overview.refresh")
                }
                // 一键体检：与助手“体检一下”同一个只读检查，结果在面板中给出结论、每项状态与修复建议。
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showCheckup = true } label: {
                        Label("一键体检", systemImage: "stethoscope")
                    }
                    .accessibilityLabel("一键体检")
                    .accessibilityIdentifier("overview.checkup")
                }
            }
            .sheet(isPresented: $showCheckup) { SystemCheckupView() }
            .refreshable { await refreshAll() }
            .task { if signals.loadedAt == nil { await signals.load(model: model) } }
            .accessibilityIdentifier("overview.screen")
    }

    // 第 21 轮：概览改紧凑版（DESIGN「概览卡片规范」）。首屏只放「服务器 + 一句状态」与一张 3 列小指标格，
    // 原来的关键数字、运行进程、基础服务、主机资源合并进同一张格子；次要细节收进点开后的页面与长按菜单。
    @ViewBuilder private var overviewContent: some View {
                statusSection
                if !tiles.isEmpty {
                    tilesSection
                }
                if !preferences.hiddenHomeModules.contains("quick") {
                    quickSection
                }
                if !preferences.hiddenHomeModules.contains("favorites") {
                    favoritesSection
                }
                // dashboard：全局启用的插件声明的原生卡片（没有时不显示）。
                PluginCardSections(slot: "dashboard")
                Section {
                    NavigationLink { OverviewCustomizationView() } label: {
                        Label("自定义概览", systemImage: "slider.horizontal.3")
                    }
                    .accessibilityIdentifier("overview.customizeButton")
                }
    }

    /// 三组读取并发进行，不再串行等待；关键数字在刷新期间保留上次结果。
    private func refreshAll() async {
        let model = self.model
        let signals = self.signals
        async let base: Void = model.refresh()
        async let workspace: Void = model.refreshWorkspace()
        async let metrics: Void = signals.load(model: model)
        _ = await (base, workspace, metrics)
    }

    // MARK: 状态摘要

    /// 顶部只陈述能确认的事实：服务器是否可达、基础数据是否读取成功。
    /// 账号、插件、AI 与日志的全面判断交给右上角「一键体检」，不在这里猜测“需要处理”的数量。
    @ViewBuilder private var statusSection: some View {
        Section {
            NavigationLink { AboutView() } label: {
                OverviewServerHeader(
                    host: model.serverURL?.host,
                    version: model.serverVersion,
                    username: model.user?.username,
                    // 显示本机快照时还不知道服务器是否在线，不显示“可达”。
                    reachable: model.showingSnapshot ? nil : (model.health == nil ? (model.healthError == nil ? nil : false) : (model.healthError == nil)),
                    refreshing: model.isRefreshing || signals.loading
                )
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    statusHeadline.font(.headline)
                    Spacer(minLength: 4)
                    if !model.showingSnapshot, let date = model.lastUpdated {
                        // 原「上次完整刷新」一行并入状态行右侧。
                        Text("更新于 \(date.formatted(date: .omitted, time: .shortened))")
                            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
                if let note = statusNote {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if model.isSyncingAfterAction {
                    Label("操作已提交，正在同步最新状态…", systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption).foregroundStyle(.secondary)
                        .accessibilityIdentifier("overview.syncing")
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("overview.status")
            // 第 22 轮：连不上时状态行下面是可展开的「诊断详情」（原因已写在状态行里），可复制发给维护者。
            if let report = model.connectionReport, model.health == nil || model.showingSnapshot || model.healthError != nil {
                ConnectionProblemView(report: report, showsReason: false)
            }
        }
        .listSectionSpacing(.compact)
    }

    @ViewBuilder private var statusHeadline: some View {
        if model.showingSnapshot {
            // 第十一轮：冷启动先显示上次读取的数据（DESIGN「过期数据」状态），不当作当前在线状态。
            Label("上次读取的状态", systemImage: "clock.arrow.circlepath").foregroundStyle(.secondary)
        } else if model.health == nil && model.healthError == nil {
            Text(model.isRefreshing ? "正在读取服务状态…" : "尚无服务状态")
        } else if model.health == nil {
            Label("无法读取服务器状态", systemImage: OpsSeverity.error.symbol).foregroundStyle(.red)
        } else if model.healthError != nil || model.accountsError != nil || model.resourcesError != nil || model.workspaceError != nil || !signals.unavailable.isEmpty {
            Label("部分状态未能读取", systemImage: "questionmark.circle").foregroundStyle(.secondary)
        } else {
            Label("服务运行中", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        }
    }

    /// 状态行下方最多一行说明；正常时不显示（全面检查在右上角「一键体检」）。
    private var statusNote: String? {
        if model.showingSnapshot {
            let progress = model.isRefreshing ? "正在读取最新状态…" : (model.connectionReport?.diagnosis.summary ?? "暂时无法连接服务器，下拉可重试。")
            return model.lastUpdated.map { "更新于 \($0.formatted(date: .abbreviated, time: .shortened))。\(progress)" } ?? progress
        }
        if model.healthError != nil {
            if model.health == nil { return model.connectionReport?.diagnosis.summary ?? "请检查网络或服务器地址，然后下拉重试。" }
            return "以下为上次读取结果，并非当前在线状态。"
        }
        if !signals.unavailable.isEmpty {
            return "暂时无法读取：\(signals.unavailable.joined(separator: "、"))。下拉可重试。"
        }
        return nil
    }

    // MARK: 小指标格

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// 要显示的格子：先是关键状态（可在「自定义概览 › 关键状态」整组隐藏），再是三个服务模块（按本机顺序，可逐个隐藏）。
    private var tiles: [OverviewTile] {
        var result: [OverviewTile] = []
        if !preferences.hiddenHomeModules.contains("metrics") {
            result += OverviewTileBuilder.keyTiles(model: model, signals: signals)
        }
        for module in layout.modules where !preferences.hiddenHomeModules.contains(module.id) {
            result.append(OverviewTileBuilder.moduleTile(module, model: model))
        }
        return result
    }

    @ViewBuilder private var tilesSection: some View {
        Section {
            let columns = dynamicTypeSize.isAccessibilitySize ? 2 : 3
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: columns), spacing: 8) {
                ForEach(tiles) { tile in
                    Button { perform(tile.action) } label: {
                        OverviewStatTileView(tile: tile)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(tile.title)
                    .accessibilityValue(tile.accessibilityValue)
                    .accessibilityHint(tile.detail ?? "")
                    .accessibilityIdentifier("overview.tile.\(tile.id)")
                    .contextMenu {
                        // 次要细节：长按看到原来写在卡片里的说明，不占首屏。
                        if let detail = tile.detail { Text(detail) }
                        Button("打开", systemImage: "arrow.forward.circle") { perform(tile.action) }
                    }
                }
            }
            .listRowInsets(EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
            .accessibilityIdentifier("overview.tiles")
        }
        .listSectionSpacing(.compact)
    }

    // MARK: 快捷操作与收藏

    /// 小指标格已经能进入账号、插件、AI、日志，这里只留格子里没有的入口，排成一行。
    private var quickActions: [OverviewQuickAction] {
        var items = [OverviewQuickAction(id: "logs", title: "日志", symbol: "list.bullet.rectangle", action: .push(.logs))]
        if model.isModuleEnabled("ledger") {
            items.append(OverviewQuickAction(id: "ledger", title: "资金台账", symbol: "book.closed", action: .push(.ledger)))
        }
        items.append(OverviewQuickAction(id: "assistant", title: "问 Agent", symbol: "sparkles", action: .route(.assistant())))
        return items
    }

    @ViewBuilder private var quickSection: some View {
        Section {
            HStack(spacing: 8) {
                ForEach(quickActions) { quick in
                    Button { perform(quick.action) } label: {
                        Label(quick.title, systemImage: quick.symbol)
                            .font(.subheadline)
                            .lineLimit(1).minimumScaleFactor(0.8)
                            .frame(maxWidth: .infinity, minHeight: 32)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("overview.quick.\(quick.id)")
                }
            }
            .listRowInsets(EdgeInsets(top: 8, leading: 12, bottom: 8, trailing: 12))
        }
        .listSectionSpacing(.compact)
    }

    /// 收藏的账号与插件合成一组；一个都没有时整组不显示（说明移到「自定义概览」）。
    @ViewBuilder private var favoritesSection: some View {
        let favorites = model.accounts.filter { preferences.isFavoriteAccount($0.id) }
        let favoritePlugins = model.plugins.filter { preferences.isFavoritePlugin($0["key"]?.stringValue ?? "") }
        if !favorites.isEmpty || !favoritePlugins.isEmpty {
            Section("收藏") {
                ForEach(favorites) { account in
                    NavigationLink { AccountDetailView(id: account.id) } label: {
                        Label(AccountPresentation.name(account), systemImage: AccountPresentation.statusSymbol(account.status))
                    }
                }
                ForEach(Array(favoritePlugins.enumerated()), id: \.offset) { _, plugin in
                    NavigationLink {
                        ManagementPluginDetailView(feature: plugin, accountID: nil, accountState: nil)
                    } label: { Label(plugin["display_name"]?.stringValue ?? plugin["key"]?.stringValue ?? "插件", systemImage: "star") }
                }
            }
            .listSectionSpacing(.compact)
        }
    }

    private func perform(_ action: OverviewQuickAction.Action) {
        switch action {
        case .push(let target): destination = target
        case .route(let route): model.requestedRoute = route
        }
    }

    @ViewBuilder private func destinationView(_ destination: OverviewDestination) -> some View {
        switch destination {
        case .logs: LogsView()
        case .ledger: LedgerView()
        case .module(let module): OverviewModuleView(module: module)
        case .runtime: PluginManagementView(isTabRoot: false, initialMode: .runtime)
        case .inbox: AssistantInboxView()
        case .emergencyStop:
            List { EmergencyStopSection() }
                .navigationTitle("紧急停止")
                .navigationBarTitleDisplayMode(.inline)
        }
    }
}

/// 图标在上、文字在下的快捷按钮样式。
struct VerticalQuickLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(spacing: 4) {
            configuration.icon.font(.title3)
            configuration.title.font(.caption).lineLimit(1).minimumScaleFactor(0.8)
        }
    }
}

extension LabelStyle where Self == VerticalQuickLabelStyle {
    static var verticalQuick: VerticalQuickLabelStyle { VerticalQuickLabelStyle() }
}

/// 概览顶部的紧凑品牌头：App 图标、名称、一句话说明与当前连接的服务器（原“关于”页顶部卡片）。
/// 概览顶部：当前连接的服务器。点按进入“关于”查看完整服务器信息（检查更新在 App 设置 › 软件更新）。
private struct OverviewServerHeader: View {
    let host: String?
    let version: ServerVersion?
    let username: String?
    /// nil = 尚未读取；true = 在线；false = 最近一次读取失败
    let reachable: Bool?
    let refreshing: Bool

    private var statusText: String {
        switch reachable {
        case .some(true): "已连接"
        case .some(false): "连接异常"
        case .none: "正在连接…"
        }
    }
    private var statusColor: Color {
        switch reachable {
        case .some(true): .green
        case .some(false): .red
        case .none: .secondary
        }
    }

    var body: some View {
        // 第 21 轮：48pt 图标 + 四行文字压成 36pt 图标 + 两行（主机名；状态 · 版本 · 通道 · 用户名）。
        HStack(alignment: .center, spacing: 12) {
            Image("BrandMark")
                .resizable()
                .scaledToFit()
                .frame(width: 36, height: 36)
                .clipShape(.rect(cornerRadius: 8, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Color(.separator), lineWidth: 0.5)
                }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(host ?? "未连接服务器")
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1).truncationMode(.middle)
                HStack(spacing: 4) {
                    Circle().fill(statusColor).frame(width: 7, height: 7)
                    Text(statusText)
                    if let version {
                        Text("· v\(version.version)").monospacedDigit()
                        Text(AppInfo.channelLabel(version.channel))
                    }
                    if let username {
                        Text("· \(username)")
                    }
                    if refreshing {
                        ProgressView().controlSize(.mini)
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("当前服务器")
        .accessibilityValue([host ?? "未连接服务器", statusText, version.map { "v\($0.version)" }, username].compactMap { $0 }.joined(separator: "，"))
        .accessibilityIdentifier("overview.server")
    }
}
