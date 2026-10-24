import Observation
import SwiftUI
import TelePilotCore

struct ProbeStatusRow: View {
    let name: String
    let symbol: String
    let probe: ProbeStatus

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LabeledContent {
                Label(probe.ok ? "正常" : "异常", systemImage: probe.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                    .foregroundStyle(probe.ok ? Color.green : Color.orange)
            } label: { Label(name, systemImage: symbol) }
            if !probe.ok { Text("请检查服务器诊断信息。").font(.footnote).foregroundStyle(.secondary) }
        }
        .accessibilityElement(children: .combine)
    }
}

struct ResourceStatusRow: View {
    let title: String
    let value: Double?
    let symbol: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            LabeledContent {
                Text(value.map { String(format: "%.0f%%", $0) } ?? "暂无数据").monospacedDigit()
            } label: { Label(title, systemImage: symbol) }
            if let value {
                ProgressView(value: min(max(value / 100, 0), 1))
                    .tint(value >= 90 ? .orange : .accentColor)
                    .accessibilityHidden(true)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - 需要处理的事项

/// 概览“需要处理”区的数据：从多个只读接口汇总，任何一个失败都不影响其他信号。
@MainActor @Observable
final class OverviewSignals {
    struct FailingPlugin: Identifiable, Sendable {
        let key: String
        let name: String
        let accounts: [String]
        var id: String { key }
    }
    struct CoolingModel: Identifiable, Sendable {
        let id: String
        let title: String
        let detail: String
    }

    private(set) var runtimeErrors: Int?
    private(set) var runtimeWarnings: Int?
    private(set) var latestError: LogEntry?
    private(set) var failedMessages: Int?
    private(set) var failingPlugins: [FailingPlugin] = []
    private(set) var untrustedPlugins: [String] = []
    private(set) var enabledPluginCount: Int?
    private(set) var coolingModels: [CoolingModel] = []
    private(set) var providerReady: Int?
    private(set) var providerTotal: Int?
    private(set) var openCompensations: Int?
    private(set) var loading = false
    private(set) var loadedAt: Date?
    private(set) var unavailable: [String] = []

    func load(model: AppModel) async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        let since = OpsFormat.iso(Date.now.addingTimeInterval(-86_400))
        async let stats = OpsFetch.result(model, OpsFormat.path("/api/logs/runtime/stats", [("since", since)]))
        async let latest = OpsFetch.result(model, OpsFormat.path("/api/logs/runtime", [("since", since), ("level", "error"), ("limit", "1")]))
        async let messages = OpsFetch.result(model, OpsFormat.path("/api/logs/messages", [("since", since), ("verdict", "failed"), ("limit", "100")]))
        async let matrix = OpsFetch.result(model, "/api/feature-matrix")
        async let health = OpsFetch.result(model, "/api/commands/llm-providers/runtime-health")
        async let providers = OpsFetch.result(model, "/api/commands/llm-providers")
        async let compensations = OpsFetch.result(model, OpsFormat.path("/api/ledger/compensations", [("limit", "100")]))
        let results = await (stats, latest, messages, matrix, health, providers, compensations)
        guard !Task.isCancelled else { return }
        var missing: [String] = []

        switch results.0 {
        case .success(let value) where value.objectValue != nil:
            runtimeErrors = value["error"]?.intValue ?? 0
            runtimeWarnings = value["warn"]?.intValue ?? 0
        case .success: runtimeErrors = nil; runtimeWarnings = nil; missing.append("日志统计")
        case .failure: runtimeErrors = nil; runtimeWarnings = nil; missing.append("日志统计")
        }
        if case .success(let value) = results.1 {
            latestError = ManagementText.items(value).map(LogEntry.runtime).first { $0.severity == .error }
        } else { latestError = nil }
        switch results.2 {
        case .success(let value): failedMessages = ManagementText.items(value).count
        case .failure: failedMessages = nil; missing.append("消息处理失败")
        }
        switch results.3 {
        case .success(let value): parsePlugins(value)
        case .failure:
            failingPlugins = []; untrustedPlugins = []; enabledPluginCount = nil
            missing.append("插件状态")
        }
        var providerNames: [Int: String] = [:]
        switch results.5 {
        case .success(let value):
            let items = ManagementText.items(value)
            providerTotal = items.count
            providerReady = items.filter { ($0["has_effective_key"]?.boolValue ?? false) || $0["provider"]?.stringValue == "ollama" }.count
            for item in items { if let id = item["id"]?.intValue { providerNames[id] = OpsFormat.text(item["name"]) } }
        case .failure: providerTotal = nil; providerReady = nil; missing.append("AI 供应商")
        }
        switch results.4 {
        case .success(let value):
            coolingModels = ManagementText.items(value).filter { $0["state"]?.stringValue == "cooling" }.map { item in
                let provider = item["provider_id"]?.intValue
                let name = provider.flatMap { providerNames[$0] } ?? provider.map { "供应商 \($0)" } ?? "AI 供应商"
                let modelName = OpsFormat.text(item["model"]) ?? "未知模型"
                let remaining = item["cooldown_remaining_seconds"]?.intValue ?? 0
                let reason = OpsFormat.text(item["last_error_message"]) ?? "连续请求失败"
                return CoolingModel(id: "\(provider ?? 0)-\(modelName)", title: "\(name) · \(modelName)",
                                    detail: "\(reason)。暂停调用约 \(max(1, remaining)) 秒后自动重试，期间会改用其他模型。")
            }
        case .failure: coolingModels = []; missing.append("AI 运行状态")
        }
        switch results.6 {
        case .success(let value):
            openCompensations = ManagementText.items(value).filter { ["pending", "abandoned"].contains($0["status"]?.stringValue ?? "") }.count
        case .failure: openCompensations = nil // 台账模块可能未启用，不视为故障
        }
        unavailable = missing
        loadedAt = Date.now
    }

    private func parsePlugins(_ value: JSONValue) {
        let features = value["features"]?.arrayValue ?? []
        var names: [String: String] = [:]
        var untrusted: [String] = []
        for feature in features {
            guard let key = feature["key"]?.stringValue else { continue }
            let name = OpsFormat.text(feature["display_name"]) ?? key
            names[key] = name
            if feature["signature_ok"]?.boolValue == false || feature["orphan"]?.boolValue == true { untrusted.append(name) }
        }
        var failing: [String: [String]] = [:]
        var enabled = Set<String>()
        for account in value["accounts"]?.arrayValue ?? [] {
            let accountName = OpsFormat.text(account["name"]) ?? "账号 \(account["id"]?.intValue ?? 0)"
            for (key, state) in account["features"]?.objectValue ?? [:] {
                if state.stringValue == "failed" { failing[key, default: []].append(accountName) }
                if account["feature_enabled"]?[key]?.boolValue == true { enabled.insert(key) }
            }
        }
        failingPlugins = failing.map { FailingPlugin(key: $0.key, name: names[$0.key] ?? $0.key, accounts: $0.value.sorted()) }
            .sorted { $0.name < $1.name }
        untrustedPlugins = untrusted
        enabledPluginCount = features.isEmpty ? nil : enabled.count
    }
}

enum OverviewDestination: Hashable {
    case account(Int)
    case health
    case workers
    case logs
    case errorLogs
    case failedMessages
    case ledger
}

struct OverviewAttentionItem: Identifiable {
    enum Action {
        case push(OverviewDestination)
        case route(AppRoute)
    }
    let id: String
    let severity: OpsSeverity
    let title: String
    let detail: String
    let action: Action
}

enum OverviewAttention {
    @MainActor static func items(model: AppModel, signals: OverviewSignals) -> [OverviewAttentionItem] {
        var items: [OverviewAttentionItem] = []
        if let health = model.health {
            let broken = [("数据库", health.db.ok), ("缓存服务", health.redis.ok), ("数据库结构", health.alembic.ok)].filter { !$0.1 }.map { $0.0 }
            if !broken.isEmpty {
                items.append(.init(id: "health", severity: .error, title: "基础服务异常：" + broken.joined(separator: "、"),
                                   detail: "服务器核心组件不可用，大部分功能会受影响。请检查服务器部署。", action: .push(.health)))
            }
        }
        for account in model.accounts {
            switch account.status {
            case "dead", "login_required":
                items.append(.init(id: "account-\(account.id)", severity: .error, title: "\(AccountPresentation.name(account))：需要重新登录",
                                   detail: "Telegram 登录已失效，这个账号目前不会收发消息。点此打开账号重新绑定。", action: .push(.account(account.id))))
            case "floodwait":
                items.append(.init(id: "account-\(account.id)", severity: .warning, title: "\(AccountPresentation.name(account))：被 Telegram 限流",
                                   detail: "Telegram 暂时限制了这个账号的操作，通常会自动恢复。", action: .push(.account(account.id))))
            default: break
            }
        }
        if let resources = model.resources {
            for worker in resources.workers where worker.desired == "running" && !worker.alive {
                let name = model.accounts.first { $0.id == worker.accountID }.map(AccountPresentation.name) ?? "账号 \(worker.accountID)"
                items.append(.init(id: "worker-\(worker.accountID)", severity: .error, title: "\(name)：后台进程未运行",
                                   detail: "账号已启用，但后台进程不在线\(worker.failCount > 0 ? "，已失败 \(worker.failCount) 次" : "")。可在账号详情中查看并重启。",
                                   action: .push(.account(worker.accountID))))
            }
        } else if let workers = model.health?.workers, workers.runtimeDesiredRunning > workers.runtimeDesiredRunningAlive {
            items.append(.init(id: "workers", severity: .error, title: "\(workers.runtimeDesiredRunning - workers.runtimeDesiredRunningAlive) 个账号进程没有在运行",
                               detail: "这些账号已启用，但后台进程不在线。", action: .push(.workers)))
        }
        for plugin in signals.failingPlugins {
            items.append(.init(id: "plugin-\(plugin.key)", severity: .error, title: "插件运行失败：\(plugin.name)",
                               detail: "在 \(plugin.accounts.prefix(3).joined(separator: "、"))\(plugin.accounts.count > 3 ? " 等 \(plugin.accounts.count) 个账号" : "")上出错。打开插件中心查看错误原因。",
                               action: .route(.plugins)))
        }
        if !signals.untrustedPlugins.isEmpty {
            items.append(.init(id: "plugin-untrusted", severity: .warning, title: "\(signals.untrustedPlugins.count) 个插件来源需要检查",
                               detail: "\(signals.untrustedPlugins.prefix(3).joined(separator: "、")) 的签名未通过或来源目录缺失。", action: .route(.plugins)))
        }
        for cooling in signals.coolingModels {
            items.append(.init(id: "ai-\(cooling.id)", severity: .warning, title: "AI 模型暂停调用：\(cooling.title)", detail: cooling.detail, action: .route(.ai)))
        }
        if let ready = signals.providerReady, let total = signals.providerTotal, total > 0, ready == 0 {
            items.append(.init(id: "ai-none", severity: .warning, title: "没有可用的 AI 供应商",
                               detail: "已配置 \(total) 个供应商，但都缺少有效密钥，AI 功能无法使用。", action: .route(.ai)))
        }
        if let errors = signals.runtimeErrors, errors > 0 {
            items.append(.init(id: "logs-errors", severity: .warning, title: "最近 24 小时有 \(errors) 条错误日志",
                               detail: signals.latestError.map { "最近一条：" + String($0.title.prefix(80)) } ?? "打开日志查看详情。", action: .push(.errorLogs)))
        } else if signals.runtimeErrors == nil, let latest = signals.latestError {
            items.append(.init(id: "logs-errors", severity: .warning, title: "最近 24 小时发现错误日志",
                               detail: "暂时无法统计总数。最近一条：" + String(latest.title.prefix(80)), action: .push(.errorLogs)))
        }
        if let failed = signals.failedMessages, failed > 0 {
            items.append(.init(id: "logs-messages", severity: .warning, title: "最近 24 小时有 \(OpsFormat.sampledCount(failed, limit: 100)) 条消息处理失败",
                               detail: "这些消息没能按预期回复，打开可以看到失败在哪一步、怎么处理。", action: .push(.failedMessages)))
        }
        if let open = signals.openCompensations, open > 0 {
            items.append(.init(id: "ledger", severity: .warning, title: "\(OpsFormat.sampledCount(open, limit: 100)) 笔付款等待补付",
                               detail: "付款失败后正在等待自动补付或人工核销。", action: .push(.ledger)))
        }
        if !model.pendingActions.isEmpty {
            items.append(.init(id: "pending", severity: .info, title: "\(model.pendingActions.count) 个助手操作等你确认",
                               detail: "助手准备执行的操作需要你同意后才会进行。", action: .route(.action(""))))
        }
        return items.sorted { $0.severity > $1.severity }
    }
}

struct OverviewAttentionRow: View {
    let item: OverviewAttentionItem
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            OpsSeverityIcon(severity: item.severity)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.title).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
            Spacer(minLength: 4)
            Image(systemName: "chevron.forward").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }
}

struct OverviewQuickAction: Identifiable {
    let id: String
    let title: String
    let symbol: String
    let action: OverviewAttentionItem.Action
}
