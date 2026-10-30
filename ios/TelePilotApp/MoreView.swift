import SwiftUI

enum MoreDestination: Hashable {
    case overview
    case accounts
    case account(Int)
    case settings
    case about
    case module(ManagementModule)

    /// 没放进 Dock 的入口在「更多」里对应的页面。
    static func workspace(_ item: DockItem) -> MoreDestination? {
        switch item {
        case .overview: .overview
        case .plugins: .module(.plugins)
        case .interaction: .module(.interaction)
        case .ai: .module(.ai)
        case .operations: .module(.operations)
        case .accounts: .accounts
        case .logs: .module(.logs)
        case .ledger: .module(.ledger)
        case .more, .agent: nil
        }
    }
}

/// 「更多」与自选 Dock 格共用的推入页面。
struct MoreDestinationView: View {
    let destination: MoreDestination
    var body: some View {
        switch destination {
        case .overview: OverviewView(embedded: true)
        case .accounts: AccountsView(embedded: true)
        case .account(let id): AccountDetailView(id: id)
        case .settings: SettingsView(embedded: true)
        case .about: AboutView()
        case .module(let module): ManagementModuleView(module: module)
        }
    }
}

/// 「更多」（0.18.0 起按信息架构分组）：紧急停止 → 常用（被移出 Dock 的概览／插件／交互）→ 管理（账号 · AI · 指令与任务 ·
/// 资金台账 · 日志）→ 工具与设置（Webhook · 服务器设置 · 备份与维护 · 命中调试）→ App（App 设置 · 关于与更新）。
/// 已在 Dock 里的入口不重复列出；服务器关闭了对应模块的入口隐藏（与 Web 侧栏一致），旧服务器没有模块信息时照常显示。
struct MoreView: View {
    @Binding var path: [MoreDestination]
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    /// 全局搜索：左上角放大镜展开系统搜索栏，列表换成搜索结果（与概览、插件、AI 页同一写法）。
    @State private var searchText = ""
    @State private var searching = false

    /// 当前 Dock 里实际显示的入口：这些不在「更多」里重复出现。
    private var dockItems: [DockItem] { preferences.dockItems(disabledModules: model.disabledModules) }

    private func visible(_ item: DockItem) -> Bool {
        !dockItems.contains(item) && (item.platformModule.map { model.isModuleEnabled($0) } ?? true)
    }
    private func visible(_ module: ManagementModule) -> Bool {
        if let item = module.dockItem, dockItems.contains(item) { return false }
        return module.platformModule.map { model.isModuleEnabled($0) } ?? true
    }

    private var commonItems: [DockItem] { [DockItem.overview, .plugins, .interaction].filter { visible($0) } }
    private var managementItems: [DockItem] { [DockItem.accounts, .ai, .operations, .ledger, .logs].filter { visible($0) } }
    /// 三级入口，不能放进 Dock。命中调试（原生页 DispatchDebugView）在本组单独一行，「日志与诊断 › 高级诊断」里保留同一入口。
    private var toolModules: [ManagementModule] { [ManagementModule.webhooks, .system, .maintenance].filter { visible($0) } }

    var body: some View {
        List {
            if searching {
                GlobalSearchResults(query: searchText)
            } else {
                moreContent
            }
        }
        .pageSearch(text: $searchText, isActive: $searching, prompt: "管理模块、账号或插件", identifier: "more.search")
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("更多")
        .accessibilityIdentifier("more.screen")
        .navigationDestination(for: MoreDestination.self) { MoreDestinationView(destination: $0) }
    }

    @ViewBuilder private var moreContent: some View {
        EmergencyStopSection()

        if !commonItems.isEmpty {
            Section("常用") {
                ForEach(commonItems) { item in workspaceRow(item) }
            }
        }

        if !managementItems.isEmpty {
            Section("管理") {
                ForEach(managementItems) { item in workspaceRow(item) }
            }
        }

        Section("工具与设置") {
            ForEach(toolModules) { module in
                NavigationLink(value: MoreDestination.module(module)) {
                    Label(module.title, systemImage: module.symbol)
                }
            }
            // 命中调试模块（dispatch_debug）关闭时与 Web 侧栏一样隐藏；旧服务器没有模块信息时照常显示。
            if model.isModuleEnabled("dispatch_debug") {
                DispatchDebugEntryLink(detail: nil, identifier: "more.dispatchDebug")
            }
        }

        Section("App") {
            NavigationLink(value: MoreDestination.settings) {
                Label("App 设置", systemImage: "apps.iphone")
            }
            .accessibilityIdentifier("more.settings")
            NavigationLink(value: MoreDestination.about) {
                Label {
                    HStack {
                        Text("关于与更新")
                        Spacer()
                        if model.serverUpdateAvailable {
                            Text("服务器有新版本").font(.caption.weight(.semibold)).foregroundStyle(.tint)
                        }
                    }
                } icon: { Image(systemName: "info.circle") }
            }
            .accessibilityIdentifier("more.about")
        }
    }

    /// 一级／二级入口的一行：账号带数量；其余用完整名称（与页面标题一致）。
    /// 只给账号、AI、概览、插件、交互加标识；指令与任务、台账、日志沿用标题查找（与之前「管理」模块行一致）。
    @ViewBuilder private func workspaceRow(_ item: DockItem) -> some View {
        if let destination = MoreDestination.workspace(item) {
            let link = NavigationLink(value: destination) {
                if item == .accounts {
                    Label {
                        LabeledContent("账号") { Text("\(model.accounts.count)") }
                    } icon: { Image(systemName: item.symbol) }
                } else {
                    Label(item.longTitle, systemImage: item.symbol)
                }
            }
            if [DockItem.accounts, .ai, .overview, .plugins, .interaction].contains(item) {
                link.accessibilityIdentifier("more.\(item.rawValue)")
            } else {
                link
            }
        }
    }
}
