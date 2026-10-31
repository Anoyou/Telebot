import SwiftUI
import TelePilotCore

@main
struct TelePilotApp: App {
    @UIApplicationDelegateAdaptor(PushNotificationAppDelegate.self) private var appDelegate
    @State private var model = AppModel()
    @State private var lock = AppLockModel()
    @State private var overviewLayout = OverviewLayout()
    @State private var preferences = WorkspacePreferences()
    @State private var push = PushNotificationManager()
    @AppStorage(AppAppearance.storageKey) private var appearanceRaw = AppAppearance.system.rawValue
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some Scene {
        WindowGroup {
            Group {
                switch model.phase {
                case .starting:
                    ProgressView("连接 TelePilot…")
                case .signedOut:
                    LoginView()
                case .signedIn:
                    MainTabs()
                }
            }
            .environment(model)
            .environment(lock)
            .preferredColorScheme(AppAppearance(rawValue: appearanceRaw)?.colorScheme)
            .environment(overviewLayout)
            .environment(preferences)
            .environment(push)
            .tint(Color("AccentColor"))
            // 已登录时请求被 Cloudflare Access 拦截（登录过期、被 302 到 cloudflareaccess.com）：直接打开 Access 设置一键重新验证，而不是只在各页显示错误。
            .sheet(isPresented: Binding(
                get: { model.cloudflareAccessPrompt && model.phase == .signedIn },
                set: { model.cloudflareAccessPrompt = $0 }
            )) {
                NavigationStack { CloudflareAccessSettingsView(presentedAsSheet: true) }
                    .environment(model)
            }
            .task {
                PushNotificationAppDelegate.manager = push
                push.onRoute = { model.requestedRoute = $0 }
                push.canRoute = { !lock.isObscured && scenePhase == .active }
                // 第十一轮：读取通知权限不阻塞解锁与启动；推送注册只由下方 user.id 变化触发一次（不再与这里重复）。
                Task { await push.prepare() }
                if lock.isObscured { await lock.unlock() }
                if !lock.isObscured { await model.start() }
                push.deliverPendingRoute(unlocked: !lock.isObscured)
            }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active {
                    lock.resume()
                    // 回前台：距上次读取不足 30 秒时不重读；推送注册 24 小时内已成功则只更新本机权限状态。
                    if !lock.isObscured { Task { await model.refreshOnForeground(); await push.connect(model: model) } }
                    push.deliverPendingRoute(unlocked: !lock.isObscured)
                } else if phase == .background {
                    lock.suspend()
                }
            }
            .onChange(of: lock.isObscured) { _, obscured in
                guard !obscured, scenePhase == .active else { return }
                Task {
                    await model.start()
                    await model.refreshOnForeground()
                    push.deliverPendingRoute(unlocked: !lock.isObscured)
                }
            }
            .background {
                AppLockShield(lock: lock).frame(width: 0, height: 0)
                if reduceTransparency {
                    Color(.systemGroupedBackground).ignoresSafeArea()
                }
            }
            .onChange(of: model.user?.id) { _, id in
                preferences.setScope(server: model.serverURL?.absoluteString ?? "", userID: id ?? 0)
                if id != nil { Task { await push.connect(model: model); push.deliverPendingRoute(unlocked: !lock.isObscured) } }
            }
        }
    }
}

private struct MainTabs: View {
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences
    @State private var selected: DockItem = .overview
    // Agent 页隐藏 Dock，最小化按钮返回进入 Agent 前所在的标签页。
    @State private var previousTab: DockItem = .overview
    @State private var morePath: [MoreDestination] = []
    /// 自选 Dock 格各自的导航路径（账号、交互、日志等推送路由时用）。
    @State private var tabPaths: [DockItem: [MoreDestination]] = [:]
    @State private var modal: WorkspaceModal?
    /// 值守全局提示：「恢复生产」确认框、请求中、失败说明。
    @State private var confirmRestoreProduction = false
    @State private var restoringProduction = false
    @State private var restoreProductionFailure: String?

    /// 实际显示的中间格：用户自选（App 设置 › 自定义 Dock）经服务器模块开关调整后的结果。
    private var dockItems: [DockItem] { preferences.dockItems(disabledModules: model.disabledModules) }

    var body: some View {
        TabView(selection: $selected) {
            ForEach(dockItems) { item in
                Tab(item.title, systemImage: item.symbol, value: item) {
                    tabRoot(item)
                }
                .badge(badge(item))
                .accessibilityIdentifier("dock.\(item.rawValue)")
            }

            Tab("更多", systemImage: DockItem.more.symbol, value: DockItem.more) {
                NavigationStack(path: $morePath) { MoreView(path: $morePath) }
            }
            .badge(model.serverUpdateAvailable ? Text("新") : nil)
            .accessibilityIdentifier("dock.more")

            if #available(iOS 27.0, *) {
                Tab("Agent", systemImage: DockItem.agent.symbol, value: DockItem.agent, role: .prominent) {
                    AssistantView(embedded: true) { selected = previousTab }
                }
                .badge(model.pendingActions.count)
                .accessibilityIdentifier("dock.assistant")
            } else {
                Tab("Agent", systemImage: DockItem.agent.symbol, value: DockItem.agent) {
                    AssistantView(embedded: true) { selected = previousTab }
                }
                .badge(model.pendingActions.count)
                .accessibilityIdentifier("dock.assistant")
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .tabBarMinimizeBehavior(.onScrollDown)
        // 值守全局提示（第 20 轮，对齐 Web 每页顶部的 SafeWatchBar）：Dock 上方，每个 Tab 都能看到。
        .safeWatchAccessory(isEnabled: model.safeWatchActive) {
            if let profile = model.runtimeProfile {
                SafeWatchAccessory(profile: profile, restoring: restoringProduction) { confirmRestoreProduction = true }
            }
        }
        .alert("恢复生产？", isPresented: $confirmRestoreProduction) {
            Button("取消", role: .cancel) {}
            Button("确认恢复生产") { Task { await restoreProduction() } }
        } message: {
            Text("按进入值守前的快照逐项还原模块开关，插件投递、定时任务和资金动作恢复。")
        }
        .alert("恢复生产没有完成", isPresented: Binding(get: { restoreProductionFailure != nil }, set: { if !$0 { restoreProductionFailure = nil } })) {
            Button("好", role: .cancel) {}
        } message: {
            Text(restoreProductionFailure ?? "")
        }
        // 与 Web 一样每 30 秒重读运行模式：在别的设备上进入值守 / 恢复生产，这里也跟上。
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(30))
                guard !Task.isCancelled else { return }
                await model.refreshRuntimeProfile()
            }
        }
        .sheet(item: $modal, onDismiss: { Task { await model.refreshWorkspace() } }) { route in
            Group {
                switch route {
                case .action(let id): AssistantInboxView(actionID: id)
                }
            }
            .id(route.id)
            .presentationDragIndicator(.visible)
        }
        .task {
            preferences.setScope(server: model.serverURL?.absoluteString ?? "", userID: model.user?.id ?? 0)
            keepSelectionVisible()
            await model.refreshWorkspace()
            openRequestedRoute()
        }
        .onChange(of: model.requestedRoute) { _, _ in openRequestedRoute() }
        // 服务器「常用入口」（网页 界面偏好）：本机没自定义 Dock 时跟随它；旧服务器没有该字段时保持现有 Dock。
        .onChange(of: model.serverPrimaryNav) { _, value in
            if let value { preferences.setServerPrimaryNav(value.paths) }
        }
        // 用户改了 Dock、或服务器关掉了某个模块：当前格不在 Dock 里时回到第一格，不停在一个看不见的 Tab 上。
        .onChange(of: dockItems) { _, _ in keepSelectionVisible() }
        .onChange(of: selected) { old, new in
            if new == .agent && old != .agent { previousTab = old }
        }
    }

    private func restoreProduction() async {
        restoringProduction = true
        defer { restoringProduction = false }
        do {
            try await model.restoreProduction()
        } catch {
            restoreProductionFailure = (error as? APIError)?.isOutcomeUncertain == true
                ? "未能确认是否已恢复生产，已重新读取运行模式；提示条还在就说明仍在值守。"
                : "恢复失败：\(OpsFormat.errorMessage(error))"
        }
    }

    @ViewBuilder private func tabRoot(_ item: DockItem) -> some View {
        switch item {
        case .overview: OverviewView()
        case .plugins: NavigationStack { PluginManagementView() }
        case .ai: NavigationStack { AIWorkspaceView() }
        default:
            NavigationStack(path: pathBinding(item)) {
                Group {
                    switch item {
                    case .interaction: InteractionCenterView(isTabRoot: true)
                    case .operations: ManagementModuleView(module: .operations)
                    case .accounts: AccountsView(embedded: true)
                    case .logs: LogsView()
                    case .ledger: LedgerView()
                    default: EmptyView()
                    }
                }
                .navigationDestination(for: MoreDestination.self) { MoreDestinationView(destination: $0) }
            }
        }
    }

    private func badge(_ item: DockItem) -> Text? {
        switch item {
        case .plugins: model.pluginUpdateCount > 0 ? Text("\(model.pluginUpdateCount)") : nil
        default: nil
        }
    }

    private func pathBinding(_ item: DockItem) -> Binding<[MoreDestination]> {
        Binding(get: { tabPaths[item] ?? [] }, set: { tabPaths[item] = $0 })
    }

    private func keepSelectionVisible() {
        guard selected != .more, selected != .agent, !dockItems.contains(selected) else { return }
        selected = dockItems.first ?? .more
        if previousTab != .more, !dockItems.contains(previousTab) { previousTab = selected }
    }

    /// 打开某个入口：在 Dock 里就切到那一格，否则进「更多」里对应的页面；`then` 是在该页面之上继续推入的页面。
    private func open(_ item: DockItem, then: [MoreDestination] = []) {
        modal = nil
        if dockItems.contains(item) {
            selected = item
            tabPaths[item] = then
        } else {
            selected = .more
            morePath = (MoreDestination.workspace(item).map { [$0] } ?? []) + then
        }
    }

    private func openMore(_ path: [MoreDestination]) {
        modal = nil
        selected = .more
        morePath = path
    }

    private func openRequestedRoute() {
            guard let route = model.requestedRoute else { return }
            switch route {
            // 助手只保留 Agent 标签页一个实例，避免两个会话状态与流式订阅并存。
            case .assistant(let id): modal = nil; model.requestAssistantSession(id); selected = .agent
            case .action(let id): modal = .action(id.isEmpty ? nil : id)
            case .account(let id): open(.accounts, then: [.account(id)])
            case .overview: open(.overview)
            case .accounts: open(.accounts)
            case .settings: openMore([.settings])
            case .plugins: open(.plugins)
            case .ai: open(.ai)
            case .interaction: open(.interaction)
            case .operations: open(.operations)
            case .logs: open(.logs)
            case .ledger: open(.ledger)
            case .webhooks: openMore([.module(.webhooks)])
            case .maintenance: openMore([.module(.maintenance)])
            }
            model.requestedRoute = nil
    }
}

private enum WorkspaceModal: Identifiable {
    case action(String?)
    var id: String {
        switch self {
        case .action(let id): "action:" + (id ?? "all")
        }
    }
}

struct NoticeView: View {
    let title: String
    var message: String?
    var symbol = "exclamationmark.circle"

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
            if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

struct ReadOnlyRow: View {
    let title: String
    let value: String

    var body: some View {
        LabeledContent {
            Text(value).multilineTextAlignment(.trailing).textSelection(.enabled)
        } label: { Text(title) }
    }
}
