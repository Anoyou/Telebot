import SwiftUI
import TelePilotCore

/// 插件页的两种视图：插件卡片（按功能）与「运行树」（这台服务器开了哪些模块、哪些插件在用）。
/// 对应 Web 插件页的「卡片视图 | 树状视图」。
enum PluginCenterMode: String, CaseIterable {
    case plugins, runtime
    var title: String { self == .plugins ? "插件" : "运行" }
}

/// 「插件 | 运行」切换：原生分段控件，插件中心与运行树共用，位置一致（账号选择下方）。
struct PluginCenterModePicker: View {
    @Binding var mode: PluginCenterMode
    var body: some View {
        Picker("插件视图", selection: $mode) {
            ForEach(PluginCenterMode.allCases, id: \.self) { item in
                Text(item.title).tag(item).accessibilityIdentifier("plugins.mode.\(item.rawValue)")
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .accessibilityIdentifier("plugins.mode")
    }
}

// MARK: - 运行树

/// 运行树（iOS 0.19.0，取代服务器设置里的「能力与插件树」与「平台运行模式」原始文档）。
///
/// 回答四个问题（`work/notes/round18/design-proposals.md` C 部分）：现在是生产还是值守；5 个可选模块开着哪些、
/// 各被几个插件需要、能不能关；插件按「直通 / 主通道 / 交互 Bot」怎么接到平台上；某个插件为什么起不来。
/// 不是消息流程图：「一条消息怎么走」交给命中调试。
///
/// 数据：`GET /api/platform/tree?account_id=`（新服务器按账号口径返回，并给出插件中文名、接入通道、
/// 全局需求与「关不掉的原因」）。旧服务器忽略 `account_id`、没有这些字段，客户端用 `/api/accounts/{id}/features`
/// 与 feature-matrix 回退计算，照常显示。运行模式读 `GET /api/platform/profile`，进入值守先
/// `POST /api/platform/profile/dry-run` 预演；模块开关 `PATCH /api/system/capabilities/{key}`（与 Web 系统设置同一接口）。
/// 所有写操作只提交一次、不自动重试；网络中断显示「结果未知」并提示刷新核对。
struct RuntimeTreeView: View {
    /// 插件页的工作区分段（插件中心／发现／已安装），作为第一行。
    var header: AnyView?
    @Binding var accountID: Int?
    @Binding var mode: PluginCenterMode
    /// feature-matrix 插件目录：中文名回退与点进配置用。
    let features: [JSONValue]
    @Environment(AppModel.self) private var model
    @State private var tree: RuntimeTree?
    @State private var treeError: String?
    @State private var profile: RuntimeProfile?
    @State private var profileError: String?
    @State private var accountEnabled: [String: Bool]?
    @State private var accountStates: [String: JSONValue] = [:]
    /// 已读到的范围（账号 ID，nil = 全部账号），用于丢弃迟到的结果。
    @State private var loadedScope: Int?? = .none

    // 运行模式
    @State private var previewing = false
    @State private var preview: RuntimeProfilePreview?
    @State private var confirmSafeWatch = false
    @State private var confirmRestore = false
    @State private var profileBusy = false
    @State private var profileMessage: String?
    @State private var profileFailure: String?

    // 模块开关
    @State private var pendingModule: RuntimeModuleChange?
    @State private var moduleBusy: String?
    @State private var moduleMessage: String?
    @State private var moduleFailure: String?

    @State private var route: RuntimeRoute?

    private var featureByKey: [String: JSONValue] {
        Dictionary(features.compactMap { item in item["key"]?.stringValue.map { ($0, item) } }, uniquingKeysWith: { first, _ in first })
    }
    private var names: [String: String] {
        featureByKey.compactMapValues { item in
            let name = item["display_name"]?.stringValue ?? ""
            return name.isEmpty ? nil : name
        }
    }

    var body: some View {
        List {
            if let header {
                Section { header.pageHeaderRow() }
            }
            scopeSection
            Section { PluginCenterModePicker(mode: $mode).pageHeaderRow() }
            profileSection
            if let tree {
                modulesSection(tree)
                pluginSections(tree)
            } else if let treeError {
                Section {
                    NoticeView(title: "运行树读取失败", message: treeError)
                    Button("重试", systemImage: "arrow.clockwise") { Task { await load(force: true) } }
                        .accessibilityIdentifier("runtime.retry")
                }
            } else {
                Section { ProgressView("正在读取运行树…").frame(maxWidth: .infinity) }
            }
            debugSection
        }
        .symbolRenderingMode(.hierarchical)
        .accessibilityIdentifier("runtime.screen")
        .refreshable { await load(force: true) }
        .task(id: accountID) { await load(force: false) }
        .navigationDestination(item: $route) { route in destination(route) }
    }

    // MARK: 账号范围

    private var scopeSection: some View {
        Section {
            Picker(selection: $accountID) {
                Label("全部账号", systemImage: "square.stack.3d.up").tag(Int?.none)
                ForEach(model.accounts) { account in
                    Label(ManagementText.accountName(account), systemImage: "person.crop.circle").tag(Optional(account.id))
                }
            } label: {
                Label("选择配置的账号", systemImage: "person.crop.circle.badge.checkmark")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("plugins.accountPicker")
        } footer: {
            Text(accountID == nil
                 ? "全部账号：插件只要在任一账号启用就算在用。选一个账号可以看这个账号的插件和需求，并点进它的配置。"
                 : "按这个账号看：插件是否启用、模块被几个插件需要都按此账号计算；模块的运行状态是整台服务器的。")
        }
    }

    // MARK: 运行模式

    private var safeWatch: Bool { profile?.isSafeWatch ?? (tree?.profile == "safe_watch") }

    private var profileSection: some View {
        Section {
            if let profile {
                LabeledContent {
                    Text(profile.statusText).foregroundStyle(profile.tint)
                } label: {
                    Label { Text("当前模式 · \(profile.label)") } icon: {
                        Image(systemName: profile.isSafeWatch ? "moon.zzz.fill" : "dial.medium").foregroundStyle(profile.tint)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("runtime.profile")
            } else if let profileError {
                Label("运行模式读取失败：\(profileError)", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            } else {
                ProgressView("正在读取运行模式…")
            }
            if let tree {
                LabeledContent {
                    Text("\(tree.workersAlive)/\(tree.workersTotal) 在线").monospacedDigit()
                        .foregroundStyle(tree.workersTotal > 0 && tree.workersAlive == tree.workersTotal ? Color.secondary : Color.orange)
                } label: {
                    Label("账号进程", systemImage: "person.2")
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("runtime.workers")
                LabeledContent {
                    Text(tree.killSwitch ? "已拉闸" : "正常").foregroundStyle(tree.killSwitch ? Color.red : Color.secondary)
                } label: {
                    Label { Text("紧急停止") } icon: {
                        Image(systemName: tree.killSwitch ? "exclamationmark.octagon.fill" : "exclamationmark.octagon")
                            .foregroundStyle(tree.killSwitch ? Color.red : Color.accentColor)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("runtime.killSwitch")
            }
            if safeWatch, let blind = profile?.blindSpot, !blind.isEmpty {
                Label(blind, systemImage: "eye.slash").font(.footnote).foregroundStyle(.orange)
            }
            if let error = profile?.lastError, !error.isEmpty {
                Label("收敛失败：\(error)", systemImage: "xmark.octagon.fill").font(.footnote).foregroundStyle(.red)
            }
            if let profileFailure {
                Label(profileFailure, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.red)
                    .accessibilityIdentifier("runtime.profile.error")
            } else if let profileMessage {
                Label(profileMessage, systemImage: "checkmark.circle.fill").font(.footnote).foregroundStyle(.green)
                    .accessibilityIdentifier("runtime.profile.message")
            }
            profileButton
        } header: {
            Text("运行模式")
        } footer: {
            Text(safeWatch
                 ? "值守中：AI、交互 Bot、入站 Webhook 已关闭，插件不再收到消息，定时任务与资金动作暂停；账号保持在线、只记录观测。恢复时逐项还原进入值守前的状态。"
                 : "出事时一键「值守」止血：关掉 AI、交互 Bot、入站 Webhook，暂停插件投递和资金动作，只保留账号在线与观测。进入前会先预演，列出会关掉什么。")
        }
    }

    @ViewBuilder private var profileButton: some View {
        let busyStatus = profile?.status == "applying" || profile?.status == "restoring"
        if safeWatch {
            Button {
                confirmRestore = true
            } label: {
                Label(profileBusy ? "正在恢复…" : "恢复生产", systemImage: "arrow.uturn.backward.circle")
            }
            .disabled(profileBusy || busyStatus || profile == nil)
            .accessibilityIdentifier("runtime.restore")
            .confirmationDialog("恢复生产？", isPresented: $confirmRestore, titleVisibility: .visible) {
                Button("确认恢复生产") { Task { await restore() } }
                    .accessibilityIdentifier("runtime.restore.confirm")
                Button("取消", role: .cancel) {}
            } message: {
                Text("逐项还原进入值守前的模块开关，插件投递、定时任务与资金动作恢复。")
            }
        } else {
            Button {
                Task { await loadPreview() }
            } label: {
                Label(previewing ? "正在预演…" : (profileBusy ? "正在进入值守…" : "进入值守…"), systemImage: "moon.zzz")
            }
            .disabled(previewing || profileBusy || busyStatus || profile == nil)
            .accessibilityIdentifier("runtime.safeWatch")
            .confirmationDialog("进入值守？", isPresented: $confirmSafeWatch, titleVisibility: .visible) {
                Button("确认进入值守", role: .destructive) { Task { await applySafeWatch() } }
                    .accessibilityIdentifier("runtime.safeWatch.confirm")
                Button("取消", role: .cancel) {}
            } message: {
                Text(preview?.message ?? "")
            }
        }
    }

    // MARK: 可选模块

    private func modulesSection(_ tree: RuntimeTree) -> some View {
        Section {
            ForEach(tree.modules) { module in moduleRow(module, tree: tree) }
            if let moduleFailure {
                Label(moduleFailure, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.red)
                    .accessibilityIdentifier("runtime.module.error")
            } else if let moduleMessage {
                Label(moduleMessage, systemImage: "checkmark.circle.fill").font(.footnote).foregroundStyle(.green)
                    .accessibilityIdentifier("runtime.module.message")
            }
        } header: {
            Text("可选模块")
        } footer: {
            Text("账号进程、插件加载、路由、审计与总闸是平台内核，一直开着。可选模块关闭只暂停入口和运行时资源，不删除配置、Token、规则或资金数据。点模块查看需要它的插件。")
        }
    }

    private func moduleRow(_ module: RuntimeTree.Module, tree: RuntimeTree) -> some View {
        let status = module.status
        let lock = module.lockReason(safeWatch: safeWatch)
        let label = RuntimeTree.moduleLabel(module.key)
        let dialogTitle: String = pendingModule?.enable == true ? "开启\(label)？" : "关闭\(label)？"
        return HStack(alignment: .center, spacing: 12) {
            Button {
                route = .module(module.key)
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: RuntimeTree.moduleSymbol(module.key))
                        .font(.title3).frame(width: 28).foregroundStyle(.tint)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(RuntimeTree.moduleLabel(module.key)).foregroundStyle(.primary)
                            Label(status.text, systemImage: status.symbol).labelStyle(.titleAndIcon)
                                .font(.caption).foregroundStyle(status.tint)
                        }
                        Text(module.demandText(scoped: tree.scopeAccountID != nil))
                            .font(.footnote).foregroundStyle(.secondary)
                        if let lock {
                            Label(lock, systemImage: "lock").font(.caption).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.borderless)
            .accessibilityIdentifier("runtime.module.\(module.key)")
            if moduleBusy == module.key {
                ProgressView()
            }
            Toggle(RuntimeTree.moduleLabel(module.key), isOn: Binding(
                get: { module.desired },
                set: { newValue in pendingModule = RuntimeModuleChange(key: module.key, enable: newValue) }
            ))
            .labelsHidden()
            .disabled(moduleBusy != nil || (module.desired ? !module.canSwitchOff(safeWatch: safeWatch) : safeWatch))
            .accessibilityIdentifier("runtime.module.\(module.key).toggle")
            .accessibilityHint(lock ?? "")
            .confirmationDialog(
                dialogTitle,
                isPresented: Binding(get: { pendingModule?.key == module.key },
                                     set: { if !$0, pendingModule?.key == module.key { pendingModule = nil } }),
                titleVisibility: .visible
            ) {
                let enable = pendingModule?.enable ?? !module.desired
                let title: String = enable ? "开启" : "关闭"
                Button(title, role: enable ? nil : .destructive) {
                    Task { await setModule(module.key, enabled: enable) }
                }
                .accessibilityIdentifier("runtime.module.confirm")
                Button("取消", role: .cancel) { pendingModule = nil }
            } message: {
                Text(RuntimeTree.moduleChangeExplanation(module.key, enable: pendingModule?.enable ?? !module.desired))
            }
        }
        .accessibilityElement(children: .contain)
    }

    // MARK: 插件分组

    @ViewBuilder private func pluginSections(_ tree: RuntimeTree) -> some View {
        if tree.plugins.isEmpty {
            Section { Text("当前没有已登记的插件。").foregroundStyle(.secondary) }
        }
        ForEach(RuntimeTree.Channel.allCases, id: \.self) { channel in
            let items = tree.plugins.filter { $0.channel == channel }
            if !items.isEmpty {
                Section {
                    ForEach(items) { plugin in pluginRow(plugin, tree: tree) }
                } header: {
                    Label(channel.title, systemImage: channel.symbol)
                        .accessibilityIdentifier("runtime.group.\(channel.rawValue)")
                } footer: {
                    Text(channel.explanation)
                }
            }
        }
    }

    private func pluginRow(_ plugin: RuntimeTree.Plugin, tree: RuntimeTree) -> some View {
        let name = plugin.name ?? names[plugin.key] ?? plugin.key
        let extra = plugin.requires.filter { !(plugin.channel == .interaction && $0 == "interaction_bot") }
        let navigable = featureByKey[plugin.key] != nil
        let hint: String = navigable ? (accountID == nil ? "打开插件详情" : "打开这个账号里的插件配置") : ""
        return VStack(alignment: .leading, spacing: 6) {
            Button {
                if navigable { route = .plugin(plugin.key) }
            } label: {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: plugin.enabled ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(plugin.enabled ? Color.green : Color.secondary)
                        .padding(.top, 2)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(name).foregroundStyle(plugin.enabled ? Color.primary : Color.secondary).lineLimit(2)
                            if !plugin.enabled { Text("未启用").font(.caption).foregroundStyle(.secondary) }
                        }
                        if name != plugin.key {
                            Text(plugin.key).font(.caption2.monospaced()).foregroundStyle(.secondary)
                        }
                        if !plugin.entryLabels.isEmpty {
                            Text("靠" + plugin.entryLabels.joined(separator: "、") + "工作").font(.caption).foregroundStyle(.secondary)
                        }
                        if !extra.isEmpty {
                            Text(extra.map { "+" + RuntimeTree.moduleLabel($0) }.joined(separator: "  "))
                                .font(.caption.weight(.medium)).foregroundStyle(.tint)
                        }
                        ForEach(plugin.blockedModules(in: tree), id: \.self) { key in
                            Label("需要的「\(RuntimeTree.moduleLabel(key))」没开，插件起不来", systemImage: "exclamationmark.triangle.fill")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        if let error = plugin.accountError {
                            Label("运行异常：" + error, systemImage: "exclamationmark.circle.fill")
                                .font(.caption).foregroundStyle(.red).lineLimit(3)
                        }
                        ForEach(plugin.warnings, id: \.self) { warning in
                            Label((plugin.sourceMissing ? "源缺失：" : "警告：") + warning, systemImage: "exclamationmark.triangle.fill")
                                .font(.caption).foregroundStyle(.orange)
                        }
                    }
                    Spacer(minLength: 4)
                    if navigable {
                        Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary).padding(.top, 4)
                    }
                }
                .contentShape(.rect)
            }
            .buttonStyle(.borderless)
            .disabled(!navigable)
            .accessibilityValue(plugin.enabled ? "已启用" : "未启用")
            .accessibilityHint(hint)
            .accessibilityIdentifier("runtime.plugin.\(plugin.key)")
            if plugin.channel == .interaction {
                Button {
                    route = .interaction
                } label: {
                    HStack(spacing: 4) {
                        Label("交互规则", systemImage: "bubble.left.and.text.bubble.right")
                        Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
                    }
                    .font(.footnote)
                    .frame(minHeight: 32)
                }
                .buttonStyle(.borderless)
                .padding(.leading, 30)
                .accessibilityLabel("交互规则")
                .accessibilityIdentifier("runtime.plugin.\(plugin.key).rules")
            }
        }
    }

    // MARK: 命中调试

    private var debugSection: some View {
        Section {
            Button {
                route = .dispatchDebug
            } label: {
                HStack {
                    Label("想看一条消息怎么走 → 命中调试", systemImage: "scope")
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                }
                .contentShape(.rect)
            }
            .buttonStyle(.borderless)
            .accessibilityIdentifier("runtime.dispatchDebug")
        } footer: {
            Text("运行树只看结构：开了哪些模块、哪些插件在用。一条消息具体被谁处理、为什么，用命中调试模拟。")
        }
    }

    // MARK: 导航

    @ViewBuilder private func destination(_ route: RuntimeRoute) -> some View {
        switch route {
        case .module(let key):
            if let tree, let module = tree.modules.first(where: { $0.key == key }) {
                RuntimeModuleDetailView(module: module, tree: tree, names: names, features: featureByKey, safeWatch: safeWatch,
                                        accountID: accountID, accountStates: accountStates)
            } else {
                ContentUnavailableView("模块不存在", systemImage: "questionmark.circle")
            }
        case .plugin(let key):
            if let feature = featureByKey[key] {
                if let accountID {
                    ManagementPluginDetailView(feature: feature, accountID: accountID, accountState: accountStates[key], initialSection: .configuration)
                } else {
                    ManagementPluginDetailView(feature: feature)
                }
            } else {
                ContentUnavailableView("插件目录里没有这个插件", systemImage: "puzzlepiece.extension")
            }
        case .interaction:
            InteractionCenterView(initialAccountID: accountID)
        case .dispatchDebug:
            ManagementFormView(action: .init(title: "命中调试", path: "/api/dispatch/simulate",
                                             explanation: "模拟一条消息会经过直通、命令、关键词、事件订阅四个阶段中的哪些插件和规则，以及为什么命中或没命中。不会真的发到 Telegram。"))
        }
    }

    // MARK: 数据

    private func load(force: Bool) async {
        let scope = accountID
        if !force, loadedScope == .some(scope), tree != nil { return }
        await loadTree(scope: scope)
        await loadProfile()
    }

    private func loadTree(scope: Int?) async {
        var enabled: [String: Bool]?
        var states: [String: JSONValue] = [:]
        do {
            let path = scope.map { "/api/platform/tree?account_id=\($0)" } ?? "/api/platform/tree"
            let value = try await model.request(path: path)
            if let scope {
                // 旧服务器忽略 account_id：按账号插件状态回退计算；新服务器也读一次，点进配置时带上账号状态。
                if let list = try? await model.request(path: "/api/accounts/\(scope)/features") {
                    var map: [String: Bool] = [:]
                    for item in list.arrayValue ?? [] {
                        guard let key = item["feature_key"]?.stringValue else { continue }
                        map[key] = (item["enabled"]?.boolValue ?? false) && item["state"]?.stringValue == "active"
                        states[key] = item
                    }
                    enabled = map
                }
            }
            guard scope == accountID else { return }
            guard let parsed = RuntimeTree(value, accountID: scope, accountEnabled: enabled) else {
                treeError = "服务器返回的格式无法识别，请更新 App 或服务器。"
                return
            }
            tree = parsed
            if scope != nil, !states.isEmpty { accountStates = states } else if scope == nil { accountStates = [:] }
            accountEnabled = enabled
            loadedScope = .some(scope)
            treeError = nil
        } catch is CancellationError {
            return
        } catch {
            guard scope == accountID else { return }
            if case .server(let status, _)? = error as? APIError, status == 404 {
                treeError = "服务器没有运行树接口（版本较旧），请先更新服务器。"
            } else {
                treeError = OpsFormat.errorMessage(error)
            }
        }
    }

    private func loadProfile() async {
        do {
            let value = try await model.request(path: "/api/platform/profile")
            profile = RuntimeProfile(value)
            profileError = nil
        } catch is CancellationError {
            return
        } catch {
            if case .server(let status, _)? = error as? APIError, status == 404 {
                profileError = "服务器版本较旧，没有运行模式接口"
            } else {
                profileError = OpsFormat.errorMessage(error)
            }
        }
    }

    private func loadPreview() async {
        previewing = true
        profileFailure = nil
        profileMessage = nil
        defer { previewing = false }
        do {
            let value = try await model.request(path: "/api/platform/profile/dry-run", method: "POST", body: .object(["preset": .string("safe_watch")]))
            preview = RuntimeProfilePreview(value)
            confirmSafeWatch = true
        } catch {
            profileFailure = "预演失败，未做任何改动：\(OpsFormat.errorMessage(error))"
        }
    }

    private func applySafeWatch() async {
        profileBusy = true
        profileFailure = nil
        profileMessage = nil
        defer { profileBusy = false }
        do {
            let value = try await model.request(path: "/api/platform/profile/apply", method: "POST", body: .object(["preset": .string("safe_watch")]))
            profile = RuntimeProfile(value) ?? profile
            profileMessage = "已进入值守：插件投递、定时任务和资金动作已暂停。"
        } catch {
            profileFailure = (error as? APIError)?.isOutcomeUncertain == true
                ? "未能确认是否已进入值守，下拉刷新查看实际状态。"
                : "进入值守失败：\(OpsFormat.errorMessage(error))"
        }
        await load(force: true)
    }

    private func restore() async {
        profileBusy = true
        profileFailure = nil
        profileMessage = nil
        defer { profileBusy = false }
        do {
            let value = try await model.request(path: "/api/platform/profile/restore", method: "POST")
            profile = RuntimeProfile(value) ?? profile
            profileMessage = "已恢复生产：插件投递与定时任务恢复。"
        } catch {
            profileFailure = (error as? APIError)?.isOutcomeUncertain == true
                ? "未能确认是否已恢复，下拉刷新查看实际状态。"
                : "恢复失败：\(OpsFormat.errorMessage(error))"
        }
        await load(force: true)
    }

    private func setModule(_ key: String, enabled: Bool) async {
        pendingModule = nil
        moduleBusy = key
        moduleFailure = nil
        moduleMessage = nil
        defer { moduleBusy = nil }
        let label = RuntimeTree.moduleLabel(key)
        do {
            let value = try await model.request(path: "/api/system/capabilities/\(key)", method: "PATCH", body: .object(["enabled": .bool(enabled)]))
            if let message = value["message"]?.stringValue, !message.isEmpty {
                if value["ok"]?.boolValue == false { moduleFailure = message } else { moduleMessage = message }
            } else {
                moduleMessage = enabled ? "\(label) 已开启，正在热加载。" : "\(label) 已关闭，运行时将收敛为暂停。"
            }
        } catch {
            moduleFailure = (error as? APIError)?.isOutcomeUncertain == true
                ? "未能确认\(label)是否已切换，下拉刷新查看实际状态。"
                : "\(label)切换失败：\(OpsFormat.errorMessage(error))"
        }
        await load(force: true)
    }
}

enum RuntimeRoute: Hashable {
    case module(String)
    case plugin(String)
    case interaction
    case dispatchDebug
}

struct RuntimeModuleChange: Equatable {
    let key: String
    let enable: Bool
}

// MARK: - 模块详情

/// 点模块：列出需要它的插件（替代 Web 的悬停联动），点插件进配置。
struct RuntimeModuleDetailView: View {
    let module: RuntimeTree.Module
    let tree: RuntimeTree
    let names: [String: String]
    let features: [String: JSONValue]
    let safeWatch: Bool
    let accountID: Int?
    let accountStates: [String: JSONValue]

    var body: some View {
        let status = module.status
        let scoped = tree.scopeAccountID != nil
        let here = module.demandedBy
        let elsewhere = scoped ? module.demandedByGlobal.filter { !here.contains($0) } : []
        let attached = tree.plugins.filter { $0.channel == .interaction && module.key == "interaction_bot" && !here.contains($0.key) }
        List {
            Section {
                LabeledContent {
                    Text(status.text).foregroundStyle(status.tint)
                } label: {
                    Label { Text("运行状态") } icon: { Image(systemName: status.symbol).foregroundStyle(status.tint) }
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("runtime.moduleDetail.status")
                if let lock = module.lockReason(safeWatch: safeWatch) {
                    Label(lock, systemImage: "lock").foregroundStyle(.orange)
                        .accessibilityIdentifier("runtime.moduleDetail.lock")
                }
            } footer: {
                Text(RuntimeTree.moduleDescription(module.key))
            }
            Section {
                if here.isEmpty {
                    Text(scoped ? "这个账号没有插件需要它。" : "没有插件需要它。").foregroundStyle(.secondary)
                }
                ForEach(here, id: \.self) { key in pluginRow(key) }
            } header: {
                Text(scoped ? "这个账号里需要它的插件 · \(here.count)" : "需要它的插件 · \(here.count)")
            }
            if !elsewhere.isEmpty {
                Section {
                    ForEach(elsewhere, id: \.self) { key in pluginRow(key) }
                } header: {
                    Text("其他账号里需要它的插件 · \(elsewhere.count)")
                } footer: {
                    Text("这些插件在别的账号启用，所以模块仍关不掉。")
                }
            }
            if !attached.isEmpty {
                Section {
                    ForEach(attached) { plugin in pluginRow(plugin.key) }
                } header: {
                    Text("经交互 Bot 接入、当前未启用的插件")
                }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .navigationTitle(RuntimeTree.moduleLabel(module.key))
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("runtime.moduleDetail")
    }

    private func pluginRow(_ key: String) -> some View {
        let plugin = tree.plugins.first { $0.key == key }
        let name = plugin?.name ?? names[key] ?? key
        return NavigationLink {
            if let feature = features[key] {
                if let accountID {
                    ManagementPluginDetailView(feature: feature, accountID: accountID, accountState: accountStates[key], initialSection: .configuration)
                } else {
                    ManagementPluginDetailView(feature: feature)
                }
            }
        } label: {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name).foregroundStyle(.primary)
                        if name != key { Text(key).font(.caption2.monospaced()).foregroundStyle(.secondary) }
                    }
                } icon: {
                    Image(systemName: "puzzlepiece.extension")
                }
        }
        .disabled(features[key] == nil)
        .accessibilityIdentifier("runtime.moduleDetail.plugin.\(key)")
    }
}

// MARK: - 数据模型

/// `/api/platform/tree` 的解析结果。新服务器（回显 `scope.kind == "account"`）按账号口径给出 `demanded_by`、
/// `demanded_by_global`、`blocked_code` / `blocked_reason`、`forced_off_reason`、插件 `display_name` / `channel` /
/// `entry_labels` / `account_error`；
/// 旧服务器缺这些字段时，按账号插件状态（`/api/accounts/{id}/features`）回退计算。
struct RuntimeTree {
    enum Channel: String, CaseIterable {
        case passthrough, main, interaction
        var title: String {
            switch self {
            case .passthrough: "直通（最先处理）"
            case .main: "主通道"
            case .interaction: "交互 Bot"
            }
        }
        var symbol: String {
            switch self {
            case .passthrough: "bolt.horizontal"
            case .main: "arrow.down.forward.circle"
            case .interaction: "bubble.left.and.text.bubble.right"
            }
        }
        var explanation: String {
            switch self {
            case .passthrough: "最先拿到原始消息，可以截住，后面的插件就收不到。"
            case .main: "账号自己的消息链路：命令、事件订阅、定时任务、消息监听等。"
            case .interaction: "经交互 Bot 接入，按交互规则（关键词、转账、按钮）启动。"
            }
        }
        init(attachment: String) {
            switch attachment {
            case "直通", "passthrough": self = .passthrough
            case "交互", "交互 Bot", "interaction": self = .interaction
            default: self = .main
            }
        }
    }

    struct Module: Identifiable {
        let key: String
        let state: String
        let desired: Bool
        let forcedOff: Bool
        /// 当前范围（账号或全部）里需要它的已启用插件。
        let demandedBy: [String]
        /// 全部账号里需要它的已启用插件：只要非空就关不掉。
        let demandedByGlobal: [String]
        /// 服务器给出的「关不掉」原因（新服务器 `blocked_reason`；`blocked_code == already_off` 时不用它）。
        let blockedReason: String?
        /// 被强制关闭的原因（新服务器 `forced_off_reason`：值守 / 管理员）。
        let forcedOffReason: String?
        var id: String { key }

        var status: (text: String, symbol: String, tint: Color) { RuntimeTree.moduleStatus(self) }

        func canSwitchOff(safeWatch: Bool) -> Bool { demandedByGlobal.isEmpty }

        /// 开关不可用或需要说明的原因：被插件需要（关不掉）、值守中（开不了）、被管理员关闭。
        func lockReason(safeWatch: Bool) -> String? {
            if desired, !demandedByGlobal.isEmpty {
                if let blockedReason { return blockedReason }
                let others = demandedByGlobal.count - demandedBy.count
                if others > 0, !demandedBy.isEmpty {
                    return "被 \(demandedByGlobal.count) 个插件需要（本账号 \(demandedBy.count) 个，其他账号 \(others) 个），先停用这些插件才能关闭"
                }
                if demandedBy.isEmpty {
                    return "本账号没有插件需要它，但其他账号有 \(demandedByGlobal.count) 个插件需要，关不掉"
                }
                return "被 \(demandedByGlobal.count) 个插件需要，先停用这些插件才能关闭"
            }
            guard !desired else { return nil }
            if forcedOff, let forcedOffReason { return forcedOffReason }
            if safeWatch { return "值守中，恢复生产后再开启" }
            if forcedOff { return "管理员手动关闭了它，需要它的插件不会自动打开它" }
            return nil
        }

        func demandText(scoped: Bool) -> String {
            if !demandedBy.isEmpty { return "被 \(demandedBy.count) 个插件需要" }
            if desired, demandedByGlobal.isEmpty { return scoped ? "本账号无插件需要 · 可关闭" : "当前无插件需要 · 可关闭" }
            return scoped ? "本账号无插件需要" : "当前无插件需要"
        }
    }

    struct Plugin: Identifiable {
        let key: String
        let name: String?
        let channel: Channel
        let entryLabels: [String]
        let enabled: Bool
        let requires: [String]
        let warnings: [String]
        let sourceMissing: Bool
        /// 按账号查看时该账号上最近的失败原因（新服务器 `account_error`）。
        let accountError: String?
        var id: String { key }

        /// 已启用却缺模块：需要的模块没开（被值守或管理员关闭），插件起不来。
        func blockedModules(in tree: RuntimeTree) -> [String] {
            guard enabled else { return [] }
            return requires.filter { key in tree.modules.first { $0.key == key }.map { !$0.desired } ?? false }
        }
    }

    static let moduleOrder = ["ai", "interaction_bot", "webhooks", "ledger", "dispatch_debug"]

    var scopeAccountID: Int?
    /// 服务器已按账号口径计算（新服务器）；false 表示客户端回退计算。
    var serverScoped: Bool
    var workersTotal: Int
    var workersAlive: Int
    var killSwitch: Bool
    var profile: String
    var modules: [Module]
    var plugins: [Plugin]

    /// 新服务器（0.113.0-beta.2 起）回显 `scope: {kind: "account", account_id}` 即表示已按账号口径计算；
    /// 旧服务器没有 `scope`、忽略 `account_id` 参数，返回全局口径。
    static func isAccountScoped(_ value: JSONValue, accountID: Int) -> Bool {
        guard let scope = value["scope"], scope["kind"]?.stringValue == "account" else { return false }
        return scope["account_id"]?.intValue == accountID
    }

    init?(_ value: JSONValue, accountID: Int?, accountEnabled: [String: Bool]?) {
        guard let trunk = value["trunk"]?.objectValue, let branchObject = value["branches"]?.objectValue,
              let leafArray = value["leaves"]?.arrayValue else { return nil }
        scopeAccountID = accountID
        serverScoped = accountID.map { Self.isAccountScoped(value, accountID: $0) } ?? true
        let userbot = trunk["userbot"]
        let workers = userbot?["workers"]?.arrayValue ?? []
        workersTotal = userbot?["total"]?.intValue ?? workers.count
        workersAlive = userbot?["alive"]?.intValue ?? workers.filter { $0["alive"]?.boolValue == true }.count
        killSwitch = trunk["kill_switch"]?.boolValue ?? false
        profile = trunk["current_profile"]?.stringValue ?? "production"

        let useOverlay = accountID != nil && !serverScoped && accountEnabled != nil
        var parsedPlugins: [Plugin] = leafArray.compactMap { item in
            guard let key = item["key"]?.stringValue else { return nil }
            let rawName = item["display_name"]?.stringValue ?? item["name"]?.stringValue
            let channel = item["channel"]?.stringValue.map(Channel.init(attachment:))
                ?? Channel(attachment: item["attachment"]?.stringValue ?? "")
            let enabled = useOverlay ? (accountEnabled?[key] ?? false) : (item["enabled"]?.boolValue ?? false)
            return Plugin(key: key, name: (rawName?.isEmpty ?? true) ? nil : rawName, channel: channel,
                          entryLabels: (item["entry_labels"]?.arrayValue ?? []).compactMap(\.stringValue),
                          enabled: enabled,
                          requires: (item["requires"]?.arrayValue ?? []).compactMap(\.stringValue),
                          warnings: (item["warnings"]?.arrayValue ?? []).compactMap(\.stringValue),
                          sourceMissing: item["source_missing"]?.boolValue ?? false,
                          accountError: item["account_error"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 })
        }
        parsedPlugins.sort { lhs, rhs in lhs.enabled != rhs.enabled ? lhs.enabled : lhs.key < rhs.key }
        plugins = parsedPlugins
        let enabledKeys = Set(parsedPlugins.filter(\.enabled).map(\.key))

        let keys = Self.moduleOrder.filter { branchObject[$0] != nil } + branchObject.keys.filter { !Self.moduleOrder.contains($0) }.sorted()
        modules = keys.compactMap { key in
            guard let item = branchObject[key] else { return nil }
            let demanded = (item["demanded_by"]?.arrayValue ?? []).compactMap(\.stringValue)
            let global = item["demanded_by_global"]?.arrayValue.map { $0.compactMap(\.stringValue) } ?? demanded
            let scoped = useOverlay ? demanded.filter { enabledKeys.contains($0) } : demanded
            let reason = item["blocked_code"]?.stringValue == "already_off" ? nil : item["blocked_reason"]?.stringValue
            let forcedReason = item["forced_off_reason"]?.stringValue
            return Module(key: key, state: item["state"]?.stringValue ?? "", desired: item["desired"]?.boolValue ?? false,
                          forcedOff: item["forced_off"]?.boolValue ?? false, demandedBy: scoped, demandedByGlobal: global,
                          blockedReason: (reason?.isEmpty ?? true) ? nil : reason,
                          forcedOffReason: (forcedReason?.isEmpty ?? true) ? nil : forcedReason)
        }
    }

    static func moduleLabel(_ key: String) -> String {
        switch key {
        case "ai": "AI"
        case "interaction_bot": "交互 Bot"
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

    /// 与 Web 系统设置「平台能力」的说明一致。
    static func moduleDescription(_ key: String) -> String {
        switch key {
        case "ai": "模型供应商、AI 指令与插件的 AI 能力。关闭后账号进程不加载密钥与代理。"
        case "interaction_bot": "交互 Bot / 测试 Bot 与交互通道。管理 Bot 与账号本身不受影响。"
        case "webhooks": "公开入站 Webhook 投递。关闭后外部地址立即返回 404，配置与 Token 保留。"
        case "ledger": "台账查询与操作面。关闭或值守时，发奖等资金动作一律拒绝；动作记录与派奖补偿主账继续写入。"
        case "dispatch_debug": "命中模拟与路由诊断记录。普通日志与基础调用链不受影响。"
        default: ""
        }
    }

    static func moduleChangeExplanation(_ key: String, enable: Bool) -> String {
        let base = moduleDescription(key)
        return enable
            ? "\(base)\n开启后热加载，各账号进程陆续生效。"
            : "\(base)\n关闭只暂停入口与运行时资源，不删除配置、Token、规则或资金数据，可随时再开。"
    }

    static func moduleStatus(_ module: Module) -> (text: String, symbol: String, tint: Color) {
        guard module.desired else {
            return module.forcedOff ? ("已关闭", "lock", .secondary) : ("已关闭", "minus.circle", .secondary)
        }
        switch module.state {
        case "ready": return ("运行中", "checkmark.circle.fill", .green)
        case "starting": return ("启动中", "arrow.triangle.2.circlepath", .orange)
        case "quiescing": return ("停用中", "hourglass", .orange)
        case "stopped": return ("已暂停", "pause.circle", .secondary)
        case "failed": return ("失败", "xmark.octagon.fill", .red)
        default: return (module.state.isEmpty ? "未知" : module.state, "questionmark.circle", .secondary)
        }
    }
}

/// `GET /api/platform/profile`。
struct RuntimeProfile {
    let current: String
    let active: String?
    let status: String
    let lastError: String?
    let blindSpot: String?

    init?(_ value: JSONValue) {
        guard let current = value["current_profile"]?.stringValue else { return nil }
        self.current = current
        active = value["active_profile"]?.stringValue
        status = value["status"]?.stringValue ?? "idle"
        lastError = value["last_error"]?.stringValue
        blindSpot = value["blind_spot"]?.stringValue
    }

    var isSafeWatch: Bool { active == "safe_watch" || current == "safe_watch" }
    var label: String { isSafeWatch ? "值守" : (current == "custom" ? "自定义" : "生产") }
    var statusText: String {
        switch status {
        case "applying": "正在进入值守"
        case "active": "值守中"
        case "restoring": "正在恢复"
        case "failed": "收敛失败"
        default: isSafeWatch ? "值守中" : "正常运行"
        }
    }
    var tint: Color {
        if status == "failed" { return .red }
        if isSafeWatch || status == "applying" || status == "restoring" { return .orange }
        return .green
    }
}

/// `POST /api/platform/profile/dry-run` 的预演结果，转成确认框文字。
struct RuntimeProfilePreview {
    struct Change { let key: String; let from: Bool; let to: Bool }
    let changes: [Change]
    let blindSpot: String?

    init(_ value: JSONValue) {
        changes = (value["diff"]?.arrayValue ?? []).compactMap { item in
            guard let key = item["key"]?.stringValue else { return nil }
            return Change(key: key, from: item["from_enabled"]?.boolValue ?? false, to: item["to_enabled"]?.boolValue ?? false)
        }
        blindSpot = value["blind_spot"]?.stringValue
    }

    var message: String {
        var lines: [String] = []
        let off = changes.filter { $0.from && !$0.to }.map { RuntimeTree.moduleLabel($0.key) }
        let on = changes.filter { !$0.from && $0.to }.map { RuntimeTree.moduleLabel($0.key) }
        if off.isEmpty && on.isEmpty {
            lines.append("模块开关不需要调整。")
        } else {
            if !off.isEmpty { lines.append("将关闭：" + off.joined(separator: "、")) }
            if !on.isEmpty { lines.append("将开启：" + on.joined(separator: "、")) }
        }
        lines.append("同时暂停所有插件投递与定时任务，拒绝资金动作；账号保持在线，平台通知与内置管理命令仍可用。")
        if let blindSpot, !blindSpot.isEmpty { lines.append(blindSpot) }
        return lines.joined(separator: "\n")
    }
}
