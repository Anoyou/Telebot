import SwiftUI
import TelePilotCore

struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @Environment(OverviewLayout.self) private var layout
    @Environment(WorkspacePreferences.self) private var preferences
    @State private var signals = OverviewSignals()
    @State private var destination: OverviewDestination?

    private var attention: [OverviewAttentionItem] { OverviewAttention.items(model: model, signals: signals) }

    var body: some View {
        let items = attention
        NavigationStack {
            List {
                statusSection(items)
                if !preferences.hiddenHomeModules.contains("attention") {
                    attentionSection(items)
                }
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
            .navigationTitle("概览")
            .navigationDestination(item: $destination) { destination in
                destinationView(destination)
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    NavigationLink { GlobalSearchView() } label: { Label("全局搜索", systemImage: "magnifyingglass") }
                        .accessibilityIdentifier("overview.search")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await refreshAll() } } label: {
                        Label("刷新状态", systemImage: "arrow.clockwise")
                    }
                    .disabled(model.isRefreshing)
                    .accessibilityIdentifier("overview.refresh")
                }
            }
            .refreshable { await refreshAll() }
            .task { if signals.loadedAt == nil { await signals.load(model: model) } }
            .accessibilityIdentifier("overview.screen")
        }
    }

    private func refreshAll() async {
        await model.refresh()
        await model.refreshWorkspace()
        await signals.load(model: model)
    }

    // MARK: 状态摘要

    @ViewBuilder private func statusSection(_ items: [OverviewAttentionItem]) -> some View {
        Section {
            OverviewBrandHeader(host: model.serverURL?.host)
            VStack(alignment: .leading, spacing: 8) {
                if model.health == nil && model.healthError == nil {
                    Text(model.isRefreshing ? "正在读取服务状态…" : "尚无服务状态").font(.title2.weight(.semibold))
                } else if model.health == nil {
                    Label("无法读取服务器状态", systemImage: OpsSeverity.error.symbol)
                        .font(.title2.weight(.semibold)).foregroundStyle(.red)
                } else {
                    let errors = items.filter { $0.severity == .error }.count
                    let warnings = items.filter { $0.severity == .warning }.count
                    if errors + warnings == 0 {
                        if signals.loadedAt == nil || signals.loading {
                            Label("正在检查工作区…", systemImage: "arrow.clockwise")
                                .font(.title2.weight(.semibold))
                        } else if !signals.unavailable.isEmpty || model.healthError != nil || model.accountsError != nil || model.resourcesError != nil || model.workspaceError != nil {
                            Label("部分状态未能确认", systemImage: "questionmark.circle")
                                .font(.title2.weight(.semibold)).foregroundStyle(.secondary)
                        } else {
                            Label("一切正常", systemImage: "checkmark.circle.fill")
                                .font(.title2.weight(.semibold)).foregroundStyle(.green)
                        }
                    } else {
                        Label(errors > 0 ? "\(errors + warnings) 项需要处理" : "\(warnings) 项需要留意",
                              systemImage: errors > 0 ? OpsSeverity.error.symbol : OpsSeverity.warning.symbol)
                            .font(.title2.weight(.semibold)).foregroundStyle(errors > 0 ? Color.red : Color.orange)
                    }
                }
                if model.healthError != nil {
                    Text(model.health == nil ? "请检查网络或服务器地址，然后下拉重试。" : "以下为上次读取结果，并非当前在线状态。")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
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

    @ViewBuilder private func attentionSection(_ items: [OverviewAttentionItem]) -> some View {
        Section {
            if items.isEmpty {
                if signals.loading && signals.loadedAt == nil {
                    ProgressView("正在检查账号、插件、AI 与日志…")
                } else if model.health != nil && signals.unavailable.isEmpty && model.healthError == nil && model.accountsError == nil && model.resourcesError == nil && model.workspaceError == nil {
                    Label("没有需要处理的事项", systemImage: "checkmark.circle").foregroundStyle(.green)
                }
            }
            ForEach(items) { item in
                Button { perform(item.action) } label: { OverviewAttentionRow(item: item) }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("overview.attention.\(item.id)")
            }
            if !signals.unavailable.isEmpty {
                Text("暂时无法检查：\(signals.unavailable.joined(separator: "、"))。下拉可重试。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        } header: { Text("需要处理") } footer: {
            Text("自动检查：账号登录状态、账号进程、插件运行、AI 模型、最近 24 小时的错误日志与消息失败、待补付款和待确认操作。")
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
                              color: signals.failingPlugins.isEmpty ? .primary : .red,
                              detail: signals.failingPlugins.isEmpty ? "运行正常" : "\(signals.failingPlugins.count) 个出错")
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

    private func perform(_ action: OverviewAttentionItem.Action) {
        switch action {
        case .push(let target): destination = target
        case .route(let route): model.requestedRoute = route
        }
    }

    @ViewBuilder private func destinationView(_ destination: OverviewDestination) -> some View {
        switch destination {
        case .account(let id): AccountDetailView(id: id)
        case .health: OverviewModuleView(module: .health)
        case .workers: OverviewModuleView(module: .workers)
        case .logs: LogsView()
        case .errorLogs: LogListView(kinds: [.runtime], level: .error, range: .day, title: "最近错误")
        case .failedMessages: LogListView(kinds: [.messages], level: .warning, range: .day, title: "处理失败的消息")
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
private struct OverviewBrandHeader: View {
    let host: String?

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            Image("BrandMark")
                .resizable()
                .scaledToFit()
                .frame(width: 52, height: 52)
                .clipShape(.rect(cornerRadius: 12, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(Color(.separator), lineWidth: 0.5)
                }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("TelePilot").font(.title3.weight(.bold))
                Text("个人 Telegram 自动化服务器的原生管理客户端")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let host {
                    Label(host, systemImage: "server.rack")
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                        .textSelection(.enabled)
                        .padding(.top, 2)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("overview.brand")
    }
}
