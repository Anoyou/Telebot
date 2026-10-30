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

    @ViewBuilder private var overviewContent: some View {
                statusSection
                if !preferences.hiddenHomeModules.contains("metrics") {
                    metricsSection
                }
                if !preferences.hiddenHomeModules.contains("quick") {
                    quickSection
                }
                if !preferences.hiddenHomeModules.contains("favorites") {
                    Section("常用账号") {
                        let favorites = model.accounts.filter { preferences.isFavoriteAccount($0.id) }
                        if favorites.isEmpty { Text("在账号详情中收藏，便可从这里快速打开。").font(.footnote).foregroundStyle(.secondary) }
                        ForEach(favorites) { account in
                            NavigationLink { AccountDetailView(id: account.id) } label: {
                                Label(AccountPresentation.name(account), systemImage: AccountPresentation.statusSymbol(account.status))
                            }
                        }
                    }
                    let favoritePlugins = model.plugins.filter { preferences.isFavoritePlugin($0["key"]?.stringValue ?? "") }
                    if !favoritePlugins.isEmpty {
                        Section("常用插件") {
                            ForEach(Array(favoritePlugins.enumerated()), id: \.offset) { _, plugin in
                                NavigationLink {
                                    ManagementPluginDetailView(feature: plugin, accountID: nil, accountState: nil)
                                } label: { Label(plugin["display_name"]?.stringValue ?? plugin["key"]?.stringValue ?? "插件", systemImage: "star") }
                            }
                        }
                    }
                }
                ForEach(layout.modules.filter { !preferences.hiddenHomeModules.contains($0.id) }) { module in
                    moduleSection(module)
                }
                // dashboard：全局启用的插件声明的原生卡片（没有时不显示）。
                PluginCardSections(slot: "dashboard")
                Section {
                    NavigationLink { OverviewCustomizationView() } label: {
                        Label("自定义概览", systemImage: "slider.horizontal.3")
                    }
                    .accessibilityIdentifier("overview.customizeButton")
                    if let date = model.lastUpdated {
                        LabeledContent {
                            Text(date.formatted(date: .omitted, time: .standard)).monospacedDigit()
                        } label: { Label("上次完整刷新", systemImage: "clock") }
                    }
                } footer: { Text("显示内容和顺序仅保存在本机；下拉或点右上角可重新读取服务器状态。") }
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
            VStack(alignment: .leading, spacing: 8) {
                if model.showingSnapshot {
                    // 第十一轮：冷启动先显示上次读取的数据（DESIGN「过期数据」状态），不当作当前在线状态。
                    Label("上次读取的状态", systemImage: "clock.arrow.circlepath")
                        .font(.title2.weight(.semibold)).foregroundStyle(.secondary)
                    let progress = model.isRefreshing ? "正在读取最新状态…" : "暂时无法连接服务器，下拉可重试。"
                    Text(model.lastUpdated.map { "更新于 \($0.formatted(date: .abbreviated, time: .shortened))。\(progress)" } ?? progress)
                        .font(.subheadline).foregroundStyle(.secondary)
                } else if model.health == nil && model.healthError == nil {
                    Text(model.isRefreshing ? "正在读取服务状态…" : "尚无服务状态").font(.title2.weight(.semibold))
                } else if model.health == nil {
                    Label("无法读取服务器状态", systemImage: OpsSeverity.error.symbol)
                        .font(.title2.weight(.semibold)).foregroundStyle(.red)
                } else if model.healthError != nil || model.accountsError != nil || model.resourcesError != nil || model.workspaceError != nil || !signals.unavailable.isEmpty {
                    Label("部分状态未能读取", systemImage: "questionmark.circle")
                        .font(.title2.weight(.semibold)).foregroundStyle(.secondary)
                } else {
                    Label("服务运行中", systemImage: "checkmark.circle.fill")
                        .font(.title2.weight(.semibold)).foregroundStyle(.green)
                }
                if model.showingSnapshot {
                    EmptyView()
                } else if model.healthError != nil {
                    Text(model.health == nil ? "请检查网络或服务器地址，然后下拉重试。" : "以下为上次读取结果，并非当前在线状态。")
                        .font(.subheadline).foregroundStyle(.secondary)
                } else if !signals.unavailable.isEmpty {
                    Text("暂时无法读取：\(signals.unavailable.joined(separator: "、"))。下拉可重试。")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Text("想全面检查账号、插件、AI、日志与推送，点右上角「一键体检」。")
                    .font(.footnote).foregroundStyle(.secondary)
                if model.isSyncingAfterAction {
                    Label("操作已提交，正在同步最新状态…", systemImage: "arrow.triangle.2.circlepath")
                        .font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("overview.syncing")
                }
            }
            .padding(.vertical, 6)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("overview.status")
        }
    }

    @ViewBuilder private var metricsSection: some View {
        Section {
            OpsMetricGrid {
                let active = model.accounts.filter { $0.status == "active" }.count
                OpsMetricTile(title: "账号", value: model.accountsLoaded ? "\(active)/\(model.accounts.count)" : "—",
                              symbol: "person.2", detail: "已启用 / 全部")
                if let workers = model.health?.workers {
                    OpsMetricTile(title: "运行进程", value: "\(workers.runtimeDesiredRunningAlive)/\(workers.runtimeDesiredRunning)",
                                  symbol: "bolt.horizontal.circle",
                                  color: workers.runtimeDesiredRunningAlive < workers.runtimeDesiredRunning ? .red : .primary,
                                  detail: "在线 / 应运行")
                } else {
                    OpsMetricTile(title: "运行进程", value: "—", symbol: "bolt.horizontal.circle", detail: "在线 / 应运行")
                }
                OpsMetricTile(title: "已启用插件", value: signals.enabledPluginCount.map { String($0) } ?? "—", symbol: "puzzlepiece.extension",
                              color: signals.failingPluginCount == 0 ? .primary : .red,
                              detail: signals.failingPluginCount == 0 ? "运行正常" : "\(signals.failingPluginCount) 个出错")
                OpsMetricTile(title: "AI 供应商", value: signals.providerTotal.map { "\(signals.providerReady ?? 0)/\($0)" } ?? "—", symbol: "cpu",
                              color: signals.coolingModels.isEmpty ? .primary : .orange,
                              detail: signals.coolingModels.isEmpty ? "可用 / 已配置" : "\(signals.coolingModels.count) 个模型暂停中")
                OpsMetricTile(title: "24 小时错误", value: signals.runtimeErrors.map { OpsFormat.integer($0) } ?? "—", symbol: OpsSeverity.error.symbol,
                              color: (signals.runtimeErrors ?? 0) > 0 ? .red : .primary,
                              detail: "告警 \(signals.runtimeWarnings.map { OpsFormat.integer($0) } ?? "—")")
                OpsMetricTile(title: "待确认", value: "\(model.pendingActions.count)", symbol: "checkmark.bubble",
                              color: model.pendingActions.isEmpty ? .primary : .blue, detail: "助手操作")
            }
        } header: { Text("关键数字") }
    }

    private var quickActions: [OverviewQuickAction] {
        [
            OverviewQuickAction(id: "logs", title: "日志", symbol: "list.bullet.rectangle", action: .push(.logs)),
            OverviewQuickAction(id: "ledger", title: "资金台账", symbol: "book.closed", action: .push(.ledger)),
            OverviewQuickAction(id: "accounts", title: "账号", symbol: "person.2", action: .route(.accounts)),
            OverviewQuickAction(id: "plugins", title: "插件", symbol: "puzzlepiece.extension", action: .route(.plugins)),
            OverviewQuickAction(id: "ai", title: "AI", symbol: "cpu", action: .route(.ai)),
            OverviewQuickAction(id: "assistant", title: "问助手", symbol: "sparkles", action: .route(.assistant()))
        ]
    }

    @ViewBuilder private var quickSection: some View {
        Section {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 10)], spacing: 10) {
                ForEach(quickActions) { quick in
                    Button { perform(quick.action) } label: {
                        Label(quick.title, systemImage: quick.symbol)
                            .labelStyle(.verticalQuick)
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("overview.quick.\(quick.id)")
                }
            }
            .padding(.vertical, 4)
        } header: { Text("快捷操作") }
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
        }
    }

    // MARK: 可排序模块

    @ViewBuilder private func moduleSection(_ module: OverviewModule) -> some View {
        switch module {
        case .health:
            Section {
                if let error = model.healthError {
                    NoticeView(title: model.health == nil ? "状态读取失败" : "服务状态已过期", message: error)
                }
                if let health = model.health {
                    ProbeStatusRow(name: "数据库", symbol: "externaldrive", probe: health.db)
                    ProbeStatusRow(name: "缓存服务", symbol: "memorychip", probe: health.redis)
                    ProbeStatusRow(name: "数据库结构", symbol: "checklist", probe: health.alembic)
                }
            } header: { Text(OverviewModule.health.title) }
        case .workers:
            Section {
                if let health = model.health {
                    ReadOnlyRow(title: "应运行进程", value: "\(health.workers.runtimeDesiredRunning)")
                    ReadOnlyRow(title: "其中已存活", value: "\(health.workers.runtimeDesiredRunningAlive)")
                    ReadOnlyRow(title: "全部存活进程", value: "\(health.workers.runtimeAlive)")
                    ReadOnlyRow(title: "存在失败记录", value: "\(health.workers.runtimeFailing)")
                } else if model.healthError == nil {
                    ProgressView("正在读取进程…")
                }
            } header: { Text(OverviewModule.workers.title) } footer: {
                Text("“已启用”是账号配置，不代表进程正在运行。操作后的进程状态以服务器后续读取结果为准。")
            }
        case .resources:
            Section {
                if let error = model.resourcesError {
                    NoticeView(title: model.resources == nil ? "资源读取失败" : "资源数据已过期", message: error)
                }
                if let resources = model.resources {
                    ResourceStatusRow(title: "处理器", value: resources.host.cpuPercent, symbol: "cpu")
                    ResourceStatusRow(title: "内存", value: resources.host.memoryUsedPercent, symbol: "memorychip")
                    ResourceStatusRow(title: "磁盘", value: resources.host.diskUsedPercent, symbol: "internaldrive")
                    if let uptime = resources.host.uptimeSeconds {
                        ReadOnlyRow(title: "主机运行时间", value: duration(uptime))
                    }
                } else if model.resourcesError == nil {
                    ProgressView("正在读取资源…")
                }
            } header: { Text(OverviewModule.resources.title) }
        }
    }

    private func duration(_ seconds: Int) -> String {
        let hours = max(0, seconds) / 3_600
        return hours >= 24 ? "\(hours / 24) 天 \(hours % 24) 小时" : "\(hours) 小时"
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
/// 概览顶部：当前连接的服务器。点按进入“关于”查看完整服务器信息与检查更新。
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
        HStack(alignment: .center, spacing: 14) {
            Image("BrandMark")
                .resizable()
                .scaledToFit()
                .frame(width: 48, height: 48)
                .clipShape(.rect(cornerRadius: 11, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 11, style: .continuous)
                        .strokeBorder(Color(.separator), lineWidth: 0.5)
                }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text("当前服务器").font(.caption).foregroundStyle(.secondary)
                Text(host ?? "未连接服务器")
                    .font(.headline)
                    .lineLimit(1).truncationMode(.middle)
                HStack(spacing: 6) {
                    Circle().fill(statusColor).frame(width: 7, height: 7)
                    Text(statusText)
                    if let version {
                        Text("·")
                        Text("v\(version.version)").monospacedDigit()
                        Text(AppInfo.channelLabel(version.channel))
                    }
                    if refreshing {
                        ProgressView().controlSize(.mini)
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(1)
                if let username {
                    Label(username, systemImage: "person.crop.circle")
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("overview.server")
    }
}
