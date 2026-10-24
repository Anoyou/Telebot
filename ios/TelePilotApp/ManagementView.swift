import SwiftUI
import TelePilotCore

enum ManagementModule: String, CaseIterable, Identifiable {
    case plugins, operations, ai, logs, ledger, webhooks, system, maintenance
    var id: String { rawValue }
    static var moreCases: [ManagementModule] {
        [.operations, .logs, .ledger, .webhooks, .system, .maintenance]
    }
    var title: String {
        switch self {
        case .plugins: "插件中心"
        case .operations: "指令与任务"
        case .ai: "AI 管理"
        case .logs: "日志与诊断"
        case .ledger: "资金台账"
        case .webhooks: "Webhook"
        case .system: "系统设置"
        case .maintenance: "备份与维护"
        }
    }
    var symbol: String {
        switch self {
        case .plugins: "puzzlepiece.extension"
        case .operations: "terminal"
        case .ai: "cpu"
        case .logs: "list.bullet.rectangle"
        case .ledger: "book.closed"
        case .webhooks: "point.3.connected.trianglepath.dotted"
        case .system: "gearshape"
        case .maintenance: "wrench.and.screwdriver"
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
            }.navigationTitle("管理")
        }
    }
}

struct ManagementModuleView: View {
    let module: ManagementModule
    @Environment(AppModel.self) private var model
    var body: some View {
        Group {
            if module == .plugins { PluginManagementView() }
            else if module == .ai { AIWorkspaceView() }
            else {
                List {
                    switch module {
                    case .plugins: EmptyView()
                    case .operations: operationSections
                    case .ai: aiSections
                    case .logs: logSections
                    case .ledger: ledgerSections
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
            collection("指令模板", "/api/commands/templates", kind: .editable, create: "新建指令模板", itemPath: "/api/commands/templates/{id}")
            collection("指令别名", "/api/aliases", kind: .editable, create: "添加指令别名", itemPath: "/api/aliases/{id}")
            document("内置指令", "/api/commands/builtin")
            NavigationLink { AICommandEnablementView() } label: { Label("AI 指令与账号启用", systemImage: "text.bubble") }
        }
        Section("模板预览与测试") {
            document("模板变量与示例", "/api/message-templates/catalog")
            ManagementActionLink(action: .init(title: "预览消息模板", path: "/api/message-templates/render", explanation: "仅渲染模板，不发送消息。"))
            ManagementActionLink(action: .init(title: "测试发送模板", path: "/api/message-templates/test-send", explanation: "将通过指定账号向目标会话发送真实消息。"))
        }
        Section("按账号配置") {
            ForEach(model.accounts) { account in
                NavigationLink(ManagementText.accountName(account) + " · 定时任务与白名单") { AccountManagementSchedulerView(id: account.id) }
                NavigationLink(ManagementText.accountName(account) + " · 全部账号配置") { AccountManagementView(id: account.id) }
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
    @ViewBuilder private var logSections: some View {
        Section("日志") {
            collection("消息日志", "/api/logs/messages")
            collection("运行日志", "/api/logs/runtime")
            collection("审计日志", "/api/logs/audit")
            collection("Trace 调用链", "/api/logs/trace/events", kind: .traces, idField: "trace_id")
            document("运行日志统计", "/api/logs/runtime/stats")
            ManagementActionLink(action: .init(title: "查看系统控制台", path: "/api/logs/system-console", explanation: "读取服务器系统进程日志。"))
        }
        Section("路由诊断") {
            ManagementActionLink(action: .init(title: "模拟消息路由", path: "/api/dispatch/simulate", explanation: "模拟路由命中过程，不向 Telegram 投递。"))
            document("投递统计", "/api/dispatch/router-delivery-stats")
            ManagementActionLink(action: .init(title: "开启路由诊断记录", path: "/api/dispatch/router-debug-trace"))
        }
    }
    @ViewBuilder private var ledgerSections: some View {
        Section("资金") {
            document("台账汇总", "/api/ledger/summary")
            document("运行统计", "/api/ledger/stats")
            collection("交易明细", "/api/ledger")
            collection("补偿与人工支付", "/api/ledger/compensations", kind: .compensations)
        }
        Section("高风险维护") {
            ManagementActionLink(action: .init(title: "重置资金台账", path: "/api/ledger/reset", destructive: true,
                explanation: "将清空服务器资金台账。此操作无法在 App 中撤销，请先完成备份并输入确认口令。"))
        }
    }
    @ViewBuilder private var webhookSections: some View {
        Section { document("Webhook 服务状态", "/api/webhooks") }
        Section("账号配置") {
            ForEach(model.accounts) { account in
                NavigationLink(AccountPresentation.name(account)) {
                    ManagementDocumentView(title: "Webhook 配置", path: "/api/webhooks/\(account.id)", actions: [
                        .init(title: "重置 Webhook Token", path: "/api/webhooks/\(account.id)/token/reset", destructive: true,
                            explanation: "旧 Token 将立即失效，已有调用方需要同步更新。")
                    ])
                }
            }
        }
    }
    @ViewBuilder private var systemSections: some View {
        Section("平台") {
            collection("平台能力开关", "/api/system/capabilities", kind: .capabilities, itemsKey: "modules", idField: "key")
            document("平台运行树", "/api/platform/tree")
            document("平台运行模式", "/api/platform/profile", actions: [
                .init(title: "预览运行模式", path: "/api/platform/profile/dry-run"),
                .init(title: "应用运行模式", path: "/api/platform/profile/apply"),
                .init(title: "恢复运行模式", path: "/api/platform/profile/restore", destructive: true)
            ])
            document("服务器设置", "/api/system/settings", actions: [.init(title: "编辑服务器设置", path: "/api/system/settings", method: "PATCH")])
        }
        Section("连接与通知") {
            collection("代理", "/api/proxies", kind: .editable, create: "添加代理", itemPath: "/api/proxies/{id}")
            collection("设备配置", "/api/device-profiles", kind: .editable, create: "添加设备配置", itemPath: "/api/device-profiles/{id}")
            collection("通知 Bot", "/api/notify-bots", kind: .editable, create: "添加通知 Bot", itemPath: "/api/notify-bots/{id}")
        }
        Section("权限与限制") {
            collection("Sudo 权限", "/api/sudo", kind: .editable, create: "添加 Sudo 用户", itemPath: "/api/sudo/{id}")
            collection("限流模板", "/api/rate-templates", kind: .rateTemplates, create: "添加限流模板", itemPath: "/api/rate-templates/{id}")
            ManagementActionLink(action: .init(title: "修改管理员密码", path: "/api/auth/change-password", explanation: "修改当前服务器管理员密码。"))
            NavigationLink("验证器与恢复码") { ManagementTOTPView() }
        }
    }
    @ViewBuilder private var maintenanceSections: some View {
        Section("备份") { NavigationLink { FileManagementView(mode: .backup) } label: { Label("配置导入与导出", systemImage: "externaldrive.badge.timemachine") } }
        Section("网络与服务") {
            document("网络诊断", "/api/system/network", actions: [.init(title: "重新检测网络", path: "/api/system/network/refresh")])
            document("服务器版本", "/api/system/version")
            ManagementActionLink(action: .init(title: "重启服务器服务", path: "/api/system/restart", destructive: true,
                explanation: "会中断当前服务器连接和正在执行的请求。请先检查运行任务。"))
        }
        Section("更新") {
            ManagementActionLink(action: .init(title: "检查服务端更新", path: "/api/system/check-update"))
            document("可用更新目标", "/api/system/update-target-options")
            document("升级状态", "/api/system/update-status", actions: [
                .init(title: "配置更新检查", path: "/api/system/update-check", method: "PATCH")
            ])
            ManagementActionLink(action: .init(title: "升级服务器", path: "/api/system/pull-update", destructive: true,
                explanation: "服务器将拉取并应用所选版本，服务可能短暂中断，请先确认备份和版本兼容性。"))
            NavigationLink("查看升级任务") { ManagementRunLookupView(updateJob: true) }
        }
        Section("紧急控制") {
            document("全局紧急停止", "/api/system/kill-switch", actions: [
                .init(title: "修改紧急停止开关", path: "/api/system/kill-switch", destructive: true,
                    explanation: "启用后将停止服务器上的自动化执行，恢复前请检查当前任务和资金状态。")
            ])
        }
    }

    private func collection(_ title: String, _ path: String, kind: ManagementCollection.Kind = .records,
                            itemsKey: String? = nil, create: String? = nil, itemPath: String? = nil, idField: String = "id") -> some View {
        NavigationLink(title) {
            ManagementCollectionView(resource: .init(title: title, path: path, kind: kind, itemsKey: itemsKey,
                create: create.map { .init(title: $0, path: path) }, itemPath: itemPath, idField: idField))
        }
    }
    private func document(_ title: String, _ path: String, actions: [ManagementAction] = []) -> some View {
        NavigationLink(title) { ManagementDocumentView(title: title, path: path, actions: actions) }
    }
}

struct ManagementRunLookupView: View {
    var updateJob = false
    @State private var identifier = ""
    var body: some View {
        Form {
            TextField(updateJob ? "升级任务编号" : "测活任务编号", text: $identifier).textInputAutocapitalization(.never).autocorrectionDisabled()
            if !identifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                NavigationLink("读取任务进度") {
                    ManagementJobView(id: identifier.trimmingCharacters(in: .whitespacesAndNewlines), updateJob: updateJob, liveness: !updateJob)
                }
            }
        }.navigationTitle(updateJob ? "升级任务" : "测活任务")
    }
}

struct ManagementTOTPView: View {
    var body: some View {
        List {
            Section {
                ManagementActionLink(action: .init(title: "生成验证器设置", path: "/api/auth/totp/enable", explanation: "生成验证器密钥后，请添加到你的验证器，再提交验证码完成启用。"))
                ManagementActionLink(action: .init(title: "验证并启用", path: "/api/auth/totp/verify"))
                ManagementActionLink(action: .init(title: "关闭验证器保护", path: "/api/auth/totp/disable", destructive: true))
            } footer: { Text("密钥和恢复码默认遮盖。请在安全环境中查看，并妥善保管恢复码。") }
        }.navigationTitle("验证器与恢复码")
    }
}
