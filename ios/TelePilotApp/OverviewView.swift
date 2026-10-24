import SwiftUI

struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @Environment(OverviewLayout.self) private var layout
    @Environment(WorkspacePreferences.self) private var preferences

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(model.serverURL?.host ?? "TelePilot", systemImage: "server.rack")
                            .font(.headline).textSelection(.enabled)
                        if let health = model.health {
                            Text((model.healthError == nil ? "" : "上次读取：") + (health.db.ok && health.redis.ok && health.alembic.ok ? "基础服务可用" : "基础服务需要检查"))
                                .font(.title2.weight(.semibold))
                            Text(model.healthError == nil ? "进程存活与账号启用状态分别显示。" : "以下为上次读取结果，并非当前在线状态。")
                                .font(.subheadline).foregroundStyle(.secondary)
                            if model.isSyncingAfterAction {
                                Label("操作已提交，正在同步最新状态…", systemImage: "arrow.triangle.2.circlepath")
                                    .font(.footnote).foregroundStyle(.secondary)
                                    .accessibilityIdentifier("overview.syncing")
                            }
                        } else {
                            Text(model.isRefreshing ? "正在读取服务状态…" : "尚无服务状态")
                                .font(.title2.weight(.semibold))
                        }
                    }
                    .padding(.vertical, 8)
                }
                if !preferences.hiddenHomeModules.contains("pending") {
                    Section("待确认") {
                        Button { model.requestedRoute = .action("") } label: {
                            Label("助手待确认事项（\(model.pendingActions.count)）", systemImage: "checkmark.bubble")
                        }
                        if let error = model.workspaceError { Text(error).font(.footnote).foregroundStyle(.secondary) }
                    }
                }
                if !preferences.hiddenHomeModules.contains("favorites") {
                    Section("常用账号") {
                        let favorites = model.accounts.filter { preferences.isFavoriteAccount($0.id) }
                        if favorites.isEmpty { Text("在账号详情中收藏，便可从这里快速打开。").font(.footnote).foregroundStyle(.secondary) }
                        ForEach(favorites) { account in
                            NavigationLink(AccountPresentation.name(account)) { AccountDetailView(id: account.id) }
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
                    NavigationLink("自定义模块顺序") { OverviewCustomizationView() }
                        .accessibilityIdentifier("overview.customizeButton")
                    if let date = model.lastUpdated {
                        LabeledContent("完整刷新", value: date.formatted(date: .omitted, time: .standard))
                    }
                    Button { Task { await model.refresh() } } label: {
                        Label(model.isRefreshing ? "正在刷新…" : "刷新状态", systemImage: "arrow.clockwise")
                            .frame(minHeight: 44)
                    }
                    .disabled(model.isRefreshing)
                } footer: { Text("模块顺序仅保存在本机；服务器数据仍由 App 在前台读取。") }
            }
            .navigationTitle("概览")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    NavigationLink { GlobalSearchView() } label: { Label("全局搜索", systemImage: "magnifyingglass") }
                        .accessibilityIdentifier("overview.search")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await model.refresh() } } label: {
                        Label("刷新状态", systemImage: "arrow.clockwise")
                    }
                    .disabled(model.isRefreshing)
                    .accessibilityIdentifier("overview.refresh")
                }
            }
            .refreshable { await model.refresh(); await model.refreshWorkspace() }
            .accessibilityIdentifier("overview.screen")
        }
    }

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
