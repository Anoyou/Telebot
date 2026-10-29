import SwiftUI
import TelePilotCore

/// 能力与插件树（iOS 0.16.0，原「平台运行树」原始文档页）。
///
/// 读 `GET /api/platform/tree`，按 Web 插件中心「树状视图（能力分类）」（`PlatformTreeView.tsx`）的结构原生呈现：
/// 主干（Userbot 进程、紧急停止、运行预设）→ 直接接入的插件（直通、命令走平台主通道）→ 各平台能力分支
/// （AI、交互、入站 Webhook、资金台账、命中调试）及挂在分支上的插件。分支与 Worker 列表可展开。
/// 选择账号时与 Web 一样按该账号的插件启用状态（`/api/accounts/{id}/features`）着色，并只统计该账号启用的调用方。
struct PlatformTreeView: View {
    @Environment(AppModel.self) private var model
    @State private var tree: PlatformTree?
    @State private var error: String?
    @State private var loading = false
    /// 0 = 服务器（全部账号）。
    @State private var scope = 0
    @State private var accountEnabled: [String: Bool]?
    @State private var names: [String: String] = [:]
    /// 展开的能力分支插件列表；与 Web 一样，交互分支上挂的插件默认展开。
    @State private var expandedBranches: Set<String> = ["interaction_bot"]

    var body: some View {
        List {
            Section {
                Picker(selection: $scope) {
                    Text("服务器（全部账号）").tag(0)
                    ForEach(model.accounts) { account in
                        Text(AccountPresentation.name(account)).tag(account.id)
                    }
                } label: {
                    Label("查看范围", systemImage: "person.crop.circle.badge.checkmark")
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("platformTree.scope")
            } footer: {
                Text("按消息入口和平台能力查看插件关系：直通、命令通过主通道接入，交互通过交互 Bot 接入；「+能力」表示插件还依赖的额外平台能力。")
            }
            if let tree {
                let view = scoped(tree)
                trunkSection(view)
                if !view.directLeaves.isEmpty {
                    Section {
                        ForEach(view.directLeaves) { leaf in leafRow(leaf, host: nil) }
                    } header: {
                        Label("直接接入 · 平台主通道", systemImage: "arrow.down.forward.circle")
                    }
                }
                ForEach(PlatformTree.branchOrder.filter { view.branches[$0] != nil }, id: \.self) { key in
                    if let branch = view.branches[key] { branchSection(key: key, branch: branch, view: view) }
                }
                if view.leaves.isEmpty {
                    Section { Text("当前没有已登记的插件。").foregroundStyle(.secondary) }
                }
            } else if let error {
                Section {
                    NoticeView(title: "读取失败", message: error)
                    Button("重试", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            } else {
                Section { ProgressView("正在读取能力与插件树…") }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle("能力与插件树")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("platformTree.screen")
        .refreshable { await load() }
        .task { if tree == nil { await load() } }
        .task(id: scope) { await loadScope() }
    }

    // MARK: 主干

    private func trunkSection(_ tree: PlatformTree) -> some View {
        let workers = tree.workers
        let alive = tree.workersAlive
        let total = tree.workersTotal
        let tint: Color = total == 0 ? .red : (alive == total ? .green : .orange)
        return Section {
            DisclosureGroup {
                if workers.isEmpty {
                    Text("没有账号进程").foregroundStyle(.secondary)
                }
                ForEach(workers) { worker in workerRow(worker) }
            } label: {
                // 标识放在标签上：挂在 DisclosureGroup 上会覆盖展开后每一行自己的标识。
                LabeledContent {
                    Text("\(alive)/\(total) 在线").monospacedDigit().foregroundStyle(.secondary)
                } label: {
                    Label { Text("Userbot 进程") } icon: {
                        Image(systemName: total == 0 ? "xmark.octagon.fill" : (alive == total ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"))
                            .foregroundStyle(tint)
                    }
                }
            }
            LabeledContent {
                Text(tree.killSwitch ? "已拉闸" : "正常").foregroundStyle(tree.killSwitch ? Color.red : Color.secondary)
            } label: {
                Label { Text("紧急停止") } icon: {
                    Image(systemName: tree.killSwitch ? "exclamationmark.octagon.fill" : "exclamationmark.octagon")
                        .foregroundStyle(tree.killSwitch ? Color.red : Color.accentColor)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("platformTree.killSwitch")
            LabeledContent {
                Text(tree.profileLabel).foregroundStyle(.secondary)
            } label: {
                Label("运行预设", systemImage: "dial.medium")
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("platformTree.profile")
            if tree.profile == "safe_watch" {
                Label("值守中 · 插件投递已暂停", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            }
        } header: { Text("主干") }
    }

    private func workerRow(_ worker: PlatformTree.Worker) -> some View {
        let name = model.accounts.first(where: { $0.id == worker.accountID }).map(AccountPresentation.name) ?? "账号 #\(worker.accountID)"
        let state: (String, String, Color)
        if worker.alive { state = ("在线", "checkmark.circle.fill", .green) }
        else if worker.starting { state = ("启动中", "arrow.triangle.2.circlepath", .orange) }
        else if worker.queued { state = ("排队启动", "clock", .orange) }
        else { state = ("离线", "xmark.circle", .secondary) }
        var details: [String] = []
        if let pid = worker.pid { details.append("PID \(pid)") }
        if !worker.desired.isEmpty { details.append("期望 \(PlatformTree.desiredLabel(worker.desired))") }
        if worker.failCount > 0 { details.append("失败 \(worker.failCount) 次") }
        return VStack(alignment: .leading, spacing: 2) {
            LabeledContent {
                Label(state.0, systemImage: state.1).labelStyle(.titleAndIcon).foregroundStyle(state.2)
            } label: { Text(name).lineLimit(1) }
            if !details.isEmpty {
                Text(details.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("platformTree.worker.\(worker.accountID)")
    }

    // MARK: 分支

    private func branchSection(key: String, branch: PlatformTree.Branch, view: PlatformTree) -> some View {
        let hosted = key == "interaction_bot" ? view.interactionLeaves : []
        let callers = branch.demandedBy.compactMap { pluginKey in view.leaves.first { $0.key == pluginKey } }
        let status = PlatformTree.branchStatus(branch)
        return Section {
            LabeledContent {
                Text(status.text).foregroundStyle(status.tint)
            } label: {
                Label { Text("运行状态") } icon: { Image(systemName: status.symbol).foregroundStyle(status.tint) }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("platformTree.branch.\(key)")
            if branch.forcedOff {
                Label("已被强制关闭", systemImage: "lock").foregroundStyle(.red)
            }
            if !hosted.isEmpty {
                DisclosureGroup(isExpanded: branchExpanded(key)) {
                    ForEach(hosted) { leaf in leafRow(leaf, host: key) }
                } label: {
                    Label("接入的插件 · \(hosted.count) 个", systemImage: "puzzlepiece.extension")
                        .accessibilityIdentifier("platformTree.branch.\(key).plugins")
                }
            } else if !callers.isEmpty {
                DisclosureGroup(isExpanded: branchExpanded(key)) {
                    ForEach(callers) { leaf in leafRow(leaf, host: key) }
                } label: {
                    Label("被 \(callers.count) 个插件调用", systemImage: "puzzlepiece.extension")
                        .accessibilityIdentifier("platformTree.branch.\(key).plugins")
                }
            } else {
                Text(branch.canTurnOff ? "当前无插件调用 · 可关闭" : "当前无插件调用").foregroundStyle(.secondary)
            }
        } header: {
            Label(PlatformTree.moduleLabel(key), systemImage: PlatformTree.moduleSymbol(key))
        }
    }

    private func branchExpanded(_ key: String) -> Binding<Bool> {
        Binding(get: { expandedBranches.contains(key) },
                set: { if $0 { expandedBranches.insert(key) } else { expandedBranches.remove(key) } })
    }

    private func leafRow(_ leaf: PlatformTree.Leaf, host: String?) -> some View {
        let displayName = names[leaf.key] ?? leaf.key
        let extra = leaf.requires.filter { $0 != host && !($0 == "interaction_bot" && leaf.attachment == "交互") }
        return Label {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(displayName).foregroundStyle(leaf.enabled ? Color.primary : Color.secondary).lineLimit(2)
                    Text(leaf.attachment).font(.caption2).foregroundStyle(.secondary)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary, in: .rect(cornerRadius: 4))
                    if !leaf.enabled { Text("未启用").font(.caption).foregroundStyle(.secondary) }
                }
                if displayName != leaf.key {
                    Text(leaf.key).font(.caption2.monospaced()).foregroundStyle(.secondary)
                }
                if !extra.isEmpty {
                    Text(extra.map { "+" + PlatformTree.moduleLabel($0) }.joined(separator: "  ")).font(.caption).foregroundStyle(.secondary)
                }
                ForEach(leaf.warnings, id: \.self) { warning in
                    Label((leaf.sourceMissing ? "源缺失：" : "警告：") + warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
        } icon: {
            Image(systemName: leaf.enabled ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(leaf.enabled ? Color.green : Color.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(leaf.enabled ? "已启用" : "未启用")
        .accessibilityIdentifier("platformTree.leaf.\(leaf.key)")
    }

    // MARK: 数据

    private func scoped(_ tree: PlatformTree) -> PlatformTree {
        guard scope != 0, let accountEnabled else { return tree }
        return tree.overlay(enabled: accountEnabled)
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: "/api/platform/tree")
            guard let parsed = PlatformTree(value) else {
                error = "服务器返回的格式无法识别，请更新 App 或服务器。"
                return
            }
            tree = parsed
            error = nil
        } catch is CancellationError {
            return
        } catch {
            if case .server(let status, _)? = error as? APIError, status == 404 {
                self.error = "服务器没有能力与插件树接口（版本较旧），请先更新服务器。"
            } else {
                self.error = OpsFormat.errorMessage(error)
            }
        }
        if names.isEmpty, let matrix = try? await model.request(path: "/api/feature-matrix") {
            var map: [String: String] = [:]
            for feature in matrix["features"]?.arrayValue ?? [] {
                if let key = feature["key"]?.stringValue, let name = feature["display_name"]?.stringValue, !name.isEmpty { map[key] = name }
            }
            names = map
        }
    }

    private func loadScope() async {
        let id = scope
        guard id != 0 else { accountEnabled = nil; return }
        guard let value = try? await model.request(path: "/api/accounts/\(id)/features"), id == scope else { return }
        var map: [String: Bool] = [:]
        for item in value.arrayValue ?? [] {
            guard let key = item["feature_key"]?.stringValue else { continue }
            map[key] = (item["enabled"]?.boolValue ?? false) && item["state"]?.stringValue == "active"
        }
        accountEnabled = map
    }
}

/// `/api/platform/tree` 的解析结果（字段见 `backend/app/schemas/platform_tree.py` 与 Web `PlatformTree` 类型）。
struct PlatformTree {
    struct Worker: Identifiable {
        let accountID: Int
        let pid: Int?
        let alive: Bool
        let desired: String
        let failCount: Int
        let queued: Bool
        let starting: Bool
        var id: Int { accountID }
    }
    struct Branch {
        let state: String
        let desired: Bool
        let forcedOff: Bool
        var demandedBy: [String]
        let canTurnOff: Bool
    }
    struct Leaf: Identifiable {
        let key: String
        let attachment: String
        var enabled: Bool
        let requires: [String]
        let warnings: [String]
        let sourceMissing: Bool
        var id: String { key }
    }

    static let branchOrder = ["ai", "interaction_bot", "webhooks", "ledger", "dispatch_debug"]

    var workers: [Worker]
    var workersTotal: Int
    var workersAlive: Int
    var killSwitch: Bool
    var profile: String
    var branches: [String: Branch]
    var leaves: [Leaf]

    init?(_ value: JSONValue) {
        guard let trunk = value["trunk"]?.objectValue, let branchObject = value["branches"]?.objectValue,
              let leafArray = value["leaves"]?.arrayValue else { return nil }
        let userbot = trunk["userbot"]
        workers = (userbot?["workers"]?.arrayValue ?? []).compactMap { item in
            guard let id = item["account_id"]?.intValue else { return nil }
            return Worker(accountID: id, pid: item["pid"]?.intValue, alive: item["alive"]?.boolValue ?? false,
                          desired: item["desired"]?.stringValue ?? "", failCount: item["fail_count"]?.intValue ?? 0,
                          queued: item["queued"]?.boolValue ?? false, starting: item["starting"]?.boolValue ?? false)
        }
        workersTotal = userbot?["total"]?.intValue ?? workers.count
        workersAlive = userbot?["alive"]?.intValue ?? workers.filter(\.alive).count
        killSwitch = trunk["kill_switch"]?.boolValue ?? false
        profile = trunk["current_profile"]?.stringValue ?? "production"
        var parsed: [String: Branch] = [:]
        for (key, item) in branchObject {
            parsed[key] = Branch(state: item["state"]?.stringValue ?? "", desired: item["desired"]?.boolValue ?? false,
                                 forcedOff: item["forced_off"]?.boolValue ?? false,
                                 demandedBy: (item["demanded_by"]?.arrayValue ?? []).compactMap(\.stringValue),
                                 canTurnOff: item["can_turn_off"]?.boolValue ?? false)
        }
        branches = parsed
        leaves = leafArray.compactMap { item in
            guard let key = item["key"]?.stringValue else { return nil }
            return Leaf(key: key, attachment: item["attachment"]?.stringValue ?? "", enabled: item["enabled"]?.boolValue ?? false,
                        requires: (item["requires"]?.arrayValue ?? []).compactMap(\.stringValue),
                        warnings: (item["warnings"]?.arrayValue ?? []).compactMap(\.stringValue),
                        sourceMissing: item["source_missing"]?.boolValue ?? false)
        }
        .sorted { lhs, rhs in lhs.enabled != rhs.enabled ? lhs.enabled : lhs.key < rhs.key }
    }

    /// 已启用在前、再按标识排序（与 Web `sortLeaves` 一致）。
    var directLeaves: [Leaf] { leaves.filter { $0.attachment != "交互" } }
    var interactionLeaves: [Leaf] { leaves.filter { $0.attachment == "交互" } }

    /// 按账号插件启用状态改写叶子，并只保留该账号启用的分支调用方（Web `accountTree`）。
    func overlay(enabled: [String: Bool]) -> PlatformTree {
        var copy = self
        copy.leaves = leaves.map { leaf in
            var next = leaf
            next.enabled = enabled[leaf.key] ?? false
            return next
        }
        .sorted { lhs, rhs in lhs.enabled != rhs.enabled ? lhs.enabled : lhs.key < rhs.key }
        let enabledKeys = Set(copy.leaves.filter(\.enabled).map(\.key))
        copy.branches = branches.mapValues { branch in
            var next = branch
            next.demandedBy = branch.demandedBy.filter { enabledKeys.contains($0) }
            return next
        }
        return copy
    }

    var profileLabel: String {
        switch profile {
        case "safe_watch": "值守"
        case "custom": "自定义"
        default: "生产"
        }
    }

    static func moduleLabel(_ key: String) -> String {
        switch key {
        case "ai": "AI"
        case "interaction_bot": "交互"
        case "webhooks": "入站 Webhook"
        case "ledger": "资金台账"
        case "dispatch_debug": "命中调试"
        default: key
        }
    }

    static func moduleSymbol(_ key: String) -> String {
        switch key {
        case "ai": "cpu"
        case "interaction_bot": "bubble.left.and.text.bubble.right"
        case "webhooks": "point.3.connected.trianglepath.dotted"
        case "ledger": "book.closed"
        case "dispatch_debug": "scope"
        default: "square.stack.3d.up"
        }
    }

    static func desiredLabel(_ raw: String) -> String {
        switch raw {
        case "running", "run", "active": "运行"
        case "stopped", "stop", "paused": "停止"
        default: raw
        }
    }

    static func branchStatus(_ branch: Branch) -> (text: String, symbol: String, tint: Color) {
        guard branch.desired else { return ("已关闭", "minus.circle", .secondary) }
        switch branch.state {
        case "ready": return ("运行中", "checkmark.circle.fill", .green)
        case "starting": return ("启动中", "arrow.triangle.2.circlepath", .orange)
        case "quiescing": return ("停用中", "hourglass", .orange)
        case "stopped": return ("已暂停", "pause.circle", .secondary)
        case "failed": return ("失败", "xmark.octagon.fill", .red)
        default: return (branch.state.isEmpty ? "未知" : branch.state, "questionmark.circle", .secondary)
        }
    }
}
