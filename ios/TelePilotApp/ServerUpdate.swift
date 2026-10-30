import SwiftUI
import UIKit
import TelePilotCore

// MARK: - 文案与分类

/// 服务器更新相关的中文文案：运行方式、需要的操作、组件、失败原因。
/// 判断规则与 Web `UpdateDialog.tsx` 保持一致，不在客户端推测结论。
enum ServerUpdateText {
    static func runtimeMode(_ mode: String?) -> String? {
        switch mode {
        case "local_source": "源码直接运行"
        case "prod_container_with_updater": "容器部署（内置更新器）"
        case "prod_container_manual": "容器部署（需在服务器手动更新）"
        case "unsupported": "不支持在线更新的环境"
        case .some(let other) where !other.isEmpty: other
        default: nil
        }
    }

    /// “需要的操作”：说明这次更新要动哪些部分。
    static func action(_ action: String?) -> String {
        switch action {
        case "docs_only": "仅文档变化，不影响运行"
        case "frontend": "只更新前端（Web 管理台）"
        case "backend": "更新后端并重启后端服务"
        case "mixed": "前端、后端或更新器都有变化，分别增量更新"
        case "updater": "更新在线更新器本身"
        case "full_update": "完整更新（重建全部服务，耗时较长）"
        case "manual": "需要在服务器上手动执行更新命令"
        case "unsupported": "当前运行环境不支持在线更新"
        case "restart": "代码已就绪，重启服务后生效"
        default: "无需操作"
        }
    }

    /// 主按钮文案，与 Web 的 `getPrimaryActionLabel` 一致。
    static func primaryLabel(action: String?, runtime: String?, fileSync: [String], rebuild: [String]) -> String {
        let local = runtime == "local_source"
        switch action {
        case "restart": return "重启使更新生效"
        case "backend":
            if local { return "拉取并重启使更新生效" }
            if fileSync.contains("web") && !rebuild.contains("web") { return "同步文件并重启后端" }
            return "拉取镜像并重启后端"
        case "frontend": return local ? "拉取并重启使更新生效" : "拉取并切换前端"
        case "full_update": return local ? "拉取并重启使更新生效" : "执行完整更新"
        case "mixed":
            if local { return "拉取并重启使更新生效" }
            return !fileSync.isEmpty && !rebuild.isEmpty ? "执行混合增量更新" : "执行增量更新"
        case "updater": return "更新在线更新器"
        case "docs_only": return "应用文档更新"
        default: return "应用更新"
        }
    }

    static func component(_ name: String) -> String {
        switch name {
        case "backend": "后端"
        case "frontend": "前端"
        case "updater": "更新器"
        case "docs_only": "仅文档"
        case "full_update": "完整更新"
        case "none": "无"
        default: name
        }
    }

    static func service(_ name: String) -> String {
        switch name {
        case "web": "后端（web）"
        case "frontend": "前端（frontend）"
        case "updater": "更新器（updater）"
        case "postgres": "数据库（postgres）"
        case "redis": "缓存（redis）"
        default: name
        }
    }

    static func jobStatus(_ status: String?) -> String {
        switch status {
        case "queued": "排队中"
        case "running": "执行中"
        case "succeeded": "已完成"
        case "failed": "失败"
        case "reconnecting": "等待服务恢复"
        case .some(let other) where !other.isEmpty: other
        default: "执行中"
        }
    }

    /// 把服务器返回的失败文本归类成可读的原因与处理建议；无法归类时返回 nil，界面只显示原文。
    static func failureHint(_ text: String) -> (title: String, advice: String)? {
        let lower = text.lowercased()
        if text.contains("未提交改动") || text.contains("工作区存在") || lower.contains("dirty") || lower.contains("would be overwritten") {
            return ("服务器仓库有未提交的改动，已拒绝更新",
                    "更新器在拉取前发现服务器仓库里有手工修改、未跟踪文件，或正处于合并／变基中。为避免覆盖这些改动，本次没有更新。请在服务器的 TelePilot 目录执行 git status 查看具体文件，提交、git stash 暂存或删除后，再回到这里重新检查并更新。")
        }
        if text.contains("index.lock") {
            return ("Git 仓库被锁定", "服务器仓库存在 .git/index.lock，可能有其他 Git 操作未结束。确认没有 Git 进程在运行后删除该文件，再重新检查。")
        }
        if text.contains("已有更新任务") || lower.contains("handoff") {
            return ("已有更新任务在执行", "请等待当前更新任务结束（可在下方进度中查看），完成后再重新检查。")
        }
        if text.contains("git fetch 失败") || text.contains("读取远程 commit 失败") || lower.contains("could not resolve host") || lower.contains("connection timed out") {
            return ("无法访问 Git 远端", "服务器拉取远端代码失败。请检查服务器网络、代理设置或仓库访问权限后重新检查。")
        }
        if lower.contains("not possible to fast-forward") || text.contains("无法快进") || lower.contains("diverging") {
            return ("本地提交与远端分叉", "服务器仓库有远端不存在的提交，无法直接快进更新。请在服务器上处理分叉（例如 git reset 到远端分支）后再更新。")
        }
        if text.contains("updater 不可用") || text.contains("内部 updater") {
            return ("内部更新器不可用", "服务器的 updater 容器没有运行或无法访问。请在服务器检查 updater 容器状态，或按“更新方式”中的命令手动更新。")
        }
        if text.contains("不是 Git 工作树") || text.contains("工作树不可用") {
            return ("服务器目录不是 Git 仓库", "当前部署目录不是 Git 工作树，无法在线更新，请按部署文档手动更新。")
        }
        if lower.contains("timeout") || text.contains("超时") {
            return ("执行超时", "某一步执行时间过长被中止。请查看下方日志确认停在哪一步，再决定是否重新检查并更新。")
        }
        if lower.contains("no space left") || text.contains("磁盘空间") {
            return ("服务器磁盘空间不足", "请清理服务器磁盘（例如旧镜像：docker image prune）后再更新。")
        }
        return nil
    }
}

// MARK: - 入口按钮

/// 唯一的“检查服务器更新”入口（App 设置 › 软件更新）：显示服务器最近一次检查（自动或手动）的结论，点按打开更新面板。
struct ServerUpdateButton: View {
    @Environment(AppModel.self) private var model
    @State private var presented = false

    var body: some View {
        Button { presented = true } label: {
            Label {
                HStack {
                    Text("检查服务器更新").foregroundStyle(Color.primary)
                    Spacer()
                    if model.serverUpdateAvailable {
                        Text(model.serverUpdateTargetVersion.map { "新版本 \($0)" } ?? "有新版本")
                            .font(.caption.weight(.semibold)).foregroundStyle(.tint)
                    }
                }
            } icon: { Image(systemName: "arrow.down.circle") }
        }
        .frame(minHeight: 44)
        .accessibilityIdentifier("update.open")
        .task { await model.refreshServerUpdateStatus() }
        .sheet(isPresented: $presented, onDismiss: { Task { await model.refreshServerUpdateStatus() } }) {
            ServerUpdateSheet()
        }
    }
}

// MARK: - 更新面板

/// 服务器更新面板：与 Web 使用同一组接口（check-update / pull-update / update-jobs / update-status）。
/// - 检查不带参数提交，服务器按已保存的通道检查，并把结论写回“最近一次检查”（角标同源）。
/// - 应用更新只在确认后提交一次；有更新任务时在面板内轮询进度与日志（只读请求），失败时显示可读原因。
/// - 网络中断或结果未知时锁定，提示核实，不自动重试写操作。
struct ServerUpdateSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var status: JSONValue?
    @State private var check: JSONValue?
    @State private var checking = false
    @State private var checkError: String?
    // 应用更新
    @State private var applying = false
    @State private var applyResult: JSONValue?
    @State private var applyError: String?
    @State private var applyUncertain = false
    @State private var confirming = false
    @State private var confirmingNewAttempt = false
    @State private var copiedCommand = false
    @State private var copiedError = false
    // 更新任务进度
    @State private var jobID: String?
    @State private var job: JSONValue?
    @State private var jobReadFailures = 0
    @State private var jobReadError: String?
    @State private var showAllLogs = false
    @State private var showFiles = false
    // 更新通道
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

    // MARK: 检查结果的派生值

    private var hasUpdate: Bool { check?["has_update"]?.boolValue == true }
    private var canCheck: Bool { check?["can_check"]?.boolValue != false }
    private var checkFailure: String? { text(check?["error"]) ?? checkError }
    private var actionRequired: String? { text(check?["action_required"]) }
    private var runtimeMode: String? { text(check?["runtime_mode"]) }
    private var manualCommand: String? { text(applyResult?["manual_command"]) ?? text(check?["manual_command"]) }
    private var canApply: Bool { check?["can_apply"]?.boolValue == true && text(check?["manual_command"]) == nil }
    private var isRestartOnly: Bool { actionRequired == "restart" }
    private var target: String? {
        guard let remote = text(check?["remote"]), let branch = text(check?["branch"]) else { return nil }
        return "\(remote)/\(branch)"
    }
    private var applySubmitted: Bool { applyResult?["success"]?.boolValue == true }
    private var jobStatus: String? { text(job?["status"]) }
    private var jobRunning: Bool { jobID != nil && jobStatus != "succeeded" && jobStatus != "failed" }
    private var jobFailed: Bool { jobStatus == "failed" }
    private var jobSucceeded: Bool { jobStatus == "succeeded" }
    private var busy: Bool { applying || jobRunning }

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
            ScrollViewReader { proxy in
            List {
                statusSection
                if jobID != nil || applying || applyError != nil || applyResult != nil { progressSection }
                versionSection
                changesSection
                planSection
                actionSection
                channelSection
                autoCheckSection
            }
            // 确认更新时列表通常停在下方“操作”分区，而进度分区在顶部且 List 懒加载：
            // 任务创建或提交失败后自动滚回进度分区，让用户（和 UI 测试）立刻看到进度或原因。
            .onChange(of: jobID) { _, id in
                guard id != nil else { return }
                withAnimation { proxy.scrollTo(Self.progressAnchor, anchor: .top) }
            }
            .onChange(of: applyError) { _, error in
                guard error != nil else { return }
                withAnimation { proxy.scrollTo(Self.progressAnchor, anchor: .top) }
            }
            }
            .symbolRenderingMode(.hierarchical)
            .navigationTitle("服务器更新")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }.disabled(applying).accessibilityIdentifier("update.done")
                }
            }
            .refreshable { if !busy { await runCheck() } }
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
            if jobID == nil { await runCheck() }
            syncSelectionToEffective()
        }
        .task { await loadTargetOptions(remote: nil) }
        .task(id: JobPollKey(id: jobID, active: scenePhase == .active)) { await pollJob() }
        .accessibilityIdentifier("update.sheet")
    }

    private struct JobPollKey: Hashable { let id: String?; let active: Bool }

    // MARK: 结论

    @ViewBuilder private var statusSection: some View {
        Section {
            if checking && check == nil && checkError == nil {
                HStack(spacing: 10) { ProgressView(); Text("正在向服务器检查更新…").foregroundStyle(.secondary) }
            } else if let failure = checkFailure {
                NoticeView(title: "检查更新失败", message: failure)
                    .accessibilityIdentifier("update.checkError")
                if let hint = ServerUpdateText.failureHint(failure) {
                    NoticeView(title: hint.title, message: hint.advice, symbol: "lightbulb")
                }
            } else if check != nil && !canCheck {
                NoticeView(title: "无法自动检查",
                           message: text(check?["plan_detail"]) ?? "当前服务器运行环境无法在进程内比对远端版本，请在服务器上手动更新。",
                           symbol: "questionmark.circle")
            } else if hasUpdate {
                Label {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("发现新版本").font(.headline)
                        Text(versionArrow).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                    }
                } icon: { Image(systemName: "arrow.down.circle.fill").foregroundStyle(.tint) }
                .accessibilityIdentifier("update.available")
            } else if check != nil {
                Label {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("服务器已是最新版本").font(.headline)
                        if let target { Text("与 \(target) 一致").font(.subheadline).foregroundStyle(.secondary) }
                    }
                } icon: { Image(systemName: "checkmark.seal.fill").foregroundStyle(.green) }
                .accessibilityIdentifier("update.upToDate")
            } else if jobID != nil {
                Label("正在跟踪更新任务", systemImage: "arrow.down.to.line.circle")
            }
            if let last = status?["last_check"], let checkedAt = AppInfo.date(text(last["checked_at"])) {
                LabeledContent("最近一次检查") {
                    Text(lastCheckSummary(last, at: checkedAt)).multilineTextAlignment(.trailing)
                }
                .font(.footnote)
                .accessibilityIdentifier("update.lastCheck")
            }
        } footer: {
            Text("打开面板或下拉时会让服务器重新检查当前通道，结论同时保存为“最近一次检查”，App 角标与 Web 管理台使用同一份结果。")
        }
    }

    private var versionArrow: String {
        let current = text(check?["current_version"]) ?? model.serverVersion?.version ?? "未知"
        let target = text(check?["target_version"]) ?? "未知"
        return "\(current) → \(target)"
    }

    private func lastCheckSummary(_ last: JSONValue, at date: Date) -> String {
        let when = date.formatted(.relative(presentation: .named))
        if text(last["error"]) != nil { return "\(when) · 检查失败" }
        if last["stale"]?.boolValue == true { return "\(when) · 已部署，结论已过期" }
        if last["can_check"]?.boolValue == false { return "\(when) · 无法自动检查" }
        if last["has_update"]?.boolValue == true {
            return "\(when) · 有新版本" + (text(last["target_version"]).map { " \($0)" } ?? "")
        }
        return "\(when) · 已是最新"
    }

    // MARK: 版本与提交

    @ViewBuilder private var versionSection: some View {
        Section {
            LabeledContent {
                Text(text(check?["current_version"]) ?? model.serverVersion?.version ?? "未知").monospacedDigit().textSelection(.enabled)
            } label: { Label("当前版本", systemImage: "shippingbox") }
            if hasUpdate {
                LabeledContent {
                    Text(text(check?["target_version"]) ?? "未知").monospacedDigit().fontWeight(.semibold).textSelection(.enabled)
                } label: { Label("目标版本", systemImage: "sparkles") }
            }
            if let current = AppInfo.shortCommit(text(check?["current_commit"]) ?? model.serverVersion?.revision) {
                LabeledContent {
                    Text(current).font(.footnote.monospaced()).textSelection(.enabled)
                } label: { Label("当前提交", systemImage: "number") }
            }
            if hasUpdate, let remote = AppInfo.shortCommit(text(check?["remote_commit"])) {
                LabeledContent {
                    Text(remote).font(.footnote.monospaced()).textSelection(.enabled)
                } label: { Label("目标提交", systemImage: "number.circle") }
            }
            if hasUpdate, let ahead = check?["ahead"]?.intValue, ahead > 0 {
                LabeledContent {
                    Text("\(ahead) 个").monospacedDigit()
                } label: { Label("待合入提交", systemImage: "arrow.triangle.pull") }
            }
            if let target {
                LabeledContent {
                    Text(target).font(.footnote.monospaced())
                } label: { Label("目标分支", systemImage: "arrow.triangle.branch") }
            }
            if let mode = ServerUpdateText.runtimeMode(runtimeMode) {
                LabeledContent {
                    Text(mode).multilineTextAlignment(.trailing)
                } label: { Label("运行方式", systemImage: "server.rack") }
            }
        } header: { Text("版本") } footer: {
            Text("提交编号是服务器代码的 Git 提交，反馈问题时可一并提供。")
        }
    }

    // MARK: 变更标题

    @ViewBuilder private var changesSection: some View {
        let titles = (check?["commit_titles"]?.arrayValue ?? []).compactMap(\.stringValue)
        let files = (check?["changed_files"]?.arrayValue ?? []).compactMap(\.stringValue)
        if hasUpdate {
            Section {
                if titles.isEmpty {
                    Text("服务器没有返回本次变更标题。").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(titles.prefix(30).enumerated()), id: \.offset) { index, title in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("\(index + 1).").font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
                            Text(title).font(.subheadline).textSelection(.enabled)
                        }
                    }
                    if titles.count > 30 {
                        Text("另有 \(titles.count - 30) 条变更").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if !files.isEmpty {
                    DisclosureGroup(isExpanded: $showFiles) {
                        ForEach(files.prefix(40), id: \.self) { file in
                            Text(file).font(.caption.monospaced()).textSelection(.enabled)
                        }
                        if files.count > 40 { Text("另有 \(files.count - 40) 个文件").font(.caption).foregroundStyle(.secondary) }
                    } label: {
                        Label("涉及 \(files.count) 个文件", systemImage: "doc.on.doc")
                    }
                }
                Link(destination: AppInfo.changelogURL) { Label("查看完整更新日志", systemImage: "list.bullet.clipboard") }
            } header: { Text("变更内容") } footer: {
                Text("列表为待合入的提交标题，正式说明以更新日志为准。")
            }
        }
    }

    // MARK: 需要的操作

    @ViewBuilder private var planSection: some View {
        if hasUpdate || (check != nil && !canCheck) {
            let components = (check?["components"]?.arrayValue ?? []).compactMap(\.stringValue).filter { $0 != "none" }
            let fileSync = (check?["file_sync_services"]?.arrayValue ?? []).compactMap(\.stringValue)
            let rebuild = (check?["rebuild_services"]?.arrayValue ?? []).compactMap(\.stringValue)
            let reasons = (check?["reasons"]?.arrayValue ?? []).compactMap(\.stringValue)
            Section {
                if hasUpdate {
                    Label {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(text(check?["plan_label"]) ?? ServerUpdateText.action(actionRequired)).font(.subheadline.weight(.semibold))
                            Text(ServerUpdateText.action(actionRequired)).font(.footnote).foregroundStyle(.secondary)
                        }
                    } icon: { Image(systemName: "wrench.and.screwdriver") }
                    .accessibilityIdentifier("update.plan")
                }
                if let detail = text(check?["plan_detail"]), canCheck { Text(detail).font(.subheadline) }
                if !components.isEmpty {
                    LabeledContent("变更部分", value: components.map(ServerUpdateText.component).joined(separator: "、"))
                }
                if !fileSync.isEmpty {
                    LabeledContent("直接同步文件", value: fileSync.map(ServerUpdateText.service).joined(separator: "、"))
                }
                if !rebuild.isEmpty {
                    LabeledContent("拉取新镜像", value: rebuild.map(ServerUpdateText.service).joined(separator: "、"))
                }
                if check?["requires_backup"]?.boolValue == true { Label("包含数据库迁移，更新前会自动备份", systemImage: "externaldrive.badge.exclamationmark") }
                if check?["requires_migration"]?.boolValue == true { Label("包含数据库结构迁移，完成后无法直接回退", systemImage: "cylinder.split.1x2") }
                if check?["requires_full_update"]?.boolValue == true { Label("需要完整更新流程，耗时更长", systemImage: "arrow.triangle.2.circlepath") }
                ForEach(reasons, id: \.self) { reason in
                    Text("分类依据：\(reason)").font(.footnote).foregroundStyle(.secondary)
                }
                if let command = manualCommand { manualCommandRows(command) }
            } header: { Text("需要的操作") }
        }
    }

    // MARK: 操作

    @ViewBuilder private var actionSection: some View {
        Section {
            if hasUpdate && canApply && !applySubmitted && jobID == nil {
                Button(role: .destructive) { confirming = true } label: {
                    Label(applying ? "正在提交…" : primaryLabel, systemImage: isRestartOnly ? "restart.circle" : "arrow.down.to.line.circle")
                        .frame(minHeight: 44)
                }
                .disabled(applying || applyUncertain || checking)
                .accessibilityIdentifier("update.apply")
                .confirmationDialog(confirmationTitle, isPresented: $confirming, titleVisibility: .visible) {
                    Button(isRestartOnly ? "确认重启" : "确认更新", role: .destructive) { Task { await apply() } }
                        .accessibilityIdentifier("update.confirm")
                    Button("取消", role: .cancel) {}
                } message: {
                    Text(confirmationMessage)
                }
            }
            Button { Task { await runCheck() } } label: {
                Label(checking ? "正在检查…" : "重新检查", systemImage: "arrow.clockwise").frame(minHeight: 44)
            }
            .disabled(checking || busy)
            .accessibilityIdentifier("update.recheck")
            if applyUncertain || (applySubmitted && jobID == nil) {
                Button("已核实上次任务，允许下次更新", systemImage: "checkmark.shield") { confirmingNewAttempt = true }
                    .disabled(applying || checking)
                    .accessibilityIdentifier("update.allowNext")
                    .confirmationDialog("已核实上次更新任务已经结束？", isPresented: $confirmingNewAttempt, titleVisibility: .visible) {
                        Button("已核实，允许下次更新", role: .destructive) { clearReceipt() }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("请先在服务器上确认上次更新没有继续执行。这里只解除重复提交保护，不会立刻开始更新。")
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

    private var primaryLabel: String {
        ServerUpdateText.primaryLabel(
            action: actionRequired, runtime: runtimeMode,
            fileSync: (check?["file_sync_services"]?.arrayValue ?? []).compactMap(\.stringValue),
            rebuild: (check?["rebuild_services"]?.arrayValue ?? []).compactMap(\.stringValue))
    }

    private var confirmationTitle: String {
        if isRestartOnly { return "重启服务器服务？" }
        return target.map { "更新服务器到 \($0) 的最新版本？" } ?? "更新服务器？"
    }

    private var confirmationMessage: String {
        if isRestartOnly { return "服务器会重启以加载已拉取的代码，当前连接和正在执行的请求会中断。" }
        var parts = ["\(versionArrow)。服务器将执行：\(ServerUpdateText.action(actionRequired))。服务会短暂中断，正在执行的任务可能受影响。"]
        if check?["requires_backup"]?.boolValue == true { parts.append("本次更新会先自动备份数据库。") }
        if check?["requires_migration"]?.boolValue == true { parts.append("本次更新包含数据库迁移，完成后无法直接回退。") }
        return parts.joined(separator: "\n")
    }

    // MARK: 进度与结果

    @ViewBuilder private var progressSection: some View {
        Section {
            if applying {
                HStack(spacing: 10) { ProgressView(); Text(isRestartOnly ? "正在提交重启请求…" : "正在创建更新任务…").foregroundStyle(.secondary) }
            }
            if let applyError {
                NoticeView(title: applyUncertain ? "更新结果待确认" : "更新未开始", message: applyError)
                    .accessibilityIdentifier("update.applyError")
                if !applyUncertain, let hint = ServerUpdateText.failureHint(applyError) {
                    NoticeView(title: hint.title, message: hint.advice, symbol: "lightbulb")
                }
            }
            if let result = applyResult, jobID == nil {
                if result["success"]?.boolValue == true {
                    NoticeView(title: text(result["plan_label"]) ?? "更新已执行",
                               message: text(result["summary"]) ?? text(result["plan_detail"]),
                               symbol: "checkmark.circle")
                        .accessibilityIdentifier("update.applied")
                    if let commit = AppInfo.shortCommit(text(result["new_commit"])) {
                        LabeledContent("新提交", value: commit).font(.footnote.monospaced())
                    }
                } else {
                    let failure = text(result["error"]) ?? text(result["plan_detail"]) ?? "服务器没有说明原因。"
                    NoticeView(title: text(result["plan_label"]) ?? "更新未执行", message: failure)
                        .accessibilityIdentifier("update.applyError")
                    if let hint = ServerUpdateText.failureHint(failure) {
                        NoticeView(title: hint.title, message: hint.advice, symbol: "lightbulb")
                    }
                }
            }
            if let jobID { jobRows(jobID) }
        } header: { Text("更新进度") }
        .id(Self.progressAnchor)
    }

    private static let progressAnchor = "update.progressSection"

    @ViewBuilder private func jobRows(_ id: String) -> some View {
        let progress = Double(min(100, max(0, job?["progress"]?.intValue ?? 0)))
        let phase = text(job?["phase"]) ?? (job == nil ? "正在读取任务进度" : ServerUpdateText.jobStatus(jobStatus))
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(phase).font(.subheadline.weight(.semibold)).foregroundStyle(jobFailed ? Color.red : Color.primary)
                Spacer()
                Text("\(Int(progress))%").font(.footnote.monospacedDigit()).foregroundStyle(.secondary)
            }
            ProgressView(value: progress, total: 100)
                .tint(jobFailed ? Color.red : Color.accentColor)
                .accessibilityLabel(phase)
                .accessibilityValue("\(Int(progress))%")
            if let detail = text(job?["detail"]) { Text(detail).font(.footnote).foregroundStyle(.secondary) }
            if let jobReadError, jobRunning {
                Label("更新期间服务暂时不可用，正在第 \(jobReadFailures) 次重新连接；任务仍在服务器后台执行。（\(jobReadError)）", systemImage: "wifi.exclamationmark")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("update.progress")
        LabeledContent("任务", value: "\(id) · \(ServerUpdateText.jobStatus(jobReadError != nil && jobRunning ? "reconnecting" : jobStatus))")
            .font(.footnote)
        if jobSucceeded {
            NoticeView(title: "更新已完成",
                       message: [text(job?["summary"]), AppInfo.shortCommit(text(job?["new_commit"])).map { "已更新到 \($0)" }]
                        .compactMap { $0 }.joined(separator: "\n"),
                       symbol: "checkmark.circle")
                .accessibilityIdentifier("update.applied")
        }
        if jobFailed {
            let failure = text(job?["error"]) ?? "更新任务失败，服务器没有说明原因。"
            if let hint = ServerUpdateText.failureHint(failure + "\n" + logs.suffix(16).joined(separator: "\n")) {
                NoticeView(title: hint.title, message: hint.advice, symbol: "lightbulb")
                    .accessibilityIdentifier("update.failureHint")
            }
            NoticeView(title: "失败原因", message: failure)
                .accessibilityIdentifier("update.jobError")
            Button {
                UIPasteboard.general.string = ([failure] + logs.suffix(40)).joined(separator: "\n")
                copiedError = true
            } label: {
                Label(copiedError ? "已复制错误与日志" : "复制错误与日志", systemImage: copiedError ? "checkmark" : "doc.on.doc")
            }
        }
        let timings = (job?["step_timings"]?.arrayValue ?? [])
        if !timings.isEmpty {
            DisclosureGroup {
                ForEach(Array(timings.enumerated()), id: \.offset) { _, item in
                    LabeledContent(text(item["phase"]) ?? "步骤", value: Self.duration(item["duration_ms"]?.doubleValue ?? 0))
                        .font(.footnote.monospacedDigit())
                }
            } label: { Label("各步耗时", systemImage: "timer") }
        }
        if !logs.isEmpty || jobRunning {
            DisclosureGroup(isExpanded: $showAllLogs) {
                Text(logs.isEmpty ? "等待更新器输出…" : logs.suffix(60).joined(separator: "\n"))
                    .font(.caption2.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Label("日志", systemImage: "text.alignleft")
                    if let last = logs.last, !showAllLogs {
                        Text(last).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(2)
                    }
                }
            }
            .accessibilityIdentifier("update.logs")
        }
        if !jobRunning {
            Button { Task { await finishTrackedJob() } } label: {
                Label("关闭任务并重新检查", systemImage: "arrow.clockwise")
            }
            .disabled(checking)
            .accessibilityIdentifier("update.finishJob")
        }
    }

    private var logs: [String] { (job?["logs"]?.arrayValue ?? []).compactMap(\.stringValue) }

    private static func duration(_ ms: Double) -> String {
        ms >= 1000 ? "\((ms / 1000).formatted(.number.precision(.fractionLength(1)))) 秒" : "\(Int(ms)) 毫秒"
    }

    // MARK: 更新通道

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
                    .disabled(savingChannel || busy || channelUncertain)
                }
                Picker(selection: $selectedBranch) {
                    ForEach(branchChoices, id: \.self) { branch in
                        Text(Self.branchTitle(branch, remote: nil)).tag(branch)
                    }
                } label: { Label("选择通道", systemImage: "arrow.left.arrow.right") }
                .pickerStyle(.menu)
                .disabled(savingChannel || busy || channelUncertain)
                .accessibilityIdentifier("update.channel.picker")
                if channelChanged {
                    Button { confirmingChannel = true } label: {
                        Label(savingChannel ? "正在切换…" : "切换到 \(Self.branchTitle(selectedBranch, remote: nil)) 并检查", systemImage: "checkmark.circle")
                            .frame(minHeight: 44)
                    }
                    .disabled(savingChannel || busy || checking || channelUncertain)
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
            Text("更新通道是服务器检查和拉取更新的 Git 分支：main 为正式版，更稳定；Beta 为测试版，新功能更早但可能不稳定。切换只修改更新来源并重新检查，不会立即更新；“正在运行”表示当前部署的版本类型。")
        }
    }

    private var channelConfirmationTitle: String {
        "切换到 \(Self.branchTitle(selectedBranch, remote: selectedRemote))？"
    }

    private var channelConfirmationMessage: String {
        var parts = ["之后服务器会从 \(selectedRemote)/\(selectedBranch) 检查和拉取更新。这一步只保存更新来源并重新检查，不会立即更新。"]
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

    // MARK: 自动检查

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

    // MARK: 请求

    private func loadStatus() async {
        if let value = try? await model.request(path: "/api/system/update-status") {
            status = value
            model.setServerUpdateStatus(value)
        }
    }

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

    private func syncSelectionToEffective() {
        guard !savingChannel else { return }
        if let remote = effectiveRemote { selectedRemote = remote }
        if let branch = effectiveBranch { selectedBranch = branch }
    }

    private func saveChannel() async {
        guard !savingChannel, !busy, !channelUncertain, channelChanged else { return }
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

    private func saveAutoCheck(enabled: Bool?, interval: Int?) async {
        guard !savingAutoCheck else { return }
        savingAutoCheck = true
        autoCheckError = nil
        defer { savingAutoCheck = false }
        var payload: [String: JSONValue] = [:]
        if let enabled { payload["enabled"] = .bool(enabled) }
        if let interval { payload["interval_minutes"] = .integer(Int64(interval)) }
        do {
            let value = try await model.request(path: "/api/system/update-check", method: "PATCH", body: .object(payload))
            status = value
            model.setServerUpdateStatus(value)
        } catch {
            autoCheckError = (error as? APIError)?.isOutcomeUncertain == true
                ? "未能确认设置是否已保存，请关闭面板后重新打开核实。"
                : error.localizedDescription
        }
    }

    /// 只读检查（服务器不拉取代码）。不带参数提交：服务器按已保存的通道检查，并把结论写回“最近一次检查”。
    private func runCheck() async {
        guard !checking, !applying else { return }
        checking = true
        checkError = nil
        defer { checking = false }
        do {
            let result = try await model.request(path: "/api/system/check-update", method: "POST")
            check = result
            checkError = nil
            // 已有成功提交的收据而服务器确认已是最新，说明上次更新已生效，解除重复提交保护。
            if result["has_update"]?.boolValue == false, result["error"] == nil || result["error"]?.isNull == true,
               applySubmitted, jobID == nil {
                clearReceipt()
            }
        } catch {
            check = nil
            if let apiError = error as? APIError, case .mutationOutcomeUnknown = apiError {
                checkError = "服务器没有返回可用的检查结果，请稍后重新检查。"
            } else {
                checkError = error.localizedDescription
            }
        }
        await loadStatus()
    }

    /// 应用更新（或仅重启）：只提交一次，网络中断或结果未知时锁定按钮，提示用户核实而不是重试。
    private func apply() async {
        guard !applying, !applyUncertain, !applySubmitted, jobID == nil, !checking, hasUpdate, canApply else { return }
        restoreReceipt()
        guard !applyUncertain, !applySubmitted, jobID == nil else { return }
        applying = true
        applyError = nil
        applyResult = nil
        defer { applying = false }
        if isRestartOnly {
            await restart()
            return
        }
        var payload: [String: JSONValue] = [:]
        if let remote = text(check?["remote"]) { payload["remote"] = .string(remote) }
        if let branch = text(check?["branch"]) { payload["branch"] = .string(branch) }
        persistReceipt(["pending": true])
        do {
            let result = try await model.request(path: "/api/system/pull-update", method: "POST", body: .object(payload))
            applyResult = result
            if result["success"]?.boolValue == true {
                if let id = text(result["job_id"]) {
                    jobID = id
                    job = nil
                    persistReceipt(["job_id": .string(id)])
                } else {
                    persistReceipt(["result": result])
                    await model.refresh()
                }
            } else {
                clearReceipt(keepingResult: true)
            }
        } catch {
            let uncertain = (error as? APIError)?.isOutcomeUncertain == true
            applyUncertain = uncertain
            if !uncertain { UserDefaults.standard.removeObject(forKey: receiptKey) }
            applyError = uncertain
                ? "连接中断，无法确认服务器是否已开始更新。更新时服务重启也会导致这种情况。请稍候重新检查核实，不要重复提交。"
                : error.localizedDescription
        }
    }

    private func restart() async {
        persistReceipt(["pending": true])
        do {
            let result = try await model.request(path: "/api/system/restart", method: "POST")
            if result["success"]?.boolValue == true {
                applyResult = ["success": true, "plan_label": "已提交重启", "summary": "服务器正在重启，稍候下拉重新检查确认新版本已生效。"]
                persistReceipt(["result": applyResult ?? .null])
            } else {
                UserDefaults.standard.removeObject(forKey: receiptKey)
                applyError = text(result["error"]) ?? "服务器拒绝了重启请求。"
            }
        } catch {
            let uncertain = (error as? APIError)?.isOutcomeUncertain == true
            applyUncertain = uncertain
            if !uncertain { UserDefaults.standard.removeObject(forKey: receiptKey) }
            applyError = uncertain
                ? "连接中断，重启时这是正常现象。请稍候重新检查确认服务已恢复，不要重复提交。"
                : error.localizedDescription
        }
    }

    /// 轮询更新任务（只读）：读取失败视为服务重启中，继续等待；任务结束后停止并清除重复提交保护。
    private func pollJob() async {
        guard let id = jobID, scenePhase == .active else { return }
        let path = "/api/system/update-jobs/\(ManagementText.pathSegment(id))"
        while !Task.isCancelled, jobID == id {
            do {
                let value = try await model.request(path: path)
                if value["ok"]?.boolValue == false, ["unknown", "unsupported"].contains(text(value["status"]) ?? "") {
                    throw JobReadError(message: text(value["error"]) ?? "暂时无法读取更新任务")
                }
                job = value
                jobReadFailures = 0
                jobReadError = nil
                if ["succeeded", "failed"].contains(text(value["status"]) ?? "") {
                    UserDefaults.standard.removeObject(forKey: receiptKey)
                    if text(value["status"]) == "succeeded" { await model.refresh() }
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                jobReadFailures += 1
                jobReadError = (error as? JobReadError)?.message ?? error.localizedDescription
            }
            let delay: Double = jobReadFailures == 0 ? 2 : min(10, 2 + Double(jobReadFailures) * 2)
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
        }
    }

    private struct JobReadError: Error { let message: String }

    private func finishTrackedJob() async {
        jobID = nil
        job = nil
        jobReadError = nil
        jobReadFailures = 0
        applyResult = nil
        applyError = nil
        applyUncertain = false
        UserDefaults.standard.removeObject(forKey: receiptKey)
        check = nil
        await runCheck()
    }

    private func clearReceipt(keepingResult: Bool = false) {
        UserDefaults.standard.removeObject(forKey: receiptKey)
        applyUncertain = false
        applyError = nil
        if !keepingResult { applyResult = nil }
    }

    private func persistReceipt(_ receipt: JSONValue) {
        if let data = try? receipt.data() { UserDefaults.standard.set(data, forKey: receiptKey) }
    }

    private func restoreReceipt() {
        guard let data = UserDefaults.standard.data(forKey: receiptKey), let receipt = try? JSONValue(data: data) else { return }
        if let id = text(receipt["job_id"]) {
            if jobID != id { jobID = id; job = nil }
        } else if let result = receipt["result"], result["success"]?.boolValue == true {
            applyResult = result
        } else if receipt["pending"]?.boolValue == true {
            applyUncertain = true
            applyError = "上次更新请求的结果尚未确认。请先重新检查或核实服务器状态，不要重复提交。"
        }
    }

    private func text(_ value: JSONValue?) -> String? {
        guard let string = value?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !string.isEmpty else { return nil }
        return string
    }
}
