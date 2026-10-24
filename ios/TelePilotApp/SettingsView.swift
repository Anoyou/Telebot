import SwiftUI

struct SettingsView: View {
    var embedded = false
    @Environment(AppModel.self) private var model
    @Environment(AppLockModel.self) private var lock
    @State private var confirmation: Confirmation?
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
                    Text("离开 App 时始终遮住任务切换器中的内容；超过所选时间后返回，需要通过 Face ID、Touch ID 或设备密码解锁。适当的宽限期便于切到 Telegram 查看验证码。此功能不删除服务器会话。")
                }
                Section {
                    NavigationLink { AboutView() } label: {
                        Label {
                            LabeledContent("关于与更新") {
                                Text(AppInfo.version).monospacedDigit()
                            }
                        } icon: { Image(systemName: "info.circle") }
                    }
                    .accessibilityIdentifier("settings.about")
                } header: { Text("关于") } footer: {
                    Text("版本信息、检查服务器更新、更新通道与自动检查都集中在“关于与更新”里。")
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
                    Button(role: .destructive) { confirmation = .logout } label: {
                        Label("退出登录", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                    .frame(minHeight: 44).accessibilityIdentifier("settings.logout")
                }
            }
            .symbolRenderingMode(.hierarchical)
            .navigationTitle("设置")
            .accessibilityIdentifier("settings.screen")
            .confirmationDialog(
                confirmation == .server ? "退出并更换服务器？" : "退出登录？",
                isPresented: Binding(get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }),
                titleVisibility: .visible
            ) {
                let forgetServer = confirmation == .server
                Button(forgetServer ? "退出并更换" : "确认退出", role: .destructive) {
                    Task { await model.logout(forgetServer: forgetServer) }
                }
                .accessibilityIdentifier("settings.confirmLogout")
                Button("取消", role: .cancel) {}
            } message: {
                Text("这会清除此设备的登录状态和已读取数据，不会停止服务器上的任务。")
            }
    }
}

extension SettingsView {
    static func graceLabel(_ seconds: Int) -> String {
        switch seconds {
        case 0: "立即"
        case 60: "离开 1 分钟后"
        default: "离开 \(seconds / 60) 分钟后"
        }
    }
}
