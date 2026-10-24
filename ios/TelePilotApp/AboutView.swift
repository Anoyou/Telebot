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
                VStack(spacing: 8) {
                    Image("BrandMark").resizable().scaledToFit().frame(width: 64, height: 64)
                        .accessibilityHidden(true)
                    Text("TelePilot").font(.title2.weight(.bold))
                    Text("个人 Telegram 自动化服务器的原生管理客户端")
                        .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .accessibilityElement(children: .combine)
            }

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
                Link(destination: AppInfo.changelogURL) { Label("更新日志", systemImage: "list.bullet.clipboard") }
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
    @State private var copiedCommand = false

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

    var body: some View {
        NavigationStack {
            List {
                statusSection
                versionSection
                notesSection
                planSection
                actionSection
                resultSection
            }
            .symbolRenderingMode(.hierarchical)
            .navigationTitle("服务器更新")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }.accessibilityIdentifier("update.done")
                }
            }
            .refreshable { await runCheck() }
            .confirmationDialog(target.map { "更新服务器到 \($0) 的最新版本？" } ?? "更新服务器？",
                                isPresented: $confirming, titleVisibility: .visible) {
                Button("确认更新", role: .destructive) { Task { await apply() } }
                    .accessibilityIdentifier("update.confirm")
                Button("取消", role: .cancel) {}
            } message: {
                Text(confirmationMessage)
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .task {
            await loadStatus()
            await runCheck()
        }
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
            if let target {
                LabeledContent {
                    Text(target).font(.body.monospaced())
                } label: { Label("更新来源", systemImage: "arrow.triangle.branch") }
            }
            if let current = AppInfo.shortCommit(text(check?["current_commit"]) ?? model.serverVersion?.revision) {
                LabeledContent {
                    Text(current).font(.footnote.monospaced()).textSelection(.enabled)
                } label: { Label("部署提交", systemImage: "number") }
            }
        } header: { Text("版本") } footer: {
            Text("“更新来源”是服务器拉取代码的远端与分支，可在 管理 › 系统设置 › 服务器设置 中修改。")
        }
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
            }
            Button { Task { await runCheck() } } label: {
                Label(checking ? "正在检查…" : "重新检查", systemImage: "arrow.clockwise").frame(minHeight: 44)
            }
            .disabled(checking || applying)
            .accessibilityIdentifier("update.recheck")
            NavigationLink { ManagementRunLookupView(updateJob: true) } label: {
                Label("查看升级任务", systemImage: "list.bullet.rectangle")
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
        guard !applying, !applyUncertain else { return }
        applying = true
        applyError = nil
        applyResult = nil
        defer { applying = false }
        var payload: [String: JSONValue] = [:]
        if let remote = text(check?["remote"]) { payload["remote"] = .string(remote) }
        if let branch = text(check?["branch"]) { payload["branch"] = .string(branch) }
        do {
            applyResult = try await model.request(path: "/api/system/pull-update", method: "POST", body: .object(payload))
        } catch {
            let uncertain = (error as? APIError)?.isOutcomeUncertain == true
            applyUncertain = uncertain
            applyError = uncertain
                ? "连接中断，无法确认服务器是否已开始更新。更新时服务重启也会导致这种情况。请稍候在“查看升级任务”或重新检查中核实，不要重复提交。"
                : error.localizedDescription
        }
    }

    private func text(_ value: JSONValue?) -> String? {
        guard let string = value?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !string.isEmpty else { return nil }
        return string
    }
}
