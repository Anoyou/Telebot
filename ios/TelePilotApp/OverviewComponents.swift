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
        ]
        for part in parts {
            if let name = await part.value { missing.append(name) }
        }
        // 与原来一致的顺序：日志统计、插件状态、AI 供应商、AI 运行状态。
        let order = ["日志统计", "插件状态", "AI 供应商", "AI 运行状态"]
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
