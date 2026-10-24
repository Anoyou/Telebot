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
