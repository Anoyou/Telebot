import SwiftUI

@main
struct TelePilotApp: App {
    @UIApplicationDelegateAdaptor(PushNotificationAppDelegate.self) private var appDelegate
    @State private var model = AppModel()
    @State private var lock = AppLockModel()
    @State private var overviewLayout = OverviewLayout()
    @State private var preferences = WorkspacePreferences()
    @State private var push = PushNotificationManager()
    @State private var sceneHidden = false
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
            .environment(overviewLayout)
            .environment(preferences)
            .environment(push)
            .tint(Color("AccentColor"))
            .task {
                PushNotificationAppDelegate.manager = push
                push.onRoute = { model.requestedRoute = $0 }
                push.canRoute = { !lock.isObscured && scenePhase == .active }
                await push.prepare()
                if lock.isObscured { await lock.unlock() }
                if !lock.isObscured { await model.start() }
                if model.phase == .signedIn { await push.connect(model: model) }
                push.deliverPendingRoute(unlocked: !lock.isObscured)
            }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active {
                    sceneHidden = false
                    lock.resume()
                    if !lock.isObscured { Task { await model.refresh(); await model.refreshWorkspace(); await push.connect(model: model) } }
                    push.deliverPendingRoute(unlocked: !lock.isObscured)
                } else {
                    sceneHidden = true
                    if phase == .background { lock.suspend() }
                }
            }
            .onChange(of: lock.isObscured) { _, obscured in
                guard !obscured, scenePhase == .active else { return }
                Task {
                    await model.start()
                    if model.phase == .signedIn { await model.refresh() }
                    push.deliverPendingRoute(unlocked: !lock.isObscured)
                }
            }
            .background {
                PrivacyShield(lock: lock, hidden: sceneHidden).frame(width: 0, height: 0)
                if reduceTransparency {
                    Color(.systemGroupedBackground).ignoresSafeArea()
                }
            }
            .overlay {
                // A normal overlay also obscures this scene in the task switcher.
                if sceneHidden { Color(.systemBackground).ignoresSafeArea().overlay(Label("TelePilot", systemImage: "lock.shield")) }
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
    @State private var morePath: [MoreDestination] = []
    @State private var modal: WorkspaceModal?

    var body: some View {
        TabView(selection: $selected) {
            Tab("概览", systemImage: DockItem.overview.symbol, value: DockItem.overview) {
                OverviewView()
            }
            .accessibilityIdentifier("dock.overview")

            Tab("插件", systemImage: DockItem.plugins.symbol, value: DockItem.plugins) {
                NavigationStack { PluginManagementView() }
            }
            .accessibilityIdentifier("dock.plugins")

            Tab("AI", systemImage: DockItem.ai.symbol, value: DockItem.ai) {
                NavigationStack { AIWorkspaceView() }
            }
            .accessibilityIdentifier("dock.ai")

            Tab("更多", systemImage: DockItem.more.symbol, value: DockItem.more) {
                NavigationStack(path: $morePath) { MoreView(path: $morePath) }
            }
            .accessibilityIdentifier("dock.more")

            if #available(iOS 27.0, *) {
                Tab("Agent", systemImage: DockItem.agent.symbol, value: DockItem.agent, role: .prominent) {
                    AssistantView(embedded: true)
                }
                .badge(model.pendingActions.count)
                .accessibilityIdentifier("dock.assistant")
            } else {
                Tab("Agent", systemImage: DockItem.agent.symbol, value: DockItem.agent) {
                    AssistantView(embedded: true)
                }
                .badge(model.pendingActions.count)
                .accessibilityIdentifier("dock.assistant")
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .tabBarMinimizeBehavior(.onScrollDown)
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
            await model.refreshWorkspace()
            openRequestedRoute()
        }
        .onChange(of: model.requestedRoute) { _, _ in openRequestedRoute() }
    }

    private func openRequestedRoute() {
            guard let route = model.requestedRoute else { return }
            switch route {
            // 助手只保留 Agent 标签页一个实例，避免两个会话状态与流式订阅并存。
            case .assistant(let id): modal = nil; model.requestAssistantSession(id); selected = .agent
            case .action(let id): modal = .action(id.isEmpty ? nil : id)
            case .account(let id): modal = nil; selected = .more; morePath = [.accounts, .account(id)]
            case .overview: modal = nil; selected = .overview
            case .accounts: modal = nil; selected = .more; morePath = [.accounts]
            case .settings: modal = nil; selected = .more; morePath = [.settings]
            case .plugins: modal = nil; selected = .plugins
            case .ai: modal = nil; selected = .ai
            case .operations: modal = nil; selected = .more; morePath = [.module(.operations)]
            case .logs: modal = nil; selected = .more; morePath = [.module(.logs)]
            case .ledger: modal = nil; selected = .more; morePath = [.module(.ledger)]
            case .webhooks: modal = nil; selected = .more; morePath = [.module(.webhooks)]
            case .maintenance: modal = nil; selected = .more; morePath = [.module(.maintenance)]
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
