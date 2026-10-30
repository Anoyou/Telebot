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
                // 0.21.0 起按 iPhone「设置 › 通用」排序：信息类在最上（关于、软件更新，像「关于本机／软件更新」），
                // 常用配置居中，危险操作（更换服务器、退出登录）单独放最底部一组。
                Section {
                    NavigationLink { AboutView() } label: { Label("关于", systemImage: "info.circle") }
                        .accessibilityIdentifier("settings.about")
                    NavigationLink { SoftwareUpdateView() } label: {
                        Label {
                            HStack {
                                Text("软件更新")
                                Spacer()
                                if model.serverUpdateAvailable {
                                    Text(model.serverUpdateTargetVersion.map { "服务器 \($0)" } ?? "服务器有新版本")
                                        .font(.caption.weight(.semibold)).foregroundStyle(.tint)
                                }
                            }
                        } icon: { Image(systemName: "arrow.down.circle") }
                    }
                    .accessibilityIdentifier("settings.update")
                }
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
                    NavigationLink { DockCustomizationView() } label: { Label("自定义 Dock", systemImage: "dock.rectangle") }
                        .accessibilityIdentifier("settings.dock")
                    NavigationLink { PushSettingsView() } label: { Label("Apple 推送通知", systemImage: "bell.badge") }
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
                } footer: {
                    Text("会清除这台设备上的登录状态和已读取的数据，不会停止服务器上的任务。")
                }
            }
            .symbolRenderingMode(.hierarchical)
            .navigationTitle("App 设置").navigationBarTitleDisplayMode(.inline)
            .accessibilityIdentifier("settings.screen")
            .task { _ = try? await model.loadCloudflareAccess() }
            .task { await model.refreshServerUpdateStatus() }
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

// MARK: - 自定义 Dock

/// App 设置 › 自定义 Dock：中间最多 3 格从「概览 / 插件 / 交互 / AI / 账号 / 日志 / 资金台账」里选并排序；
/// 「更多」与 Agent 固定。没放进 Dock 的入口自动出现在「更多」，任何入口始终可达。只保存在本机。
///
/// iPhone 底部 Dock 不支持系统的 Tab 编辑（`TabViewCustomization` 只在 iPad 侧边栏生效），所以这里用自己的设置页；
/// 不提供第 4 格：普通 Tab 超过 4 个（加 Agent 超过 5 个）时系统会自动生成第二个“更多”。
struct DockCustomizationView: View {
    @Environment(AppModel.self) private var model
    @Environment(WorkspacePreferences.self) private var preferences

    private var slots: [DockItem] { preferences.dockSlots }
    private var available: [DockItem] { DockItem.customizable.filter { !slots.contains($0) } }
    private var effective: [DockItem] { preferences.dockItems(disabledModules: model.disabledModules) }
    private var full: Bool { slots.count >= DockItem.maxSlots }

    var body: some View {
        Form {
            Section {
                Text((effective + [.more]).map(\.title).joined(separator: " · ") + "　+ Agent")
                    .font(.subheadline.weight(.medium))
                    .accessibilityIdentifier("dock.preview")
            } header: { Text("当前 Dock") } footer: {
                if effective != slots {
                    Text("服务器关闭了部分模块，对应的格暂时隐藏，由下面列表顺序里的下一个入口补上（默认是 AI）。模块重新开启后自动恢复。")
                }
            }

            Section {
                ForEach(Array(slots.enumerated()), id: \.element) { index, item in
                    HStack(spacing: 12) {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.longTitle)
                                if let note = moduleNote(item) { Text(note).font(.caption).foregroundStyle(.secondary) }
                            }
                        } icon: { Image(systemName: item.symbol) }
                        Spacer(minLength: 8)
                        if index > 0 {
                            Button { preferences.moveDockSlotUp(item) } label: {
                                Image(systemName: "arrow.up").frame(width: 44, height: 44)
                            }
                            .buttonStyle(.borderless)
                            .accessibilityLabel("上移\(item.longTitle)")
                            .accessibilityIdentifier("dock.moveUp.\(item.rawValue)")
                        }
                        Button(role: .destructive) { preferences.removeDockSlot(item) } label: {
                            Image(systemName: "minus.circle.fill").foregroundStyle(slots.count > 1 ? Color.red : Color.secondary)
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.borderless)
                        .disabled(slots.count <= 1)
                        .accessibilityLabel("从 Dock 移除\(item.longTitle)")
                        .accessibilityIdentifier("dock.remove.\(item.rawValue)")
                    }
                }
            } header: { Text("Dock 中间的入口（\(slots.count)/\(DockItem.maxSlots)）") } footer: {
                Text("从左到右排列；「更多」固定在最后，Agent 固定在右侧。至少保留一个入口。")
            }

            Section {
                ForEach(available) { item in
                    Button { preferences.addDockSlot(item) } label: {
                        HStack(spacing: 12) {
                            Label {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.longTitle).foregroundStyle(Color.primary)
                                    if let note = moduleNote(item) { Text(note).font(.caption).foregroundStyle(.secondary) }
                                }
                            } icon: { Image(systemName: item.symbol) }
                            Spacer(minLength: 8)
                            Image(systemName: "plus.circle.fill").foregroundStyle(full ? Color.secondary : Color.green)
                        }
                        .frame(minHeight: 44)
                    }
                    .disabled(full)
                    .accessibilityLabel("加入 Dock：\(item.longTitle)")
                    .accessibilityIdentifier("dock.add.\(item.rawValue)")
                }
            } header: { Text("在「更多」里的入口") } footer: {
                Text(full ? "Dock 已放满 \(DockItem.maxSlots) 个，先移除一个再添加。iPhone 底部最多 4 个普通标签（含「更多」），再多系统会自动生成第二个“更多”。"
                          : "没放进 Dock 的入口都在「更多」里，随时可以打开。")
            }

            Section {
                Button("恢复默认（概览 · 插件 · 交互）", systemImage: "arrow.uturn.backward") { preferences.resetDock() }
                    .disabled(slots == DockItem.defaultSlots)
                    .accessibilityIdentifier("dock.reset")
            } footer: { Text("设置只保存在这台设备上。") }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("自定义 Dock")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("dock.settings")
    }

    private func moduleNote(_ item: DockItem) -> String? {
        guard let module = item.platformModule, !model.isModuleEnabled(module) else { return nil }
        return "服务器已关闭此模块，暂不显示"
    }
}
