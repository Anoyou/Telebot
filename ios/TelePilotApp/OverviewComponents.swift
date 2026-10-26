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
        async let stats = OpsFetch.result(model, OpsFormat.path("/api/logs/runtime/stats", [("since", since)]))
        async let matrix = OpsFetch.result(model, "/api/feature-matrix")
        async let health = OpsFetch.result(model, "/api/commands/llm-providers/runtime-health")
        async let providers = OpsFetch.result(model, "/api/commands/llm-providers")
        let results = await (stats, matrix, health, providers)
        guard !Task.isCancelled else { return }
        var missing: [String] = []

        switch results.0 {
        case .success(let value) where value.objectValue != nil:
            runtimeErrors = value["error"]?.intValue ?? 0
            runtimeWarnings = value["warn"]?.intValue ?? 0
        case .success, .failure: runtimeErrors = nil; runtimeWarnings = nil; missing.append("日志统计")
        }
        switch results.1 {
        case .success(let value): parsePlugins(value)
        case .failure:
            failingPluginCount = 0; enabledPluginCount = nil
            missing.append("插件状态")
        }
        switch results.3 {
        case .success(let value):
            let items = ManagementText.items(value)
            providerTotal = items.count
            providerReady = items.filter { ($0["has_effective_key"]?.boolValue ?? false) || $0["provider"]?.stringValue == "ollama" }.count
        case .failure: providerTotal = nil; providerReady = nil; missing.append("AI 供应商")
        }
        switch results.2 {
        case .success(let value):
            coolingModels = ManagementText.items(value).filter { $0["state"]?.stringValue == "cooling" }.map { item in
                CoolingModel(id: "\(item["provider_id"]?.intValue ?? 0)-\(OpsFormat.text(item["model"]) ?? "")")
            }
        case .failure: coolingModels = []; missing.append("AI 运行状态")
        }
        unavailable = missing
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
