import SwiftUI
import UIKit
import TelePilotCore

/// App 自身的静态信息：版本、构建号与项目链接。
enum AppInfo {
    static let repositoryURL = URL(string: "https://github.com/Anoyou/Telebot")!
    static let changelogURL = URL(string: "https://github.com/Anoyou/Telebot/blob/main/CHANGELOG.md")!
    static let issuesURL = URL(string: "https://github.com/Anoyou/Telebot/issues")!
    static let licenseURL = URL(string: "https://github.com/Anoyou/Telebot/blob/main/LICENSE")!
    static let usageGuideURL = URL(string: "https://github.com/Anoyou/Telebot/blob/main/docs/USAGE-GUIDE.md")!

    static var version: String { Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "未知" }
    static var build: String { Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "未知" }
    static var versionLabel: String { "\(version)（\(build)）" }

    static func channelLabel(_ channel: String) -> String {
        switch channel {
        case "stable": "正式版"
        case "beta": "测试版"
        case "dev": "开发版"
        default: channel
        }
    }

    static func shortCommit(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return String(value.prefix(12))
    }

    /// 解析服务器返回的 ISO 8601 时间（Python 可能带 6 位微秒）。
    static func date(_ value: String?) -> Date? {
        guard let value, !value.isEmpty else { return nil }
        let trimmed = value.replacingOccurrences(of: #"\.\d+"#, with: "", options: .regularExpression)
        return ISO8601DateFormatter().date(from: trimmed)
    }
}

struct AboutView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            Section {
                LabeledContent {
                    Text(AppInfo.version).monospacedDigit().textSelection(.enabled)
                } label: { Label("App 版本", systemImage: "iphone") }
                    .accessibilityIdentifier("about.appVersion")
                LabeledContent {
                    Text(AppInfo.build).monospacedDigit().textSelection(.enabled)
                } label: { Label("构建号", systemImage: "hammer") }
                LabeledContent {
                    Text("iOS \(UIDevice.current.systemVersion)").monospacedDigit()
                } label: { Label("系统版本", systemImage: "gearshape.2") }
                NavigationLink { AppChangelogView() } label: { Label("App 更新日志", systemImage: "list.bullet.rectangle") }
                    .accessibilityIdentifier("about.appChangelog")
            } header: { Text("App") } footer: {
                Text("构建号是同一版本内的打包序号，反馈问题时请一并提供版本与构建号。")
            }

            Section {
                if let url = model.serverURL {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("服务器地址", systemImage: "server.rack")
                        Text(url.absoluteString).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    .accessibilityElement(children: .combine)
                }
                LabeledContent {
                    Text(model.serverVersion?.version ?? "尚未读取").monospacedDigit().textSelection(.enabled)
                } label: { Label("服务器版本", systemImage: "shippingbox") }
                    .accessibilityIdentifier("about.serverVersion")
                if let version = model.serverVersion {
                    LabeledContent {
                        Text(AppInfo.channelLabel(version.channel))
                    } label: { Label("发布通道", systemImage: "arrow.triangle.branch") }
                    if let revision = AppInfo.shortCommit(version.revision) {
                        LabeledContent {
                            Text(revision).font(.body.monospaced()).textSelection(.enabled)
                        } label: { Label("部署提交", systemImage: "number") }
                    }
                }
                if let error = model.versionError {
                    NoticeView(title: model.serverVersion == nil ? "版本读取失败" : "版本信息已过期", message: error)
                }
                ServerUpdateButton()
                if let url = model.serverURL {
                    Link(destination: url) { Label("打开 Web 管理台", systemImage: "safari") }
                }
            } header: { Text("服务器") } footer: {
                Text("服务器与 App 独立更新，版本号不同不代表不兼容。兼容接口的修复通常不需要重装 App；新增原生页面或接口不兼容变化才需要更新客户端。")
            }

            Section("项目") {
                Link(destination: AppInfo.repositoryURL) { Label("GitHub 仓库", systemImage: "chevron.left.forwardslash.chevron.right") }
                Link(destination: AppInfo.changelogURL) { Label("服务器更新日志", systemImage: "list.bullet.clipboard") }
                Link(destination: AppInfo.usageGuideURL) { Label("使用指南", systemImage: "book") }
                Link(destination: AppInfo.issuesURL) { Label("问题反馈", systemImage: "exclamationmark.bubble") }
            }

            Section("许可与隐私") {
                NavigationLink { AboutLicensesView() } label: { Label("开源许可", systemImage: "doc.plaintext") }
                NavigationLink { AboutPrivacyView() } label: { Label("隐私说明", systemImage: "hand.raised") }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("关于")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("about.screen")
    }
}

/// App 自身的更新日志：读取随 App 打包的 `CHANGELOG.md`（来自仓库 `ios/CHANGELOG.md`），离线显示，不请求服务器。
struct AppChangelogView: View {
    private let releases: [AppChangelogRelease]?

    init(bundle: Bundle = .main) {
        if let url = bundle.url(forResource: "CHANGELOG", withExtension: "md"),
           let text = try? String(contentsOf: url, encoding: .utf8) {
            releases = AppChangelog.parse(text)
        } else {
            releases = nil
        }
    }

    var body: some View {
        Group {
            if let releases, !releases.isEmpty {
                List {
                    ForEach(releases) { release in
                        Section {
                            if let note = release.note {
                                Text(note).font(.footnote).foregroundStyle(.secondary)
                            }
                            ForEach(release.groups) { group in
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(group.title).font(.subheadline.weight(.semibold))
                                    ForEach(Array(group.items.enumerated()), id: \.offset) { _, item in
                                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                                            Text("•").foregroundStyle(.secondary)
                                            Text(Self.inline(item)).font(.callout).fixedSize(horizontal: false, vertical: true)
                                        }
                                    }
                                }
                                .padding(.vertical, 2)
                            }
                        } header: {
                            ReleaseHeader(release: release)
                        }
                        .accessibilityIdentifier("changelog.release.\(release.version)")
                    }
                }
                .accessibilityIdentifier("changelog.list")
            } else {
                ContentUnavailableView("无法读取更新日志", systemImage: "doc.questionmark",
                                       description: Text("此安装包未包含 App 更新日志。"))
            }
        }
        .navigationTitle("App 更新日志")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("changelog.screen")
    }

    private static func inline(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }

    private struct ReleaseHeader: View {
        let release: AppChangelogRelease

        var body: some View {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(release.isUnreleased ? "未发布" : release.version).monospacedDigit()
                    if !release.isUnreleased, release.version == AppInfo.version {
                        Text("当前版本").font(.caption2.weight(.semibold)).foregroundStyle(.tint)
                            .accessibilityIdentifier("changelog.current")
                    }
                    Spacer()
                    if let date = release.date { Text(date).monospacedDigit() }
                }
                if let title = release.title { Text(title).textCase(nil) }
            }
        }
    }
}

private struct AboutLicensesView: View {
    private struct Component: Identifiable {
        let name: String
        let license: String
        let url: URL
        var id: String { name }
    }
    private let components: [Component] = [
        .init(name: "Swift OpenAPI Runtime", license: "Apache License 2.0", url: URL(string: "https://github.com/apple/swift-openapi-runtime")!),
        .init(name: "Swift OpenAPI URLSession", license: "Apache License 2.0", url: URL(string: "https://github.com/apple/swift-openapi-urlsession")!),
        .init(name: "Swift HTTP Types", license: "Apache License 2.0", url: URL(string: "https://github.com/apple/swift-http-types")!)
    ]

    var body: some View {
        List {
            Section {
                Link(destination: AppInfo.licenseURL) {
                    Label {
                        LabeledContent("TelePilot", value: "MIT License")
                    } icon: { Image(systemName: "checkmark.seal") }
                }
            } header: { Text("本项目") } footer: {
                Text("TelePilot 以 MIT 许可证开源，可自由使用、修改与分发，但需保留版权与许可声明。")
            }
            Section {
                ForEach(components) { component in
                    Link(destination: component.url) {
                        Label {
                            LabeledContent(component.name, value: component.license)
                        } icon: { Image(systemName: "shippingbox") }
                    }
                }
            } header: { Text("第三方组件") } footer: {
                Text("App 通过 Swift Package Manager 使用以上 Apple 开源组件。点按可查看完整许可证文本。")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("开源许可")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct AboutPrivacyView: View {
    var body: some View {
        List {
            Section {
                Label("不追踪、不含广告或第三方统计", systemImage: "eye.slash")
                Label("数据只在本机与你连接的服务器之间传输", systemImage: "arrow.left.arrow.right")
                Label("密码不保存在 App 中；登录会话存放在系统钥匙串", systemImage: "key")
                Label("开启推送后，设备令牌只登记到你的服务器", systemImage: "bell.badge")
                Label("界面锁使用 Face ID／设备密码，生物特征不离开设备", systemImage: "faceid")
            } footer: {
                Text("TelePilot 是自托管工具：账号、日志与配置都保存在你自己的服务器上，开发者无法访问。服务器端的数据保留与备份由你自行管理。")
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("隐私说明")
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - 服务器更新

/// 独立的“检查服务器更新”入口：显示最近一次自动检查是否发现新版本，点按打开更新面板。
struct ServerUpdateButton: View {
    @Environment(AppModel.self) private var model
    @State private var presented = false
    @State private var hasUpdate = false

    var body: some View {
        Button { presented = true } label: {
            Label {
                HStack {
                    Text("检查服务器更新").foregroundStyle(Color.primary)
                    Spacer()
                    if hasUpdate {
                        Text("有新版本").font(.caption.weight(.semibold)).foregroundStyle(.tint)
                    }
                }
            } icon: { Image(systemName: "arrow.down.circle") }
        }
        .frame(minHeight: 44)
        .accessibilityIdentifier("update.open")
        .task { await loadBadge() }
        .sheet(isPresented: $presented, onDismiss: { Task { await loadBadge() } }) {
            ServerUpdateSheet()
        }
    }

    private func loadBadge() async {
        guard let status = try? await model.request(path: "/api/system/update-status") else { return }
        hasUpdate = status["last_check"]?["has_update"]?.boolValue == true
    }
}

/// 中等高度的更新面板：当前版本、最新版本、变更摘要与带确认的更新操作。
/// 写操作（应用更新）只在用户确认后提交一次，失败或结果未知时不会自动重试。
struct ServerUpdateSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var status: JSONValue?
    @State private var check: JSONValue?
    @State private var checking = false
    @State private var checkError: String?
    @State private var applying = false
    @State private var applyResult: JSONValue?
    @State private var applyError: String?
    @State private var applyUncertain = false
    @State private var confirming = false
    @State private var confirmingNewAttempt = false
    @State private var copiedCommand = false
    // 更新通道（服务器检查与拉取更新的 Git 远端/分支）
    @State private var savedRemote: String?
    @State private var savedBranch: String?
    @State private var selectedRemote = ""
    @State private var selectedBranch = ""
    @State private var remoteOptions: [String] = []
    @State private var branchOptions: [String] = []
    @State private var loadingChannel = false
    @State private var optionsError: String?
    @State private var channelReadError: String?
    @State private var savingChannel = false
    @State private var channelError: String?
    @State private var channelUncertain = false
    @State private var confirmingChannel = false
    // 自动检查
    @State private var savingAutoCheck = false
    @State private var autoCheckError: String?

    private var receiptKey: String {
        let testing = ProcessInfo.processInfo.arguments.contains("--ui-testing") ? ".fixture.\(ProcessInfo.processInfo.processIdentifier)" : ""
        return "telepilot.serverUpdate\(testing).\(model.serverURL?.absoluteString ?? "").\(model.user?.id ?? 0)"
    }

    private var hasUpdate: Bool { check?["has_update"]?.boolValue == true }
    private var canCheck: Bool { check?["can_check"]?.boolValue != false }
    private var checkFailure: String? { text(check?["error"]) ?? checkError }
    private var manualCommand: String? { text(applyResult?["manual_command"]) ?? text(check?["manual_command"]) }
    private var canApply: Bool { check?["can_apply"]?.boolValue == true && text(check?["manual_command"]) == nil }
    private var target: String? {
        guard let remote = text(check?["remote"]), let branch = text(check?["branch"]) else { return nil }
        return "\(remote)/\(branch)"
    }
    private var applySubmitted: Bool { applyResult?["success"]?.boolValue == true }

    /// 实际生效的通道：优先用检查结果里服务器解析出的远端/分支，其次是已保存的设置。
    private var effectiveRemote: String? { text(check?["remote"]) ?? savedRemote }
    private var effectiveBranch: String? { text(check?["branch"]) ?? savedBranch }
    private var channelLoaded: Bool { savedBranch != nil }
    private var channelChanged: Bool {
        channelLoaded && !selectedBranch.isEmpty && !selectedRemote.isEmpty
            && (selectedBranch != effectiveBranch || selectedRemote != effectiveRemote)
    }
    private var branchChoices: [String] {
        var seen = Set<String>()
        return ([effectiveBranch, selectedBranch.isEmpty ? nil : selectedBranch].compactMap { $0 } + branchOptions)
            .filter { seen.insert($0).inserted }
    }
    private var remoteChoices: [String] {
        var seen = Set<String>()
        return ([effectiveRemote, selectedRemote.isEmpty ? nil : selectedRemote].compactMap { $0 } + remoteOptions)
            .filter { seen.insert($0).inserted }
    }
    private var autoCheckEnabled: Bool { status?["enabled"]?.boolValue ?? true }
    private var autoCheckInterval: Int { status?["interval_minutes"]?.intValue ?? 1440 }
    private static let intervalPresets: [(minutes: Int, title: String)] = [
        (360, "每 6 小时"), (720, "每 12 小时"), (1440, "每天"), (4320, "每 3 天"), (10080, "每周")
    ]

    var body: some View {
        NavigationStack {
            List {
                statusSection
                versionSection
                channelSection
                notesSection
                planSection
                actionSection
                resultSection
                autoCheckSection
            }
            .symbolRenderingMode(.hierarchical)
            .navigationTitle("服务器更新")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }.disabled(applying).accessibilityIdentifier("update.done")
                }
            }
            .refreshable { await runCheck() }
            .onChange(of: selectedRemote) { old, new in
                guard !old.isEmpty, old != new else { return }
                Task { await loadTargetOptions(remote: new) }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .interactiveDismissDisabled(applying)
        .task {
            restoreReceipt()
            await loadStatus()
            await loadChannel()
            await runCheck()
            syncSelectionToEffective()
        }
        .task { await loadTargetOptions(remote: nil) }
        .accessibilityIdentifier("update.sheet")
    }

    // MARK: Sections

    @ViewBuilder private var statusSection: some View {
        Section {
            if checking && check == nil && checkError == nil {
                HStack(spacing: 10) { ProgressView(); Text("正在向服务器检查更新…").foregroundStyle(.secondary) }
            } else if let failure = checkFailure {
                NoticeView(title: "检查更新失败", message: failure)
            } else if check != nil && !canCheck {
                NoticeView(title: "无法自动检查",
                           message: text(check?["plan_detail"]) ?? "当前服务器运行环境无法在进程内比对远端版本，请在服务器上手动更新。",
                           symbol: "questionmark.circle")
            } else if hasUpdate {
                Label {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("发现新版本").font(.headline)
                        if let label = text(check?["plan_label"]) { Text(label).font(.subheadline).foregroundStyle(.secondary) }
                    }
                } icon: { Image(systemName: "arrow.down.circle.fill").foregroundStyle(.tint) }
                .accessibilityIdentifier("update.available")
            } else if check != nil {
                Label {
                    Text("服务器已是最新版本").font(.headline)
                } icon: { Image(systemName: "checkmark.seal.fill").foregroundStyle(.green) }
                .accessibilityIdentifier("update.upToDate")
            }
            if let checkedAt = AppInfo.date(text(status?["last_check"]?["checked_at"])), check == nil {
                LabeledContent("上次自动检查", value: checkedAt.formatted(.relative(presentation: .named)))
                    .font(.footnote)
            }
        }
    }

    @ViewBuilder private var versionSection: some View {
        Section {
            LabeledContent {
                Text(text(check?["current_version"]) ?? model.serverVersion?.version ?? "未知").monospacedDigit()
            } label: { Label("当前版本", systemImage: "shippingbox") }
            if hasUpdate {
                LabeledContent {
                    Text(text(check?["target_version"]) ?? "未知").monospacedDigit().fontWeight(.semibold)
                } label: { Label("最新版本", systemImage: "sparkles") }
                if let ahead = check?["ahead"]?.intValue, ahead > 0 {
                    LabeledContent {
                        Text("\(ahead) 个").monospacedDigit()
                    } label: { Label("待合入提交", systemImage: "arrow.triangle.pull") }
                }
            }
            if let current = AppInfo.shortCommit(text(check?["current_commit"]) ?? model.serverVersion?.revision) {
                LabeledContent {
                    Text(current).font(.footnote.monospaced()).textSelection(.enabled)
                } label: { Label("部署提交", systemImage: "number") }
            }
        } header: { Text("版本") } footer: {
            Text("“部署提交”是服务器当前运行代码的提交编号，反馈问题时可一并提供。")
        }
    }

    @ViewBuilder private var channelSection: some View {
        Section {
            LabeledContent {
                Text(effectiveBranch.map { Self.branchTitle($0, remote: effectiveRemote) } ?? (loadingChannel ? "正在读取…" : "未知"))
                    .multilineTextAlignment(.trailing)
            } label: { Label("当前通道", systemImage: "arrow.triangle.branch") }
                .accessibilityIdentifier("update.channel.current")
            if let version = model.serverVersion {
                LabeledContent {
                    Text(AppInfo.channelLabel(version.channel))
                } label: { Label("正在运行", systemImage: "shippingbox") }
            }
            if channelLoaded {
                if remoteChoices.count > 1 {
                    Picker(selection: $selectedRemote) {
                        ForEach(remoteChoices, id: \.self) { Text($0).tag($0) }
                    } label: { Label("Git 远端", systemImage: "server.rack") }
                    .pickerStyle(.menu)
                    .disabled(savingChannel || applying || channelUncertain)
                }
                Picker(selection: $selectedBranch) {
                    ForEach(branchChoices, id: \.self) { branch in
                        Text(Self.branchTitle(branch, remote: nil)).tag(branch)
                    }
                } label: { Label("选择通道", systemImage: "arrow.left.arrow.right") }
                .pickerStyle(.menu)
                .disabled(savingChannel || applying || channelUncertain)
                .accessibilityIdentifier("update.channel.picker")
                if channelChanged {
                    Button { confirmingChannel = true } label: {
                        Label(savingChannel ? "正在切换…" : "切换到 \(Self.branchTitle(selectedBranch, remote: nil))", systemImage: "checkmark.circle")
                            .frame(minHeight: 44)
                    }
                    .disabled(savingChannel || applying || checking || channelUncertain)
                    .accessibilityIdentifier("update.channel.save")
                    .confirmationDialog(channelConfirmationTitle, isPresented: $confirmingChannel, titleVisibility: .visible) {
                        Button("切换通道") { Task { await saveChannel() } }
                            .accessibilityIdentifier("update.channel.confirm")
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text(channelConfirmationMessage)
                    }
                }
            } else if loadingChannel {
                HStack(spacing: 10) { ProgressView(); Text("正在读取更新通道…").foregroundStyle(.secondary) }
            } else if let channelReadError {
                NoticeView(title: "更新通道读取失败", message: channelReadError)
            }
            if let optionsError {
                Label("未能读取完整的分支列表：\(optionsError)", systemImage: "exclamationmark.triangle")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if let channelError {
                NoticeView(title: channelUncertain ? "通道切换结果待确认" : "通道未切换", message: channelError)
                    .accessibilityIdentifier("update.channel.error")
            }
            if channelUncertain {
                Button { Task { await loadChannel(); syncSelectionToEffective() } } label: {
                    Label("重新读取当前通道", systemImage: "arrow.clockwise")
                }
                .disabled(loadingChannel)
            }
        } header: { Text("更新通道") } footer: {
            Text("更新通道是服务器检查和拉取更新的 Git 分支：main 为正式版，更稳定；Beta 为测试版，新功能更早但可能不稳定。切换只修改更新来源，不会立即更新；“正在运行”表示当前部署的版本类型。")
        }
    }

    @ViewBuilder private var autoCheckSection: some View {
        if status != nil {
            Section {
                Toggle(isOn: Binding(
                    get: { autoCheckEnabled },
                    set: { value in Task { await saveAutoCheck(enabled: value, interval: nil) } }
                )) { Label("自动检查更新", systemImage: "clock.arrow.2.circlepath") }
                .disabled(savingAutoCheck)
                .accessibilityIdentifier("update.autoCheck")
                if autoCheckEnabled {
                    Picker(selection: Binding(
                        get: { autoCheckInterval },
                        set: { value in Task { await saveAutoCheck(enabled: nil, interval: value) } }
                    )) {
                        ForEach(intervalChoices, id: \.minutes) { choice in Text(choice.title).tag(choice.minutes) }
                    } label: { Label("检查频率", systemImage: "timer") }
                    .pickerStyle(.menu)
                    .disabled(savingAutoCheck)
                }
                if let autoCheckError {
                    NoticeView(title: "自动检查设置未保存", message: autoCheckError)
                }
            } header: { Text("自动检查") } footer: {
                Text("服务器按所选频率在后台只读检查当前通道，发现新版本时在 App 与 Web 管理台提示，不会自动安装。")
            }
        }
    }

    private var intervalChoices: [(minutes: Int, title: String)] {
        let current = autoCheckInterval
        if Self.intervalPresets.contains(where: { $0.minutes == current }) { return Self.intervalPresets }
        let hours = (Double(current) / 60).formatted(.number.precision(.fractionLength(0...1)))
        return Self.intervalPresets + [(current, "每 \(hours) 小时（自定义）")]
    }

    private var channelConfirmationTitle: String {
        "切换到 \(Self.branchTitle(selectedBranch, remote: selectedRemote))？"
    }

    private var channelConfirmationMessage: String {
        var parts = ["之后服务器会从 \(selectedRemote)/\(selectedBranch) 检查和拉取更新。这一步只保存更新来源，不会立即更新；需要时再点“应用更新”。"]
        if Self.isStable(selectedBranch) {
            parts.append("从测试版切回正式版时，如果正式版比当前版本旧，可能暂时不会显示可用更新，请等待新的正式版。")
        } else {
            parts.append("非正式版分支可能包含尚未充分验证的改动。")
        }
        return parts.joined(separator: "\n")
    }

    static func isStable(_ branch: String) -> Bool { branch == "main" || branch == "master" }

    /// 分支的中文说明：main 正式版、Beta 测试版，其余按原名显示为开发分支。
    static func branchTitle(_ branch: String, remote: String?) -> String {
        let name = remote.map { "\($0)/\(branch)" } ?? branch
        let lower = branch.lowercased()
        if isStable(branch) { return "\(name) · 正式版" }
        if lower == "beta" { return "\(name) · 测试版" }
        if lower == "dev" || lower == "develop" { return "\(name) · 开发版" }
        return name
    }

    @ViewBuilder private var notesSection: some View {
        let titles = (check?["commit_titles"]?.arrayValue ?? []).compactMap(\.stringValue)
        if hasUpdate {
            Section {
                if titles.isEmpty {
                    Text("服务器没有返回本次变更摘要。").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(titles.prefix(20).enumerated()), id: \.offset) { _, title in
                        Text(title).font(.subheadline).textSelection(.enabled)
                    }
                    if titles.count > 20 {
                        Text("另有 \(titles.count - 20) 条变更").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Link(destination: AppInfo.changelogURL) { Label("查看完整更新日志", systemImage: "list.bullet.clipboard") }
            } header: { Text("更新内容") } footer: {
                Text("列表为待合入的提交标题，正式说明以更新日志为准。")
            }
        }
    }

    @ViewBuilder private var planSection: some View {
        if hasUpdate || (check != nil && !canCheck) {
            let detail = text(check?["plan_detail"])
            let needsBackup = check?["requires_backup"]?.boolValue == true
            let needsMigration = check?["requires_migration"]?.boolValue == true
            let needsFull = check?["requires_full_update"]?.boolValue == true
            if detail != nil || needsBackup || needsMigration || needsFull || manualCommand != nil {
                Section {
                    if let detail, canCheck { Text(detail).font(.subheadline) }
                    if needsBackup { Label("更新前建议先备份配置", systemImage: "externaldrive.badge.exclamationmark") }
                    if needsMigration { Label("包含数据库结构迁移", systemImage: "cylinder.split.1x2") }
                    if needsFull { Label("需要完整更新（重建服务）", systemImage: "arrow.triangle.2.circlepath") }
                    if let command = manualCommand { manualCommandRows(command) }
                } header: { Text("更新方式") }
            }
        }
    }

    @ViewBuilder private var actionSection: some View {
        Section {
            if hasUpdate && canApply && !applySubmitted {
                Button(role: .destructive) { confirming = true } label: {
                    Label(applying ? "正在提交更新…" : "应用更新", systemImage: "arrow.down.to.line.circle")
                        .frame(minHeight: 44)
                }
                .disabled(applying || applyUncertain || checking)
                .accessibilityIdentifier("update.apply")
                .confirmationDialog(target.map { "更新服务器到 \($0) 的最新版本？" } ?? "更新服务器？",
                                    isPresented: $confirming, titleVisibility: .visible) {
                    Button("确认更新", role: .destructive) { Task { await apply() } }
                        .accessibilityIdentifier("update.confirm")
                    Button("取消", role: .cancel) {}
                } message: {
                    Text(confirmationMessage)
                }
            }
            Button { Task { await runCheck() } } label: {
                Label(checking ? "正在检查…" : "重新检查", systemImage: "arrow.clockwise").frame(minHeight: 44)
            }
            .disabled(checking || applying)
            .accessibilityIdentifier("update.recheck")
            NavigationLink { ManagementRunLookupView(updateJob: true) } label: {
                Label("查看升级任务", systemImage: "list.bullet.rectangle")
            }
            if applyUncertain || applySubmitted {
                Button("已核实上次任务，允许下次更新", systemImage: "checkmark.shield") { confirmingNewAttempt = true }
                    .disabled(applying || checking)
                    .accessibilityIdentifier("update.allowNext")
                    .confirmationDialog("已核实上次更新任务已经结束？", isPresented: $confirmingNewAttempt, titleVisibility: .visible) {
                        Button("已核实，允许下次更新", role: .destructive) {
                            UserDefaults.standard.removeObject(forKey: receiptKey)
                            applyUncertain = false
                            applyResult = nil
                            applyError = nil
                        }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("请先在升级任务或服务器上确认上次更新没有继续执行。这里只解除重复提交保护，不会立刻开始更新。")
                    }
            }
        } footer: {
            if hasUpdate && !canApply && check != nil {
                Text("当前运行环境不能从 App 直接应用更新，请按上方命令在服务器上执行。")
            } else {
                Text("更新期间服务会短暂重启，App 可能暂时连不上。更新请求只在你确认后提交一次，不会自动重试。")
            }
        }
    }

    @ViewBuilder private var resultSection: some View {
        if applying || applyError != nil || applyResult != nil {
            Section {
                if applying {
                    HStack(spacing: 10) { ProgressView(); Text("正在提交更新请求…").foregroundStyle(.secondary) }
                }
                if let applyError {
                    NoticeView(title: applyUncertain ? "更新结果待确认" : "更新未开始", message: applyError)
                        .accessibilityIdentifier("update.applyError")
                }
                if let result = applyResult {
                    if result["success"]?.boolValue == true {
                        NoticeView(title: text(result["plan_label"]) ?? "更新已提交",
                                   message: text(result["plan_detail"]) ?? text(result["summary"]),
                                   symbol: "checkmark.circle")
                            .accessibilityIdentifier("update.applied")
                        if let jobID = text(result["job_id"]) {
                            NavigationLink { ManagementJobView(id: jobID, updateJob: true) } label: {
                                Label("查看升级进度", systemImage: "chart.bar.doc.horizontal")
                            }
                            .accessibilityIdentifier("update.job")
                        }
                        if let commit = AppInfo.shortCommit(text(result["new_commit"])) {
                            LabeledContent("新提交", value: commit).font(.footnote.monospaced())
                        }
                    } else {
                        NoticeView(title: text(result["plan_label"]) ?? "更新未执行",
                                   message: text(result["error"]) ?? text(result["plan_detail"]))
                    }
                }
            } header: { Text("更新结果") }
        }
    }

    @ViewBuilder private func manualCommandRows(_ command: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("在服务器上执行").font(.caption).foregroundStyle(.secondary)
            Text(command).font(.footnote.monospaced()).textSelection(.enabled)
        }
        Button {
            UIPasteboard.general.string = command
            copiedCommand = true
        } label: {
            Label(copiedCommand ? "已复制命令" : "复制命令", systemImage: copiedCommand ? "checkmark" : "doc.on.doc")
        }
    }

    private var confirmationMessage: String {
        var parts = ["服务器将拉取并应用更新，服务会短暂中断，正在执行的任务可能受影响。"]
        if check?["requires_backup"]?.boolValue == true { parts.append("本次更新建议先完成配置备份。") }
        if check?["requires_migration"]?.boolValue == true { parts.append("本次更新包含数据库迁移，完成后无法直接回退。") }
        return parts.joined(separator: "\n")
    }

    // MARK: Requests

    private func loadStatus() async {
        status = try? await model.request(path: "/api/system/update-status")
    }

    /// 读取已保存的更新通道与可选分支（只读请求）。
    private func loadChannel() async {
        guard !loadingChannel else { return }
        loadingChannel = true
        defer { loadingChannel = false }
        do {
            let settings = try await model.request(path: "/api/system/settings")
            let target = settings["app_update_target"]
            savedRemote = text(target?["remote"]) ?? "origin"
            savedBranch = text(target?["branch"]) ?? "main"
            channelReadError = nil
            channelUncertain = false
            channelError = nil
            if selectedBranch.isEmpty || selectedRemote.isEmpty { syncSelectionToEffective() }
        } catch {
            channelReadError = error.localizedDescription
        }
    }

    /// 读取可选远端与分支。服务器需要访问远端仓库，可能较慢，因此与检查更新并行进行。
    private func loadTargetOptions(remote: String?) async {
        var path = "/api/system/update-target-options"
        if let remote, let encoded = remote.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "._-"))) {
            path += "?remote=\(encoded)"
        }
        do {
            let options = try await model.request(path: path)
            remoteOptions = (options["remotes"]?.arrayValue ?? []).compactMap(\.stringValue)
            branchOptions = (options["branches"]?.arrayValue ?? []).compactMap(\.stringValue)
            optionsError = text(options["error"])
        } catch {
            optionsError = error.localizedDescription
        }
    }

    /// 选择器默认指向实际生效的通道。
    private func syncSelectionToEffective() {
        guard !savingChannel else { return }
        if let remote = effectiveRemote { selectedRemote = remote }
        if let branch = effectiveBranch { selectedBranch = branch }
    }

    /// 保存更新通道：确认后只提交一次；网络中断时提示核实，不自动重试。
    private func saveChannel() async {
        guard !savingChannel, !applying, !channelUncertain, channelChanged else { return }
        let remote = selectedRemote, branch = selectedBranch
        savingChannel = true
        channelError = nil
        do {
            let payload: JSONValue = .object(["app_update_target": .object(["remote": .string(remote), "branch": .string(branch)])])
            let settings = try await model.request(path: "/api/system/settings", method: "PATCH", body: payload)
            savedRemote = text(settings["app_update_target"]?["remote"]) ?? remote
            savedBranch = text(settings["app_update_target"]?["branch"]) ?? branch
            savingChannel = false
            check = nil
            await runCheck()
            syncSelectionToEffective()
        } catch {
            savingChannel = false
            let uncertain = (error as? APIError)?.isOutcomeUncertain == true
            channelUncertain = uncertain
            channelError = uncertain
                ? "连接中断，无法确认通道是否已切换。请点“重新读取当前通道”核实，App 不会自动重试。"
                : error.localizedDescription
        }
    }

    /// 修改自动检查设置：失败时保留原值并显示原因，不自动重试。
    private func saveAutoCheck(enabled: Bool?, interval: Int?) async {
        guard !savingAutoCheck else { return }
        savingAutoCheck = true
        autoCheckError = nil
        defer { savingAutoCheck = false }
        var payload: [String: JSONValue] = [:]
        if let enabled { payload["enabled"] = .bool(enabled) }
        if let interval { payload["interval_minutes"] = .integer(Int64(interval)) }
        do {
            status = try await model.request(path: "/api/system/update-check", method: "PATCH", body: .object(payload))
        } catch {
            autoCheckError = (error as? APIError)?.isOutcomeUncertain == true
                ? "未能确认设置是否已保存，请关闭面板后重新打开核实。"
                : error.localizedDescription
        }
    }

    /// 只读检查（服务器不拉取代码）；失败时显示原因，由用户决定是否重新检查。
    private func runCheck() async {
        guard !checking, !applying else { return }
        checking = true
        checkError = nil
        defer { checking = false }
        do {
            check = try await model.request(path: "/api/system/check-update", method: "POST")
            checkError = nil
        } catch {
            check = nil
            if let apiError = error as? APIError, case .mutationOutcomeUnknown = apiError {
                checkError = "服务器没有返回可用的检查结果，请稍后重新检查。"
            } else {
                checkError = error.localizedDescription
            }
        }
    }

    /// 应用更新：只提交一次，网络中断或结果未知时锁定按钮，提示用户核实而不是重试。
    private func apply() async {
        guard !applying, !applyUncertain, !applySubmitted, !checking, hasUpdate, canApply else { return }
        // 收据在发请求前持久化；面板关闭、进程被杀或超时都不能绕过重复提交保护。
        restoreReceipt()
        guard !applyUncertain, !applySubmitted else { return }
        applying = true
        applyError = nil
        applyResult = nil
        defer { applying = false }
        var payload: [String: JSONValue] = [:]
        if let remote = text(check?["remote"]) { payload["remote"] = .string(remote) }
        if let branch = text(check?["branch"]) { payload["branch"] = .string(branch) }
        persistReceipt(["pending": true])
        do {
            applyResult = try await model.request(path: "/api/system/pull-update", method: "POST", body: .object(payload))
            if applySubmitted, let applyResult { persistReceipt(["result": applyResult]) }
            else { UserDefaults.standard.removeObject(forKey: receiptKey) }
        } catch {
            let uncertain = (error as? APIError)?.isOutcomeUncertain == true
            applyUncertain = uncertain
            if !uncertain { UserDefaults.standard.removeObject(forKey: receiptKey) }
            applyError = uncertain
                ? "连接中断，无法确认服务器是否已开始更新。更新时服务重启也会导致这种情况。请稍候在“查看升级任务”或重新检查中核实，不要重复提交。"
                : error.localizedDescription
        }
    }

    private func persistReceipt(_ receipt: JSONValue) {
        if let data = try? receipt.data() { UserDefaults.standard.set(data, forKey: receiptKey) }
    }

    private func restoreReceipt() {
        guard let data = UserDefaults.standard.data(forKey: receiptKey), let receipt = try? JSONValue(data: data) else { return }
        if let result = receipt["result"], result["success"]?.boolValue == true { applyResult = result }
        else if receipt["pending"]?.boolValue == true {
            applyUncertain = true
            applyError = "上次更新请求的结果尚未确认。请先查看升级任务或核实服务器状态，不要重复提交。"
        }
    }

    private func text(_ value: JSONValue?) -> String? {
        guard let string = value?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !string.isEmpty else { return nil }
        return string
    }
}
