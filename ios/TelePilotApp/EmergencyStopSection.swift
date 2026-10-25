import SwiftUI
import TelePilotCore

/// “更多”页顶部的全局紧急停止。状态读自 `/api/system/kill-switch`，
/// 切换前必须确认，失败或结果未知时只提示，不自动重试。
struct EmergencyStopSection: View {
    @Environment(AppModel.self) private var model
    @State private var enabled: Bool?
    @State private var loadError: String?
    @State private var busy = false
    @State private var confirming = false
    @State private var resultMessage: String?

    var body: some View {
        Section {
            Button(role: .destructive) {
                confirming = true
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: enabled == true ? "exclamationmark.octagon.fill" : "exclamationmark.octagon")
                        .font(.title2)
                        .foregroundStyle(.white)
                        .frame(width: 40, height: 40)
                        .background(.red, in: .rect(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("全局紧急停止").font(.headline).foregroundStyle(.red)
                        Text(statusText).font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if busy {
                        ProgressView()
                    } else {
                        Text(actionTitle).font(.subheadline.weight(.semibold)).foregroundStyle(.red)
                    }
                }
                .padding(.vertical, 4)
            }
            .disabled(busy || enabled == nil)
            .listRowBackground(Color.red.opacity(enabled == true ? 0.22 : 0.10))
            .accessibilityIdentifier("more.emergencyStop")
            .accessibilityValue(statusText)
            .confirmationDialog(confirmTitle, isPresented: $confirming, titleVisibility: .visible) {
                Button(enabled == true ? "解除紧急停止" : "立即紧急停止", role: enabled == true ? nil : .destructive) {
                    Task { await toggle() }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text(enabled == true
                     ? "解除后自动化会恢复运行。请先确认当前任务和资金状态正常。"
                     : "会立刻停止服务器上的全部自动化执行，直到手动解除。")
            }
        } footer: {
            if let resultMessage {
                Text(resultMessage).foregroundStyle(.red)
            } else {
                Text("启用后服务器会停止所有自动化执行（插件、AI 自动回复、定时任务等）。")
            }
        }
        .task { await load() }
    }

    private var statusText: String {
        if let loadError { return "状态读取失败：\(loadError)" }
        switch enabled {
        case .some(true): return "已启用 · 自动化已停止"
        case .some(false): return "未启用 · 自动化正常运行"
        case .none: return "正在读取状态…"
        }
    }
    private var actionTitle: String { enabled == true ? "解除" : "停止" }
    private var confirmTitle: String { enabled == true ? "解除全局紧急停止？" : "启用全局紧急停止？" }

    private func load() async {
        do {
            let value = try await model.request(path: "/api/system/kill-switch")
            if case .object(let object) = value, case .bool(let flag) = object["enabled"] {
                enabled = flag
                loadError = nil
            } else {
                loadError = "响应格式无法识别"
            }
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func toggle() async {
        guard let current = enabled, !busy else { return }
        busy = true
        defer { busy = false }
        let target = !current
        do {
            let value = try await model.request(path: "/api/system/kill-switch", method: "POST", body: .object(["enabled": .bool(target)]))
            if case .object(let object) = value, case .bool(let flag) = object["enabled"] {
                enabled = flag
                resultMessage = nil
            } else {
                resultMessage = "已提交，但无法确认结果。请下拉刷新查看实际状态，不会自动重试。"
                await load()
            }
        } catch {
            resultMessage = "操作失败或结果未知：\(error.localizedDescription)。不会自动重试，请核对状态后再决定。"
            await load()
        }
    }
}
