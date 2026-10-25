import SwiftUI
import UIKit
import UniformTypeIdentifiers
import TelePilotCore

struct PushSettingsView: View {
    @Environment(PushNotificationManager.self) private var push
    @Environment(AppModel.self) private var model
    @State private var teamID = ""
    @State private var keyID = ""
    @State private var bundleID = ""
    @State private var environment = "sandbox"
    @State private var enabled = false
    @State private var privateKey: String?
    @State private var pickingKey = false
    @State private var saving = false
    @State private var feedback: String?
    @State private var confirmingSave = false
    @State private var refreshing = false

    var body: some View {
        Form {
            Section {
                PushCheckRow(title: "系统通知权限", symbol: "hand.raised", check: permissionCheck)
                PushCheckRow(title: "设备已注册", symbol: "iphone", check: deviceCheck)
                PushCheckRow(title: "服务器 APNs 已配置", symbol: "server.rack", check: serverCheck)
                PushCheckRow(title: "推送环境匹配", symbol: "arrow.triangle.branch", check: environmentCheck)
                if push.authorization == .denied, let url = URL(string: UIApplication.openNotificationSettingsURLString) {
                    Link(destination: url) { Label("打开系统通知设置", systemImage: "gearshape") }
                }
                NavigationLink { PushSetupGuideView() } label: { Label("配置教程", systemImage: "book") }
                Button(refreshing ? "正在刷新…" : "刷新状态", systemImage: "arrow.clockwise") { Task { await refresh() } }
                    .disabled(refreshing)
            } header: { Text("推送状态") } footer: {
                Text("四项都为绿色勾选时，App 在后台或关闭后也能收到通知。状态直接读取自系统设置和服务器。")
            }
            Section {
                if let message = push.statusMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
                if let error = push.registrationError { NoticeView(title: "推送尚未就绪", message: error) }
                Button(push.isRegistering ? "正在注册…" : "授权并注册设备", systemImage: "bell.badge") { Task { await push.enable() } }
                    .disabled(push.isRegistering || saving)
                Button(push.isTesting ? "正在发送…" : "发送测试通知", systemImage: "paperplane") { Task { await push.test() } }
                    .disabled(!canTest || push.isTesting || saving)
                if let result = push.testResult { PushTestResultView(result: result) }
                Button("注销本机通知", systemImage: "bell.slash", role: .destructive) { Task { await push.disable() } }
                    .disabled(!push.isEnabled || saving)
            } header: { Text("本机通知") } footer: {
                Text("“Apple 已接受”表示服务器与 Apple 的连接、密钥和设备令牌都有效；是否弹出横幅仍受专注模式和系统通知设置影响。")
            }
            Section {
                categoryToggle("assistant_approval", title: "助手待确认", symbol: "checkmark.shield",
                               detail: "助手需要你批准一项操作时提醒。")
                categoryToggle("assistant_result", title: "助手任务结果", symbol: "sparkles",
                               detail: "助手任务成功、失败或取消时提醒。")
                categoryToggle("account_alert", title: "账号告警", symbol: "exclamationmark.triangle",
                               detail: "Telegram 账号失效、需要重新登录或被限流时提醒。")
            } header: { Text("通知类别") } footer: {
                Text("只影响本机。通知仅包含固定文案，不包含聊天内容、账号名称或密钥。测试通知不受类别影响。")
            }
            Section {
                if let error = push.configurationError { NoticeView(title: "无法读取服务器配置", message: error) }
                Toggle(isOn: $enabled) { Label("启用 APNs 服务", systemImage: "antenna.radiowaves.left.and.right") }
                TextField("Team ID（10 位）", text: $teamID).textInputAutocapitalization(.characters).autocorrectionDisabled()
                TextField("Key ID（10 位）", text: $keyID).textInputAutocapitalization(.characters).autocorrectionDisabled()
                TextField("Bundle ID", text: $bundleID).textInputAutocapitalization(.never).autocorrectionDisabled()
                Picker("推送环境", selection: $environment) {
                    Text("sandbox（开发／Xcode 调试）").tag("sandbox")
                    Text("production（Ad Hoc／TestFlight）").tag("production")
                }
                if let local = PushNotificationManager.signingEnvironment, local != environment {
                    Button("改为本机环境：\(Self.environmentLabel(local))", systemImage: "arrow.triangle.2.circlepath") { environment = local }
                }
                if let local = Bundle.main.bundleIdentifier, !local.isEmpty, local != bundleID {
                    Button("填入本机 Bundle ID", systemImage: "square.and.pencil") { bundleID = local }
                }
                LabeledContent {
                    Text(privateKey == nil ? (push.configuration?["has_auth_key"]?.boolValue == true ? "服务器已保存" : "尚未配置") : "已选择新密钥")
                } label: { Label("Auth Key（.p8）", systemImage: "key") }
                Button("选择 .p8 私钥文件", systemImage: "doc.badge.plus") { pickingKey = true }
                if privateKey != nil { Button("取消替换密钥", systemImage: "xmark.circle") { privateKey = nil } }
                Button(saving ? "正在保存…" : "保存服务器配置", systemImage: "square.and.arrow.down") { confirmingSave = true }
                    .disabled(saving || push.configuration == nil)
                    .confirmationDialog("保存推送通道配置？", isPresented: $confirmingSave, titleVisibility: .visible) {
                        Button("确认保存") { Task { await save() } }
                    } message: { Text("这会修改服务器所有设备使用的 APNs 通道。") }
            } header: { Text("服务器推送通道") } footer: {
                Text("Team ID、Key ID 和 .p8 私钥来自 Apple Developer 后台，Bundle ID 必须与本机安装包一致。私钥加密保存在你自己的服务器，不会再次显示。")
            }
            if let feedback { Section { Text(feedback).font(.footnote) } }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("Apple 推送")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await push.connect(model: model)
            fillConfiguration()
        }
        .refreshable { await refresh() }
        .onDisappear { privateKey = nil }
        .fileImporter(isPresented: $pickingKey, allowedContentTypes: [.item]) { result in
            do {
                let url = try result.get()
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                let handle = try FileHandle(forReadingFrom: url)
                defer { try? handle.close() }
                let data = try handle.read(upToCount: 16_385) ?? Data()
                guard data.count <= 16_384, let text = String(data: data, encoding: .utf8), text.contains("PRIVATE KEY") else {
                    feedback = "请选择有效的 .p8 私钥文件。"; return
                }
                privateKey = text
                feedback = nil
            } catch { feedback = "未能读取文件，请重新选择。" }
        }
    }

    // MARK: - 状态检查

    private var canTest: Bool { push.isEnabled || push.serverDevice != nil }

    private var permissionCheck: PushCheck {
        switch push.authorization {
        case .authorized: PushCheck(state: .ok, detail: "已允许")
        case .provisional: PushCheck(state: .ok, detail: "临时允许（通知会静默出现在通知中心）")
        case .ephemeral: PushCheck(state: .ok, detail: "临时授权")
        case .denied: PushCheck(state: .failed, detail: "已关闭，请在系统设置中允许 TelePilot 发送通知")
        default: PushCheck(state: .warning, detail: "尚未询问，点按“授权并注册设备”")
        }
    }

    private var deviceCheck: PushCheck {
        if !push.hasLoadedDevices { return PushCheck(state: .unknown, detail: "正在读取服务器设备列表…") }
        if push.deviceLookupFailed { return PushCheck(state: .unknown, detail: "无法读取服务器设备列表，请检查连接后刷新") }
        guard let device = push.serverDevice else {
            return PushCheck(state: .failed, detail: push.isEnabled ? "服务器上还没有本机记录，请点按“授权并注册设备”重试" : "尚未注册本机")
        }
        let lastError = device["last_error"]?.stringValue
        if device["enabled"]?.boolValue == false {
            return PushCheck(state: .failed, detail: "已暂停（\(lastError ?? "未知原因")），请检查签名环境后重新注册")
        }
        if let expires = Self.parseDate(device["expires_at"]?.stringValue), expires < .now {
            return PushCheck(state: .warning, detail: "注册已过期，App 回到前台时会自动续期")
        }
        if let lastError { return PushCheck(state: .warning, detail: "已注册，但最近一次投递失败：\(lastError)") }
        if let expires = Self.parseDate(device["expires_at"]?.stringValue) {
            return PushCheck(state: .ok, detail: "已注册，有效期至 \(expires.formatted(date: .abbreviated, time: .shortened))")
        }
        return PushCheck(state: .ok, detail: "已注册")
    }

    private var serverCheck: PushCheck {
        guard let config = push.configuration else {
            return PushCheck(state: .unknown, detail: push.configurationError == nil ? "正在读取…" : "无法读取服务器配置")
        }
        if config["configured"]?.boolValue != true {
            var missing: [String] = []
            if config["team_id"]?.stringValue == nil { missing.append("Team ID") }
            if config["key_id"]?.stringValue == nil { missing.append("Key ID") }
            if config["bundle_id"]?.stringValue == nil { missing.append("Bundle ID") }
            if config["has_auth_key"]?.boolValue != true { missing.append(".p8 私钥") }
            return PushCheck(state: .failed, detail: missing.isEmpty ? "配置不完整" : "缺少 " + missing.joined(separator: "、"))
        }
        if config["enabled"]?.boolValue != true { return PushCheck(state: .warning, detail: "已填写但未启用，请打开“启用 APNs 服务”并保存") }
        return PushCheck(state: .ok, detail: "已启用（Key ID \(config["key_id"]?.stringValue ?? "—")）")
    }

    private var environmentCheck: PushCheck {
        guard let local = PushNotificationManager.signingEnvironment else {
            return PushCheck(state: .failed, detail: "本机签名没有 Push Notifications 权限（缺少 aps-environment）")
        }
        guard let config = push.configuration else { return PushCheck(state: .unknown, detail: "本机为 \(Self.environmentLabel(local))，等待服务器配置") }
        let server = config["environment"]?.stringValue ?? "production"
        if server != local {
            return PushCheck(state: .failed, detail: "本机为 \(Self.environmentLabel(local))，服务器为 \(Self.environmentLabel(server))")
        }
        if let serverBundle = config["bundle_id"]?.stringValue, let localBundle = Bundle.main.bundleIdentifier, serverBundle != localBundle {
            return PushCheck(state: .failed, detail: "Bundle ID 不一致：本机 \(localBundle)，服务器 \(serverBundle)")
        }
        return PushCheck(state: .ok, detail: "均为 \(Self.environmentLabel(local))")
    }

    static func environmentLabel(_ value: String) -> String {
        value == "sandbox" ? "sandbox（开发）" : value == "production" ? "production（生产）" : value
    }

    static func parseDate(_ raw: String?) -> Date? {
        guard let raw else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: raw) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: raw)
    }

    private func categoryToggle(_ category: String, title: String, symbol: String, detail: String) -> some View {
        Toggle(isOn: Binding(
            get: { push.categories.contains(category) },
            set: { push.setCategory(category, enabled: $0) }
        )) {
            VStack(alignment: .leading, spacing: 2) {
                Label(title, systemImage: symbol)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        await push.prepare()
        await push.reloadConfiguration()
        await push.reloadDevices()
        if privateKey == nil { fillConfiguration() }
    }

    private func fillConfiguration() {
        guard let config = push.configuration else { return }
        teamID = config["team_id"]?.stringValue ?? ""
        keyID = config["key_id"]?.stringValue ?? ""
        bundleID = config["bundle_id"]?.stringValue ?? Bundle.main.bundleIdentifier ?? ""
        environment = config["environment"]?.stringValue ?? PushNotificationManager.signingEnvironment ?? "sandbox"
        enabled = config["enabled"]?.boolValue ?? false
    }

    private func save() async {
        guard !saving else { return }
        saving = true
        defer { saving = false; privateKey = nil }
        var fields: [String: JSONValue] = ["enabled": .bool(enabled), "environment": .string(environment),
                                         "team_id": .string(teamID.trimmingCharacters(in: .whitespacesAndNewlines)),
                                         "key_id": .string(keyID.trimmingCharacters(in: .whitespacesAndNewlines)),
                                         "bundle_id": .string(bundleID.trimmingCharacters(in: .whitespacesAndNewlines))]
        if let privateKey { fields["auth_key_p8"] = .string(privateKey) }
        do {
            _ = try await model.request(path: "/api/apns/config", method: "PATCH", body: .object(fields))
            await push.reloadConfiguration()
            fillConfiguration()
            feedback = "服务器配置已保存。请注册本机设备并发送测试通知确认。"
        } catch {
            feedback = (error as? APIError)?.localizedDescription ?? "未能确认保存结果，请重新读取后核实。"
        }
    }
}

// MARK: - 检查项

struct PushCheck: Equatable {
    enum Level { case ok, warning, failed, unknown }
    let state: Level
    let detail: String
}

private struct PushCheckRow: View {
    let title: String
    let symbol: String
    let check: PushCheck

    var body: some View {
        LabeledContent {
            Label(stateText, systemImage: stateSymbol)
                .labelStyle(.titleAndIcon)
                .font(.subheadline)
                .foregroundStyle(stateColor)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Label(title, systemImage: symbol)
                Text(check.detail).font(.caption).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var stateText: String {
        switch check.state {
        case .ok: "正常"
        case .warning: "注意"
        case .failed: "未就绪"
        case .unknown: "未知"
        }
    }

    private var stateSymbol: String {
        switch check.state {
        case .ok: "checkmark.circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .failed: "xmark.circle.fill"
        case .unknown: "questionmark.circle"
        }
    }

    private var stateColor: Color {
        switch check.state {
        case .ok: .green
        case .warning: .orange
        case .failed: .red
        case .unknown: .secondary
        }
    }
}

private struct PushTestResultView: View {
    let result: PushTestResult

    var body: some View {
        switch result.outcome {
        case .accepted:
            NoticeView(title: "Apple 已接受测试通知", message: result.message, symbol: "checkmark.circle")
        case .rejected:
            NoticeView(title: "测试通知未送出", message: result.message + "\n可在“配置教程 → 常见错误”中按错误代码排查。", symbol: "xmark.circle")
        case .unknown:
            NoticeView(title: "结果未知", message: result.message, symbol: "questionmark.circle")
        }
    }
}

// MARK: - 配置教程

struct PushSetupGuideView: View {
    static let docsURL = URL(string: "https://github.com/Anoyou/Telebot/blob/Beta/docs/IOS-PUSH.md")!

    var body: some View {
        List {
            Section {
                Text("TelePilot 通过 Apple 推送服务（APNs）在 App 处于后台或关闭时提醒你。需要一个付费的 Apple Developer 账号；免费个人签名没有推送权限。")
                    .font(.callout)
                Link(destination: Self.docsURL) { Label("在 GitHub 查看完整教程", systemImage: "safari") }
            }
            step(1, "确认 App ID 已开启推送", symbol: "app.badge",
                 "在 Apple Developer → Certificates, Identifiers & Profiles → Identifiers 中找到与本机 Bundle ID 相同的 App ID，勾选 Push Notifications，然后重新生成并安装描述文件（Xcode 自动签名会自动处理）。")
            step(2, "创建 APNs 密钥（.p8）", symbol: "key",
                 "进入 Keys，点 + 新建密钥，勾选 Apple Push Notification service (APNs) 并点 Configure 选择环境（Sandbox、Production 或两者）。下载 .p8 文件——它只能下载一次，请妥善保存。")
            step(3, "记下 Key ID 与 Team ID", symbol: "number",
                 "Key ID 是密钥详情页的 10 位字符；Team ID 在 Membership details（会员资格详情）中，也是 10 位字符。")
            step(4, "在本页填写服务器配置", symbol: "server.rack",
                 "在“服务器推送通道”中填写 Team ID、Key ID、Bundle ID，选择 .p8 文件，选好推送环境并打开“启用 APNs 服务”，保存。")
            step(5, "选对推送环境", symbol: "arrow.triangle.branch",
                 "Xcode 直接运行或开发描述文件签名 → sandbox；Ad Hoc、TestFlight、App Store → production。上方“推送环境匹配”会显示本机实际环境。若密钥只限单一环境，也要与此一致。")
            step(6, "注册设备并测试", symbol: "paperplane",
                 "点按“授权并注册设备”，允许通知，然后点“发送测试通知”。显示“Apple 已接受”后几秒内应收到一条测试横幅。")
            Section("常见错误") {
                errorRow("BadDeviceToken", "设备令牌与环境不符：检查服务器推送环境是否与本机签名一致，然后重新注册。")
                errorRow("DeviceTokenNotForTopic", "Bundle ID 与安装包不一致：把服务器 Bundle ID 改成本机 Bundle ID。")
                errorRow("InvalidProviderToken", "Team ID、Key ID 或 .p8 不匹配，或密钥已被撤销：核对后重新上传。")
                errorRow("BadEnvironmentKeyIdInToken", "密钥只允许另一个环境使用：换用对应环境的密钥，或切换推送环境。")
                errorRow("TopicDisallowed", "该 Bundle ID 不允许推送：确认 App ID 已开启 Push Notifications 且属于同一 Team。")
                errorRow("Unregistered", "App 已删除或令牌失效：服务器已自动删除该设备，重新打开 App 注册即可。")
                errorRow("TooManyRequests", "短时间内向同一设备发送过多：稍等片刻再试。")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("推送配置教程")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func step(_ number: Int, _ title: String, symbol: String, _ detail: String) -> some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Label("\(number). \(title)", systemImage: symbol).font(.headline)
                Text(detail).font(.callout).foregroundStyle(.secondary)
            }
            .padding(.vertical, 2)
        }
    }

    private func errorRow(_ code: String, _ fix: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(code).font(.subheadline.monospaced().weight(.semibold))
            Text(fix).font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}
