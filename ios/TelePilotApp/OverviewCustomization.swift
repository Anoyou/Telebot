import Foundation
import Observation
import SwiftUI
import TelePilotCore

enum OverviewModule: String, CaseIterable, Identifiable, Codable {
    case health, workers, resources

    var id: String { rawValue }
    var title: String {
        switch self {
        case .health: "基础服务"
        case .workers: "账号运行进程"
        case .resources: "主机资源"
        }
    }
    var symbol: String {
        switch self {
        case .health: "heart.text.square"
        case .workers: "person.2"
        case .resources: "gauge.with.dots.needle.67percent"
        }
    }
}

/// 概览顶部固定区块（不参与排序，只能显示或隐藏）。
enum OverviewHomeSection: String, CaseIterable, Identifiable {
    case attention, metrics, quick, favorites
    var id: String { rawValue }
    var title: String {
        switch self {
        case .attention: "需要处理"
        case .metrics: "关键数字"
        case .quick: "快捷操作"
        case .favorites: "收藏"
        }
    }
    var symbol: String {
        switch self {
        case .attention: "exclamationmark.bubble"
        case .metrics: "number.square"
        case .quick: "square.grid.2x2"
        case .favorites: "star"
        }
    }
}

@MainActor @Observable
final class OverviewLayout {
    private(set) var modules: [OverviewModule]
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let key = "telepilot.overview.order"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        guard let data = defaults.data(forKey: key),
              let decoded = try? JSONDecoder().decode([OverviewModule].self, from: data),
              Set(decoded).count == decoded.count,
              Set(decoded) == Set(OverviewModule.allCases) else {
            modules = [.health, .workers, .resources]
            return
        }
        modules = decoded
    }

    func move(from: IndexSet, to: Int) {
        modules.move(fromOffsets: from, toOffset: to)
        save()
    }

    func moveUp(_ module: OverviewModule) {
        guard let index = modules.firstIndex(of: module), index > 0 else { return }
        modules.swapAt(index, index - 1)
        save()
    }

    func moveDown(_ module: OverviewModule) {
        guard let index = modules.firstIndex(of: module), index < modules.count - 1 else { return }
        modules.swapAt(index, index + 1)
        save()
    }

    func reset() {
        modules = OverviewModule.allCases
        save()
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(modules) else { return }
        defaults.set(data, forKey: key)
    }
}

struct OverviewCustomizationView: View {
    @Environment(OverviewLayout.self) private var layout
    @Environment(WorkspacePreferences.self) private var preferences

    var body: some View {
        Form {
            Section {
                ForEach(layout.modules) { module in
                    HStack {
                        Label(module.title, systemImage: module.symbol)
                        Spacer()
                        Toggle("显示", isOn: Binding(get: { !preferences.hiddenHomeModules.contains(module.id) }, set: { visible in
                            if visible { preferences.hiddenHomeModules.remove(module.id) }
                            else { preferences.hiddenHomeModules.insert(module.id) }
                        })).labelsHidden()
                        Button("上移", systemImage: "chevron.up") { layout.moveUp(module) }
                            .buttonStyle(.borderless)
                            .labelStyle(.iconOnly)
                            .disabled(layout.modules.first == module)
                            .accessibilityIdentifier("overview.moveUp.\(module.id)")
                            .frame(minWidth: 44, minHeight: 44)
                        Button("下移", systemImage: "chevron.down") { layout.moveDown(module) }
                            .buttonStyle(.borderless)
                            .labelStyle(.iconOnly)
                            .disabled(layout.modules.last == module)
                            .accessibilityIdentifier("overview.moveDown.\(module.id)")
                            .frame(minWidth: 44, minHeight: 44)
                    }
                }
                .onMove(perform: layout.move)
            } header: { Text("模块顺序") } footer: {
                Text("调整本机首页模块的显示与顺序。")
            }
            Section {
                Button("恢复默认顺序", role: .destructive) { layout.reset() }
                    .accessibilityIdentifier("overview.resetLayout")
            }
            Section {
                ForEach(OverviewHomeSection.allCases) { section in
                    Toggle(isOn: Binding(get: { !preferences.hiddenHomeModules.contains(section.rawValue) }, set: {
                        if $0 { preferences.hiddenHomeModules.remove(section.rawValue) } else { preferences.hiddenHomeModules.insert(section.rawValue) }
                    })) {
                        Label(section.title, systemImage: section.symbol)
                    }
                    .accessibilityIdentifier("overview.toggle.\(section.rawValue)")
                }
            } header: { Text("快捷内容") } footer: {
                Text("“需要处理”会汇总账号、插件、AI、日志与台账里需要你处理的事项，建议保持开启。")
            }
        }
        .navigationTitle("自定义概览")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { EditButton() }
        .accessibilityIdentifier("overview.customize")
    }
}

struct GlobalSearchView: View {
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    @State private var query = ""

    private var modules: [OverviewModule] {
        OverviewModule.allCases.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) }
    }
    private var managementModules: [ManagementModule] {
        ManagementModule.allCases.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) }
    }
    private var plugins: [JSONValue] {
        model.plugins.filter {
            query.isEmpty || ($0["display_name"]?.stringValue ?? "").localizedCaseInsensitiveContains(query)
                || ($0["key"]?.stringValue ?? "").localizedCaseInsensitiveContains(query)
        }
    }
    private var accounts: [Account] {
        guard !query.isEmpty else { return model.accounts }
        return model.accounts.filter {
            AccountPresentation.name($0).localizedCaseInsensitiveContains(query)
                || $0.phone.localizedCaseInsensitiveContains(query)
                || ($0.tgUsername?.localizedCaseInsensitiveContains(query) ?? false)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                if !managementModules.isEmpty {
                    Section("管理模块") {
                        ForEach(managementModules) { module in
                            NavigationLink { ManagementModuleView(module: module) } label: { Label(module.title, systemImage: module.symbol) }
                        }
                    }
                }
                if !modules.isEmpty {
                    Section("服务模块") {
                        ForEach(modules) { module in
                            NavigationLink(module.title) { OverviewModuleView(module: module) }
                                .accessibilityIdentifier("search.module.\(module.id)")
                        }
                    }
                }
                Section("账号") {
                    if !accounts.isEmpty {
                        ForEach(accounts) { account in
                            NavigationLink {
                                AccountDetailView(id: account.id)
                            } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(AccountPresentation.name(account))
                                    Text(PrivacyFormatting.phone(account.phone)).font(.footnote).foregroundStyle(.secondary)
                                }
                            }
                            .accessibilityIdentifier("search.account.\(account.id)")
                        }
                    } else if model.accountsLoaded {
                        Text("没有匹配的账号").foregroundStyle(.secondary)
                    } else {
                        ProgressView("正在读取账号…")
                    }
                }
                if !plugins.isEmpty {
                    Section("插件") {
                        ForEach(Array(plugins.enumerated()), id: \.offset) { _, plugin in
                            NavigationLink {
                                ManagementPluginDetailView(feature: plugin, accountID: nil, accountState: nil)
                            } label: {
                                Label(plugin["display_name"]?.stringValue ?? plugin["key"]?.stringValue ?? "插件", systemImage: "puzzlepiece.extension")
                            }
                        }
                    }
                }
                if modules.isEmpty && accounts.isEmpty && model.accountsLoaded {
                    ContentUnavailableView.search(text: query)
                }
            }
            .navigationTitle("全局搜索")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "管理模块、账号或插件")
            .task { if !model.accountsLoaded { await model.refresh() }; await model.refreshWorkspace() }
            .accessibilityIdentifier("search.screen")
        }
    }
}

struct OverviewModuleView: View {
    let module: OverviewModule
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            if module == .resources {
                if let error = model.resourcesError { NoticeView(title: "资源数据已过期", message: error) }
                if let resources = model.resources {
                    ResourceStatusRow(title: "处理器", value: resources.host.cpuPercent, symbol: "cpu")
                    ResourceStatusRow(title: "内存", value: resources.host.memoryUsedPercent, symbol: "memorychip")
                    ResourceStatusRow(title: "磁盘", value: resources.host.diskUsedPercent, symbol: "internaldrive")
                } else { ProgressView("正在读取资源…") }
            } else if let error = model.healthError {
                NoticeView(title: "服务数据已过期", message: error)
            }
            if module != .resources, let health = model.health {
                switch module {
                case .health:
                    ProbeStatusRow(name: "数据库", symbol: "externaldrive", probe: health.db)
                    ProbeStatusRow(name: "缓存服务", symbol: "memorychip", probe: health.redis)
                    ProbeStatusRow(name: "数据库结构", symbol: "checklist", probe: health.alembic)
                case .workers:
                    ReadOnlyRow(title: "应运行进程", value: "\(health.workers.runtimeDesiredRunning)")
                    ReadOnlyRow(title: "其中已存活", value: "\(health.workers.runtimeDesiredRunningAlive)")
                    ReadOnlyRow(title: "全部存活进程", value: "\(health.workers.runtimeAlive)")
                    ReadOnlyRow(title: "存在失败记录", value: "\(health.workers.runtimeFailing)")
                case .resources: EmptyView()
                }
            }
        }
        .navigationTitle(module.title)
        .refreshable { await model.refresh() }
    }
}
