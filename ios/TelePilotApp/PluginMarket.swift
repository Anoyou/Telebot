import SwiftUI
import TelePilotCore

// 插件市场（服务器 0.113.0+）：与 Web 管理台同一组接口 /api/plugin-market/*。
// 浏览 / 搜索 / 安装 / 更新角标 / 一键全部更新（服务器并行执行，结果逐个显示）/ 固定版本 /
// 健康观察期内回滚或确认 / 来源管理（官方源 + 自定义来源与信任等级、自动更新开关）/ 更新参数。
// 旧服务器没有这些接口（404）时显示“请更新服务器”，其它插件功能照常可用。
// 服务器 0.117.0 起（iOS 0.37.0）：分类标签带数量、详情页有完整简介 / 截图 / 怎么用 / 版本说明 / 权限大白话 / 兼容性 /
// 主页与许可证、安装未审核插件前二次确认、稳定 / 测试通道（插件真有第二个通道时才显示）。这些字段在旧服务器的目录里没有，
// 解析放在 TelePilotCore `PluginMarketDetails`，缺什么就不显示什么。
// 写操作只在用户点按后执行一次；网络中断导致结果未知时提示下拉刷新确认，从不自动重试。

enum PluginMarketText {
    static func trust(_ value: String?) -> String {
        switch value {
        case "official": "官方"
        case "community": "社区"
        default: "未审核"
        }
    }

    static func trustColor(_ value: String?) -> Color {
        switch value {
        case "official": .green
        case "community": .blue
        default: .orange
        }
    }

    static func kind(_ value: String?) -> String {
        switch value {
        case "interactive": "互动玩法"
        case "automation": "自动化"
        case "utility": "工具"
        case "middleware": "消息中间件"
        case "payment_source": "转账来源"
        case "ui": "界面扩展"
        default: value ?? "其它"
        }
    }

    static func status(_ value: String?) -> String {
        switch value {
        case "updated": "已更新"
        case "pinned": "已固定"
        case "skipped": "跳过"
        default: "失败"
        }
    }

    static func failure(_ error: Error) -> String {
        if (error as? APIError)?.isOutcomeUncertain == true {
            return "网络中断，无法确认操作是否完成。请下拉刷新，确认实际状态后再操作。"
        }
        return error.localizedDescription
    }
}

@MainActor @Observable
final class PluginMarketModel {
    private(set) var catalog: JSONValue?
    private(set) var loading = false
    private(set) var loaded = false
    private(set) var unsupported = false
    private(set) var loadError: String?
    /// 正在执行的操作：插件 key、"__all__"（全部更新）或 "source:<id>" / "settings"。
    private(set) var busy: String?
    private(set) var lastResult: JSONValue?
    var notice: String?
    var noticeIsProblem = false

    var items: [JSONValue] { catalog?["items"]?.arrayValue ?? [] }
    var sources: [JSONValue] { catalog?["sources"]?.arrayValue ?? [] }
    var rollouts: [JSONValue] { catalog?["rollouts"]?.arrayValue ?? [] }
    var updateCount: Int { catalog?["updates_available"]?.intValue ?? 0 }
    var concurrency: Int { catalog?["settings"]?["update_concurrency"]?.intValue ?? 3 }
    var healthWindow: Int { catalog?["settings"]?["health_window_minutes"]?.intValue ?? 10 }
    var watching: [JSONValue] { rollouts.filter { $0["status"]?.stringValue == "watching" } }
    var rolledBack: [JSONValue] {
        rollouts.filter { ["rolled_back", "rollback_failed"].contains($0["status"]?.stringValue ?? "") }
    }

    var serverVersion: String? { catalog?["server_version"]?.stringValue }
    var kindCounts: [PluginMarketInfo.KindCount] {
        PluginMarketInfo.kindCounts(items.compactMap { $0["kind"]?.stringValue })
    }

    func item(id: String) -> JSONValue? { items.first { $0["id"]?.stringValue == id } }

    func load(model: AppModel, refresh: Bool = false) async {
        loading = true
        defer { loading = false }
        do {
            catalog = try await model.request(path: "/api/plugin-market/catalog" + (refresh ? "?refresh=true" : ""))
            unsupported = false
            loadError = nil
        } catch {
            if case .server(let status, _)? = error as? APIError, status == 404 || status == 405 {
                unsupported = true
                loadError = nil
            } else {
                loadError = error.localizedDescription
            }
        }
        loaded = true
    }

    private func run(_ key: String, model: AppModel, success: String, _ work: () async throws -> Void) async {
        guard busy == nil else { return }
        busy = key
        defer { busy = nil }
        do {
            try await work()
            notice = success
            noticeIsProblem = false
        } catch {
            notice = PluginMarketText.failure(error)
            noticeIsProblem = true
        }
        await load(model: model)
    }

    /// `channel` 只在选了非稳定通道时才发给服务器（旧服务器不认识这个字段）。
    func install(_ item: JSONValue, channel: String? = nil, model: AppModel) async {
        let key = item["key"]?.stringValue ?? ""
        let title = item["name"]?["zh"]?.stringValue ?? key
        let picked = channel == "stable" ? nil : channel
        let label = picked.map { "（\(PluginMarketInfo.channelLabel($0))）" } ?? ""
        let body = PluginMarketInfo.installBody(sourceID: item["source_id"] ?? .null, key: key, channel: picked)
        await run(key, model: model, success: "已安装「\(title)」\(label)，到插件 ›「启用与配置」按账号启用。") {
            _ = try await model.request(path: "/api/plugin-market/install", method: "POST", body: body)
        }
    }

    /// 服务器 0.117.0+：把已安装插件切到另一个通道。与更新同一流程（保留旧版、健康观察、不健康自动回滚），结果形状与更新相同。
    func switchChannel(_ item: JSONValue, to channel: String, model: AppModel) async {
        guard busy == nil else { return }
        let key = item["key"]?.stringValue ?? ""
        busy = key
        defer { busy = nil }
        let body = PluginMarketInfo.channelBody(key: key, channel: channel, sourceID: item["source_id"]?.stringValue)
        do {
            let result = try await model.request(path: "/api/plugin-market/channel", method: "POST", body: body)
            lastResult = result
            if (result["failed"]?.intValue ?? 0) > 0 {
                notice = "切换到\(PluginMarketInfo.channelLabel(channel))失败（原因见插件市场里的更新结果）。"
                noticeIsProblem = true
            } else {
                notice = "已切换到\(PluginMarketInfo.channelLabel(channel))，正在观察新版本是否正常。"
                noticeIsProblem = false
            }
        } catch {
            notice = PluginMarketText.failure(error)
            noticeIsProblem = true
        }
        await load(model: model)
    }

    /// keys 为 nil 时更新全部有新版本且未固定的插件；服务器按“同时更新数量”并行执行。
    func update(keys: [String]?, model: AppModel) async {
        guard busy == nil else { return }
        busy = keys?.count == 1 ? keys?.first : "__all__"
        defer { busy = nil }
        do {
            var body: JSONValue = [:]
            if let keys { body = ["keys": .array(keys.map { JSONValue.string($0) })] }
            let result = try await model.request(path: "/api/plugin-market/update", method: "POST", body: body)
            lastResult = result
            let updated = result["updated"]?.intValue ?? 0
            let failed = result["failed"]?.intValue ?? 0
            notice = failed > 0 ? "已更新 \(updated) 个，\(failed) 个失败（原因见下方结果）。" : "已更新 \(updated) 个。"
            noticeIsProblem = failed > 0
        } catch {
            notice = PluginMarketText.failure(error)
            noticeIsProblem = true
        }
        await load(model: model)
    }

    func setPinned(_ key: String, pinned: Bool, model: AppModel) async {
        await run(key, model: model, success: pinned ? "已固定版本，全部更新与自动更新会跳过它。" : "已取消固定。") {
            _ = try await model.request(path: "/api/plugin-market/pins/\(ManagementText.pathSegment(key))", method: "PUT",
                                        body: ["pinned": .bool(pinned)])
        }
    }

    func rollback(_ key: String, model: AppModel) async {
        await run(key, model: model, success: "已回滚到更新前的版本。") {
            _ = try await model.request(path: "/api/plugin-market/rollouts/\(ManagementText.pathSegment(key))/rollback", method: "POST")
        }
    }

    func accept(_ key: String, model: AppModel) async {
        await run(key, model: model, success: "已确认新版本正常。") {
            _ = try await model.request(path: "/api/plugin-market/rollouts/\(ManagementText.pathSegment(key))/accept", method: "POST")
        }
    }

    func patchSource(_ id: String, body: JSONValue, model: AppModel) async {
        await run("source:" + id, model: model, success: "已保存来源设置。") {
            _ = try await model.request(path: "/api/plugin-market/sources/\(ManagementText.pathSegment(id))", method: "PATCH", body: body)
        }
    }

    func addSource(url: String, name: String, trust: String, model: AppModel) async {
        await run("source:new", model: model, success: "已添加来源。") {
            _ = try await model.request(path: "/api/plugin-market/sources", method: "POST",
                                        body: ["url": .string(url), "name": name.isEmpty ? .null : .string(name), "trust": .string(trust)])
        }
    }

    func deleteSource(_ id: String, model: AppModel) async {
        await run("source:" + id, model: model, success: "已删除来源。") {
            _ = try await model.request(path: "/api/plugin-market/sources/\(ManagementText.pathSegment(id))", method: "DELETE")
        }
    }

    func saveSettings(concurrency: Int, window: Int, model: AppModel) async {
        await run("settings", model: model, success: "已保存更新参数。") {
            _ = try await model.request(path: "/api/plugin-market/settings", method: "PUT",
                                        body: ["update_concurrency": .integer(Int64(concurrency)), "health_window_minutes": .integer(Int64(window))])
        }
    }
}

/// 插件市场的图标：索引有 https 图片图标（服务器 0.117.0+ 的 `icon_url`）时用图片，加载中或失败退回 emoji；
/// emoji 放在圆角方块里；都没有时退回插件商店的首字图标。
private struct PluginMarketArtwork: View {
    let item: JSONValue
    var size: CGFloat = 52

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.225, style: .continuous)
        if let url = PluginMarketDetails.httpsURL(item["icon_url"]?.stringValue) {
            AsyncImage(url: url) { phase in
                Group {
                    if let image = phase.image {
                        image.resizable().scaledToFill().frame(width: size, height: size).clipShape(shape)
                    } else {
                        fallback
                    }
                }
            }
            .frame(width: size, height: size)
            .accessibilityHidden(true)
        } else {
            fallback
        }
    }

    @ViewBuilder private var fallback: some View {
        let key = item["key"]?.stringValue ?? ""
        let title = item["name"]?["zh"]?.stringValue ?? key
        if let icon = item["icon"]?.stringValue, !icon.isEmpty {
            let shape = RoundedRectangle(cornerRadius: size * 0.225, style: .continuous)
            Text(icon)
                .font(.system(size: size * 0.5))
                .frame(width: size, height: size)
                .background(Color(.secondarySystemGroupedBackground), in: shape)
                .overlay(shape.strokeBorder(Color.primary.opacity(0.08), lineWidth: 0.5))
                .accessibilityHidden(true)
        } else {
            PluginArtwork(key: key, title: title, category: item["kind"]?.stringValue, size: size)
        }
    }
}

/// 分类标签（带数量）：像应用商店那样横向排列，选中的高亮。整块 44pt 高，方便点按。
private struct PluginMarketKindChip: View {
    let title: String
    let count: Int
    let selected: Bool
    let identifier: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Text(title)
                Text("\(count)").monospacedDigit().opacity(0.75)
            }
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 14)
            .frame(minHeight: 34)
            .foregroundStyle(selected ? Color.white : Color.primary)
            .background(selected ? Color.accentColor : Color(.tertiarySystemFill), in: Capsule())
            .padding(.vertical, 5)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title)，\(count) 个")
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier(identifier)
    }
}

struct PluginMarketView: View {
    @Environment(AppModel.self) private var model
    @State private var market = PluginMarketModel()
    @State private var search = ""
    @State private var searching = false
    @State private var kind = "all"
    @State private var onlyUpdates = false
    @State private var confirmUpdateAll = false
    @State private var detailID: String?
    /// 待确认安装的未审核插件（列表里点「获取」时）。
    @State private var pendingInstall: JSONValue?

    private var filtered: [JSONValue] {
        market.items.filter { item in
            if kind != "all" && item["kind"]?.stringValue != kind { return false }
            if onlyUpdates && !(item["update_available"]?.boolValue == true && item["pinned_version"]?.stringValue == nil) { return false }
            return PluginMarketInfo.matches(item, query: search)
        }
    }

    private func requestInstall(_ item: JSONValue) {
        if PluginMarketInfo.needsInstallConfirmation(trust: item["trust"]?.stringValue) {
            pendingInstall = item
        } else {
            Task { await market.install(item, model: model) }
        }
    }

    var body: some View {
        List {
            if market.unsupported {
                Section {
                    ContentUnavailableView("服务器版本过旧", systemImage: "server.rack",
                                           description: Text("插件市场需要 TelePilot 服务器 0.113.0 或更新。请先更新服务器；在此之前仍可在“发现”里用插件仓库、Git 地址或文件安装插件。"))
                        .accessibilityIdentifier("plugins.market.unsupported")
                }
            } else {
                content
            }
        }
        .pageSearch(text: $search, isActive: $searching, prompt: "搜索名称、简介、指令或标签", identifier: "plugins.market.search")
        .navigationTitle("插件市场")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await market.load(model: model, refresh: true) }
        .task { if !market.loaded { await market.load(model: model) } }
        .navigationDestination(item: $detailID) { id in
            PluginMarketDetailView(market: market, itemID: id)
        }
        .confirmationDialog("更新全部 \(market.updateCount) 个插件？", isPresented: $confirmUpdateAll, titleVisibility: .visible) {
            Button("全部更新") { Task { await market.update(keys: nil, model: model) } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("服务器会同时更新 \(market.concurrency) 个，单个失败不影响其它；更新后观察 \(market.healthWindow) 分钟，不健康会自动回滚。固定版本的插件会跳过。")
        }
        .alert(pendingInstallTitle, isPresented: Binding(get: { pendingInstall != nil }, set: { if !$0 { pendingInstall = nil } })) {
            Button("仍然安装", role: .destructive) {
                if let item = pendingInstall { Task { await market.install(item, model: model) } }
                pendingInstall = nil
            }
            Button("取消", role: .cancel) { pendingInstall = nil }
        } message: {
            Text(PluginMarketInfo.unreviewedInstallWarning)
        }
        .accessibilityIdentifier("plugins.market.screen")
    }

    private var pendingInstallTitle: String {
        let title = pendingInstall?["name"]?["zh"]?.stringValue ?? pendingInstall?["key"]?.stringValue ?? ""
        return "安装未审核的插件「\(title)」？"
    }

    @ViewBuilder private var kindChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                PluginMarketKindChip(title: "全部", count: market.items.count, selected: kind == "all",
                                     identifier: "plugins.market.kind.all") { kind = "all" }
                ForEach(market.kindCounts) { entry in
                    PluginMarketKindChip(title: PluginMarketText.kind(entry.kind), count: entry.count, selected: kind == entry.kind,
                                         identifier: "plugins.market.kind." + entry.kind) { kind = entry.kind }
                }
            }
        }
        .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
        .accessibilityIdentifier("plugins.market.kinds")
    }

    @ViewBuilder private var content: some View {
        Section {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(market.updateCount > 0 ? "\(market.updateCount) 个插件可更新" : "所有已安装插件都是最新的")
                        .font(.headline)
                        .accessibilityIdentifier("plugins.market.updateCount")
                    Text("同时更新 \(market.concurrency) 个 · 更新后观察 \(market.healthWindow) 分钟")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if market.busy == "__all__" {
                    ProgressView()
                } else {
                    Button("全部更新") { confirmUpdateAll = true }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.capsule)
                        .disabled(market.updateCount == 0 || market.busy != nil)
                        .accessibilityIdentifier("plugins.market.updateAll")
                }
            }
            if let notice = market.notice {
                Label(notice, systemImage: market.noticeIsProblem ? "exclamationmark.triangle" : "checkmark.shield")
                    .font(.subheadline)
                    .foregroundStyle(market.noticeIsProblem ? Color.orange : Color.secondary)
                    .accessibilityIdentifier("plugins.market.notice")
            }
        }
        if let result = market.lastResult, let rows = result["items"]?.arrayValue, !rows.isEmpty {
            Section("上次更新结果（同时 \(result["concurrency"]?.intValue ?? market.concurrency) 个）") {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    let key = row["key"]?.stringValue ?? ""
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(row["display_name"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } ?? key).font(.subheadline.weight(.semibold))
                            Spacer()
                            Text(PluginMarketText.status(row["status"]?.stringValue))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(row["status"]?.stringValue == "failed" ? Color.red : Color.secondary)
                        }
                        Text("\(row["from_version"]?.stringValue ?? "?") → \(row["to_version"]?.stringValue ?? "?")"
                             + (row["message"]?.stringValue.map { " · " + $0 } ?? "")
                             + (row["health_check"]?.boolValue == true ? " · 进入健康观察" : ""))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("plugins.market.result." + key)
                }
            }
        }
        if !market.watching.isEmpty {
            Section {
                ForEach(Array(market.watching.enumerated()), id: \.offset) { _, rollout in
                    let key = rollout["key"]?.stringValue ?? ""
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(key)：\(rollout["from_version"]?.stringValue ?? "?") → \(rollout["to_version"]?.stringValue ?? "?")")
                            .font(.subheadline.weight(.semibold))
                        HStack {
                            Button("确认正常") { Task { await market.accept(key, model: model) } }
                                .buttonStyle(.bordered)
                            Button("立即回滚", role: .destructive) { Task { await market.rollback(key, model: model) } }
                                .buttonStyle(.bordered)
                                .accessibilityIdentifier("plugins.market.rollback." + key)
                        }
                        .disabled(market.busy != nil)
                    }
                }
            } header: { Text("正在观察的更新") } footer: {
                Text("观察期内出现加载失败、熔断或钩子失败率明显升高，会自动回滚到旧版本并告警。")
            }
        }
        if !market.rolledBack.isEmpty {
            Section("已自动回滚") {
                ForEach(Array(market.rolledBack.enumerated()), id: \.offset) { _, rollout in
                    Label("\(rollout["key"]?.stringValue ?? "")：\(rollout["to_version"]?.stringValue ?? "?") → \(rollout["from_version"]?.stringValue ?? "?")（\(rollout["reason"]?.stringValue ?? "不健康")）",
                          systemImage: "arrow.uturn.backward")
                        .font(.subheadline)
                        .accessibilityIdentifier("plugins.market.rolledBack." + (rollout["key"]?.stringValue ?? ""))
                }
            }
        }
        if let error = market.loadError {
            Section { NoticeView(title: "读取插件市场失败", message: error) }
        }
        if !market.loaded && market.loading {
            Section { PluginStoreSkeleton() } header: { Text("正在读取插件市场…") }
        } else if market.loaded {
            Section {
                if !market.items.isEmpty { kindChips }
                Toggle("只看可更新", isOn: $onlyUpdates)
                    .accessibilityIdentifier("plugins.market.onlyUpdates")
            }
            Section {
                if filtered.isEmpty {
                    if search.isEmpty {
                        ContentUnavailableView("没有符合条件的插件", systemImage: "shippingbox", description: Text("换个筛选条件，或在下方添加更多来源。"))
                    } else {
                        ContentUnavailableView.search(text: search)
                    }
                }
                ForEach(Array(filtered.enumerated()), id: \.offset) { _, item in row(item) }
            } header: {
                Text("插件（\(filtered.count)）")
            }
        }
        Section {
            NavigationLink {
                PluginMarketSourcesView(market: market)
            } label: {
                LabeledContent {
                    Text("\(market.sources.count) 个")
                } label: {
                    Label("来源管理", systemImage: "antenna.radiowaves.left.and.right")
                }
            }
            .accessibilityIdentifier("plugins.market.sources")
            NavigationLink {
                PluginMarketSettingsView(market: market)
            } label: {
                Label("更新参数", systemImage: "arrow.clockwise")
            }
            .accessibilityIdentifier("plugins.market.settings")
        } footer: {
            Text("官方源来自 TelePilot 插件库；也可以添加自己的来源。只有“官方”等级的来源可以打开自动更新（默认关闭）。")
        }
    }

    @ViewBuilder private func row(_ item: JSONValue) -> some View {
        let key = item["key"]?.stringValue ?? ""
        let id = item["id"]?.stringValue ?? key
        let installed = item["installed"]?.objectValue
        let pinned = item["pinned_version"]?.stringValue
        let canUpdate = item["update_available"]?.boolValue == true && pinned == nil
        let compatible = item["compatible"]?.boolValue != false
        HStack(spacing: 12) {
            Button { detailID = id } label: {
                HStack(spacing: 12) {
                    PluginMarketArtwork(item: item)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(item["name"]?["zh"]?.stringValue ?? key).font(.body.weight(.semibold)).foregroundStyle(.primary).lineLimit(1)
                            Text(PluginMarketText.trust(item["trust"]?.stringValue))
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(PluginMarketText.trustColor(item["trust"]?.stringValue))
                            if let channel = item["channel"]?.stringValue, channel != "stable" {
                                Text(PluginMarketInfo.channelLabel(channel))
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(.orange)
                            }
                        }
                        Text(item["summary"]?.stringValue ?? "").font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                        Text(detail(item, installed: installed, pinned: pinned)).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("plugins.market.row." + key)
            if installed == nil {
                PluginCapsuleButton(title: "获取", prominent: true, running: market.busy == key, disabled: market.busy != nil || !compatible,
                                    identifier: "plugins.market.action." + key) {
                    requestInstall(item)
                }
            } else if canUpdate {
                PluginCapsuleButton(title: "更新", prominent: true, running: market.busy == key || market.busy == "__all__",
                                    disabled: market.busy != nil, identifier: "plugins.market.action." + key) {
                    Task { await market.update(keys: [key], model: model) }
                }
            } else {
                PluginCapsuleButton(title: pinned == nil ? "最新" : "已固定", disabled: true, identifier: "plugins.market.action." + key) {}
            }
        }
        .padding(.vertical, 2)
    }

    private func detail(_ item: JSONValue, installed: [String: JSONValue]?, pinned: String?) -> String {
        var parts = [PluginMarketText.kind(item["kind"]?.stringValue), "v" + (item["version"]?.stringValue ?? "?")]
        if let version = installed?["version"]?.stringValue { parts.append("已装 v" + version) }
        if let pinned { parts.append("固定 " + pinned) }
        if item["compatible"]?.boolValue == false { parts.append("不兼容") }
        parts.append(item["source_name"]?.stringValue ?? "")
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

private struct PluginMarketDetailView: View {
    let market: PluginMarketModel
    let itemID: String

    var body: some View {
        if let item = market.item(id: itemID) {
            PluginMarketDetailContent(market: market, item: item)
        } else {
            ContentUnavailableView("插件已不在市场里", systemImage: "shippingbox", description: Text("返回后下拉刷新再试。"))
        }
    }
}

/// 详情页（像应用商店）：头部与主操作、信任说明、简介、截图、怎么用（预览）、版本与更新说明、版本通道、权限大白话、
/// 兼容性、更多信息（作者 / 许可证 / 来源 / 主页）、固定版本。旧服务器缺的字段对应区块不显示。
private struct PluginMarketDetailContent: View {
    let market: PluginMarketModel
    let item: JSONValue
    @Environment(AppModel.self) private var model
    @State private var installChannel = "stable"
    @State private var confirmInstall = false
    @State private var switchTarget: PluginMarketChannel?

    private var key: String { item["key"]?.stringValue ?? "" }
    private var title: String { item["name"]?["zh"]?.stringValue ?? key }
    private var trust: String? { item["trust"]?.stringValue }
    private var installed: [String: JSONValue]? { item["installed"]?.objectValue }
    private var sameSource: Bool { installed?["same_source"]?.boolValue == true }
    private var pinned: String? { item["pinned_version"]?.stringValue }
    private var compatible: Bool { item["compatible"]?.boolValue != false }
    private var version: String { item["version"]?.stringValue ?? "?" }
    private var details: PluginMarketDetails { PluginMarketDetails(item) }
    /// 安装时要传给服务器的通道：只有插件真有多个通道、且选了非稳定通道时才传。
    private var pickedChannel: String? {
        details.hasMultipleChannels && installChannel != "stable" ? installChannel : nil
    }

    var body: some View {
        let info = details
        List {
            headerSection(info)
            descriptionSection(info)
            if !info.screenshots.isEmpty { screenshotsSection(info) }
            if info.hasUsagePreview { usageSection(info) }
            versionSection(info)
            if info.hasMultipleChannels { channelsSection(info) }
            permissionsSection
            compatibilitySection
            moreSection(info)
            if installed != nil && sameSource { pinSection }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .alert("安装未审核的插件「\(title)」？", isPresented: $confirmInstall) {
            Button("仍然安装", role: .destructive) {
                let channel = pickedChannel
                Task { await market.install(item, channel: channel, model: model) }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text(PluginMarketInfo.unreviewedInstallWarning)
        }
        .alert(switchTitle, isPresented: Binding(get: { switchTarget != nil }, set: { if !$0 { switchTarget = nil } }),
               presenting: switchTarget) { target in
            Button("切换", role: isDowngrade(target) ? ButtonRole.destructive : nil) {
                Task { await market.switchChannel(item, to: target.name, model: model) }
                switchTarget = nil
            }
            Button("取消", role: .cancel) { switchTarget = nil }
        } message: { target in
            Text(switchMessage(target))
        }
        .accessibilityIdentifier("plugins.market.detail")
    }

    // MARK: 头部与主操作

    @ViewBuilder private func headerSection(_ details: PluginMarketDetails) -> some View {
        Section {
            HStack(spacing: 14) {
                PluginMarketArtwork(item: item, size: 72)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(title).font(.title3.weight(.semibold))
                        if let channel = details.channel, channel != "stable" {
                            Text(PluginMarketInfo.channelLabel(channel))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.orange)
                        }
                    }
                    Text(headerLine).font(.subheadline).foregroundStyle(.secondary)
                    Text(PluginMarketText.trust(trust))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(PluginMarketText.trustColor(trust))
                        .accessibilityIdentifier("plugins.market.detail.trust")
                }
            }
            primaryAction(details)
            if let notice = market.notice {
                Text(notice).font(.subheadline).foregroundStyle(market.noticeIsProblem ? Color.orange : Color.secondary)
                    .accessibilityIdentifier("plugins.market.detail.notice")
            }
            Label(PluginMarketInfo.trustDescription(trust),
                  systemImage: trust == "official" ? "checkmark.shield" : "exclamationmark.shield")
                .font(.footnote)
                .foregroundStyle(trust == "official" || trust == "community" ? Color.secondary : Color.orange)
                .accessibilityIdentifier("plugins.market.detail.trustNote")
        }
    }

    private var headerLine: String {
        var parts = [PluginMarketText.kind(item["kind"]?.stringValue), "v" + version]
        if let author = item["author"]?.stringValue, !author.isEmpty { parts.append(author) }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder private func primaryAction(_ details: PluginMarketDetails) -> some View {
        if installed == nil {
            let channelSuffix = pickedChannel.map { "（\(PluginMarketInfo.channelLabel($0))）" } ?? ""
            Button {
                if PluginMarketInfo.needsInstallConfirmation(trust: trust) {
                    confirmInstall = true
                } else {
                    let channel = pickedChannel
                    Task { await market.install(item, channel: channel, model: model) }
                }
            } label: {
                Label("安装" + channelSuffix, systemImage: "arrow.down.circle")
            }
            .disabled(market.busy != nil || !compatible)
            .accessibilityIdentifier("plugins.market.detail.install")
        } else if item["update_available"]?.boolValue == true && pinned == nil {
            Button {
                Task { await market.update(keys: [key], model: model) }
            } label: {
                Label("更新到 v\(details.updateVersion ?? version)", systemImage: "arrow.down.circle")
            }
            .disabled(market.busy != nil)
            .accessibilityIdentifier("plugins.market.detail.update")
        } else {
            Label("已安装 v\(installed?["version"]?.stringValue ?? "?")" + (pinned == nil ? "，已是最新" : "，已固定版本"),
                  systemImage: "checkmark.circle")
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("plugins.market.detail.installed")
        }
        if market.busy == key {
            ProgressView("正在处理…")
        }
    }

    // MARK: 内容

    @ViewBuilder private func descriptionSection(_ details: PluginMarketDetails) -> some View {
        let text = details.description ?? item["summary"]?.stringValue ?? ""
        if !text.isEmpty {
            Section("简介") {
                Text(text)
                    .font(.body)
                    .accessibilityIdentifier("plugins.market.detail.description")
                if let english = details.summaryEnglish {
                    Text(english).font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder private func screenshotsSection(_ details: PluginMarketDetails) -> some View {
        Section("截图") {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(Array(details.screenshots.enumerated()), id: \.offset) { index, url in
                        Link(destination: url) {
                            AsyncImage(url: url) { phase in
                                Group {
                                    if let image = phase.image {
                                        image.resizable().scaledToFit()
                                    } else {
                                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                                            .fill(Color(.tertiarySystemFill))
                                            .frame(width: 140)
                                            .overlay(Image(systemName: "photo").foregroundStyle(.secondary))
                                    }
                                }
                            }
                            .frame(maxWidth: 260)
                            .frame(height: 220)
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        }
                        .accessibilityLabel("\(title) 截图 \(index + 1)")
                        .accessibilityIdentifier("plugins.market.detail.screenshot.\(index)")
                    }
                }
                .padding(.vertical, 4)
            }
            .accessibilityIdentifier("plugins.market.detail.screenshots")
        }
    }

    @ViewBuilder private func usageSection(_ details: PluginMarketDetails) -> some View {
        Section {
            if !details.commands.isEmpty {
                Text(details.commands.map { "," + $0 }.joined(separator: "   "))
                    .font(.callout.monospaced())
                    .accessibilityIdentifier("plugins.market.detail.commands")
            }
            if let usage = details.usage {
                Text(PluginMarketInfo.renderUsage(usage))
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .accessibilityIdentifier("plugins.market.detail.usage")
            }
        } header: {
            Text(details.screenshots.isEmpty ? "怎么用（预览）" : "怎么用")
        } footer: {
            Text("示例按默认指令前缀「,」显示，实际以账号设置为准。")
        }
    }

    @ViewBuilder private func versionSection(_ details: PluginMarketDetails) -> some View {
        Section("版本") {
            LabeledContent("最新版本", value: version)
            LabeledContent("已安装", value: installed?["version"]?.stringValue ?? "未安装")
            if installed != nil, let channel = details.channel, details.hasMultipleChannels {
                LabeledContent("当前通道", value: PluginMarketInfo.channelLabel(channel))
            }
            if let notes = details.releaseNotes {
                VStack(alignment: .leading, spacing: 4) {
                    Text("v\(version) 更新内容" + (details.releasedAt.map { "（\($0)）" } ?? ""))
                        .font(.subheadline.weight(.semibold))
                    Text(notes).font(.callout).foregroundStyle(.secondary)
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("plugins.market.detail.releaseNotes")
            }
        }
    }

    @ViewBuilder private func channelsSection(_ details: PluginMarketDetails) -> some View {
        Section {
            ForEach(details.channels) { channel in
                channelRow(channel, details: details)
            }
            if installed != nil, pinned != nil {
                Text("已固定版本，先取消固定才能切换通道。").font(.footnote).foregroundStyle(.secondary)
            }
        } header: {
            Text("版本通道")
        } footer: {
            Text("稳定版适合日常使用；测试版能先用上新功能，但可能有问题，也不会自动更新。切换后同样会观察一段时间，出问题自动回滚。")
        }
    }

    @ViewBuilder private func channelRow(_ channel: PluginMarketChannel, details: PluginMarketDetails) -> some View {
        let versionText = channel.version.map { "v" + $0 } ?? "版本未知"
        if installed != nil {
            let current = details.channel == channel.name
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(PluginMarketInfo.channelLabel(channel.name))
                    Text(versionText).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if current {
                    Text("当前").font(.caption.weight(.semibold)).foregroundStyle(.green)
                        .accessibilityIdentifier("plugins.market.detail.current." + channel.name)
                } else if sameSource {
                    Button("切换") { switchTarget = channel }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                        .disabled(market.busy != nil || pinned != nil || !compatible)
                        .accessibilityIdentifier("plugins.market.detail.switch." + channel.name)
                }
            }
        } else {
            let selected = installChannel == channel.name
            Button {
                installChannel = channel.name
            } label: {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(PluginMarketInfo.channelLabel(channel.name)).foregroundStyle(.primary)
                        Text(versionText).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? Color.accentColor : Color.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityIdentifier("plugins.market.detail.pick." + channel.name)
        }
    }

    @ViewBuilder private var permissionsSection: some View {
        let permissions = (item["permissions"]?.arrayValue ?? []).compactMap(\.stringValue)
        if !permissions.isEmpty {
            Section {
                ForEach(permissions, id: \.self) { permission in
                    VStack(alignment: .leading, spacing: 2) {
                        if let plain = PluginMarketInfo.permissionDescription(permission) {
                            Text(plain)
                            Text(permission).font(.caption.monospaced()).foregroundStyle(.secondary)
                        } else {
                            Text(permission).font(.callout.monospaced())
                        }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("plugins.market.detail.permission." + permission)
                }
            } header: {
                Text("需要的权限")
            } footer: {
                Text("插件只能使用这里声明的权限。")
            }
        }
    }

    @ViewBuilder private var compatibilitySection: some View {
        Section("兼容性") {
            if compatible {
                Label(compatibilityText, systemImage: "checkmark.seal")
                    .accessibilityIdentifier("plugins.market.detail.compat")
            } else {
                Label(item["incompatible_reason"]?.stringValue ?? "与当前服务器不兼容", systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                    .accessibilityIdentifier("plugins.market.detail.compat")
            }
            if installed != nil, !sameSource {
                Text("已安装的版本来自别的仓库，这里不提示更新，也不能切换通道。").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var compatibilityText: String {
        var text = "兼容当前服务器" + (market.serverVersion.map { "（\($0)）" } ?? "")
        if let minimum = item["min_telepilot_version"]?.stringValue { text += "，最低需要 \(minimum)" }
        if let api = item["plugin_api"]?.stringValue { text += "，Plugin API \(api)" }
        return text + "。"
    }

    @ViewBuilder private func moreSection(_ details: PluginMarketDetails) -> some View {
        Section("更多信息") {
            if let author = item["author"]?.stringValue, !author.isEmpty {
                LabeledContent("作者", value: author)
            }
            LabeledContent("来源", value: item["source_name"]?.stringValue ?? "—")
            if let license = details.license {
                LabeledContent("许可证", value: license)
                    .accessibilityIdentifier("plugins.market.detail.license")
            }
            if let homepage = details.homepage {
                Link(destination: homepage) {
                    Label("插件主页", systemImage: "safari")
                }
                .accessibilityIdentifier("plugins.market.detail.homepage")
            }
        }
    }

    @ViewBuilder private var pinSection: some View {
        Section {
            Toggle(isOn: Binding(get: { pinned != nil }, set: { value in
                Task { await market.setPinned(key, pinned: value, model: model) }
            })) {
                Label(pinned.map { "固定在 \($0)" } ?? "固定版本", systemImage: pinned == nil ? "lock.open" : "lock")
            }
            .disabled(market.busy != nil)
            .accessibilityIdentifier("plugins.market.detail.pin")
        } footer: {
            Text("固定版本后，“全部更新”和自动更新都会跳过这个插件。安装或更新后会观察一段时间，出问题自动回滚到旧版本。")
        }
    }

    // MARK: 切换通道确认

    private var switchTitle: String {
        "把「\(title)」切换到\(PluginMarketInfo.channelLabel(switchTarget?.name))？"
    }

    private func isDowngrade(_ target: PluginMarketChannel) -> Bool {
        PluginMarketInfo.isChannelDowngrade(installed: installed?["version"]?.stringValue, target: target.version)
    }

    private func switchMessage(_ target: PluginMarketChannel) -> String {
        let label = PluginMarketInfo.channelLabel(target.name)
        var lines = ["将安装\(label) " + (target.version.map { "v" + $0 } ?? "的最新版本")
                     + "（当前 v\(installed?["version"]?.stringValue ?? "?")）。"]
        if isDowngrade(target) {
            lines.append("这是降级：插件的数据不会跟着回退，个别插件可能读不懂新版本写下的数据。")
        }
        if target.name != "stable" { lines.append("测试版可能有问题，不会自动更新。") }
        lines.append("切换后同样会观察一段时间，出问题自动回滚。")
        return lines.joined(separator: "\n")
    }
}

private struct PluginMarketSourcesView: View {
    let market: PluginMarketModel
    @Environment(AppModel.self) private var model
    @State private var url = ""
    @State private var name = ""
    @State private var trust = "unreviewed"
    @State private var deleteID: String?

    var body: some View {
        List {
            ForEach(Array(market.sources.enumerated()), id: \.offset) { _, source in
                sourceSection(source)
            }
            Section {
                StackedTextField(title: "仓库地址", text: $url, prompt: "https://example.com/registry.json", lines: 1...3,
                                 keyboard: .URL, identifier: "plugins.market.source.url")
                LabeledTextField(title: "名称", text: $name, optional: true, capitalization: .sentences,
                                 identifier: "plugins.market.source.name")
                Picker("信任等级", selection: $trust) {
                    Text("未审核").tag("unreviewed")
                    Text("社区").tag("community")
                    Text("官方").tag("official")
                }
                .accessibilityIdentifier("plugins.market.source.trust")
                Button("添加来源") {
                    let value = url.trimmingCharacters(in: .whitespaces)
                    Task {
                        await market.addSource(url: value, name: name.trimmingCharacters(in: .whitespaces), trust: trust, model: model)
                        if !market.noticeIsProblem { url = ""; name = "" }
                    }
                }
                .disabled(url.trimmingCharacters(in: .whitespaces).isEmpty || market.busy != nil)
                .accessibilityIdentifier("plugins.market.source.add")
            } header: { Text("添加来源") } footer: {
                Text(market.notice ?? "填 registry.json 的 https 地址。自定义来源不能把自己标成比你选的等级更高。")
            }
        }
        .navigationTitle("来源管理")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("删除这个来源？", isPresented: Binding(get: { deleteID != nil }, set: { if !$0 { deleteID = nil } }), titleVisibility: .visible) {
            Button("删除", role: .destructive) {
                if let id = deleteID { Task { await market.deleteSource(id, model: model) } }
                deleteID = nil
            }
            Button("取消", role: .cancel) { deleteID = nil }
        } message: {
            Text("已经安装的插件不会被卸载。")
        }
        .accessibilityIdentifier("plugins.market.sources.screen")
    }

    @ViewBuilder private func sourceSection(_ source: JSONValue) -> some View {
        let id = source["id"]?.stringValue ?? ""
        let builtin = source["builtin"]?.boolValue == true
        let available = source["available"]?.boolValue != false
        let enabled = source["enabled"]?.boolValue == true
        let official = source["trust"]?.stringValue == "official"
        Section {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(source["name"]?.stringValue ?? id).font(.body.weight(.semibold))
                    Text(PluginMarketText.trust(source["trust"]?.stringValue))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(PluginMarketText.trustColor(source["trust"]?.stringValue))
                    if builtin { Text("内置").font(.caption).foregroundStyle(.secondary) }
                }
                Text(source["url"]?.stringValue ?? "").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                Text(status(source)).font(.caption).foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("plugins.market.source." + id)
            Toggle("启用", isOn: Binding(get: { enabled }, set: { value in
                Task { await market.patchSource(id, body: ["enabled": .bool(value)], model: model) }
            }))
            .disabled(!available || market.busy != nil)
            .accessibilityIdentifier("plugins.market.source.enabled." + id)
            if official {
                Toggle("自动更新", isOn: Binding(get: { source["auto_update"]?.boolValue == true }, set: { value in
                    Task { await market.patchSource(id, body: ["auto_update": .bool(value)], model: model) }
                }))
                .disabled(!enabled || market.busy != nil)
                .accessibilityIdentifier("plugins.market.source.auto." + id)
            }
            if !builtin {
                Button("删除来源", role: .destructive) { deleteID = id }
                    .accessibilityIdentifier("plugins.market.source.delete." + id)
            }
        } footer: {
            if official {
                Text("打开自动更新后，服务器按“远程插件检查更新”的周期自动更新这个来源里有新版本、没有固定版本的插件；更新后观察期内不健康会自动回滚。")
            }
        }
    }

    private func status(_ source: JSONValue) -> String {
        let count = source["plugin_count"]?.intValue ?? 0
        if source["enabled"]?.boolValue != true {
            return source["available"]?.boolValue == false ? "服务器已关闭官方源" : "已停用"
        }
        switch source["status"]?.stringValue {
        case "ok": return "\(count) 个插件"
        case "fallback": return "拉取失败，使用内置副本（\(count) 个）"
        case "stale": return "拉取失败，显示上次结果（\(count) 个）"
        default: return "拉取失败：" + (source["error"]?.stringValue ?? "未知错误")
        }
    }
}

private struct PluginMarketSettingsView: View {
    let market: PluginMarketModel
    @Environment(AppModel.self) private var model
    @State private var concurrency = 3
    @State private var window = 10
    @State private var prepared = false

    var body: some View {
        Form {
            Section {
                Stepper("同时更新 \(concurrency) 个", value: $concurrency, in: 1...8)
                    .accessibilityIdentifier("plugins.market.concurrency")
                Stepper("更新后观察 \(window) 分钟", value: $window, in: 3...60)
                    .accessibilityIdentifier("plugins.market.window")
            } footer: {
                Text("多个插件一起更新时同时进行的数量（默认 3），以及更新后观察多久再确认健康（默认 10 分钟）。")
            }
            Section {
                Button("保存") { Task { await market.saveSettings(concurrency: concurrency, window: window, model: model) } }
                    .disabled(market.busy != nil)
                    .accessibilityIdentifier("plugins.market.settings.save")
            } footer: {
                if let notice = market.notice { Text(notice) }
            }
        }
        .navigationTitle("更新参数")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            guard !prepared else { return }
            prepared = true
            concurrency = market.concurrency
            window = market.healthWindow
        }
    }
}
