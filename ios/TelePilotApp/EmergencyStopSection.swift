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
    @State private var showingInfo = false

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
                Text(enabled == true ? Self.resumeMessage : Self.stopMessage)
            }
        } footer: {
            if let resultMessage {
                Text(resultMessage).foregroundStyle(.red)
            } else {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(Self.footer)
                    Button {
                        showingInfo = true
                    } label: {
                        Image(systemName: "info.circle")
                            .imageScale(.medium)
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(.rect)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("紧急停止说明")
                    .accessibilityIdentifier("more.emergencyStop.info")
                    .popover(isPresented: $showingInfo, arrowEdge: .top) {
                        EmergencyStopInfoView()
                            .presentationCompactAdaptation(.popover)
                    }
                }
            }
        }
        .task { await load() }
    }

    private var statusText: String {
        if let loadError { return "状态读取失败：\(loadError)" }
        switch enabled {
        case .some(true): return "已开启 · 账号进程与 Bot 已停止"
        case .some(false): return "未开启 · 账号与 Bot 正常运行"
        case .none: return "正在读取状态…"
        }
    }
    private var actionTitle: String { enabled == true ? "解除" : "停止" }

    // 文案依据服务器实现（kill_switch_service.converge_runtime、supervisor、account_bot_runtime）：
    // 开启时停止全部账号 worker 子进程、账号 Bot 与交互 Bot，并阻止它们再启动；
    // 解除时重新启动所有状态为“启用”的账号与 Bot。其余服务不读取该开关。
    static let footer = "停止所有账号与 Bot 的消息处理，数据和管理台不受影响。"
    /// 说明弹层的三段：停止什么 / 不影响 / 如何恢复（原 footer 全文）。
    static let details: [EmergencyStopDetail] = [
        .init(title: "停止什么", symbol: "stop.circle", text: "所有 Telegram 账号的运行进程（其中的插件、自动回复、指令、定时任务随之停止），以及账号 Bot 和交互 Bot 的消息处理；开启期间新启用或恢复的账号也不会运行。"),
        .init(title: "不影响", symbol: "checkmark.shield", text: "服务器、Web 管理台与本 App 照常可用；账号登录状态、配置与数据全部保留；助手对话、服务器通知与推送照常工作。"),
        .init(title: "如何恢复", symbol: "arrow.uturn.backward.circle", text: "点按“解除”，服务器会重新启动所有处于启用状态的账号与 Bot；原本已暂停的账号保持暂停。")
    ]
    static let stopMessage = "立即停止所有 Telegram 账号的运行进程（插件、自动回复、指令、定时任务）以及账号 Bot、交互 Bot，直到你手动解除。账号登录状态和数据不会受影响，管理台与 App 仍可使用。"
    static let resumeMessage = "解除后服务器会重新启动所有处于启用状态的账号与 Bot，插件、自动回复和定时任务随之恢复；已暂停的账号保持暂停。请先确认当前任务和资金状态正常。"
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

struct EmergencyStopDetail {
    let title: String
    let symbol: String
    let text: String
}

/// 紧急停止说明（原生 popover，iPhone 上也保持 popover 而不是 sheet）。
private struct EmergencyStopInfoView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(EmergencyStopSection.details, id: \.title) { item in
                    Label {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(item.title).font(.subheadline.weight(.semibold))
                            Text(item.text).font(.subheadline).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    } icon: {
                        Image(systemName: item.symbol).foregroundStyle(.tint)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(16)
        }
        .frame(idealWidth: 340, maxWidth: 360, maxHeight: 420)
        .accessibilityIdentifier("more.emergencyStop.details")
    }
}
