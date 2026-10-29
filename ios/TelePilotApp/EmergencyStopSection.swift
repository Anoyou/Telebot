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
            // 0.16.1：说明行（ⓘ + 文字）并入红色高亮行，替换原来的状态文字；高亮区外不再单独显示说明。
            // 行内有两个可点目标（ⓘ 与整行），不能再把整行包成一个 Button（嵌套 Button 的点按会被外层吞掉），
            // 所以整行用 onTapGesture 打开确认，ⓘ 是独立的 borderless Button（子视图手势优先）；
            // 无障碍上右侧“停止／解除”是真正的按钮，承载原来的标识与状态值。
            HStack(spacing: 12) {
                Image(systemName: enabled == true ? "exclamationmark.octagon.fill" : "exclamationmark.octagon")
                    .font(.title2)
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(.red, in: .rect(cornerRadius: 10))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("全局紧急停止").font(.headline).foregroundStyle(.red)
                        .accessibilityIdentifier("more.emergencyStop.title")
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Button {
                            showingInfo = true
                        } label: {
                            Image(systemName: "info.circle")
                                .font(.footnote)
                                // 视觉保持小图标，点按区域向四周扩 10pt（约 37pt），不撑高说明行。
                                .padding(10)
                                .contentShape(.rect)
                                .padding(-10)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("紧急停止说明")
                        .accessibilityIdentifier("more.emergencyStop.info")
                        .popover(isPresented: $showingInfo, arrowEdge: .top) {
                            EmergencyStopInfoView()
                                .presentationCompactAdaptation(.popover)
                        }
                        Text(summaryText)
                            .font(.footnote)
                            .foregroundStyle(enabled == true ? AnyShapeStyle(Color.red) : AnyShapeStyle(HierarchicalShapeStyle.secondary))
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("more.emergencyStop.summary")
                    }
                }
                Spacer(minLength: 8)
                if busy {
                    ProgressView()
                } else {
                    Button(role: .destructive) {
                        confirming = true
                    } label: {
                        Text(actionTitle).font(.subheadline.weight(.semibold))
                    }
                    .buttonStyle(.borderless)
                    .tint(.red)
                    .disabled(enabled == nil)
                    .accessibilityLabel("全局紧急停止")
                    .accessibilityValue(statusText)
                    .accessibilityHint(enabled == true ? "解除紧急停止" : "立即紧急停止")
                    .accessibilityIdentifier("more.emergencyStop")
                }
            }
            .padding(.vertical, 4)
            .contentShape(.rect)
            .onTapGesture {
                guard !busy, enabled != nil else { return }
                confirming = true
            }
            .listRowBackground(Color.red.opacity(enabled == true ? 0.22 : 0.10))
            .confirmationDialog(confirmTitle, isPresented: $confirming, titleVisibility: .visible) {
                Button(enabled == true ? "解除紧急停止" : "立即紧急停止", role: enabled == true ? nil : .destructive) {
                    Task { await toggle() }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text(enabled == true ? Self.resumeMessage : Self.stopMessage)
            }
        } footer: {
            // 只在切换失败或结果未知时出现；平时说明已在高亮行内。
            if let resultMessage {
                Text(resultMessage).foregroundStyle(.red)
                    .accessibilityIdentifier("more.emergencyStop.result")
            }
        }
        .task { await load() }
    }

    /// 无障碍状态值（VoiceOver 读出），保留原来明确的“未开启／已开启”状态。
    private var statusText: String {
        if let loadError { return "状态读取失败：\(loadError)" }
        switch enabled {
        case .some(true): return "已开启 · 账号进程与 Bot 已停止"
        case .some(false): return "未开启 · 账号与 Bot 正常运行"
        case .none: return "正在读取状态…"
        }
    }
    /// 高亮行内 ⓘ 右边的一行：未开启时是说明，开启时明确“已停止”和恢复方式。
    private var summaryText: String {
        if let loadError { return "状态读取失败：\(loadError)" }
        switch enabled {
        case .some(true): return Self.stoppedSummary
        case .some(false): return Self.summary
        case .none: return "正在读取状态…"
        }
    }
    private var actionTitle: String { enabled == true ? "解除" : "停止" }

    // 文案依据服务器实现（kill_switch_service.converge_runtime、supervisor、account_bot_runtime）：
    // 开启时停止全部账号 worker 子进程、账号 Bot 与交互 Bot，并阻止它们再启动；
    // 解除时重新启动所有状态为“启用”的账号与 Bot。其余服务不读取该开关。
    static let summary = "停止全部消息处理，数据和管理台不受影响"
    static let stoppedSummary = "已停止全部消息处理，点“解除”恢复"
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
