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

// MARK: - 关键数字的数据

/// 概览“关键数字”的数据：从多个只读接口汇总，任何一个失败都不影响其他数字。
/// 不再推断“需要处理”的事项——那部分不准确且给不出解决方法，由「一键体检」给出结论与修复建议。
@MainActor @Observable
final class OverviewSignals {
    struct CoolingModel: Identifiable, Sendable {
        let id: String
    }

    private(set) var runtimeErrors: Int?
    private(set) var runtimeWarnings: Int?
    private(set) var failingPluginCount = 0
    private(set) var enabledPluginCount: Int?
    private(set) var coolingModels: [CoolingModel] = []
    private(set) var providerReady: Int?
    private(set) var providerTotal: Int?
    /// 全局紧急停止是否开启（`GET /api/system/kill-switch`）；读取失败为 nil。
    private(set) var killSwitchEnabled: Bool?
    private(set) var loading = false
    private(set) var loadedAt: Date?
    private(set) var unavailable: [String] = []

    func load(model: AppModel) async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        let since = OpsFormat.iso(Date.now.addingTimeInterval(-86_400))
        // 第十一轮：四项并发读取，每项一返回就更新对应的数字，不再等最慢的一项。
        var missing: [String] = []
        let parts: [Task<String?, Never>] = [
            Task { @MainActor in
                let result = await OpsFetch.result(model, OpsFormat.path("/api/logs/runtime/stats", [("since", since)]))
                switch result {
                case .success(let value) where value.objectValue != nil:
                    self.runtimeErrors = value["error"]?.intValue ?? 0
                    self.runtimeWarnings = value["warn"]?.intValue ?? 0
                    return nil
                case .success, .failure:
                    self.runtimeErrors = nil; self.runtimeWarnings = nil
                    return "日志统计"
                }
            },
            Task { @MainActor in
                switch await OpsFetch.result(model, "/api/feature-matrix") {
                case .success(let value): self.parsePlugins(value); return nil
                case .failure:
                    self.failingPluginCount = 0; self.enabledPluginCount = nil
                    return "插件状态"
                }
            },
            Task { @MainActor in
                switch await OpsFetch.result(model, "/api/commands/llm-providers/runtime-health") {
                case .success(let value):
                    self.coolingModels = ManagementText.items(value).filter { $0["state"]?.stringValue == "cooling" }.map { item in
                        CoolingModel(id: "\(item["provider_id"]?.intValue ?? 0)-\(OpsFormat.text(item["model"]) ?? "")")
                    }
                    return nil
                case .failure:
                    self.coolingModels = []
                    return "AI 运行状态"
                }
            },
            Task { @MainActor in
                switch await OpsFetch.result(model, "/api/commands/llm-providers") {
                case .success(let value):
                    let items = ManagementText.items(value)
                    self.providerTotal = items.count
                    self.providerReady = items.filter { ($0["has_effective_key"]?.boolValue ?? false) || $0["provider"]?.stringValue == "ollama" }.count
                    return nil
                case .failure:
                    self.providerTotal = nil; self.providerReady = nil
                    return "AI 供应商"
                }
            },
            Task { @MainActor in
                switch await OpsFetch.result(model, "/api/system/kill-switch") {
                case .success(let value):
                    if case .bool(let flag)? = value["enabled"] {
                        self.killSwitchEnabled = flag
                        return nil
                    }
                    self.killSwitchEnabled = nil
                    return "紧急停止状态"
                case .failure:
                    self.killSwitchEnabled = nil
                    return "紧急停止状态"
                }
            },
        ]
        for part in parts {
            if let name = await part.value { missing.append(name) }
        }
        // 与原来一致的顺序：日志统计、插件状态、AI 供应商、AI 运行状态。
        let order = ["日志统计", "插件状态", "AI 供应商", "AI 运行状态", "紧急停止状态"]
        unavailable = order.filter { missing.contains($0) }
        loadedAt = Date.now
    }

    private func parsePlugins(_ value: JSONValue) {
        let features = value["features"]?.arrayValue ?? []
        var failing = Set<String>()
        var enabled = Set<String>()
        for account in value["accounts"]?.arrayValue ?? [] {
            for (key, state) in account["features"]?.objectValue ?? [:] {
                if state.stringValue == "failed" { failing.insert(key) }
                if account["feature_enabled"]?[key]?.boolValue == true { enabled.insert(key) }
            }
        }
        failingPluginCount = failing.count
        enabledPluginCount = features.isEmpty ? nil : enabled.count
    }
}

enum OverviewDestination: Hashable {
    case logs
    case ledger
    /// 服务模块详情（基础服务 / 账号运行进程 / 主机资源）。
    case module(OverviewModule)
    /// 插件 ›「运行」（运行树与运行模式）。
    case runtime
    /// Agent 待确认。
    case inbox
    /// 紧急停止开启时，「运行模式」格进入紧急停止（与「更多」顶部同一行）。
    case emergencyStop
}

struct OverviewQuickAction: Identifiable {
    enum Action {
        case push(OverviewDestination)
        case route(AppRoute)
    }
    let id: String
    let title: String
    let symbol: String
    let action: Action
}

// MARK: - 概览小指标格（第 21 轮）

/// 一个格子的状态：图标 + 文字 + 颜色同时表达（DESIGN「色彩」：不能只靠颜色）。
enum OverviewTileStatus: Equatable {
    case ok, attention, error, neutral, unknown

    var symbol: String {
        switch self {
        case .ok: "checkmark.circle.fill"
        case .attention: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        case .neutral: "circle.fill"
        case .unknown: "questionmark.circle"
        }
    }
    var color: Color {
        switch self {
        case .ok: .green
        case .attention: .orange
        case .error: .red
        case .neutral: .secondary
        case .unknown: .secondary
        }
    }
    /// 数字本身只在出问题时着色，平时用主文字色，避免一屏都是绿色。
    var valueColor: Color {
        switch self {
        case .attention: .orange
        case .error: .red
        default: .primary
        }
    }
    var text: String {
        switch self {
        case .ok: "正常"
        case .attention: "需关注"
        case .error: "异常"
        case .neutral: "无"
        case .unknown: "未读取"
        }
    }
}

struct OverviewTile: Identifiable {
    let id: String
    /// 短标签（≤ 6 个汉字），写在数字下面。
    let title: String
    let value: String
    let status: OverviewTileStatus
    /// 原来写在卡片里的说明（如“已启用 / 全部”），只出现在长按菜单与 VoiceOver 提示里。
    var detail: String?
    let action: OverviewQuickAction.Action

    var accessibilityValue: String { "\(value)，\(status.text)" }
}

/// 小指标格的单个格子：数字（左上）+ 状态点（右上）+ 短标签（下）。
struct OverviewStatTileView: View {
    let tile: OverviewTile

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(tile.value)
                    .font(.headline)
                    .monospacedDigit()
                    .foregroundStyle(tile.status.valueColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                    // 第 24 轮：数字变化时滚动式过渡（减弱动态效果时淡变）。
                    .motionNumeric(value: tile.value)
                Spacer(minLength: 0)
                Image(systemName: tile.status.symbol)
                    .font(.caption2)
                    .foregroundStyle(tile.status.color)
                    .motionSymbolSwap(value: tile.status.symbol)
                    .accessibilityHidden(true)
            }
            Text(tile.title)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
        .background(Color(uiColor: .tertiarySystemGroupedBackground), in: .rect(cornerRadius: 10, style: .continuous))
        .contentShape(.rect(cornerRadius: 10, style: .continuous))
    }
}

/// 把 AppModel 与 OverviewSignals 的数据变成格子。只用已经读到的数据；没读到显示“—”并标“未读取”。
@MainActor
enum OverviewTileBuilder {
    static func keyTiles(model: AppModel, signals: OverviewSignals) -> [OverviewTile] {
        var tiles: [OverviewTile] = []

        // 运行模式：紧急停止 > 值守 > 生产（叫法同 Web 运行模式卡与紧急停止红条）。
        if signals.killSwitchEnabled == true {
            tiles.append(OverviewTile(id: "runtime", title: "运行模式", value: "紧急停止", status: .error,
                                      detail: "已停止全部消息处理，点开可解除", action: .push(.emergencyStop)))
        } else if let profile = model.runtimeProfile {
            let status: OverviewTileStatus
            if profile.status == "failed" {
                status = .error
            } else if profile.isSafeWatch || profile.status == "applying" || profile.status == "restoring" {
                status = .attention
            } else {
                status = .ok
            }
            let value = profile.isSafeWatch ? "值守" : profile.label
            tiles.append(OverviewTile(id: "runtime", title: "运行模式", value: value, status: status,
                                      detail: profile.statusText, action: .push(.runtime)))
        } else {
            tiles.append(OverviewTile(id: "runtime", title: "运行模式", value: "—", status: .unknown,
                                      detail: "服务器未返回运行模式", action: .push(.runtime)))
        }

        // 账号：已启用 / 全部（账号配置，不代表进程存活；进程看「运行进程」格）。
        let active = model.accounts.filter { $0.status == "active" }.count
        let needsAttention = model.accounts.contains { ["login_required", "dead", "floodwait"].contains($0.status) }
        let accountStatus: OverviewTileStatus
        if !model.accountsLoaded {
            accountStatus = model.accountsError == nil ? .unknown : .error
        } else if needsAttention {
            accountStatus = .attention
        } else {
            accountStatus = model.accounts.isEmpty ? .neutral : .ok
        }
        tiles.append(OverviewTile(
            id: "accounts", title: "账号",
            value: model.accountsLoaded ? "\(active)/\(model.accounts.count)" : "—",
            status: accountStatus,
            detail: needsAttention ? "已启用 / 全部 · 有账号需重新登录或受限" : "已启用 / 全部",
            action: .route(.accounts)))

        // 插件：已启用数；有运行失败标红。
        tiles.append(OverviewTile(
            id: "plugins", title: "插件",
            value: signals.enabledPluginCount.map { String($0) } ?? "—",
            status: signals.enabledPluginCount == nil ? .unknown : (signals.failingPluginCount > 0 ? .error : .ok),
            detail: signals.failingPluginCount > 0 ? "已启用 · \(signals.failingPluginCount) 个出错" : "已启用 · 运行正常",
            action: .route(.plugins)))

        // AI：可用 / 已配置供应商（叫法同 Web「AI」格）；模块关闭时显示「已关闭」。
        if !model.isModuleEnabled("ai") {
            tiles.append(OverviewTile(id: "ai", title: "AI", value: "已关闭", status: .neutral,
                                      detail: "平台 AI 能力已关闭", action: .push(.runtime)))
        } else {
            let aiStatus: OverviewTileStatus
            if signals.providerTotal == nil {
                aiStatus = .unknown
            } else if !signals.coolingModels.isEmpty || (signals.providerReady ?? 0) == 0 {
                aiStatus = .attention
            } else {
                aiStatus = .ok
            }
            tiles.append(OverviewTile(
                id: "ai", title: "AI",
                value: signals.providerTotal.map { "\(signals.providerReady ?? 0)/\($0)" } ?? "—",
                status: aiStatus,
                detail: signals.coolingModels.isEmpty ? "可用 / 已配置供应商" : "可用 / 已配置 · \(signals.coolingModels.count) 个模型暂停中",
                action: .route(.ai)))
        }

        // 待确认：Agent 等你确认的操作。
        let pending = model.pendingActions.count
        tiles.append(OverviewTile(id: "pending", title: "待确认", value: "\(pending)",
                                  status: pending > 0 ? .attention : .neutral,
                                  detail: "Agent 等你确认的操作", action: .push(.inbox)))

        // 24 小时错误（告警数收进细节）。
        let errors = signals.runtimeErrors
        let errorStatus: OverviewTileStatus
        if let errors {
            errorStatus = errors > 0 ? .error : ((signals.runtimeWarnings ?? 0) > 0 ? .attention : .ok)
        } else {
            errorStatus = .unknown
        }
        tiles.append(OverviewTile(
            id: "errors", title: "24 小时错误",
            value: errors.map { OpsFormat.integer($0) } ?? "—",
            status: errorStatus,
            detail: "告警 \(signals.runtimeWarnings.map { OpsFormat.integer($0) } ?? "—")",
            action: .push(.logs)))
        return tiles
    }

    /// 三个服务模块（可在「自定义概览」排序、隐藏），原来各占一整组，现在各一格，点开是原来的整组内容。
    static func moduleTile(_ module: OverviewModule, model: AppModel) -> OverviewTile {
        switch module {
        case .health:
            guard let health = model.health else {
                return OverviewTile(id: module.id, title: module.shortTitle, value: "—", status: model.healthError == nil ? .unknown : .error,
                                    detail: "数据库、缓存服务、数据库结构", action: .push(.module(module)))
            }
            let probes = [health.db, health.redis, health.alembic]
            let failing = probes.filter { !$0.ok }.count
            return OverviewTile(id: module.id, title: module.shortTitle,
                                value: failing == 0 ? "正常" : "\(failing) 项异常",
                                status: model.healthError != nil ? .unknown : (failing == 0 ? .ok : .error),
                                detail: "数据库、缓存服务、数据库结构", action: .push(.module(module)))
        case .workers:
            guard let workers = model.health?.workers else {
                return OverviewTile(id: module.id, title: module.shortTitle, value: "—", status: model.healthError == nil ? .unknown : .error,
                                    detail: "在线 / 应运行", action: .push(.module(module)))
            }
            let short = workers.runtimeDesiredRunningAlive < workers.runtimeDesiredRunning
            return OverviewTile(id: module.id, title: module.shortTitle,
                                value: "\(workers.runtimeDesiredRunningAlive)/\(workers.runtimeDesiredRunning)",
                                status: short ? OverviewTileStatus.error : (workers.runtimeFailing > 0 ? OverviewTileStatus.attention : OverviewTileStatus.ok),
                                detail: "在线 / 应运行 · 失败记录 \(workers.runtimeFailing)", action: .push(.module(module)))
        case .resources:
            guard let host = model.resources?.host else {
                return OverviewTile(id: module.id, title: module.shortTitle, value: "—", status: model.resourcesError == nil ? .unknown : .error,
                                    detail: "处理器、内存、磁盘", action: .push(.module(module)))
            }
            let values = [host.cpuPercent, host.memoryUsedPercent, host.diskUsedPercent].compactMap { $0 }
            let peak = values.max()
            let readings: [(String, Double?)] = [("处理器", host.cpuPercent), ("内存", host.memoryUsedPercent), ("磁盘", host.diskUsedPercent)]
            let parts: [String] = readings.map { name, value in
                let text: String = value.map { String(format: "%.0f%%", $0) } ?? "—"
                return "\(name) \(text)"
            }
            let resourceStatus: OverviewTileStatus = peak == nil ? .unknown : ((peak ?? 0) >= 90 ? .attention : .ok)
            return OverviewTile(id: module.id, title: module.shortTitle,
                                value: peak.map { String(format: "%.0f%%", $0) } ?? "—",
                                status: resourceStatus,
                                detail: "最高占用 · " + parts.joined(separator: "、"), action: .push(.module(module)))
        }
    }
}
