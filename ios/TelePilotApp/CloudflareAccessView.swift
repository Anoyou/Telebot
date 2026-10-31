import AuthenticationServices
import SwiftUI
import TelePilotCore
import UIKit

/// 系统登录窗（ASWebAuthenticationSession）。Google 不允许在 App 内嵌网页（WKWebView）里登录，
/// 所以 Access 登录页交给系统浏览器组件打开；令牌由 Cloudflare 转交服务交回 App（见 TelePilotCore 的
/// CloudflareAccessLogin），登录窗本身不需要回调，取回令牌后由 App 主动关闭。
@MainActor
final class CloudflareAccessBrowser: NSObject, ASWebAuthenticationPresentationContextProviding {
    /// 不会真正被调用的回调 scheme：登录完成后 Access 会跳回服务器首页，App 在拿到令牌后主动关闭登录窗。
    static let callbackScheme = "telepilot-cf-access"
    private var session: ASWebAuthenticationSession?
    private var closing = false
    /// 用户自己关闭了登录窗（而不是 App 拿到令牌后关闭）。
    private(set) var dismissedByUser = false
    var onDismiss: (() -> Void)?

    func open(_ url: URL) -> Bool {
        let session = ASWebAuthenticationSession(url: url, callback: .customScheme(Self.callbackScheme)) { [weak self] _, _ in
            let owner = self
            Task { @MainActor in owner?.finished() }
        }
        session.presentationContextProvider = self
        // 共用 Safari 的登录状态：已在 Safari 登录过 Google 时只需点选账号。
        session.prefersEphemeralWebBrowserSession = false
        self.session = session
        return session.start()
    }

    func close() {
        closing = true
        session?.cancel()
        session = nil
    }

    private func finished() {
        session = nil
        guard !closing else { return }
        dismissedByUser = true
        onDismiss?()
        onDismiss = nil
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            if let window = scenes.flatMap(\.windows).first(where: \.isKeyWindow) ?? scenes.first?.windows.first {
                return window
            }
            return ASPresentationAnchor()
        }
    }
}

/// Cloudflare Access 状态的展示文案。
enum CloudflareAccessText {
    /// 设置页入口右侧的简短状态。
    static func summary(_ config: CloudflareAccessConfig?) -> String {
        guard let config else { return "—" }
        if config.serviceToken != nil { return "Service Token" }
        guard let session = config.session else { return "未验证" }
        return session.isExpired() ? "已过期" : "已验证"
    }

    static func sessionState(_ session: CloudflareAccessSession?) -> String {
        guard let session else { return "未验证" }
        return session.isExpired() ? "已过期" : "有效"
    }

    static func expiry(_ session: CloudflareAccessSession) -> String {
        session.expiresAt.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? "未知"
    }

    static func verified(_ session: CloudflareAccessSession) -> String {
        let who = session.email.map { "（\($0)）" } ?? ""
        return "已通过 Google 验证 Cloudflare Access\(who)，有效期至 \(expiry(session))。"
    }

    static func failure(_ error: Error) -> String? {
        if let apiError = error as? APIError {
            if case .cloudflareAccessLogin(.cancelled) = apiError { return nil }
            return apiError.localizedDescription
        }
        return "未能完成 Cloudflare Access 验证，请稍后重试。"
    }
}

/// Access 登录令牌的状态行（状态、账号、到期时间）。
struct CloudflareAccessSessionRows: View {
    let session: CloudflareAccessSession?
    let identifierPrefix: String

    var body: some View {
        LabeledContent {
            Text(CloudflareAccessText.sessionState(session))
                .foregroundStyle(session?.isExpired() == false ? Color.green : Color.secondary)
        } label: { Label("Google 验证", systemImage: "person.badge.shield.checkmark") }
            .accessibilityIdentifier(identifierPrefix + ".sessionStatus")
        if let session {
            if let email = session.email {
                LabeledContent {
                    Text(email).foregroundStyle(.secondary).textSelection(.enabled)
                } label: { Label("账号", systemImage: "envelope") }
            }
            LabeledContent {
                Text(CloudflareAccessText.expiry(session)).foregroundStyle(.secondary)
            } label: { Label(session.isExpired() ? "已于" : "到期时间", systemImage: "clock") }
                .accessibilityIdentifier(identifierPrefix + ".expiry")
        }
    }
}

/// 设置 → Cloudflare Access：用 Google 验证（主方案），或在“高级”里使用 Service Token。
/// 已登录时请求被 Access 拦截（会话过期、被 302 到 cloudflareaccess.com），也以 sheet 形式直接打开本页（`presentedAsSheet`），
/// 一键重新验证。
struct CloudflareAccessSettingsView: View {
    var presentedAsSheet = false
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var config = CloudflareAccessConfig()
    @State private var loading = true
    @State private var loadError: String?
    @State private var clientID = ""
    @State private var clientSecret = ""
    @State private var isWorking = false
    @State private var outcome: Outcome?
    @State private var confirmClear = false
    @State private var confirmClearSession = false
    @FocusState private var focusedField: Field?
    private enum Field { case clientID, clientSecret }

    private enum Outcome: Equatable {
        case success(String)
        case failure(String)
    }

    private var busy: Bool { isWorking || model.isVerifyingCloudflareAccess }

    private var canSave: Bool {
        !busy && !clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !clientSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        Form {
            if presentedAsSheet {
                Section {
                    NoticeView(title: "请求被 Cloudflare Access 拦截",
                               message: APIError.cloudflareAccessRequired(model.cloudflareAccessBlockKind).localizedDescription,
                               symbol: "lock.icloud")
                        .accessibilityIdentifier("cfAccess.blockedNotice")
                } footer: {
                    Text("被拦截的请求没有到达 TelePilot。验证后 App 只重新读取数据，不会自动重新提交任何写操作。")
                }
            }

            Section {
                if loading { ProgressView() }
                else { CloudflareAccessSessionRows(session: config.session, identifierPrefix: "cfAccess") }
                if let loadError {
                    NoticeView(title: "未能读取钥匙串", message: loadError)
                }
                Button(action: verifyWithGoogle) {
                    HStack {
                        Label(model.isVerifyingCloudflareAccess ? "正在等待 Google 登录…"
                              : (config.session == nil ? "使用 Google 验证" : "重新用 Google 验证"),
                              systemImage: "person.badge.key")
                        Spacer()
                        if model.isVerifyingCloudflareAccess { ProgressView() }
                    }
                    .frame(minHeight: 44)
                }
                .disabled(busy)
                .accessibilityIdentifier("cfAccess.google")
                if let outcome {
                    switch outcome {
                    case .success(let message):
                        NoticeView(title: "连接正常", message: message, symbol: "checkmark.circle.fill")
                            .accessibilityIdentifier("cfAccess.result")
                    case .failure(let message):
                        NoticeView(title: "未能通过验证", message: message, symbol: "exclamationmark.triangle.fill")
                            .accessibilityIdentifier("cfAccess.result")
                    }
                }
                if config.session != nil {
                    Button(role: .destructive) { confirmClearSession = true } label: {
                        Label("移除 Access 登录", systemImage: "rectangle.portrait.and.arrow.right").frame(minHeight: 44)
                    }
                    .disabled(busy)
                    .accessibilityIdentifier("cfAccess.clearSession")
                    .confirmationDialog("移除本机的 Cloudflare Access 登录？", isPresented: $confirmClearSession, titleVisibility: .visible) {
                        Button("移除", role: .destructive) { clearSession() }
                            .accessibilityIdentifier("cfAccess.confirmClearSession")
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("移除后，服务器仍启用 Cloudflare Access 时需要重新用 Google 验证。")
                    }
                }
            } header: { Text("Google 验证（推荐）") } footer: {
                Text("在系统登录窗里完成和浏览器相同的 Google 登录，App 取回 Cloudflare Access 登录令牌后自动关闭窗口。无需在 Cloudflare 后台新增任何策略。令牌只保存在本机钥匙串，按服务器分别保存，不同步到 iCloud；到期后 App 会提示重新验证。")
            }

            Section {
                LabeledContent {
                    if loading { ProgressView() }
                    else { Text(config.serviceToken == nil ? "未配置" : "已配置") }
                } label: { Label("Service Token", systemImage: "lock.icloud") }
                .accessibilityIdentifier("cfAccess.status")
                if let saved = config.serviceToken {
                    LabeledContent {
                        Text(saved.maskedClientID).font(.body.monospaced()).foregroundStyle(.secondary)
                    } label: { Label("Client ID", systemImage: "person.badge.key") }
                }
                LabeledFieldRow(title: "Client ID", identifier: "cfAccess.clientID") {
                    TextField("Client ID", text: $clientID, prompt: Text("形如 xxxx.access"))
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .keyboardType(.asciiCapable)
                        .focused($focusedField, equals: .clientID)
                        .submitLabel(.next)
                        .onSubmit { focusedField = .clientSecret }
                        .accessibilityLabel("Cloudflare Access Client ID")
                        .accessibilityIdentifier("cfAccess.clientID")
                }
                LabeledFieldRow(title: "Client Secret", identifier: "cfAccess.clientSecret") {
                    RevealableSecureField(
                        placeholder: "未填写", text: $clientSecret, identifier: "cfAccess.clientSecret",
                        accessibilityLabel: "Cloudflare Access Client Secret",
                        focus: $focusedField, focusValue: .clientSecret
                    ) { if canSave { save() } }
                }
                Button(action: save) {
                    HStack {
                        Label(isWorking ? "正在保存并验证…" : "保存并验证连接", systemImage: "checkmark.shield")
                        Spacer()
                        if isWorking { ProgressView() }
                    }
                    .frame(minHeight: 44)
                }
                .disabled(!canSave)
                .accessibilityIdentifier("cfAccess.save")
                if config.serviceToken != nil {
                    Button(role: .destructive) { confirmClear = true } label: {
                        Label("清除 Service Token", systemImage: "trash").frame(minHeight: 44)
                    }
                    .disabled(busy)
                    .accessibilityIdentifier("cfAccess.clear")
                    .confirmationDialog("清除 Cloudflare Access Service Token？", isPresented: $confirmClear, titleVisibility: .visible) {
                        Button("清除", role: .destructive) { clear() }
                            .accessibilityIdentifier("cfAccess.confirmClear")
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("清除后改用 Google 验证的登录令牌；只删除本机保存的值，不会吊销 Cloudflare 后台的 Token。")
                    }
                }
            } header: { Text("高级：Service Token") } footer: {
                Text("一般不需要。只有在 Access 应用里另加了一条 Action 为 Service Auth 的策略时才有效；填写后 App 只带 Service Token，不再带 Google 验证的登录令牌。")
            }

            Section {
                NavigationLink { CloudflareAccessHelpView() } label: {
                    Label("Cloudflare Access 使用说明", systemImage: "book")
                }
                .accessibilityIdentifier("cfAccess.help")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .scrollDismissesKeyboard(.interactively)
        .navigationTitle("Cloudflare Access")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if presentedAsSheet {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.cloudflareAccessPrompt = false; dismiss() }
                        .accessibilityIdentifier("cfAccess.close")
                }
            }
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("完成") { focusedField = nil }
                    .accessibilityIdentifier("cfAccess.keyboardDone")
            }
        }
        .accessibilityIdentifier("cfAccess.screen")
        .task { await load() }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            config = try await model.loadCloudflareAccess()
            loadError = nil
        } catch {
            loadError = (error as? APIError)?.localizedDescription ?? "无法访问系统钥匙串，请解锁设备后重试。"
        }
    }

    private func verifyWithGoogle() {
        focusedField = nil
        outcome = nil
        Task {
            do {
                let session = try await model.authenticateCloudflareAccess()
                config = (try? await model.loadCloudflareAccess()) ?? config
                if config.serviceToken != nil {
                    outcome = .success(CloudflareAccessText.verified(session) + "当前仍优先使用 Service Token；清除它后改用此登录。")
                } else {
                    outcome = .success(CloudflareAccessText.verified(session))
                }
                if presentedAsSheet && config.serviceToken == nil {
                    model.cloudflareAccessPrompt = false
                    dismiss()
                }
            } catch {
                if let message = CloudflareAccessText.failure(error) { outcome = .failure(message) }
            }
        }
    }

    private func clearSession() {
        outcome = nil
        isWorking = true
        Task {
            defer { isWorking = false }
            do {
                try await model.clearCloudflareAccessSession()
                config.session = nil
            } catch {
                outcome = .failure((error as? APIError)?.localizedDescription ?? "未能从系统钥匙串移除，请解锁设备后重试。")
            }
        }
    }

    private func save() {
        focusedField = nil
        outcome = nil
        let credentials: CloudflareAccessCredentials
        do { credentials = try CloudflareAccessCredentials(clientID: clientID, clientSecret: clientSecret) }
        catch {
            outcome = .failure((error as? APIError)?.localizedDescription ?? "Service Token 格式不正确。")
            return
        }
        isWorking = true
        Task {
            defer { isWorking = false }
            do {
                try await model.setCloudflareAccess(credentials)
                config.serviceToken = credentials
                clientID = ""
                clientSecret = ""
            } catch {
                outcome = .failure((error as? APIError)?.localizedDescription ?? "未能保存到系统钥匙串，请解锁设备后重试。")
                return
            }
            if let failure = await model.verifyCloudflareAccess() {
                outcome = .failure("Service Token 已保存，但验证没有通过：" + failure)
            } else {
                outcome = .success("已保存，并已通过 Cloudflare Access 连接到 TelePilot。")
            }
        }
    }

    private func clear() {
        outcome = nil
        isWorking = true
        Task {
            defer { isWorking = false }
            do {
                try await model.setCloudflareAccess(nil)
                config.serviceToken = nil
            } catch {
                outcome = .failure((error as? APIError)?.localizedDescription ?? "未能从系统钥匙串移除，请解锁设备后重试。")
            }
        }
    }
}

/// App 内帮助：Google 验证怎样工作，以及（高级）Service Token 的后台配置。
struct CloudflareAccessHelpView: View {
    static let docsURL = URL(string: "https://github.com/Anoyou/Telebot/blob/Beta/docs/CLOUDFLARE-ACCESS.md")!

    private let googleSteps: [(title: String, detail: String)] = [
        ("后台不用改", "沿用浏览器访问用的 Access 应用和 Allow 策略（邮箱白名单 + Google 登录方式），不需要新增绕过或其他策略。"),
        ("点“使用 Google 验证”", "登录页或 设置 → Cloudflare Access 中点按后，系统会询问是否允许 TelePilot 使用你的服务器域名登录，选择“继续”。"),
        ("在系统登录窗完成 Google 登录", "和浏览器里一样选择 Google 账号。完成后 App 自动取回 Access 登录令牌并关闭窗口。"),
        ("到期后重新验证", "令牌有效期等于 Access 应用的会话时长。到期或被 Cloudflare 拦截时，App 会弹出提示，点一次即可重新验证。"),
    ]

    private let tokenSteps: [(title: String, detail: String)] = [
        ("创建 Service Token", "Cloudflare 控制台 → Zero Trust → Access → Service Auth → Service Tokens，点击“Create Service Token”。Client Secret 只显示一次。"),
        ("另加一条 Service Auth 策略", "编辑保护 TelePilot 的 Access 应用，新增策略：Action 选 Service Auth，Include 选 Service Token 并选中刚创建的 Token。原来的 Google 策略保留。"),
        ("在 App 里填写", "App 设置 → Cloudflare Access → 高级：Service Token，填写 Client ID 与 Client Secret。"),
    ]

    var body: some View {
        Form {
            steps(googleSteps, header: "用 Google 验证（推荐）",
                  footer: "App 使用与 cloudflared access login 相同的官方流程：登录页在系统浏览器组件中打开（Google 不允许 App 内嵌网页登录），令牌经 Cloudflare 转交服务加密交回本机。")
            steps(tokenSteps, header: "高级：Service Token",
                  footer: "只在无法使用 Google 验证（例如自动化场景）时需要。Token 到期或被吊销后，App 会提示“Cloudflare Access 拒绝了已填写的 Service Token”。")
            Section {
                Text("若 Access 应用开启了 Binding Cookie，App 取回的令牌可能被拒绝；遇到反复要求验证时，可在该应用的设置里关闭 Binding Cookie，或改用 Service Token。")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Text("注意") }
            Section {
                Link(destination: Self.docsURL) { Label("在 GitHub 查看完整说明", systemImage: "safari").frame(minHeight: 44) }
                    .accessibilityIdentifier("cfAccess.docs")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("Cloudflare Access 说明")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func steps(_ items: [(title: String, detail: String)], header: String, footer: String) -> some View {
        Section {
            ForEach(Array(items.enumerated()), id: \.offset) { index, step in
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(index + 1). \(step.title)").font(.subheadline.weight(.semibold))
                    Text(step.detail).font(.footnote).foregroundStyle(.secondary)
                }
                .padding(.vertical, 2)
                .accessibilityElement(children: .combine)
            }
        } header: { Text(header) } footer: { Text(footer) }
    }
}
