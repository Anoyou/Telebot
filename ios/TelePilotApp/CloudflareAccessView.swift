import SwiftUI
import TelePilotCore

/// 设置 → Cloudflare Access：查看、修改或清除当前服务器的 Service Token。
/// 已登录时请求被 Access 拦截，也以 sheet 形式直接打开本页（`presentedAsSheet`）。
struct CloudflareAccessSettingsView: View {
    var presentedAsSheet = false
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var saved: CloudflareAccessCredentials?
    @State private var loading = true
    @State private var loadError: String?
    @State private var clientID = ""
    @State private var clientSecret = ""
    @State private var isWorking = false
    @State private var outcome: Outcome?
    @State private var confirmClear = false
    @FocusState private var focusedField: Field?
    private enum Field { case clientID, clientSecret }

    private enum Outcome: Equatable {
        case success(String)
        case failure(String)
    }

    private var canSave: Bool {
        !isWorking && !clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !clientSecret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        Form {
            if presentedAsSheet {
                Section {
                    NoticeView(title: "请求被 Cloudflare Access 拦截",
                               message: APIError.cloudflareAccessRequired(tokenConfigured: saved != nil).localizedDescription,
                               symbol: "lock.icloud")
                        .accessibilityIdentifier("cfAccess.blockedNotice")
                }
            }
            Section {
                LabeledContent {
                    if loading { ProgressView() }
                    else { Text(saved == nil ? "未配置" : "已配置") }
                } label: { Label("Service Token", systemImage: "lock.icloud") }
                .accessibilityIdentifier("cfAccess.status")
                if let saved {
                    LabeledContent {
                        Text(saved.maskedClientID).font(.body.monospaced()).foregroundStyle(.secondary)
                    } label: { Label("Client ID", systemImage: "person.badge.key") }
                }
                if let loadError {
                    NoticeView(title: "未能读取钥匙串", message: loadError)
                }
            } header: { Text("当前状态") } footer: {
                Text("服务器前套了 Cloudflare Access（例如 Google 账号验证）时，App 无法像浏览器那样完成网页登录，需要用 Service Token 通过。Token 只保存在本机钥匙串，按服务器分别保存，不同步到 iCloud，也不会写入日志。")
            }

            Section {
                TextField("Client ID（形如 xxxx.access）", text: $clientID)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .keyboardType(.asciiCapable)
                    .focused($focusedField, equals: .clientID)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .clientSecret }
                    .accessibilityLabel("Cloudflare Access Client ID")
                    .accessibilityIdentifier("cfAccess.clientID")
                SecureField("Client Secret", text: $clientSecret)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .focused($focusedField, equals: .clientSecret)
                    .submitLabel(.done)
                    .onSubmit { if canSave { save() } }
                    .accessibilityLabel("Cloudflare Access Client Secret")
                    .accessibilityIdentifier("cfAccess.clientSecret")
            } header: { Text(saved == nil ? "填写 Service Token" : "更换 Service Token") } footer: {
                Text("在 Cloudflare Zero Trust 创建 Service Token 后复制这两个值；Client Secret 只在创建时显示一次。")
            }

            Section {
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
            } footer: {
                Text("保存后用一次只读请求验证能否穿过 Cloudflare Access 到达 TelePilot；不会重试或重新提交任何写操作。")
            }

            if saved != nil {
                Section {
                    Button(role: .destructive) { confirmClear = true } label: {
                        Label("清除 Service Token", systemImage: "trash").frame(minHeight: 44)
                    }
                    .disabled(isWorking)
                    .accessibilityIdentifier("cfAccess.clear")
                    .confirmationDialog("清除 Cloudflare Access Service Token？", isPresented: $confirmClear, titleVisibility: .visible) {
                        Button("清除", role: .destructive) { clear() }
                            .accessibilityIdentifier("cfAccess.confirmClear")
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("清除后，如果服务器仍启用 Cloudflare Access，App 的请求会被拦截，直到重新填写。")
                    }
                } footer: {
                    Text("只删除本机保存的值，不会吊销 Cloudflare 后台的 Token。")
                }
            }

            Section {
                NavigationLink { CloudflareAccessHelpView() } label: {
                    Label("如何在 Cloudflare 后台创建 Service Token", systemImage: "book")
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
            saved = try await model.cloudflareAccess()
            loadError = nil
        } catch {
            loadError = (error as? APIError)?.localizedDescription ?? "无法访问系统钥匙串，请解锁设备后重试。"
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
                saved = credentials
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
                saved = nil
            } catch {
                outcome = .failure((error as? APIError)?.localizedDescription ?? "未能从系统钥匙串移除，请解锁设备后重试。")
            }
        }
    }
}

/// App 内帮助：在 Cloudflare 后台为 TelePilot iOS 创建 Service Token 并放行。
struct CloudflareAccessHelpView: View {
    static let docsURL = URL(string: "https://github.com/Anoyou/Telebot/blob/Beta/docs/CLOUDFLARE-ACCESS.md")!

    private let steps: [(title: String, detail: String)] = [
        ("创建 Service Token", "打开 Cloudflare 控制台 → Zero Trust → Access → Service Auth → Service Tokens，点击“Create Service Token”，填一个名称（如 TelePilot iOS）并选择有效期。"),
        ("复制 Client ID 和 Client Secret", "创建完成的页面会显示 CF-Access-Client-Id 与 CF-Access-Client-Secret。Client Secret 只显示这一次，请立即复制。"),
        ("给 Access 应用加一条 Service Auth 策略", "Zero Trust → Access → Applications，编辑保护 TelePilot 的那个应用，在 Policies 里新增一条策略：Action 选 Service Auth，Include 规则选 Service Token，并选中刚创建的 Token。保存。"),
        ("保留原有的 Google 登录策略", "原来的 Allow（Google 账号）策略不用改，Web 管理台照常用 Google 登录；Service Auth 策略只放行带 Token 的 App 请求。"),
        ("在 App 里填写", "登录页打开“通过 Cloudflare Access 连接”，或登录后在 设置 → Cloudflare Access 中填写 Client ID 与 Client Secret。App 会给每个请求带上这两个请求头。"),
    ]

    var body: some View {
        Form {
            Section {
                ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(index + 1). \(step.title)").font(.subheadline.weight(.semibold))
                        Text(step.detail).font(.footnote).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                    .accessibilityElement(children: .combine)
                }
            } header: { Text("Cloudflare 后台步骤") } footer: {
                Text("Token 到期或被吊销后，App 会提示“Cloudflare Access 拒绝了已填写的 Service Token”，回到这里更换即可。")
            }
            Section {
                Label("App 无法完成 Google 网页登录", systemImage: "info.circle")
                    .font(.subheadline)
                Text("Google 不允许在 App 内嵌网页中登录，系统浏览器里的 Cloudflare 登录 Cookie 也不会共享给 App，所以 App 使用 Service Token 通过 Access。")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Text("为什么不用 Google 登录") }
            Section {
                Link(destination: Self.docsURL) { Label("在 GitHub 查看完整说明", systemImage: "safari").frame(minHeight: 44) }
                    .accessibilityIdentifier("cfAccess.docs")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("创建 Service Token")
        .navigationBarTitleDisplayMode(.inline)
    }
}
