import SwiftUI
import UIKit
import TelePilotCore

/// 第 22 轮：连接失败时一句原因 + 怎么办，下面是可展开的「诊断详情」（目标地址、尝试次数、耗时、底层错误码），
/// 可一键复制发给维护者。详情只来自 `ConnectionReport`，不含 Token、Cookie 或查询参数。
struct ConnectionProblemView: View {
    let report: ConnectionReport
    /// 原因已在别处显示（例如概览状态行、登录页提示）时只显示「诊断详情」。
    var showsReason = true
    @State private var expanded = false
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if showsReason {
                Label(report.diagnosis.title, systemImage: ConnectionProblemStyle.symbol(report.diagnosis.problem))
                    .font(.subheadline.weight(.semibold))
                    .accessibilityIdentifier("connection.problem.title")
                Text(report.diagnosis.summary)
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("connection.problem.reason")
            }
            DisclosureGroup(isExpanded: $expanded) {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(report.detailLines()) { line in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(line.label).foregroundStyle(.secondary)
                            Spacer(minLength: 8)
                            Text(line.value)
                                .multilineTextAlignment(.trailing)
                                .textSelection(.enabled)
                        }
                        .font(.caption.monospacedDigit())
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("connection.details.\(line.id)")
                    }
                    Button(action: copy) {
                        Label(copied ? "已复制" : "复制诊断信息", systemImage: copied ? "checkmark" : "doc.on.doc")
                    }
                    .motionSymbolSwap(value: copied)
                    .font(.caption)
                    .buttonStyle(.borderless)
                    .accessibilityIdentifier("connection.details.copy")
                }
                .padding(.top, 4)
            } label: {
                Text("诊断详情").font(.caption)
                    .accessibilityIdentifier("connection.details.toggle")
            }
        }
        .onChange(of: report) { _, _ in copied = false }
    }

    private func copy() {
        UIPasteboard.general.string = report.copyText()
        copied = true
    }
}

enum ConnectionProblemStyle {
    static func symbol(_ problem: ConnectionProblem) -> String {
        switch problem {
        case .offline: "wifi.slash"
        case .dnsFailed: "questionmark.circle"
        case .connectionRefused: "xmark.octagon"
        case .timedOut: "hourglass"
        case .tlsFailed: "lock.trianglebadge.exclamationmark"
        case .connectionLost: "bolt.horizontal.circle"
        case .cloudflareAccess, .cloudflareFirewall: "lock.shield"
        case .tunnelOffline: "icloud.slash"
        case .originUnreachable, .gatewayError, .serverError: "server.rack"
        case .serviceUnavailable: "clock.arrow.circlepath"
        case .rateLimited: "tortoise"
        case .unauthorized: "person.crop.circle.badge.exclamationmark"
        case .serverTooOld: "arrow.down.circle"
        case .redirect: "arrow.uturn.right"
        case .unknownNetwork: "exclamationmark.triangle"
        }
    }
}

/// Agent 进度流断线横幅：「已断开 x 秒，正在第 n 次重连」+ 原因；重连成功后由 `AssistantStore` 清除而消失。
struct StreamReconnectBanner: View {
    let status: StreamReconnectStatus

    var body: some View {
        TimelineView(.periodic(from: status.since, by: 1)) { context in
            HStack(alignment: .top, spacing: 10) {
                ProgressView().controlSize(.small)
                VStack(alignment: .leading, spacing: 2) {
                    Text(status.text(now: context.date))
                        .font(.subheadline.weight(.medium)).monospacedDigit()
                    if let diagnosis = status.diagnosis {
                        Text("\(diagnosis.title)：\(diagnosis.reason)")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("任务仍在服务器执行，连上后自动接着显示。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal).padding(.vertical, 10)
            .background(Color.orange.opacity(0.08))
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("assistant.reconnect")
        }
    }
}
