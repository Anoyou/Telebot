import SwiftUI
import TelePilotCore

/// 已保存密钥的查看目标（`GET /api/secrets/reveal`，服务器每次读取写审计、no-store）。
struct SecretRevealTarget: Hashable {
    var kind: String
    var accountID: Int? = nil
    var id: Int? = nil
    var pluginKey: String? = nil
    var field: String? = nil

    var path: String {
        var items = [URLQueryItem(name: "kind", value: kind)]
        if let accountID { items.append(URLQueryItem(name: "account_id", value: String(accountID))) }
        if let id { items.append(URLQueryItem(name: "id", value: String(id))) }
        if let pluginKey { items.append(URLQueryItem(name: "plugin_key", value: pluginKey)) }
        if let field { items.append(URLQueryItem(name: "field", value: field)) }
        var components = URLComponents()
        components.path = "/api/secrets/reveal"
        components.queryItems = items
        return components.string ?? "/api/secrets/reveal?kind=\(kind)"
    }
}

/// 统一的密钥 / 密码 / Token 输入行（第 20 轮）。
///
/// - 第一行是标题（如「交互 Bot Token」），不再只靠占位文字说明是什么；
/// - 默认圆点遮罩，右侧「眼睛」切换明文；
/// - 用户正在输入的新值：眼睛直接在本机切换；
/// - 服务器上已保存的旧值：点眼睛时才请求 `/api/secrets/reveal`，明文只放在内存，
///   隐藏、离开页面或 App 进入后台即清除；旧服务器没有该接口时显示「已配置（此服务器版本不支持查看）」，
///   眼睛只对新输入生效。
struct SecretField: View {
    let title: String
    @Binding var text: String
    var configured = false
    var reveal: SecretRevealTarget? = nil
    var placeholder = "未配置"
    var footnote: String? = nil
    var symbol = "key"
    var identifier: String
    var disabled = false
    var contentType: UITextContentType? = nil
    var keyboard: UIKeyboardType = .default

    @Environment(AppModel.self) private var model
    @Environment(AppLockModel.self) private var lock
    @Environment(\.scenePhase) private var scenePhase
    @State private var visible = false
    @State private var revealed: String?
    @State private var loading = false
    @State private var errorText: String?
    @State private var unsupported = false

    static let configuredMask = "••••••••"

    private var hasDraft: Bool { !text.isEmpty }
    private var showingSaved: Bool { !hasDraft && configured }
    private var serverSupportsReveal: Bool { model.supportsSecretReveal && !unsupported }
    private var canRevealSaved: Bool { showingSaved && reveal != nil && serverSupportsReveal }
    private var eyeEnabled: Bool { !disabled && !loading && (hasDraft || canRevealSaved) }

    private var fieldPlaceholder: String {
        showingSaved ? "已配置 \(Self.configuredMask)（留空保持不变）" : placeholder
    }

    private var statusNote: String? {
        if let errorText { return errorText }
        if showingSaved, reveal != nil, !serverSupportsReveal { return "已配置（此服务器版本不支持查看）" }
        return footnote
    }

    private var display: Binding<String> {
        Binding(
            get: { hasDraft ? text : (visible ? (revealed ?? "") : "") },
            set: { newValue in
                // 在取回的旧值上编辑 = 输入新值
                revealed = nil
                text = newValue
            })
    }

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                    .accessibilityIdentifier("\(identifier).title")
                HStack(spacing: 8) {
                    Group {
                        if visible {
                            TextField(fieldPlaceholder, text: display)
                                .font(.body.monospaced())
                        } else {
                            SecureField(fieldPlaceholder, text: display)
                        }
                    }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textContentType(contentType)
                    .keyboardType(keyboard)
                    .privacySensitive()
                    .disabled(disabled)
                    .accessibilityLabel(title)
                    // 明文可见时朗读真实内容（与系统 TextField 一致），隐藏时只报状态、不念圆点。
                    .accessibilityValue(visible ? (display.wrappedValue.isEmpty ? fieldPlaceholder : display.wrappedValue) : (showingSaved ? "已配置，已隐藏" : (hasDraft ? "已输入，已隐藏" : "未填写")))
                    .accessibilityIdentifier(identifier)
                    Button {
                        Task { await toggle() }
                    } label: {
                        Group {
                            if loading {
                                ProgressView().controlSize(.small)
                            } else {
                                Image(systemName: visible ? "eye.slash" : "eye")
                            }
                        }
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.borderless)
                    .disabled(!eyeEnabled)
                    .accessibilityLabel(visible ? "隐藏" : "显示")
                    .accessibilityIdentifier("\(identifier).reveal")
                }
                .frame(minHeight: 44)
                if let statusNote {
                    Text(statusNote)
                        .font(.footnote)
                        .foregroundStyle(errorText == nil ? Color.secondary : Color.red)
                        .accessibilityIdentifier("\(identifier).note")
                }
            }
        } icon: {
            Image(systemName: symbol)
        }
        .task {
            // 直接进入本页、概览还没读过能力标志时补读一次，避免误显示「不支持查看」。
            if configured, reveal != nil, model.serverFeatures.isEmpty { await model.refreshModules() }
        }
        .onDisappear(perform: hide)
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { hide() }
        }
        .onChange(of: reveal) { _, _ in hide() }
        .onChange(of: configured) { _, _ in hide() }
    }

    private func hide() {
        visible = false
        revealed = nil
        loading = false
    }

    private func toggle() async {
        errorText = nil
        if visible {
            hide()
            return
        }
        if canRevealSaved, revealed == nil, let reveal {
            // 与「验证后查看主密钥」一致：开启界面锁时先验证 Face ID / 密码。
            guard await lock.verifySensitiveAccess() else { return }
            loading = true
            defer { loading = false }
            do {
                let value = try await model.request(path: reveal.path)
                if value["configured"]?.boolValue == false {
                    errorText = "服务器上没有保存这一项"
                    revealed = ""
                } else {
                    revealed = value["value"]?.stringValue ?? ""
                }
            } catch {
                if Self.isMissingEndpoint(error) {
                    unsupported = true
                } else {
                    errorText = "读取失败：\(error.localizedDescription)"
                }
                return
            }
        }
        visible = true
    }

    /// 旧服务器没有 `/api/secrets/reveal`：404（无错误码）或 405。
    static func isMissingEndpoint(_ error: Error) -> Bool {
        guard let apiError = error as? APIError, case .server(let status, let code) = apiError else { return false }
        // 新服务器的 404 一定带错误码（ACCOUNT_NOT_FOUND 等）；没有路由时是框架的 404 / 405。
        return (status == 404 && (code == nil || code == "HTTP")) || status == 405
    }
}

/// 只有输入框 + 眼睛的轻量版本（登录页、Cloudflare Access 等需要保持原有焦点链的地方）：
/// 圆点遮罩，眼睛只切换本机正在输入的内容；标题由所在 Section 或占位文字给出。
struct RevealableSecureField<Focus: Hashable>: View {
    let placeholder: String
    @Binding var text: String
    let identifier: String
    var accessibilityLabel: String? = nil
    var contentType: UITextContentType? = nil
    var focus: FocusState<Focus?>.Binding
    let focusValue: Focus
    var submitLabel: SubmitLabel = .done
    var onSubmit: () -> Void = {}

    @Environment(\.scenePhase) private var scenePhase
    @State private var visible = false

    var body: some View {
        HStack(spacing: 8) {
            Group {
                if visible {
                    TextField(placeholder, text: $text).font(.body.monospaced())
                } else {
                    SecureField(placeholder, text: $text)
                }
            }
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .textContentType(contentType)
            .focused(focus, equals: focusValue)
            .submitLabel(submitLabel)
            .onSubmit(onSubmit)
            .privacySensitive()
            .accessibilityLabel(accessibilityLabel ?? placeholder)
            .accessibilityIdentifier(identifier)
            Button {
                visible.toggle()
                focus.wrappedValue = focusValue
            } label: {
                Image(systemName: visible ? "eye.slash" : "eye")
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .disabled(text.isEmpty)
            .accessibilityLabel(visible ? "隐藏" : "显示")
            .accessibilityIdentifier("\(identifier).reveal")
        }
        .onDisappear { visible = false }
        .onChange(of: scenePhase) { _, phase in if phase != .active { visible = false } }
    }
}
