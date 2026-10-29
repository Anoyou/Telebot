import SwiftUI
import TelePilotCore

struct SettingsView: View {
    var embedded = false
    @Environment(AppModel.self) private var model
    @Environment(AppLockModel.self) private var lock
    @State private var confirmation: Confirmation?
    @AppStorage(AppAppearance.storageKey) private var appearanceRaw = AppAppearance.system.rawValue
    private enum Confirmation: String, Identifiable {
        case logout, server
        var id: String { rawValue }
    }

    var body: some View {
        Group {
            if embedded { form }
            else { NavigationStack { form } }
        }
    }

    private var form: some View {
            Form {
                Section {
                    Picker(selection: $appearanceRaw) {
                        ForEach(AppAppearance.allCases) { item in
                            Label(item.title, systemImage: item.symbol).tag(item.rawValue)
                        }
                    } label: { Label("外观", systemImage: "circle.lefthalf.filled") }
                    .pickerStyle(.menu)
                    .accessibilityIdentifier("settings.appearance")
                } header: { Text("外观") } footer: {
                    Text("“自动”跟随系统的浅色／深色设置；选择后立即生效，仅保存在本机。")
                }
                Section("原生体验") {
                    NavigationLink { OverviewCustomizationView() } label: { Label("首页模块", systemImage: "square.grid.2x2") }
                    NavigationLink { PushSettingsView() } label: { Label("Apple 推送通知", systemImage: "bell.badge") }
                }
                Section("当前连接") {
                    LabeledContent {
                        Text(model.user?.username ?? "未登录").textSelection(.enabled)
                    } label: { Label("用户名", systemImage: "person.crop.circle") }
                    if let url = model.serverURL {
                        VStack(alignment: .leading, spacing: 6) {
                            Label("服务器", systemImage: "server.rack").font(.subheadline)
                            Text(url.absoluteString).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                        Link(destination: url) { Label("打开 Web 管理台", systemImage: "safari").frame(minHeight: 44) }
                            .accessibilityIdentifier("settings.openWeb")
                    }
                    NavigationLink { CloudflareAccessSettingsView() } label: {
                        Label {
                            LabeledContent("Cloudflare Access") {
                                Text(CloudflareAccessText.summary(model.cloudflareAccess))
                            }
                        } icon: { Image(systemName: "lock.icloud") }
                    }
                    .accessibilityIdentifier("settings.cfAccess")
                }
                Section {
                    Toggle(isOn: Binding(
                        get: { lock.isEnabled },
                        set: { enabled in Task { await lock.setEnabled(enabled) } }
                    )) { Label("Face ID／设备验证", systemImage: "faceid") }
                    .disabled(lock.isAuthenticating)
                    .accessibilityIdentifier("settings.interfaceLock")
                    if lock.isEnabled {
                        Picker(selection: Binding(get: { lock.graceSeconds }, set: { lock.graceSeconds = $0 })) {
                            ForEach(AppLockModel.graceOptions, id: \.self) { seconds in
                                Text(Self.graceLabel(seconds)).tag(seconds)
                            }
                        } label: { Label("需要验证", systemImage: "timer") }
                        .accessibilityIdentifier("settings.interfaceLockGrace")
                    }
                    if let error = lock.failureMessage {
                        NoticeView(title: "界面锁设置未改变", message: error)
                    }
                } header: { Text("界面锁") } footer: {
                    Text("开启后，离开 App 超过所选时间再返回，需要通过 Face ID、Touch ID 或设备密码解锁。App 不会在多任务切换器、截屏或录屏时遮挡内容。适当的宽限期便于切到 Telegram 查看验证码。此功能不删除服务器会话。")
                }
                Section {
                    Label("密码不保存在 App 中", systemImage: "lock.shield")
                    Label("仅在前台读取服务器数据", systemImage: "iphone")
                    Label("管理功能使用服务器现有权限", systemImage: "server.rack")
                } header: { Text("使用方式") } footer: {
                    Text("浏览器使用独立登录状态，打开管理台时可能需要重新登录。手机锁屏或关闭 App 不影响服务器任务。")
                }
                Section {
                    Button(role: .destructive) { confirmation = .server } label: {
                        Label("更换服务器", systemImage: "arrow.left.arrow.right.circle")
                    }
                    .frame(minHeight: 44).accessibilityIdentifier("settings.changeServer")
                    .modifier(logoutConfirmation(.server))
                    Button(role: .destructive) { confirmation = .logout } label: {
                        Label("退出登录", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                    .frame(minHeight: 44).accessibilityIdentifier("settings.logout")
                    .modifier(logoutConfirmation(.logout))
                }
            }
            .symbolRenderingMode(.hierarchical)
            .navigationTitle("App 设置").navigationBarTitleDisplayMode(.inline)
            .accessibilityIdentifier("settings.screen")
            .task { _ = try? await model.loadCloudflareAccess() }
    }
}

extension SettingsView {
    /// iOS 26+ 确认框以 popover 锚定在所修饰的视图上：“更换服务器”和“退出登录”各自挂一个，
    /// 共用 `confirmation` 状态，只有被点按的那个按钮弹出。
    private func logoutConfirmation(_ kind: Confirmation) -> SettingsLogoutConfirmation {
        SettingsLogoutConfirmation(
            isPresented: Binding(get: { confirmation == kind }, set: { if !$0 { confirmation = nil } }),
            forgetServer: kind == .server,
            logout: { Task { await model.logout(forgetServer: kind == .server) } })
    }

    static func graceLabel(_ seconds: Int) -> String {
        switch seconds {
        case 0: "立即"
        case 60: "离开 1 分钟后"
        default: "离开 \(seconds / 60) 分钟后"
        }
    }
}

/// 设置页“更换服务器 / 退出登录”的二次确认框，挂在触发它的按钮上。
private struct SettingsLogoutConfirmation: ViewModifier {
    @Binding var isPresented: Bool
    let forgetServer: Bool
    let logout: () -> Void

    func body(content: Content) -> some View {
        content.confirmationDialog(
            forgetServer ? "退出并更换服务器？" : "退出登录？",
            isPresented: $isPresented,
            titleVisibility: .visible
        ) {
            Button(forgetServer ? "退出并更换" : "确认退出", role: .destructive) { logout() }
                .accessibilityIdentifier("settings.confirmLogout")
            Button("取消", role: .cancel) {}
        } message: {
            Text(forgetServer
                 ? "这会清除此设备的登录状态、已读取数据和这台服务器的 Cloudflare Access 登录与 Service Token，不会停止服务器上的任务。"
                 : "这会清除此设备的登录状态和已读取数据，不会停止服务器上的任务。")
        }
    }
}

/// App 外观：自动跟随系统，或固定浅色／深色。保存在本机 UserDefaults。
enum AppAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    static let storageKey = "app.appearance"
    var id: String { rawValue }
    var title: String {
        switch self {
        case .system: "自动"
        case .light: "浅色"
        case .dark: "深色"
        }
    }
    var symbol: String {
        switch self {
        case .system: "circle.lefthalf.filled"
        case .light: "sun.max"
        case .dark: "moon"
        }
    }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}
