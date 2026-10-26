import SwiftUI
import TelePilotCore

// App Store 式的插件「发现 / 已安装（更新）/ 产品页」。
// 数据全部来自服务器现有接口：/api/feature-matrix、/api/plugins/installed-overview、/api/plugins/installed-packages、
// /api/plugin-repos（及各仓库的插件目录）、/api/plugins/install/{key}/history 与 changelog。
// 写操作（安装、更新、检查更新、停用、卸载）只在用户点按后执行一次，网络中断时标为“结果待确认”，从不自动重试。

// MARK: - 数据

/// 插件仓库（或本地参考插件目录）里的一项。
struct PluginCatalogEntry: Identifiable, Hashable {
    /// 仓库 ID；本地参考插件为 nil。
    let repoID: Int?
    let repoName: String
    let plugin: JSONValue

    var name: String { plugin["name"]?.stringValue ?? "" }
    var id: String { (repoID.map { String($0) } ?? "local") + "/" + name }
    var title: String { plugin["display_name"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } ?? name }
    var summary: String? {
        [plugin["description"], plugin["usage"]].compactMap { $0?.stringValue }.first { !$0.isEmpty }
    }
    var version: String? { plugin["version"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
    var author: String? { plugin["author"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
    var category: String? { plugin["category"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
    var tags: [String] { (plugin["tags"]?.arrayValue ?? []).compactMap(\.stringValue) }
    var permissions: [String] { (plugin["permissions"]?.arrayValue ?? []).compactMap(\.stringValue) }
    var minVersion: String? { plugin["min_telepilot_version"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } }
    var installedFlag: Bool { plugin["installed"]?.boolValue == true }
    var sourceMatches: Bool { plugin["source_matches"]?.boolValue == true }
    /// 旧服务器没有 `changelog` 字段时为空，界面不显示“新功能”。
    var releases: [PluginRelease] { plugin["changelog"]?.stringValue.map { PluginReleaseNotes.parse($0) } ?? [] }
    var installPath: String {
        let segment = ManagementText.pathSegment(name)
        if let repoID { return "/api/plugin-repos/\(repoID)/plugins/\(segment)/install" }
        return "/api/plugin-repos/local/plugins/\(segment)/install"
    }
}

struct PluginUpdateRecord: Identifiable, Hashable {
    let key: String
    let title: String
    let from: String?
    let to: String?
    let date: Date
    var id: String { key + "@" + (to ?? "") }
}

enum PluginOperationPhase: Equatable {
    case running(String)
    case succeeded(String)
    case failed(String)
    case uncertain(String)

    var isRunning: Bool { if case .running = self { true } else { false } }
    /// 进行中或结果待确认时不允许再次提交（必须先刷新确认真实状态）。
    var blocksAction: Bool {
        switch self {
        case .running, .uncertain: true
        case .succeeded, .failed: false
        }
    }
}

enum PluginStoreRoute: Hashable {
    case product(String)
    case catalog(PluginCatalogEntry)
    case open(String)
}

@MainActor @Observable
final class PluginStoreModel {
    private(set) var features: [JSONValue] = []
    private(set) var overview: [String: JSONValue] = [:]
    private(set) var packages: [String: JSONValue] = [:]
    private(set) var loading = false
    private(set) var loaded = false
    private(set) var loadError: String?

    private(set) var repos: [JSONValue] = []
    private(set) var catalog: [PluginCatalogEntry] = []
    private(set) var catalogNotices: [String] = []
    private(set) var catalogLoading = false
    private(set) var catalogLoaded = false
    private(set) var catalogError: String?

    private(set) var history: [PluginUpdateRecord] = []
    private(set) var historyLoaded = false
    private(set) var sessionUpdates: [PluginUpdateRecord] = []
    /// 已安装版本的 CHANGELOG（按插件缓存；没有提供时为空数组）。
    private(set) var installedNotes: [String: [PluginRelease]] = [:]

    /// 键：已安装插件为插件 key（更新）；仓库条目为 `PluginCatalogEntry.id`（安装）。
    private(set) var operations: [String: PluginOperationPhase] = [:]
    private(set) var bulkRunning = false
    private(set) var bulkMessage: String?
    private(set) var bulkProblem = false
    private(set) var checking = false
    private(set) var checkMessage: String?
    private(set) var checkError: String?
    private(set) var checkUncertain = false

    // MARK: 派生

    static func key(_ feature: JSONValue) -> String { feature["key"]?.stringValue ?? "" }
    static func title(_ feature: JSONValue) -> String { ManagementText.title(feature) }

    func feature(_ key: String) -> JSONValue? { features.first { Self.key($0) == key } }

    var installedFeatures: [JSONValue] {
        features.sorted { Self.title($0).localizedStandardCompare(Self.title($1)) == .orderedAscending }
    }
    func hasUpdate(_ feature: JSONValue) -> Bool {
        feature["update_available"]?.boolValue == true
            || overview[Self.key(feature)]?["update"]?["update_available"]?.boolValue == true
    }
    var pendingUpdates: [JSONValue] { installedFeatures.filter { hasUpdate($0) } }
    var updateCount: Int { pendingUpdates.count }
    var remotelyUpdatableKeys: [String] {
        pendingUpdates.map { Self.key($0) }.filter { canUpdateRemotely($0) }
    }
    func installedVersion(_ feature: JSONValue) -> String? {
        feature["version"]?.stringValue ?? overview[Self.key(feature)]?["version"]?.stringValue
    }
    func latestVersion(_ feature: JSONValue) -> String? {
        feature["latest_version"]?.stringValue ?? overview[Self.key(feature)]?["update"]?["latest_version"]?.stringValue
    }
    func lastChecked(_ feature: JSONValue) -> Date? {
        PluginStoreFormat.date(feature["last_update_check_at"] ?? overview[Self.key(feature)]?["update"]?["last_update_check_at"])
    }
    var lastCheckedAny: Date? { features.compactMap { lastChecked($0) }.max() }
    func source(_ key: String) -> String? { overview[key]?["source"]?.stringValue ?? packages[key]?["source"]?.stringValue }
    func sourceURL(_ key: String) -> String? { overview[key]?["source_url"]?.stringValue ?? packages[key]?["source_url"]?.stringValue }
    func isBuiltin(_ key: String) -> Bool { feature(key)?["is_builtin"]?.boolValue == true }
    /// 与 Web 一致：从插件仓库或 Git 安装的插件可以在线更新；本地导入、文件上传与内置插件不行。
    func canUpdateRemotely(_ key: String) -> Bool {
        guard !isBuiltin(key) else { return false }
        if sourceURL(key)?.hasPrefix("local://") == true { return false }
        if let source = self.source(key) { return ["repo", "git"].contains(source) }
        return feature(key)?["source_type"]?.stringValue == "remote"
    }
    /// 卸载走远程插件接口（同时清理账号开关）的来源，与 Web 相同。
    func isRemoteManaged(_ key: String) -> Bool { ["repo", "git", "local"].contains(source(key) ?? "") }
    func globalEnabled(_ key: String) -> Bool? { overview[key]?["global_enabled"]?.boolValue ?? packages[key]?["enabled"]?.boolValue }
    func manifest(_ key: String) -> JSONValue? { packages[key]?["manifest"] }

    func author(_ key: String, entry: PluginCatalogEntry? = nil) -> String? {
        if let author = manifest(key)?["author"]?.stringValue, !author.isEmpty { return author }
        if let author = (entry ?? bestCatalogEntry(key))?.author { return author }
        return isBuiltin(key) ? "TelePilot" : nil
    }
    func summary(_ key: String, entry: PluginCatalogEntry? = nil) -> String? {
        let candidates: [String?] = [manifest(key)?["description"]?.stringValue, (entry ?? bestCatalogEntry(key))?.summary, feature(key)?["usage"]?.stringValue]
        return candidates.compactMap { $0 }.first { !$0.isEmpty }
    }
    func minVersion(_ key: String, entry: PluginCatalogEntry? = nil) -> String? {
        for field in ["min_telepilot_version", "min_telebot_version"] {
            if let value = manifest(key)?[field]?.stringValue, !value.isEmpty { return value }
        }
        return (entry ?? bestCatalogEntry(key))?.minVersion
    }

    func catalogEntries(_ key: String) -> [PluginCatalogEntry] { catalog.filter { $0.name == key } }
    func bestCatalogEntry(_ key: String) -> PluginCatalogEntry? {
        let entries = catalogEntries(key)
        return entries.first { $0.sourceMatches } ?? entries.first
    }
    /// 更新卡片的「新功能」：仓库 CHANGELOG 里比已安装版本新、且不超过最新版本的段落。
    func pendingReleases(_ feature: JSONValue) -> [PluginRelease] {
        guard let entry = bestCatalogEntry(Self.key(feature)) else { return [] }
        return PluginReleaseNotes.releases(entry.releases, newerThan: installedVersion(feature), upTo: latestVersion(feature))
    }
    /// 仓库条目是否已安装：以服务器当前插件列表为准（仓库目录的 installed 字段可能是旧的）。
    func isInstalled(_ entry: PluginCatalogEntry) -> Bool { loaded ? feature(entry.name) != nil : entry.installedFlag }
    /// 仓库里有比已安装版本新的同来源版本，并且可以在线更新。
    func catalogHasUpdate(_ entry: PluginCatalogEntry) -> Bool {
        guard entry.repoID != nil, entry.sourceMatches, let feature = self.feature(entry.name),
              let version = entry.version, let current = installedVersion(feature) else { return false }
        return PluginVersion.isNewer(version, than: current) && canUpdateRemotely(entry.name)
    }
    var recentUpdates: [PluginUpdateRecord] {
        let cutoff = Date().addingTimeInterval(-30 * 86_400)
        var seen = Set<String>()
        return (sessionUpdates + history).sorted { $0.date > $1.date }.filter { record in
            guard record.date >= cutoff, !seen.contains(record.key) else { return false }
            seen.insert(record.key)
            return true
        }
    }

    // MARK: 读取

    func load(model: AppModel) async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        async let matrixResult = OpsFetch.result(model, "/api/feature-matrix")
        async let overviewResult = OpsFetch.result(model, "/api/plugins/installed-overview")
        async let packagesResult = OpsFetch.result(model, "/api/plugins/installed-packages")
        let (matrix, overviewValue, packagesValue) = await (matrixResult, overviewResult, packagesResult)
        switch matrix {
        case .success(let value):
            features = value["features"]?.arrayValue ?? []
            model.updatePlugins(features)
            loadError = nil
            loaded = true
        case .failure(let error):
            loadError = error.localizedDescription
        }
        // 旧服务器缺少这两个接口时不影响主列表，只是少了作者、简介与安装来源。
        if case .success(let value) = overviewValue { overview = Self.index(value.arrayValue ?? []) }
        if case .success(let value) = packagesValue { packages = Self.index(value.arrayValue ?? []) }
        // 已重新读取服务器真实状态：清除“结果待确认”和已完成提示，失败原因保留到下次操作。
        operations = operations.filter { _, phase in
            switch phase {
            case .running, .failed: true
            case .succeeded, .uncertain: false
            }
        }
        checkUncertain = false
    }

    private static func index(_ items: [JSONValue]) -> [String: JSONValue] {
        Dictionary(items.compactMap { item in item["key"]?.stringValue.map { ($0, item) } }, uniquingKeysWith: { _, new in new })
    }

    func loadCatalog(model: AppModel) async {
        guard !catalogLoading else { return }
        catalogLoading = true
        defer { catalogLoading = false }
        do {
            repos = try await model.request(path: "/api/plugin-repos").arrayValue ?? []
            catalogError = nil
        } catch {
            catalogError = error.localizedDescription
            return
        }
        var entries: [PluginCatalogEntry] = []
        var notices: [String] = []
        for repo in repos {
            guard let id = repo["id"]?.intValue else { continue }
            let name = repo["name"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } ?? "仓库 \(id)"
            do {
                let items = try await model.request(path: "/api/plugin-repos/\(id)/plugins").arrayValue ?? []
                entries += items.map { PluginCatalogEntry(repoID: id, repoName: name, plugin: $0) }
            } catch {
                notices.append(name + "：" + error.localizedDescription)
            }
        }
        if let local = try? await model.request(path: "/api/plugin-repos/local/plugins").arrayValue {
            entries += local.map { PluginCatalogEntry(repoID: nil, repoName: "本地参考插件", plugin: $0) }
        }
        catalog = entries
        catalogNotices = notices
        catalogLoaded = true
    }

    /// 最近 30 天的更新记录（来自各插件的安装历史）；内置插件没有安装历史。
    func loadHistory(model: AppModel, force: Bool = false) async {
        guard force || !historyLoaded else { return }
        historyLoaded = true
        var records: [PluginUpdateRecord] = []
        let cutoff = Date().addingTimeInterval(-30 * 86_400)
        for feature in features where feature["is_builtin"]?.boolValue != true {
            let key = Self.key(feature)
            guard !key.isEmpty,
                  let rows = try? await model.request(path: "/api/plugins/install/\(ManagementText.pathSegment(key))/history?limit=5").arrayValue
            else { continue }
            for row in rows where row["event_type"]?.stringValue == "updated" {
                guard let date = PluginStoreFormat.date(row["created_at"]), date >= cutoff else { continue }
                records.append(PluginUpdateRecord(key: key, title: Self.title(feature), from: row["previous_version"]?.stringValue,
                                                  to: row["version"]?.stringValue, date: date))
                break
            }
        }
        history = records
        for record in recentUpdates { await loadInstalledNotes(record.key, model: model) }
    }

    func loadInstalledNotes(_ key: String, model: AppModel, force: Bool = false) async {
        guard force || installedNotes[key] == nil, feature(key) != nil, !isBuiltin(key) else { return }
        do {
            let response = try await model.request(path: "/api/plugins/install/\(ManagementText.pathSegment(key))/changelog")
            let content = response["available"]?.boolValue == false ? "" : (response["content"]?.stringValue ?? "")
            installedNotes[key] = PluginReleaseNotes.parse(content)
        } catch {
            // 读取失败不显示“新功能”，下次进入再读。
            if force { installedNotes[key] = nil }
        }
    }

    // MARK: 写操作（每次只提交一次，不自动重试）

    @discardableResult
    func update(_ key: String, model: AppModel, reload: Bool = true) async -> PluginOperationPhase? {
        guard let feature = self.feature(key) else { return nil }
        if let phase = operations[key], phase.blocksAction { return phase }
        guard canUpdateRemotely(key) else {
            let phase = PluginOperationPhase.failed("此插件不是从插件仓库或 Git 安装的，不能在线更新。")
            operations[key] = phase
            return phase
        }
        let from = installedVersion(feature)
        operations[key] = .running("正在更新…")
        let phase: PluginOperationPhase
        do {
            let row = try await model.request(path: "/api/remote-plugins/\(ManagementText.pathSegment(key))/update", method: "POST")
            let to = row["version"]?.stringValue ?? latestVersion(feature)
            sessionUpdates.removeAll { $0.key == key }
            sessionUpdates.append(PluginUpdateRecord(key: key, title: Self.title(feature), from: from, to: to, date: Date()))
            phase = .succeeded(to.map { "已更新到 v\($0)" } ?? "已更新")
        } catch {
            phase = (error as? APIError)?.isOutcomeUncertain == true
                ? .uncertain("网络中断，无法确认是否已更新。请下拉刷新，确认实际版本后再操作。")
                : .failed(error.localizedDescription)
        }
        operations[key] = phase
        if case .succeeded = phase {
            installedNotes[key] = nil
            if reload {
                await load(model: model)
                await loadInstalledNotes(key, model: model)
            }
        }
        return phase
    }

    /// 「全部更新」：逐个执行并记录每一项的真实结果；某项失败继续下一项，网络中断（结果未知）时停止。
    func updateAll(model: AppModel) async {
        guard !bulkRunning else { return }
        let keys = remotelyUpdatableKeys.filter { !(operations[$0]?.blocksAction ?? false) }
        let manual = updateCount - remotelyUpdatableKeys.count
        guard !keys.isEmpty else { return }
        bulkRunning = true
        bulkMessage = nil
        bulkProblem = false
        var updated = 0
        var failed = 0
        var stopped = false
        for (index, key) in keys.enumerated() {
            bulkMessage = "正在更新第 \(index + 1) / \(keys.count) 个…"
            switch await update(key, model: model, reload: false) {
            case .succeeded?: updated += 1
            case .uncertain?: stopped = true
            default: failed += 1
            }
            if stopped { break }
        }
        await load(model: model)
        for key in keys where sessionUpdates.contains(where: { $0.key == key }) { await loadInstalledNotes(key, model: model) }
        var parts: [String] = []
        if stopped {
            parts.append("已更新 \(updated) 个；第 \(updated + failed + 1) 个因网络中断结果待确认，已停止后续更新。已重新读取状态，请核对后再操作")
        } else if failed > 0 {
            parts.append("已更新 \(updated) 个，\(failed) 个失败（原因见各项）")
        } else {
            parts.append("已全部更新（\(updated) 个）")
        }
        if manual > 0 { parts.append("另有 \(manual) 个不是从仓库安装，需要手动更新") }
        bulkMessage = parts.joined(separator: "；") + "。"
        bulkProblem = stopped || failed > 0
        bulkRunning = false
    }

    func checkUpdates(model: AppModel) async {
        guard !checking, !checkUncertain else { return }
        checking = true
        checkMessage = nil
        checkError = nil
        defer { checking = false }
        do {
            let result = try await model.request(path: "/api/remote-plugins/check-updates", method: "POST")
            let checked = result["checked"]?.intValue ?? 0
            let available = result["update_available"]?.intValue ?? 0
            let failed = result["failed"]?.intValue ?? 0
            checkMessage = "已检查 \(checked) 个插件：" + (available > 0 ? "\(available) 个有更新" : "均为最新版本") + (failed > 0 ? "，\(failed) 个检查失败" : "") + "。"
        } catch {
            checkError = error.localizedDescription
            checkUncertain = (error as? APIError)?.isOutcomeUncertain == true
            return
        }
        await load(model: model)
    }

    func install(_ entry: PluginCatalogEntry, model: AppModel) async {
        if let phase = operations[entry.id], phase.blocksAction { return }
        operations[entry.id] = .running("正在安装…")
        do {
            let row = try await model.request(path: entry.installPath, method: "POST", body: .object([:]))
            operations[entry.id] = .succeeded(row["version"]?.stringValue.map { "已安装 v\($0)" } ?? "已安装")
        } catch {
            operations[entry.id] = (error as? APIError)?.isOutcomeUncertain == true
                ? .uncertain("网络中断，无法确认是否已安装。请下拉刷新确认。")
                : .failed(error.localizedDescription)
            return
        }
        await load(model: model)
    }
}

// MARK: - 格式

enum PluginStoreFormat {
    static func date(_ value: JSONValue?) -> Date? {
        guard let text = value?.stringValue, !text.isEmpty else { return nil }
        let precise = ISO8601DateFormatter()
        precise.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = precise.date(from: text) ?? ISO8601DateFormatter().date(from: text) { return date }
        // 不带时区的时间按 UTC 解析（服务器以 UTC 存储）。
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
            formatter.dateFormat = format
            if let date = formatter.date(from: text) { return date }
        }
        return nil
    }

    static func relative(_ date: Date) -> String {
        if abs(Date().timeIntervalSince(date)) < 60 { return "刚刚" }
        return date.formatted(.relative(presentation: .named))
    }

    /// CHANGELOG 里的日期（只有年月日）：今天、昨天、N 天前，再早显示日期。
    static func releaseDay(_ text: String?) -> String? {
        guard let text else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-M-d"
        guard let date = formatter.date(from: text) else { return text }
        let calendar = Calendar.current
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: Date())).day ?? 0
        switch days {
        case 0: return "今天"
        case 1: return "昨天"
        case 2..<30: return "\(days) 天前"
        default: return date.formatted(date: .abbreviated, time: .omitted)
        }
    }

    static func markdown(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }

    static func version(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value.hasPrefix("v") ? value : "v" + value
    }
}

// MARK: - 通用组件

/// App Store 式的插件图标：插件没有自带图标时，用名称首字（或分类符号）生成圆角方块，颜色由插件标识决定。
struct PluginArtwork: View {
    let key: String
    let title: String
    var category: String?
    var size: CGFloat = 52

    private static let palette: [Color] = [.orange, .blue, .green, .purple, .pink, .teal, .indigo, .red, .mint, .cyan]
    private var tint: Color {
        let seed = key.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0x7fff_ffff }
        return Self.palette[seed % Self.palette.count]
    }
    private var initial: String? {
        guard let first = title.trimmingCharacters(in: .whitespaces).first, first.isLetter else { return nil }
        return String(first).uppercased()
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.225, style: .continuous)
        ZStack {
            shape.fill(tint.gradient)
            if let initial {
                Text(initial)
                    .font(.system(size: size * 0.46, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.white)
                    .minimumScaleFactor(0.5)
            } else {
                Image(systemName: PluginPresentation.symbol(category: category))
                    .font(.system(size: size * 0.42, weight: .semibold))
                    .foregroundStyle(Color.white)
            }
        }
        .frame(width: size, height: size)
        .overlay(shape.strokeBorder(Color.primary.opacity(0.08), lineWidth: 0.5))
        .accessibilityHidden(true)
    }
}

/// 列表右侧的胶囊按钮（获取 / 更新 / 打开）；进行中时显示系统进度指示。
struct PluginCapsuleButton: View {
    let title: String
    var prominent = false
    var running = false
    var disabled = false
    let identifier: String
    let action: () -> Void

    var body: some View {
        Group {
            if running {
                ProgressView()
                    .frame(minWidth: 64, minHeight: 34)
                    .accessibilityLabel("正在处理")
            } else if prominent {
                Button(title, action: action).buttonStyle(.borderedProminent)
            } else {
                Button(title, action: action).buttonStyle(.bordered)
            }
        }
        .buttonBorderShape(.capsule)
        .font(.subheadline.weight(.semibold))
        .disabled(disabled)
        .accessibilityIdentifier(identifier)
    }
}

/// App Store 列表行：图标、名称、一行副标题、可选的补充信息与右侧胶囊按钮。点名称区域进入产品页。
struct PluginStoreRow: View {
    let key: String
    let title: String
    var subtitle: String?
    var category: String?
    var detail: String?
    var actionTitle: String?
    var prominent = false
    var running = false
    var actionDisabled = false
    var identifierPrefix = "plugin.store"
    let open: () -> Void
    var action: () -> Void = {}

    var body: some View {
        HStack(spacing: 12) {
            Button(action: open) {
                HStack(spacing: 12) {
                    PluginArtwork(key: key, title: title, category: category, size: 52)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(.body.weight(.semibold)).foregroundStyle(.primary).lineLimit(2)
                        if let subtitle { Text(subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
                        if let detail { Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                    }
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier(identifierPrefix + ".row." + key)
            if let actionTitle {
                PluginCapsuleButton(title: actionTitle, prominent: prominent, running: running, disabled: actionDisabled,
                                    identifier: identifierPrefix + ".action." + key, action: action)
            }
        }
        .padding(.vertical, 2)
    }
}

/// 操作结果：成功、失败、结果待确认分别用图标 + 文字 + 颜色表达。
struct PluginOperationStatus: View {
    let phase: PluginOperationPhase?
    var body: some View {
        switch phase {
        case .succeeded(let text)?:
            Label(text, systemImage: "checkmark.circle.fill").font(.footnote).foregroundStyle(.green)
        case .failed(let text)?:
            Label(text, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.red).textSelection(.enabled)
        case .uncertain(let text)?:
            Label("结果待确认：" + text, systemImage: "questionmark.circle.fill").font(.footnote).foregroundStyle(.orange)
        case .running(let text)?:
            Label(text, systemImage: "hourglass").font(.footnote).foregroundStyle(.secondary)
        case nil:
            EmptyView()
        }
    }
}

/// 版本说明列表（「新功能」与版本历史共用）。
struct PluginReleaseList: View {
    let releases: [PluginRelease]
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(releases.enumerated()), id: \.offset) { _, release in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("版本 " + release.version).font(.subheadline.weight(.semibold))
                        Spacer(minLength: 8)
                        if let day = PluginStoreFormat.releaseDay(release.date) {
                            Text(day).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    ForEach(Array(release.items.enumerated()), id: \.offset) { _, item in
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text("•").foregroundStyle(.secondary).accessibilityHidden(true)
                            Text(PluginStoreFormat.markdown(item)).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
        .padding(.vertical, 2)
    }
}

/// 加载骨架：与真实行同样的结构，系统占位样式。
struct PluginStoreSkeleton: View {
    var rows = 4
    var body: some View {
        ForEach(0..<rows, id: \.self) { _ in
            PluginStoreRow(key: "placeholder", title: "插件名称占位", subtitle: "正在读取插件简介与版本", detail: "来源 · 版本",
                           actionTitle: "打开", actionDisabled: true, open: {})
        }
        .redacted(reason: .placeholder)
        .disabled(true)
        .accessibilityHidden(true)
    }
}

/// 分段头部（与插件中心一致）：作为列表第一个分组，随内容滚动。
private struct PluginStoreHeaderSection: View {
    let header: AnyView?
    var body: some View {
        if let header {
            Section {
                header
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
                    .listRowSeparator(.hidden)
            }
            .listSectionSeparator(.hidden)
        }
    }
}

struct PluginStoreDestination: View {
    let route: PluginStoreRoute
    let store: PluginStoreModel
    var body: some View {
        switch route {
        case .product(let key):
            PluginStoreDetailView(store: store, key: key)
        case .catalog(let entry):
            PluginStoreDetailView(store: store, key: entry.name, entry: entry)
        case .open(let key):
            if let feature = store.feature(key) {
                ManagementPluginDetailView(feature: feature.withInstallState(store.overview[key]), initialSection: .accounts)
            } else {
                ContentUnavailableView("插件已不在服务器上", systemImage: "puzzlepiece.extension", description: Text("下拉刷新插件列表后再试。"))
                    .navigationTitle("插件").navigationBarTitleDisplayMode(.inline)
            }
        }
    }
}

// MARK: - 已安装（App Store「更新」页）

struct PluginInstalledView: View {
    let store: PluginStoreModel
    var header: AnyView? = nil
    @Environment(AppModel.self) private var model
    @State private var route: PluginStoreRoute?
    @State private var confirmUpdateAll = false

    var body: some View {
        List {
            PluginStoreHeaderSection(header: header)
            if let error = store.loadError {
                Section { NoticeView(title: "插件列表读取失败", message: error + (store.features.isEmpty ? "" : "（下面是上次读取的结果）")) }
            }
            if store.features.isEmpty && !store.loaded {
                Section { PluginStoreSkeleton() } header: { Text("正在读取已安装插件…") }
            } else if store.features.isEmpty {
                Section {
                    ContentUnavailableView("还没有安装插件", systemImage: "puzzlepiece.extension",
                                           description: Text("到「发现」浏览插件仓库并安装。"))
                }
            } else {
                updatesSection
                recentSection
                installedSection
            }
        }
        .refreshable {
            await store.load(model: model)
            await store.loadHistory(model: model, force: true)
        }
        .task(id: store.loaded) {
            guard store.loaded else { return }
            await store.loadHistory(model: model)
            // 「新功能」来自仓库目录里的 CHANGELOG；仓库首次读取较慢，放在后台。
            if !store.catalogLoaded { await store.loadCatalog(model: model) }
        }
        .navigationDestination(item: $route) { PluginStoreDestination(route: $0, store: store) }
        .accessibilityIdentifier("plugins.installed.screen")
    }

    @ViewBuilder private var updatesSection: some View {
        let pending = store.pendingUpdates.map { PluginStoreModel.key($0) }
        let updatable = store.remotelyUpdatableKeys.count
        Section {
            if pending.isEmpty {
                Label("所有插件均为最新版本", systemImage: "checkmark.circle").foregroundStyle(.secondary)
            }
            ForEach(pending, id: \.self) { key in
                if let feature = store.feature(key) {
                    PluginUpdateCard(store: store, feature: feature,
                                     open: { route = .product(key) },
                                     update: { Task { await store.update(key, model: model) } })
                }
            }
            if let message = store.bulkMessage {
                Label(message, systemImage: store.bulkRunning ? "hourglass" : (store.bulkProblem ? "exclamationmark.triangle.fill" : "checkmark.circle.fill"))
                    .font(.footnote)
                    .foregroundStyle(store.bulkRunning ? Color.secondary : (store.bulkProblem ? Color.orange : Color.green))
                    .accessibilityIdentifier("plugins.updateAll.result")
            }
            checkRow
        } header: {
            HStack {
                Text(pending.isEmpty ? "更新" : "待更新 · \(pending.count)")
                Spacer()
                if updatable > 0 {
                    Button("全部更新") { confirmUpdateAll = true }
                        .font(.subheadline.weight(.semibold))
                        .textCase(nil)
                        .disabled(store.bulkRunning)
                        .accessibilityIdentifier("plugins.updateAll")
                        .confirmationDialog("更新 \(updatable) 个插件？", isPresented: $confirmUpdateAll, titleVisibility: .visible) {
                            Button("全部更新") { Task { await store.updateAll(model: model) } }
                            Button("取消", role: .cancel) {}
                        } message: {
                            Text("将逐个从插件仓库下载新版本并重新加载，运行中的插件可能短暂中断。某一项失败不影响其他项，也不会自动重试。")
                        }
                }
            }
        } footer: {
            Text("更新只替换服务器上的插件包，各账号的开关、配置和规则保持不变。")
        }
    }

    @ViewBuilder private var checkRow: some View {
        Button { Task { await store.checkUpdates(model: model) } } label: {
            HStack {
                Label("检查更新", systemImage: "arrow.triangle.2.circlepath")
                Spacer(minLength: 8)
                if store.checking {
                    ProgressView()
                } else if let date = store.lastCheckedAny {
                    Text("上次检查：" + PluginStoreFormat.relative(date)).font(.footnote).foregroundStyle(.secondary)
                }
            }
            .frame(minHeight: 44)
        }
        .disabled(store.checking || store.checkUncertain || store.bulkRunning)
        .accessibilityIdentifier("plugins.checkUpdates")
        if let message = store.checkMessage {
            Label(message, systemImage: "checkmark.circle.fill").font(.footnote).foregroundStyle(.green)
        }
        if let error = store.checkError {
            NoticeView(title: store.checkUncertain ? "结果待确认" : "检查失败",
                       message: store.checkUncertain ? error + " 请下拉刷新后再检查。" : error)
        }
    }

    @ViewBuilder private var recentSection: some View {
        let recent = store.recentUpdates
        if !recent.isEmpty {
            Section {
                ForEach(recent) { record in
                    PluginRecentUpdateRow(store: store, record: record,
                                          open: { route = .product(record.key) },
                                          launch: { route = .open(record.key) })
                }
            } header: { Text("最近已更新") } footer: { Text("最近 30 天内的插件更新。") }
        }
    }

    @ViewBuilder private var installedSection: some View {
        let features = store.installedFeatures
        Section {
            ForEach(features.map { PluginStoreModel.key($0) }, id: \.self) { key in
                if let feature = store.feature(key) {
                    installedRow(key: key, feature: feature)
                }
            }
        } header: { Text("已安装 · \(features.count)") } footer: {
            Text("点插件名称查看详情、版本历史与权限；「打开」进入插件的账号启用与配置。")
        }
    }

    @ViewBuilder private func installedRow(key: String, feature: JSONValue) -> some View {
        let phase = store.operations[key]
        let canUpdate = store.hasUpdate(feature) && store.canUpdateRemotely(key)
        VStack(alignment: .leading, spacing: 4) {
            PluginStoreRow(key: key, title: PluginStoreModel.title(feature), subtitle: store.summary(key),
                           category: feature["category"]?.stringValue, detail: installedDetail(key: key, feature: feature),
                           actionTitle: canUpdate ? "更新" : "打开", prominent: canUpdate,
                           running: phase?.isRunning == true,
                           actionDisabled: store.bulkRunning || phase?.blocksAction == true,
                           identifierPrefix: "plugin.installed",
                           open: { route = .product(key) },
                           action: {
                               if canUpdate { Task { await store.update(key, model: model) } } else { route = .open(key) }
                           })
            PluginOperationStatus(phase: phase)
        }
    }

    private func installedDetail(key: String, feature: JSONValue) -> String {
        var details: [String] = []
        if let version = PluginStoreFormat.version(store.installedVersion(feature)) { details.append(version) }
        details.append(PluginPresentation.sourceShort(feature))
        if store.globalEnabled(key) == false { details.append("服务器已停用") }
        return details.joined(separator: " · ")
    }
}

/// 待更新卡片：版本 x → y、发布时间、可折叠的「新功能」与单个更新按钮。
private struct PluginUpdateCard: View {
    let store: PluginStoreModel
    let feature: JSONValue
    let open: () -> Void
    let update: () -> Void
    @State private var expanded = false

    var body: some View {
        let key = PluginStoreModel.key(feature)
        let phase = store.operations[key]
        let releases = store.pendingReleases(feature)
        let remote = store.canUpdateRemotely(key)
        VStack(alignment: .leading, spacing: 8) {
            PluginStoreRow(key: key, title: PluginStoreModel.title(feature), subtitle: versionLine,
                           category: feature["category"]?.stringValue, detail: timeLine(releases),
                           actionTitle: remote ? "更新" : nil, prominent: true,
                           running: phase?.isRunning == true,
                           actionDisabled: store.bulkRunning || phase?.blocksAction == true,
                           identifierPrefix: "plugin.update", open: open, action: update)
            if !remote {
                Label("此插件不是从插件仓库安装的，需要重新上传新版本。", systemImage: "info.circle")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            if !releases.isEmpty {
                DisclosureGroup(isExpanded: $expanded) {
                    PluginReleaseList(releases: releases)
                } label: {
                    Label("新功能", systemImage: "sparkles").font(.subheadline.weight(.semibold))
                }
                .accessibilityIdentifier("plugin.update.notes." + key)
            }
            if let error = feature["last_update_check_error"]?.stringValue, !error.isEmpty {
                Label("上次检查出错：" + error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange).lineLimit(3)
            }
            PluginOperationStatus(phase: phase)
        }
        .padding(.vertical, 2)
    }

    private var versionLine: String {
        let from = PluginStoreFormat.version(store.installedVersion(feature)) ?? "当前版本"
        let to = PluginStoreFormat.version(store.latestVersion(feature)) ?? "新版本"
        return from + " → " + to
    }

    private func timeLine(_ releases: [PluginRelease]) -> String? {
        if let day = PluginStoreFormat.releaseDay(releases.first?.date) { return "发布于 " + day }
        if let checked = store.lastChecked(feature) { return "发现于 " + PluginStoreFormat.relative(checked) }
        return nil
    }
}

private struct PluginRecentUpdateRow: View {
    let store: PluginStoreModel
    let record: PluginUpdateRecord
    let open: () -> Void
    let launch: () -> Void
    @State private var expanded = false

    var body: some View {
        let notes = store.installedNotes[record.key] ?? []
        let current = PluginReleaseNotes.release(notes, for: record.to).map { [$0] } ?? []
        let feature = store.feature(record.key)
        VStack(alignment: .leading, spacing: 8) {
            PluginStoreRow(key: record.key, title: feature.map { PluginStoreModel.title($0) } ?? record.title, subtitle: versionLine,
                           category: feature?["category"]?.stringValue, detail: "更新于 " + PluginStoreFormat.relative(record.date),
                           actionTitle: feature == nil ? nil : "打开", identifierPrefix: "plugin.recent", open: open, action: launch)
            if !current.isEmpty {
                DisclosureGroup(isExpanded: $expanded) {
                    PluginReleaseList(releases: current)
                } label: {
                    Label("新功能", systemImage: "sparkles").font(.subheadline.weight(.semibold))
                }
            }
        }
        .padding(.vertical, 2)
    }

    private var versionLine: String {
        let to = PluginStoreFormat.version(record.to) ?? "新版本"
        guard let from = PluginStoreFormat.version(record.from) else { return "已更新到 " + to }
        return from + " → " + to
    }
}

// MARK: - 发现

private struct PluginCatalogGroup: Identifiable {
    let category: String
    let entries: [PluginCatalogEntry]
    var id: String { category }
}

struct PluginDiscoverView: View {
    let store: PluginStoreModel
    var header: AnyView? = nil
    @Environment(AppModel.self) private var model
    @State private var search = ""
    @State private var category = "all"
    @State private var route: PluginStoreRoute?
    @State private var confirmEntryID: String?

    private static let categoryOrder = ["interactive", "automation", "utility", "other"]
    private var categories: [String] {
        Array(Set(store.catalog.map { $0.category ?? "other" })).sorted { lhs, rhs in
            (Self.categoryOrder.firstIndex(of: lhs) ?? 99, lhs) < (Self.categoryOrder.firstIndex(of: rhs) ?? 99, rhs)
        }
    }
    private var filtered: [PluginCatalogEntry] {
        let query = search.trimmingCharacters(in: .whitespaces)
        return store.catalog.filter { entry in
            (category == "all" || (entry.category ?? "other") == category)
                && (query.isEmpty || entry.title.localizedCaseInsensitiveContains(query)
                    || entry.name.localizedCaseInsensitiveContains(query)
                    || (entry.summary?.localizedCaseInsensitiveContains(query) ?? false)
                    || entry.tags.contains { $0.localizedCaseInsensitiveContains(query) })
        }
    }
    private var groups: [PluginCatalogGroup] {
        let grouped = Dictionary(grouping: filtered) { $0.category ?? "other" }
        return categories.compactMap { name -> PluginCatalogGroup? in
            guard let entries = grouped[name], !entries.isEmpty else { return nil }
            return PluginCatalogGroup(category: name, entries: entries.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending })
        }
    }

    var body: some View {
        List {
            PluginStoreHeaderSection(header: header)
            if let error = store.catalogError {
                Section { NoticeView(title: "插件仓库读取失败", message: error) }
            }
            ForEach(store.catalogNotices, id: \.self) { notice in
                Section { NoticeView(title: "部分仓库读取失败", message: notice) }
            }
            if store.catalog.isEmpty && (store.catalogLoading || (!store.catalogLoaded && store.catalogError == nil)) {
                Section { PluginStoreSkeleton() } header: { Text("正在读取插件仓库…") } footer: {
                    Text("首次读取需要服务器下载仓库，可能要一分钟左右。")
                }
            } else if store.catalogLoaded && store.repos.isEmpty && store.catalog.isEmpty {
                Section {
                    ContentUnavailableView("还没有插件仓库", systemImage: "books.vertical",
                                           description: Text("在下方「管理插件仓库」添加仓库后，这里会列出仓库里可以安装的插件。"))
                }
            } else if store.catalogLoaded && filtered.isEmpty {
                Section {
                    if search.isEmpty && category == "all" {
                        ContentUnavailableView("仓库里没有插件", systemImage: "shippingbox", description: Text("已添加的仓库里没有找到可安装的插件。"))
                    } else if search.isEmpty {
                        ContentUnavailableView("此分类下没有插件", systemImage: "line.3.horizontal.decrease.circle", description: Text("换一个分类看看。"))
                    } else {
                        ContentUnavailableView.search(text: search)
                    }
                }
                if category != "all" { categorySection }
            } else if !store.catalog.isEmpty {
                categorySection
                ForEach(groups) { group in
                    Section(PluginPresentation.category(group.category == "other" ? "未分类" : group.category)) {
                        ForEach(group.entries) { entry in entryRow(entry) }
                    }
                }
            }
            moreSourcesSection
        }
        .searchable(text: $search, prompt: "搜索插件名称、简介或标签")
        .refreshable {
            await store.loadCatalog(model: model)
            await store.load(model: model)
        }
        .task { if !store.catalogLoaded { await store.loadCatalog(model: model) } }
        .navigationDestination(item: $route) { PluginStoreDestination(route: $0, store: store) }
        .accessibilityIdentifier("plugins.discover.screen")
    }

    private var categorySection: some View {
        Section {
            Picker(selection: $category) {
                Text("全部分类").tag("all")
                ForEach(categories, id: \.self) { name in
                    Text(PluginPresentation.category(name == "other" ? "未分类" : name)).tag(name)
                }
            } label: {
                Label("分类", systemImage: "line.3.horizontal.decrease.circle")
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("plugins.discover.category")
        } footer: {
            Text("共 \(store.catalog.count) 个插件，来自 \(store.repos.count) 个仓库" + (store.catalog.contains { $0.repoID == nil } ? "与本地参考插件。" : "。"))
        }
    }

    @ViewBuilder private func entryRow(_ entry: PluginCatalogEntry) -> some View {
        let installed = store.isInstalled(entry)
        let canUpdate = store.catalogHasUpdate(entry)
        let phase = store.operations[entry.id] ?? (installed ? store.operations[entry.name] : nil)
        let title = installed ? (canUpdate ? "更新" : "打开") : "获取"
        VStack(alignment: .leading, spacing: 4) {
            PluginStoreRow(key: entry.name, title: entry.title, subtitle: entry.summary, category: entry.category,
                           detail: entryDetail(entry, installed: installed), actionTitle: title, prominent: canUpdate,
                           running: phase?.isRunning == true, actionDisabled: store.bulkRunning || phase?.blocksAction == true,
                           identifierPrefix: "plugin.discover",
                           open: { route = installed ? .product(entry.name) : .catalog(entry) },
                           action: {
                               if !installed { confirmEntryID = entry.id }
                               else if canUpdate { Task { await store.update(entry.name, model: model) } }
                               else { route = .open(entry.name) }
                           })
            PluginOperationStatus(phase: phase)
        }
        .confirmationDialog("安装「\(entry.title)」？", isPresented: Binding(
            get: { confirmEntryID == entry.id },
            set: { if !$0 { confirmEntryID = nil } }
        ), titleVisibility: .visible) {
            Button("安装") { Task { await store.install(entry, model: model) } }
            Button("取消", role: .cancel) {}
        } message: {
            Text(PluginStoreText.installMessage(entry))
        }
    }

    private func entryDetail(_ entry: PluginCatalogEntry, installed: Bool) -> String {
        var details: [String] = [entry.repoName]
        if let version = PluginStoreFormat.version(entry.version) { details.append(version) }
        if installed, let feature = store.feature(entry.name), let current = PluginStoreFormat.version(store.installedVersion(feature)) {
            details.append("已安装 " + current)
        }
        return details.joined(separator: " · ")
    }

    private var moreSourcesSection: some View {
        Section {
            NavigationLink {
                ManagementCollectionView(resource: .init(title: "插件仓库", path: "/api/plugin-repos", kind: .repos,
                                                         create: .init(title: "添加仓库", path: "/api/plugin-repos"), itemPath: "/api/plugin-repos/{id}"))
            } label: { Label("管理插件仓库", systemImage: "books.vertical") }
            .accessibilityIdentifier("plugins.repositories")
            NavigationLink {
                ManagementFormView(action: .init(title: "从 Git 地址安装", path: "/api/remote-plugins/install",
                                                 explanation: "填写插件的 Git 仓库地址，服务器会下载并安装。安装后不会自动在任何账号中启用。"))
            } label: { Label("从 Git 地址安装", systemImage: "link.badge.plus") }
            .accessibilityIdentifier("plugins.installFromURL")
            NavigationLink { FileManagementView(mode: .plugin) } label: { Label("从文件安装", systemImage: "doc.badge.plus") }
                .accessibilityIdentifier("plugins.installFromFile")
            NavigationLink {
                ManagementCollectionView(resource: .init(title: "远程插件", path: "/api/remote-plugins", kind: .remotePlugins,
                                                         create: .init(title: "从地址安装插件", path: "/api/remote-plugins/install"),
                                                         itemPath: "/api/remote-plugins/{id}", idField: "name"))
            } label: { Label("远程插件记录", systemImage: "icloud.and.arrow.down") }
        } header: { Text("其他安装方式") } footer: {
            Text("添加或修改仓库后下拉刷新，上方列表会显示新的插件。")
        }
    }
}

enum PluginStoreText {
    static func installMessage(_ entry: PluginCatalogEntry) -> String {
        var text = "将从「\(entry.repoName)」安装"
        if let version = PluginStoreFormat.version(entry.version) { text += " " + version }
        text += " 到服务器。安装后不会自动在任何账号中启用，需要在「打开」后为账号开启。"
        let permissions = entry.permissions.count
        text += permissions > 0 ? "插件申请 \(permissions) 项权限，请只安装可信来源的插件。" : "插件未申请额外权限。"
        return text
    }
}

// MARK: - 产品页

struct PluginStoreDetailView: View {
    let store: PluginStoreModel
    let key: String
    var entry: PluginCatalogEntry? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var route: PluginStoreRoute?
    @State private var confirmInstall = false
    @State private var confirmGlobal = false
    @State private var confirmUninstall = false
    @State private var globalBusy = false
    @State private var globalUncertain = false
    @State private var globalMessage: String?
    @State private var globalError: String?
    @State private var uninstalling = false
    @State private var uninstallUncertain = false
    @State private var uninstallError: String?
    @State private var showFullDescription = false

    private var feature: JSONValue? { store.feature(key) }
    private var catalogEntry: PluginCatalogEntry? { entry ?? store.bestCatalogEntry(key) }
    private var installed: Bool { feature != nil }
    private var title: String { feature.map { PluginStoreModel.title($0) } ?? catalogEntry?.title ?? key }
    private var category: String? { feature?["category"]?.stringValue ?? catalogEntry?.category }
    private var permissions: [String] {
        if let feature { return (feature["permissions"]?.arrayValue ?? []).compactMap(\.stringValue) }
        return catalogEntry?.permissions ?? []
    }
    private var currentVersion: String? { feature.flatMap { store.installedVersion($0) } ?? catalogEntry?.version }
    private var hasUpdate: Bool { feature.map { store.hasUpdate($0) } ?? false }
    private var canUpdate: Bool { hasUpdate && store.canUpdateRemotely(key) }
    private var phase: PluginOperationPhase? {
        if installed { return store.operations[key] }
        return catalogEntry.flatMap { store.operations[$0.id] }
    }

    var body: some View {
        List {
            headerSection
            infoStripSection
            whatsNewSection
            descriptionSection
            permissionsSection
            if let feature { manageSection(feature) }
            informationSection
            if installed { dangerSection }
            if !installed && catalogEntry == nil {
                Section {
                    ContentUnavailableView("插件已不在服务器上", systemImage: "puzzlepiece.extension", description: Text("它可能已被卸载。下拉刷新后再试。"))
                }
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadInstalledNotes(key, model: model) }
        .refreshable {
            await store.load(model: model)
            await store.loadInstalledNotes(key, model: model, force: true)
            globalUncertain = false
            uninstallUncertain = false
        }
        .navigationDestination(item: $route) { PluginStoreDestination(route: $0, store: store) }
        .accessibilityIdentifier("plugin.product.screen")
    }

    // MARK: 头部

    private var headerSection: some View {
        Section {
            HStack(alignment: .top, spacing: 16) {
                PluginArtwork(key: key, title: title, category: category, size: 104)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.title2.weight(.bold)).lineLimit(3)
                        .accessibilityAddTraits(.isHeader)
                    if let author = store.author(key, entry: catalogEntry) {
                        Text(author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    }
                    if let category {
                        Text(PluginPresentation.category(category)).font(.footnote).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    primaryButton
                }
            }
            .padding(.vertical, 6)
            PluginOperationStatus(phase: phase)
        }
    }

    @ViewBuilder private var primaryButton: some View {
        if let feature {
            if canUpdate {
                PluginCapsuleButton(title: "更新", prominent: true, running: phase?.isRunning == true,
                                    disabled: store.bulkRunning || phase?.blocksAction == true, identifier: "plugin.product.primary") {
                    Task { await store.update(key, model: model) }
                }
            } else {
                PluginCapsuleButton(title: "打开", prominent: true, identifier: "plugin.product.primary") { route = .open(PluginStoreModel.key(feature)) }
            }
        } else if let catalogEntry {
            PluginCapsuleButton(title: "获取", prominent: true, running: phase?.isRunning == true,
                                disabled: phase?.blocksAction == true, identifier: "plugin.product.primary") { confirmInstall = true }
                .confirmationDialog("安装「\(catalogEntry.title)」？", isPresented: $confirmInstall, titleVisibility: .visible) {
                    Button("安装") { Task { await store.install(catalogEntry, model: model) } }
                    Button("取消", role: .cancel) {}
                } message: { Text(PluginStoreText.installMessage(catalogEntry)) }
        }
    }

    // MARK: 信息条

    private var infoStripSection: some View {
        Section {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 0) {
                    PluginInfoStripItem(caption: "版本", value: PluginStoreFormat.version(currentVersion) ?? "未标注",
                                        footnote: hasUpdate ? "可更新到 " + (PluginStoreFormat.version(feature.flatMap { store.latestVersion($0) }) ?? "新版本")
                                                            : (installed ? "当前版本" : "仓库版本"))
                    Divider().frame(height: 36)
                    PluginInfoStripItem(caption: "权限", value: "\(permissions.count)", footnote: permissions.isEmpty ? "未申请权限" : "项权限")
                    Divider().frame(height: 36)
                    PluginInfoStripItem(caption: "最低版本", value: store.minVersion(key, entry: catalogEntry) ?? "未声明", footnote: "TelePilot")
                    Divider().frame(height: 36)
                    PluginInfoStripItem(caption: "来源", value: sourceTitle, footnote: signatureText)
                    if let accounts = accountUsage {
                        Divider().frame(height: 36)
                        PluginInfoStripItem(caption: "账号", value: accounts, footnote: "个账号启用")
                    }
                }
                .padding(.vertical, 6)
            }
            .listRowInsets(EdgeInsets(top: 0, leading: 8, bottom: 0, trailing: 8))
            .accessibilityIdentifier("plugin.product.infoStrip")
        }
    }

    private var sourceTitle: String {
        if let feature { return PluginPresentation.sourceShort(feature) }
        return catalogEntry?.repoName ?? "未知"
    }
    private var signatureText: String {
        switch feature?["signature_ok"]?.boolValue {
        case true?: "签名有效"
        case false?: "签名异常"
        case nil: installed ? "未签名" : "安装时校验"
        }
    }
    private var accountUsage: String? {
        guard let accounts = store.overview[key]?["accounts"]?.arrayValue, !accounts.isEmpty else { return nil }
        return "\(accounts.filter { $0["enabled"]?.boolValue == true }.count)/\(accounts.count)"
    }

    // MARK: 新功能

    @ViewBuilder private var whatsNewSection: some View {
        let pending = feature.map { store.pendingReleases($0) } ?? []
        let installedNotes = store.installedNotes[key] ?? []
        let catalogNotes = catalogEntry?.releases ?? []
        let history = mergedHistory(pending: pending, installed: installedNotes, catalog: catalogNotes)
        let headline: [PluginRelease] = {
            if !pending.isEmpty { return pending }
            if installed {
                if let current = PluginReleaseNotes.release(installedNotes, for: currentVersion) ?? installedNotes.first { return [current] }
                return []
            }
            return catalogNotes.first.map { [$0] } ?? []
        }()
        if !headline.isEmpty {
            Section {
                PluginReleaseList(releases: headline)
                if history.count > headline.count {
                    NavigationLink {
                        PluginVersionHistoryView(title: title, releases: history, currentVersion: installed ? currentVersion : nil)
                    } label: { Label("版本历史", systemImage: "clock.arrow.circlepath") }
                    .accessibilityIdentifier("plugin.product.history")
                }
            } header: { Text("新功能") } footer: {
                if !pending.isEmpty { Text("以上是更新后可用的变化，来自插件仓库里的更新日志。") }
            }
            .accessibilityIdentifier("plugin.product.whatsNew")
        }
    }

    private func mergedHistory(pending: [PluginRelease], installed: [PluginRelease], catalog: [PluginRelease]) -> [PluginRelease] {
        var result: [PluginRelease] = []
        for release in pending + installed + catalog where !result.contains(where: { PluginVersion.compare($0.version, release.version) == .orderedSame }) {
            result.append(release)
        }
        return result.sorted { PluginVersion.isNewer($0.version, than: $1.version) }
    }

    // MARK: 简介与权限

    @ViewBuilder private var descriptionSection: some View {
        let description = store.summary(key, entry: catalogEntry)
        let usage = feature?["usage"]?.stringValue ?? catalogEntry?.plugin["usage"]?.stringValue
        if description != nil || (usage?.isEmpty == false) {
            Section("简介") {
                if let description {
                    Text(description).font(.subheadline)
                        .lineLimit(showFullDescription ? nil : 4)
                        .textSelection(.enabled)
                    if description.count > 120 && !showFullDescription {
                        Button("更多") { showFullDescription = true }.font(.subheadline)
                    }
                }
                if let usage, !usage.isEmpty, usage != description {
                    Label { Text(usage).font(.subheadline) } icon: { Image(systemName: "lightbulb") }
                }
            }
        }
    }

    @ViewBuilder private var permissionsSection: some View {
        let warnings = PluginPresentation.riskWarnings(feature ?? catalogEntry?.plugin ?? .object([:]))
        Section {
            if permissions.isEmpty { Label("未申请任何权限", systemImage: "lock.open").foregroundStyle(.secondary) }
            ForEach(permissions, id: \.self) { permission in
                let info = PluginPresentation.permissionLabels[permission]
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(info?.0 ?? permission)
                        Text(info?.1 ?? "未收录的权限：" + permission).font(.caption).foregroundStyle(.secondary)
                    }
                } icon: { Image(systemName: "lock") }
            }
            ForEach(warnings, id: \.self) { warning in
                Label(warning, systemImage: "exclamationmark.shield").font(.footnote).foregroundStyle(.red)
            }
        } header: { Text("权限") } footer: { Text("插件以账号身份能做的事情。请只安装你信任的插件。") }
    }

    // MARK: 配置与管理（已安装）

    private func manageSection(_ feature: JSONValue) -> some View {
        let installState = feature.withInstallState(store.overview[key])
        return Section {
            NavigationLink { ManagementPluginDetailView(feature: installState, initialSection: .accounts) } label: {
                Label("账号启用与配置", systemImage: "person.2")
            }
            .accessibilityIdentifier("plugin.product.accounts")
            NavigationLink { ManagementPluginDetailView(feature: installState, initialSection: .configuration) } label: {
                Label("全局配置", systemImage: "slider.horizontal.3")
            }
            .accessibilityIdentifier("plugin.product.globalConfig")
            if !store.isBuiltin(key) {
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "安装历史", path: "/api/plugins/install/\(ManagementText.pathSegment(key))/history"))
                } label: { Label("安装历史", systemImage: "clock") }
            }
            NavigationLink { PluginTechnicalInfoView(feature: feature, accountState: nil, installRecord: store.overview[key]) } label: {
                Label("完整技术信息", systemImage: "info.circle")
            }
        } header: { Text("配置与管理") } footer: {
            Text("插件在每个账号上单独启用和配置；全局配置由所有账号共享。")
        }
    }

    private var informationSection: some View {
        Section("信息") {
            if let author = store.author(key, entry: catalogEntry) { PluginInfoRow(title: "提供者", value: author, symbol: "person.crop.square") }
            PluginInfoRow(title: "插件标识", value: key, symbol: "tag", monospaced: true)
            if let feature {
                PluginInfoRow(title: "来源", value: PluginPresentation.source(feature), symbol: "shippingbox")
            } else if let catalogEntry {
                PluginInfoRow(title: "来源", value: catalogEntry.repoName, symbol: "books.vertical")
            }
            if let version = PluginStoreFormat.version(currentVersion) { PluginInfoRow(title: "版本", value: version, symbol: "number") }
            if let installedAt = PluginStoreFormat.date(store.packages[key]?["installed_at"]) {
                PluginInfoRow(title: "安装时间", value: installedAt.formatted(date: .abbreviated, time: .shortened), symbol: "calendar")
            }
            if let record = store.recentUpdates.first(where: { $0.key == key }) {
                PluginInfoRow(title: "最近更新", value: PluginStoreFormat.relative(record.date), symbol: "arrow.down.circle")
            }
            if let tags = catalogEntry?.tags, !tags.isEmpty {
                PluginInfoRow(title: "标签", value: tags.joined(separator: "、"), symbol: "number.square")
            }
        }
    }

    // MARK: 停用与卸载（已安装）

    @ViewBuilder private var dangerSection: some View {
        let enabled = store.globalEnabled(key)
        let builtin = store.isBuiltin(key)
        if enabled != nil || !builtin {
            Section {
                if let enabled {
                    LabeledContent {
                        Text(enabled ? "已启用" : "已停用").foregroundStyle(enabled ? Color.green : Color.orange)
                    } label: { Label("服务器总开关", systemImage: enabled ? "power.circle.fill" : "power.circle") }
                    Button(role: enabled ? .destructive : nil) { confirmGlobal = true } label: {
                        Label(enabled ? "在服务器上停用" : "在服务器上启用", systemImage: enabled ? "pause.circle" : "play.circle")
                            .frame(minHeight: 44)
                    }
                    .disabled(globalBusy || globalUncertain || uninstalling)
                    .accessibilityIdentifier("plugin.product.globalToggle")
                    .confirmationDialog(enabled ? "在服务器上停用此插件？" : "在服务器上启用此插件？", isPresented: $confirmGlobal, titleVisibility: .visible) {
                        Button(enabled ? "确认停用" : "确认启用", role: enabled ? .destructive : nil) { Task { await toggleGlobal(enabled) } }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text(enabled ? "停用后所有账号都不会运行此插件；各账号的开关、配置和规则会保留，重新启用即可恢复。"
                                     : "启用后，已在账号中开启此插件的账号会重新开始运行它。")
                    }
                }
                if globalBusy { ProgressView("正在提交并刷新状态…") }
                if let globalMessage { Label(globalMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                if let globalError { NoticeView(title: globalUncertain ? "结果待确认" : "操作未完成", message: globalError) }
                if !builtin {
                    Button(role: .destructive) { confirmUninstall = true } label: {
                        Label("卸载插件", systemImage: "trash").frame(minHeight: 44)
                    }
                    .disabled(uninstalling || uninstallUncertain || globalBusy)
                    .accessibilityIdentifier("plugin.product.uninstall")
                    .confirmationDialog("卸载「\(title)」？", isPresented: $confirmUninstall, titleVisibility: .visible) {
                        Button("卸载", role: .destructive) { Task { await uninstall() } }
                        Button("取消", role: .cancel) {}
                    } message: {
                        Text("将从服务器移除插件文件与安装记录，所有账号都无法再使用它，各账号的开关与规则也可能被清除。需要重新安装才能恢复。")
                    }
                    if uninstalling { ProgressView("正在卸载…") }
                    if let uninstallError { NoticeView(title: uninstallUncertain ? "结果待确认" : "卸载失败", message: uninstallError) }
                }
            } header: { Text("停用与卸载") } footer: {
                Text(globalUncertain || uninstallUncertain
                     ? "网络中断，无法确认是否已生效。请下拉刷新，确认实际状态后再操作。"
                     : "这些操作影响所有账号。只想在某个账号停用，请到「账号启用与配置」。")
            }
        }
    }

    private func toggleGlobal(_ enabled: Bool) async {
        guard !globalBusy, !globalUncertain else { return }
        globalBusy = true
        globalMessage = nil
        globalError = nil
        defer { globalBusy = false }
        do {
            _ = try await model.request(path: "/api/plugins/install/\(ManagementText.pathSegment(key))/\(enabled ? "disable" : "enable")", method: "POST")
        } catch {
            globalError = error.localizedDescription
            globalUncertain = (error as? APIError)?.isOutcomeUncertain == true
            return
        }
        await store.load(model: model)
        globalMessage = enabled ? "已在服务器上停用。" : "已在服务器上启用。"
    }

    private func uninstall() async {
        guard !uninstalling, !uninstallUncertain else { return }
        uninstalling = true
        uninstallError = nil
        defer { uninstalling = false }
        let segment = ManagementText.pathSegment(key)
        let path = store.isRemoteManaged(key) ? "/api/remote-plugins/\(segment)" : "/api/plugins/install/\(segment)"
        do {
            _ = try await model.request(path: path, method: "DELETE")
        } catch {
            uninstallError = error.localizedDescription
            uninstallUncertain = (error as? APIError)?.isOutcomeUncertain == true
            return
        }
        await store.load(model: model)
        dismiss()
    }
}

private struct PluginInfoStripItem: View {
    let caption: String
    let value: String
    let footnote: String
    var body: some View {
        VStack(spacing: 3) {
            Text(caption).font(.caption2.weight(.semibold)).foregroundStyle(.secondary).textCase(.uppercase)
            Text(value).font(.headline).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
            Text(footnote).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
        }
        .frame(minWidth: 92)
        .padding(.horizontal, 6)
        .accessibilityElement(children: .combine)
    }
}

private struct PluginVersionHistoryView: View {
    let title: String
    let releases: [PluginRelease]
    let currentVersion: String?
    var body: some View {
        List {
            ForEach(Array(releases.enumerated()), id: \.offset) { _, release in
                Section {
                    PluginReleaseList(releases: [release])
                } footer: {
                    if let currentVersion, PluginVersion.compare(release.version, currentVersion) == .orderedSame {
                        Label("当前安装的版本", systemImage: "checkmark.circle").font(.caption)
                    }
                }
            }
        }
        .navigationTitle("版本历史")
        .navigationBarTitleDisplayMode(.inline)
    }
}
