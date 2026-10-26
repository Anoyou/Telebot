import SwiftUI
import TelePilotCore

struct LoginView: View {
    @Environment(AppModel.self) private var model
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var code = ""
    @State private var totpCode: String?
    @State private var challenge: LoginResult?
    @State private var usingRecovery = false
    @State private var error: String?
    @State private var accessEnabled = false
    @State private var accessClientID = ""
    @State private var accessSecret = ""
    /// 服务器启用了 Cloudflare Access 时的引导文案（显示在 Cloudflare Access 区内）。
    @State private var accessNotice: String?
    @FocusState private var focusedField: Field?
    private enum Field { case server, username, password, code, accessClientID, accessSecret }

    private var accessIncomplete: Bool {
        accessEnabled && (accessClientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || accessSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    private var isChallenged: Bool { challenge?.requireTotp == true || challenge?.requireOtp == true }
    private var submitLabel: String {
        model.isAuthenticating ? "正在连接…" : (isChallenged ? "验证并登录" : "连接并登录")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(alignment: .center, spacing: 14) {
                        Image("BrandMark")
                            .resizable().scaledToFit().frame(width: 52, height: 52)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("TelePilot").font(.title2.weight(.bold))
                            Text("你的服务器，原生掌控。")
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 8)
                }
                Section {
                    TextField("https://telepilot.example.com", text: $server)
                        .textContentType(.URL).keyboardType(.URL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .focused($focusedField, equals: .server)
                        .submitLabel(.next)
                        .onSubmit { focusedField = .username }
                        .accessibilityLabel("服务器地址")
                        .accessibilityIdentifier("login.server")
                } header: { Text("服务器") } footer: {
                    Text("填写管理台的 HTTPS 根地址，可省略 https://。登录信息只发送到这台服务器，不经过第三方服务。")
                }
                Section("登录账号") {
                    TextField("用户名", text: $username)
                        .textContentType(.username).textInputAutocapitalization(.never)
                        .autocorrectionDisabled().focused($focusedField, equals: .username)
                        .submitLabel(.next)
                        .onSubmit { focusedField = .password }
                        .accessibilityIdentifier("login.username")
                    SecureField("密码", text: $password)
                        .textContentType(.password).focused($focusedField, equals: .password)
                        .submitLabel(.go)
                        .onSubmit {
                            if !server.isEmpty && !username.isEmpty && !password.isEmpty && !isChallenged { submit() }
                        }
                        .accessibilityIdentifier("login.password")
                }
                if isChallenged {
                    Section {
                        NoticeView(
                            title: usingRecovery ? "使用恢复码" : (challenge?.requireOtp == true ? "输入通知验证码" : "输入验证器验证码"),
                            message: challenge?.requireOtp == true ? (challenge?.otpMessage ?? "请查看已绑定通知渠道收到的验证码。") : "请输入验证器当前显示的六位验证码。",
                            symbol: "lock.shield"
                        )
                        .accessibilityIdentifier("login.challenge")
                        TextField(usingRecovery ? "恢复码" : "验证码", text: $code)
                            .keyboardType(usingRecovery ? .asciiCapable : .numberPad)
                            .textContentType(.oneTimeCode)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .focused($focusedField, equals: .code)
                            .accessibilityIdentifier("login.code")
                        if challenge?.recoveryAvailable == true || challenge?.requireTotp == true {
                            Button(usingRecovery ? "改用验证码" : "改用恢复码", systemImage: usingRecovery ? "number" : "key") {
                                usingRecovery.toggle()
                                code = ""
                                error = nil
                            }
                            .accessibilityIdentifier("login.recovery")
                        }
                        Button("重新开始验证", systemImage: "arrow.counterclockwise") { resetChallenge(); error = nil }
                            .accessibilityIdentifier("login.reset")
                    } header: { Text("安全验证") } footer: {
                        if challenge?.requireOtp == true, let ttl = challenge?.otpTTLSeconds {
                            Text("验证码有效期约 \(ttl) 秒；过期后请重新开始验证。")
                        }
                    }
                }
                accessSection
                // 被 Cloudflare Access 拦截时，引导已显示在 Cloudflare Access 区内，不再重复。
                if let message = error ?? (accessNotice == nil ? model.loginNotice : nil) {
                    Section { NoticeView(title: "未能完成登录", message: message) }
                }
                if let message = model.sessionCleanupError {
                    Section {
                        NoticeView(title: "旧会话尚未安全移除", message: message)
                        Button("重试清理旧会话", systemImage: "arrow.clockwise") {
                            error = nil
                            Task { await model.retrySessionCleanup() }
                        }
                            .accessibilityIdentifier("login.retryCleanup")
                    }
                } else if model.canRetrySession {
                    Section {
                        Button("重试已有会话", systemImage: "arrow.clockwise") { retrySession() }
                            .disabled(accessIncomplete)
                            .accessibilityIdentifier("login.retrySession")
                    } footer: { Text("连接失败没有删除原会话。恢复网络后可直接重试，无需重新输入密码。") }
                }
                Section {
                    Button(action: submit) {
                        HStack {
                            Spacer()
                            if model.isAuthenticating { ProgressView() }
                            Text(submitLabel).fontWeight(.semibold)
                            Spacer()
                        }
                        .frame(minHeight: 44)
                    }
                    .disabled(model.sessionCleanupError != nil || server.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || username.isEmpty || password.isEmpty || (isChallenged && code.isEmpty) || accessIncomplete)
                    .accessibilityIdentifier("login.submit")
                } footer: {
                    Text("密码仅用于本次登录，不保存在 App 中。已有会话由系统安全存储管理。高级配置仍可在 Web 管理台完成。")
                }
            }
            .disabled(model.isAuthenticating)
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("连接服务器")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("完成") { focusedField = nil }
                        .accessibilityIdentifier("login.keyboardDone")
                }
            }
            .onAppear { server = model.serverURL?.absoluteString ?? "" }
            .task { await loadSavedAccess() }
            .onChange(of: model.loginAccessBlocked) { _, blocked in if blocked { showAccessRequired(configured: accessEnabled) } }
            .onChange(of: model.serverURL) { _, value in
                if value == nil { server = "" }
            }
            .onChange(of: server) { _, _ in password = ""; resetChallenge(); error = nil }
            // 不清空密码：系统密码自动填充可能先写入密码再写入用户名。
            .onChange(of: username) { _, _ in resetChallenge(); error = nil }
            .onChange(of: password) { _, _ in resetChallenge(); error = nil }
            .onDisappear { password = ""; resetChallenge() }
        }
    }

    /// 用户省略协议时补全 https://；其他校验仍由 TelePilotClient.normalizedServerURL 负责。
    static func serverInput(_ text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !trimmed.contains("://") else { return trimmed }
        return "https://" + trimmed
    }

    /// 可选的 Cloudflare Access 区：服务器前套了 Access 时填写 Service Token。
    @ViewBuilder private var accessSection: some View {
        Section {
            if let accessNotice {
                NoticeView(title: "需要 Cloudflare Access", message: accessNotice, symbol: "lock.icloud")
                    .accessibilityIdentifier("login.cfAccess.notice")
            }
            Toggle(isOn: $accessEnabled.animation()) {
                Label("通过 Cloudflare Access 连接", systemImage: "lock.icloud")
            }
            .accessibilityIdentifier("login.cfAccess.toggle")
            if accessEnabled {
                TextField("Client ID（形如 xxxx.access）", text: $accessClientID)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .keyboardType(.asciiCapable)
                    .focused($focusedField, equals: .accessClientID)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .accessSecret }
                    .accessibilityLabel("Cloudflare Access Client ID")
                    .accessibilityIdentifier("login.cfAccess.clientID")
                SecureField("Client Secret", text: $accessSecret)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .focused($focusedField, equals: .accessSecret)
                    .submitLabel(.done)
                    .onSubmit { focusedField = nil }
                    .accessibilityLabel("Cloudflare Access Client Secret")
                    .accessibilityIdentifier("login.cfAccess.clientSecret")
                NavigationLink { CloudflareAccessHelpView() } label: {
                    Label("如何创建 Service Token", systemImage: "book")
                }
                .accessibilityIdentifier("login.cfAccess.help")
            }
        } header: { Text("Cloudflare Access（可选）") } footer: {
            Text(accessEnabled
                 ? "App 会给每个请求带上 Service Token 请求头。Token 只保存在本机钥匙串，按服务器分别保存，不会写入日志。"
                 : "服务器前套了 Cloudflare Access（例如 Google 账号验证）时打开，填写 Service Token；Web 管理台照常用 Google 登录。")
        }
    }

    /// 读取这台服务器已保存的 Service Token 并预填；启动时已被 Access 拦截则直接展开引导。
    private func loadSavedAccess() async {
        if let saved = try? await model.cloudflareAccess() {
            accessEnabled = true
            accessClientID = saved.clientID
            accessSecret = saved.clientSecret
        }
        if model.loginAccessBlocked { showAccessRequired(configured: accessEnabled) }
    }

    private func showAccessRequired(configured: Bool) {
        withAnimation { accessEnabled = true }
        accessNotice = APIError.cloudflareAccessRequired(tokenConfigured: configured).localizedDescription
        focusedField = configured ? nil : .accessClientID
    }

    /// 登录页填写的 Service Token；关闭开关时为 nil（清除此服务器已保存的值）。
    private func accessCredentials() throws -> CloudflareAccessCredentials? {
        guard accessEnabled else { return nil }
        return try CloudflareAccessCredentials(clientID: accessClientID, clientSecret: accessSecret)
    }

    private func retrySession() {
        error = nil
        Task {
            do {
                // 先保存刚填写的 Service Token，再用原会话重试（只读请求）。
                try await model.setCloudflareAccess(try accessCredentials())
                accessNotice = nil
            } catch {
                self.error = (error as? APIError)?.localizedDescription ?? "未能保存 Cloudflare Access 设置。"
                return
            }
            await model.retrySession()
        }
    }

    private func resetChallenge() {
        challenge = nil
        code = ""
        totpCode = nil
        usingRecovery = false
    }

    private func submit() {
        focusedField = nil
        error = nil
        if challenge?.requireTotp == true && challenge?.requireOtp != true && !usingRecovery { totpCode = code }
        let request = LoginRequest(
            username: username,
            password: password,
            totpCode: totpCode,
            otpToken: challenge?.otpToken,
            otpCode: challenge?.requireOtp == true && !usingRecovery ? code : nil,
            recoveryCode: usingRecovery ? code : nil
        )
        let access: CloudflareAccessCredentials?
        do { access = try accessCredentials() }
        catch {
            self.error = (error as? APIError)?.localizedDescription ?? "Cloudflare Access 设置不完整。"
            return
        }
        accessNotice = nil
        Task {
            do {
                if let result = try await model.login(server: Self.serverInput(server), request: request, access: access), !result.ok {
                    challenge = result
                    code = ""
                    usingRecovery = false
                    if result.requireTotp || result.requireOtp { focusedField = .code }
                    else { error = "服务器没有完成登录，请在 Web 管理台检查登录设置。" }
                }
            } catch {
                if let apiError = error as? APIError, case .cloudflareAccessRequired(let configured) = apiError {
                    // 不当作普通失败：直接展开 Cloudflare Access 区并定位到 Client ID。
                    showAccessRequired(configured: configured)
                } else if let apiError = error as? APIError,
                   case .server(_, let code) = apiError, code == "TOTP_INVALID", challenge?.requireOtp == true {
                    resetChallenge()
                    self.error = "验证器验证码已过期或不正确，请重新开始登录验证。"
                } else {
                    self.error = (error as? APIError)?.localizedDescription ?? "无法完成登录，请检查服务器地址和网络后重试。"
                }
            }
        }
    }
}
