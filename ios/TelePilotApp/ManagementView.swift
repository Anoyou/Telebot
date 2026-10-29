import SwiftUI
import TelePilotCore

enum ManagementModule: String, CaseIterable, Identifiable {
    case plugins, operations, interaction, ai, logs, ledger, webhooks, system, maintenance
    var id: String { rawValue }
    static var moreCases: [ManagementModule] {
        [.operations, .interaction, .logs, .ledger, .webhooks, .system, .maintenance]
    }
    var title: String {
        switch self {
        case .plugins: "插件中心"
        case .operations: "指令与任务"
        case .interaction: "交互"
        case .ai: "AI 管理"
        case .logs: "日志与诊断"
        case .ledger: "资金台账"
        case .webhooks: "Webhook"
        case .system: "服务器设置"
        case .maintenance: "备份与维护"
        }
    }
    var symbol: String {
        switch self {
        case .plugins: "puzzlepiece.extension"
        case .operations: "terminal"
        case .interaction: "bubble.left.and.text.bubble.right"
        case .ai: "cpu"
        case .logs: "list.bullet.rectangle"
        case .ledger: "book.closed"
        case .webhooks: "point.3.connected.trianglepath.dotted"
        case .system: "server.rack"
        case .maintenance: "wrench.and.screwdriver"
        }
    }
    /// 对应的服务器平台模块；模块关闭时「更多」与全局搜索里隐藏入口（与 Web 侧栏一致），旧服务器没有信息时照常显示。
    var platformModule: String? {
        switch self {
        case .interaction: "interaction_bot"
        case .ai: "ai"
        case .ledger: "ledger"
        case .webhooks: "webhooks"
        default: nil
        }
    }
    /// 可以放进 Dock 的同一入口；已在 Dock 里时「更多」不再重复列出。
    var dockItem: DockItem? {
        switch self {
        case .plugins: .plugins
        case .interaction: .interaction
        case .ai: .ai
        case .operations: .operations
        case .logs: .logs
        case .ledger: .ledger
        default: nil
        }
    }
}

struct ManagementView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("服务器工作台") {
                    ForEach(ManagementModule.moreCases) { module in
                        NavigationLink { ManagementModuleView(module: module) } label: { Label(module.title, systemImage: module.symbol) }
                    }
                }
            }.navigationTitle("管理").navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct ManagementModuleView: View {
    let module: ManagementModule
    @Environment(AppModel.self) private var model
    var body: some View {
        Group {
            if module == .plugins { PluginManagementView(isTabRoot: false) }
            else if module == .ai { AIWorkspaceView().navigationBarTitleDisplayMode(.inline) }
            else if module == .logs { LogsView() }
            else if module == .ledger { LedgerView() }
            else if module == .interaction { InteractionCenterView() }
            else {
                List {
                    switch module {
                    case .plugins: EmptyView()
                    case .operations: operationSections
                    case .ai: aiSections
                    case .logs, .ledger, .interaction: EmptyView() // 由 LogsView / LedgerView / InteractionCenterView 承载
                    case .webhooks: webhookSections
                    case .system: systemSections
                    case .maintenance: maintenanceSections
                    }
                }.navigationTitle(module.title).navigationBarTitleDisplayMode(.inline)
            }
        }
    }

    @ViewBuilder private var operationSections: some View {
        Section("指令") {
            collection("指令模板", "/api/commands/templates", kind: .editable, create: "新建指令模板", itemPath: "/api/commands/templates/{id}", symbol: "text.badge.plus")
            collection("指令别名", "/api/aliases", kind: .editable, create: "添加指令别名", itemPath: "/api/aliases/{id}", symbol: "arrow.triangle.swap")
            document("内置指令", "/api/commands/builtin", symbol: "terminal")
            NavigationLink { AICommandEnablementView() } label: { Label("AI 指令与账号启用", systemImage: "text.bubble") }
        }
        Section("模板预览与测试") {
            document("模板变量与示例", "/api/message-templates/catalog", symbol: "curlybraces")
            ManagementActionLink(action: .init(title: "预览消息模板", path: "/api/message-templates/render", explanation: "仅渲染模板，不发送消息。"), symbol: "eye")
            ManagementActionLink(action: .init(title: "测试发送模板", path: "/api/message-templates/test-send", explanation: "将通过指定账号向目标会话发送真实消息。"), symbol: "paperplane")
        }
        Section("按账号配置") {
            ForEach(model.accounts) { account in
                NavigationLink { AccountManagementSchedulerView(id: account.id) } label: {
                    Label(ManagementText.accountName(account) + " · 定时任务与白名单", systemImage: "calendar.badge.clock")
                }
                NavigationLink { AccountManagementView(id: account.id) } label: {
                    Label(ManagementText.accountName(account) + " · 全部账号配置", systemImage: "slider.horizontal.3")
                }
            }
        }
    }
    // AI 模块由 AIWorkspaceView 承载（见 body）；此处仅保留原生入口，避免再暴露原始 JSON 文档。
    @ViewBuilder private var aiSections: some View {
        Section {
            NavigationLink { AIWorkspaceView() } label: { Label("打开 AI 管理", systemImage: "cpu") }
            NavigationLink { AICommandEnablementView() } label: { Label("AI 指令与账号启用", systemImage: "text.bubble") }
        }
    }
    @ViewBuilder private var webhookSections: some View {
        Section { document("Webhook 服务状态", "/api/webhooks", symbol: "dot.radiowaves.left.and.right") }
        Section("账号配置") {
            ForEach(model.accounts) { account in
                NavigationLink {
                    ManagementDocumentView(title: "Webhook 配置", path: "/api/webhooks/\(account.id)", actions: [
                        .init(title: "重置 Webhook Token", path: "/api/webhooks/\(account.id)/token/reset", destructive: true,
                            explanation: "旧 Token 将立即失效，已有调用方需要同步更新。")
                    ])
                } label: { Label(AccountPresentation.name(account), systemImage: "person.crop.circle") }
            }
        }
    }
    @ViewBuilder private var systemSections: some View {
        Section("平台") {
            collection("平台能力开关", "/api/system/capabilities", kind: .capabilities, itemsKey: "modules", idField: "key", symbol: "switch.2")
            NavigationLink { PlatformTreeView() } label: { Label("能力与插件树", systemImage: "point.3.filled.connected.trianglepath.dotted") }
                .accessibilityIdentifier("system.platformTree")
            document("平台运行模式", "/api/platform/profile", symbol: "dial.medium", actions: [
                .init(title: "预览运行模式", path: "/api/platform/profile/dry-run"),
                .init(title: "应用运行模式", path: "/api/platform/profile/apply"),
                .init(title: "恢复运行模式", path: "/api/platform/profile/restore", destructive: true)
            ])
            document("服务器参数", "/api/system/settings", symbol: "slider.horizontal.3", actions: [.init(title: "编辑服务器参数", path: "/api/system/settings", method: "PATCH")])
        }
        Section("连接与通知") {
            collection("代理", "/api/proxies", kind: .editable, create: "添加代理", itemPath: "/api/proxies/{id}", symbol: "network")
            collection("设备配置", "/api/device-profiles", kind: .editable, create: "添加设备配置", itemPath: "/api/device-profiles/{id}", symbol: "iphone.gen3")
            collection("通知 Bot", "/api/notify-bots", kind: .editable, create: "添加通知 Bot", itemPath: "/api/notify-bots/{id}", symbol: "bell.badge")
        }
        Section("权限与限制") {
            collection("Sudo 权限", "/api/sudo", kind: .editable, create: "添加 Sudo 用户", itemPath: "/api/sudo/{id}", symbol: "person.badge.shield.checkmark")
            collection("限流模板", "/api/rate-templates", kind: .rateTemplates, create: "添加限流模板", itemPath: "/api/rate-templates/{id}", symbol: "gauge.with.dots.needle.33percent")
            ManagementActionLink(action: .init(title: "修改管理员密码", path: "/api/auth/change-password", explanation: "修改当前服务器管理员密码。"), symbol: "key")
            NavigationLink { ManagementTOTPView() } label: { Label("验证器与恢复码", systemImage: "lock.shield") }
        }
    }
    @ViewBuilder private var maintenanceSections: some View {
        Section("备份") { NavigationLink { FileManagementView(mode: .backup) } label: { Label("配置导入与导出", systemImage: "externaldrive.badge.timemachine") } }
        Section("网络与服务") {
            document("网络诊断", "/api/system/network", symbol: "stethoscope", actions: [.init(title: "重新检测网络", path: "/api/system/network/refresh")])
            ManagementActionLink(action: .init(title: "重启服务器服务", path: "/api/system/restart", destructive: true,
                explanation: "会中断当前服务器连接和正在执行的请求。请先检查运行任务。"), symbol: "restart.circle")
        }
        Section("紧急控制") {
            document("全局紧急停止", "/api/system/kill-switch", symbol: "exclamationmark.octagon", actions: [
                .init(title: "修改紧急停止开关", path: "/api/system/kill-switch", destructive: true,
                    explanation: "启用后将停止服务器上的自动化执行，恢复前请检查当前任务和资金状态。")
            ])
        }
    }

    private func collection(_ title: String, _ path: String, kind: ManagementCollection.Kind = .records,
                            itemsKey: String? = nil, create: String? = nil, itemPath: String? = nil, idField: String = "id",
                            symbol: String = "list.bullet.rectangle.portrait") -> some View {
        NavigationLink {
            ManagementCollectionView(resource: .init(title: title, path: path, kind: kind, itemsKey: itemsKey,
                create: create.map { .init(title: $0, path: path) }, itemPath: itemPath, idField: idField))
        } label: { Label(title, systemImage: symbol) }
    }
    private func document(_ title: String, _ path: String, symbol: String = "doc.text", actions: [ManagementAction] = []) -> some View {
        NavigationLink { ManagementDocumentView(title: title, path: path, actions: actions) } label: { Label(title, systemImage: symbol) }
    }
}

struct ManagementRunLookupView: View {
    var updateJob = false
    @State private var identifier = ""
    var body: some View {
        Form {
            TextField(updateJob ? "升级任务编号" : "测活任务编号", text: $identifier).textInputAutocapitalization(.never).autocorrectionDisabled()
            if !identifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                NavigationLink {
                    ManagementJobView(id: identifier.trimmingCharacters(in: .whitespacesAndNewlines), updateJob: updateJob, liveness: !updateJob)
                } label: { Label("读取任务进度", systemImage: "chart.bar.doc.horizontal") }
            }
        }.navigationTitle(updateJob ? "升级任务" : "测活任务").navigationBarTitleDisplayMode(.inline)
    }
}

struct ManagementTOTPView: View {
    var body: some View {
        List {
            Section {
                ManagementActionLink(action: .init(title: "生成验证器设置", path: "/api/auth/totp/enable", explanation: "生成验证器密钥后，请添加到你的验证器，再提交验证码完成启用。"), symbol: "qrcode")
                ManagementActionLink(action: .init(title: "验证并启用", path: "/api/auth/totp/verify"), symbol: "checkmark.shield")
                ManagementActionLink(action: .init(title: "关闭验证器保护", path: "/api/auth/totp/disable", destructive: true), symbol: "lock.open")
            } footer: { Text("密钥和恢复码默认遮盖。请在安全环境中查看，并妥善保管恢复码。") }
        }.navigationTitle("验证器与恢复码").navigationBarTitleDisplayMode(.inline)
    }
}
