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
                    }
                }
                .accessibilityIdentifier("changelog.list")
            } else {
                ContentUnavailableView("无法读取更新日志", systemImage: "doc.questionmark",
                                       description: Text("此安装包未包含 App 更新日志。"))
                    .accessibilityIdentifier("changelog.unavailable")
            }
        }
        // 不在外层 Group 上设 accessibilityIdentifier：它会覆盖列表与“当前版本”标记各自的标识。
        .navigationTitle("App 更新日志")
        .navigationBarTitleDisplayMode(.inline)
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
