import SwiftUI
import Observation

/// 底部 Dock 的入口。iPhone 的 `TabView` 超过 5 个 Tab 时系统会自动生成第二个“更多”，
/// 所以普通 Tab 最多 4 个：中间最多 3 格由用户自选（`customizable`），「更多」固定在最后，Agent 独立。
enum DockItem: String, CaseIterable, Codable, Identifiable {
    case overview, plugins, interaction, ai, operations, accounts, logs, ledger, more, agent
    var id: String { rawValue }
    var title: String {
        switch self {
        case .overview: "概览"
        case .plugins: "插件"
        case .interaction: "交互"
        case .ai: "AI"
        case .operations: "指令"
        case .accounts: "账号"
        case .logs: "日志"
        case .ledger: "台账"
        case .more: "更多"
        case .agent: "Agent"
        }
    }
    /// 设置页与“更多”里用的完整名称。
    var longTitle: String {
        switch self {
        case .operations: "指令与任务"
        case .logs: "日志"
        case .ledger: "资金台账"
        default: title
        }
    }
    var symbol: String {
        switch self {
        case .overview: "square.grid.2x2"
        case .plugins: "puzzlepiece.extension"
        case .interaction: "bubble.left.and.text.bubble.right"
        case .ai: "cpu"
        case .operations: "terminal"
        case .accounts: "person.2"
        case .logs: "list.bullet.rectangle"
        case .ledger: "book.closed"
        case .more: "ellipsis.circle"
        case .agent: "sparkles"
        }
    }
    /// 对应的服务器平台模块（`/api/system/capabilities` 的 `key`）；模块关闭时入口隐藏，与 Web 侧栏一致。
    var platformModule: String? {
        switch self {
        case .interaction: "interaction_bot"
        case .ai: "ai"
        case .ledger: "ledger"
        default: nil
        }
    }

    /// 可以放进 Dock 中间格的入口（一级 + 二级；「更多」与 Agent 固定，三级的 Webhook／命中调试／服务器设置不可选）。
    /// 顺序也是补位顺序：Dock 里某格的模块被服务器关闭时，按这个顺序取下一个没在 Dock 里、模块开着的入口补上（默认即 AI）。
    static let customizable: [DockItem] = [.overview, .plugins, .interaction, .ai, .operations, .accounts, .logs, .ledger]
    /// 默认 Dock：概览 · 插件 · 交互（+ 固定的「更多」与 Agent）。AI 进「更多」。
    static let defaultSlots: [DockItem] = [.overview, .plugins, .interaction]
    /// 中间格上限：3 格 +「更多」= 4 个普通 Tab，再加 Agent 共 5 个，不会触发系统自动生成的第二个“更多”。
    static let maxSlots = 3

    /// Web「界面偏好 › 常用入口」（`ui_preferences.primary_nav`）的路由 → Dock 入口。
    init?(webPath: String) {
        switch webPath {
        case "/overview": self = .overview
        case "/plugins": self = .plugins
        case "/interaction": self = .interaction
        case "/ai": self = .ai
        case "/operations": self = .operations
        case "/accounts": self = .accounts
        case "/logs": self = .logs
        case "/ledger": self = .ledger
        default: return nil
        }
    }
}

/// Dock 当前用的是哪份设置。
enum DockSource: Equatable {
    /// 本机没自定义过，跟随服务器「常用入口」（网页 设置 › 界面偏好）。
    case server
    /// 本机自定义过（App 设置 › 自定义 Dock），只在这台设备上生效。
    case local
    /// 本机没自定义、服务器也没有常用入口（旧服务器或没设置过）：App 默认。
    case defaults
}

enum DockLayout {
    /// 去重、只保留可自选入口、最多 3 个；为空时回到默认。
    static func normalized(_ items: [DockItem]) -> [DockItem] {
        var result: [DockItem] = []
        for item in items where DockItem.customizable.contains(item) && !result.contains(item) {
            result.append(item)
            if result.count == DockItem.maxSlots { break }
        }
        return result.isEmpty ? DockItem.defaultSlots : result
    }

    /// 服务器「常用入口」换成 Dock 中间格：只保留 Dock 能放的入口，按服务器顺序取前 3 个（Web 最多 4 个，
    /// iPhone 底部放不下第 4 个）。没有字段（旧服务器）、空列表（用默认）或一个都放不进时返回 nil。
    static func fromPrimaryNav(_ paths: [String]?) -> [DockItem]? {
        guard let paths else { return nil }
        var result: [DockItem] = []
        for path in paths {
            guard let item = DockItem(webPath: path), DockItem.customizable.contains(item), !result.contains(item) else { continue }
            result.append(item)
            if result.count == DockItem.maxSlots { break }
        }
        return result.isEmpty ? nil : result
    }

    /// 按服务器模块开关得出实际显示的中间格：模块关闭的入口隐藏，由补位顺序（`DockItem.customizable`）里下一个
    /// 不在 Dock、模块开着的入口补在它原来的位置（默认 Dock 里交互关闭时补上的就是 AI）；没有可补的就少一格。
    /// 服务器没有模块信息（旧版本）时 `disabledModules` 为空，保持显示。
    static func effective(slots: [DockItem], disabledModules: Set<String>) -> [DockItem] {
        func isEnabled(_ item: DockItem) -> Bool { item.platformModule.map { !disabledModules.contains($0) } ?? true }
        var result: [DockItem] = []
        var candidates = DockItem.customizable.filter { !slots.contains($0) && isEnabled($0) }
        for item in slots {
            if isEnabled(item) {
                if !result.contains(item) { result.append(item) }
            } else if !candidates.isEmpty {
                result.append(candidates.removeFirst())
            }
        }
        return result
    }
}

@MainActor @Observable
final class WorkspacePreferences {
    var hiddenHomeModules: Set<String> { didSet { save() } }
    /// 本机保存的 Dock 中间格（只存在本机，所有服务器共用）。实际用哪份见 `activeDockSlots`，显示时再经 `DockLayout.effective` 按模块开关调整。
    var dockSlots: [DockItem] { didSet { saveDock() } }
    /// 本机是否自定义过 Dock。没自定义时跟随服务器「常用入口」（第 21 轮）；在「自定义 Dock」里改动任何一项即为自定义，恢复默认取消。
    private(set) var dockCustomized: Bool { didSet { defaults.set(dockCustomized, forKey: Self.dockCustomizedKey) } }
    /// 当前服务器的「常用入口」（`ui_preferences.primary_nav`）。nil = 旧服务器没有该字段或还没读到；空列表 = 服务器用默认。
    /// 按服务器缓存在本机，启动时先用缓存，不等网络就显示对的 Dock。
    private(set) var serverPrimaryNav: [String]?
    @ObservationIgnored private var server = ""
    private(set) var accountFavorites: Set<String>
    private(set) var pluginFavorites: Set<String>
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var scope = ""

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        hiddenHomeModules = Set(defaults.stringArray(forKey: "telepilot.home.hidden") ?? [])
        accountFavorites = Set(defaults.stringArray(forKey: "telepilot.favorites.accounts") ?? [])
        pluginFavorites = Set(defaults.stringArray(forKey: "telepilot.favorites.plugins") ?? [])
        // 0.29.0 之前没有“是否自定义”的记录：存过且不是默认 Dock 的，按自定义处理（保持原样，不被服务器常用入口覆盖）。
        let savedSlots = (defaults.stringArray(forKey: Self.dockKey) ?? []).compactMap(DockItem.init(rawValue:))
        let customizedFlag = defaults.object(forKey: Self.dockCustomizedKey) as? Bool
        #if DEBUG
        // 界面测试：默认每次从默认 Dock 开始，避免上一个用例的自定义影响；`--ui-test-dock=a,b` 指定初始 Dock，
        // `--ui-test-dock-keep` 保留上次启动保存的值（验证持久化）。
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--ui-testing") {
            // 指定初始 Dock 的用例按“本机自定义过”处理，不受服务器常用入口影响。
            if let seeded = arguments.first(where: { $0.hasPrefix("--ui-test-dock=") }) {
                let items = seeded.dropFirst("--ui-test-dock=".count).split(separator: ",").compactMap { DockItem(rawValue: String($0)) }
                defaults.set(items.map(\.rawValue), forKey: Self.dockKey)
                defaults.set(true, forKey: Self.dockCustomizedKey)
            } else if !arguments.contains("--ui-test-dock-keep") {
                defaults.removeObject(forKey: Self.dockKey)
                defaults.removeObject(forKey: Self.dockCustomizedKey)
                for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Self.serverPrimaryNavKey) { defaults.removeObject(forKey: key) }
            }
            let seededSlots = (defaults.stringArray(forKey: Self.dockKey) ?? []).compactMap(DockItem.init(rawValue:))
            dockSlots = seededSlots.isEmpty ? DockItem.defaultSlots : DockLayout.normalized(seededSlots)
            dockCustomized = defaults.object(forKey: Self.dockCustomizedKey) as? Bool ?? false
            return
        }
        #endif
        dockSlots = savedSlots.isEmpty ? DockItem.defaultSlots : DockLayout.normalized(savedSlots)
        dockCustomized = customizedFlag ?? (!savedSlots.isEmpty && DockLayout.normalized(savedSlots) != DockItem.defaultSlots)
    }

    static let dockKey = "telepilot.dock.slots"
    static let dockCustomizedKey = "telepilot.dock.customized"
    static let serverPrimaryNavKey = "telepilot.dock.serverPrimaryNav."

    /// 服务器常用入口换算出的 Dock（没有可用的为 nil）。
    var serverDockSlots: [DockItem]? { DockLayout.fromPrimaryNav(serverPrimaryNav) }
    /// Dock 当前来源：本机自定义 > 服务器常用入口 > App 默认。
    var dockSource: DockSource {
        if dockCustomized { return .local }
        return serverDockSlots == nil ? .defaults : .server
    }
    /// 实际使用的 Dock 中间格（模块开关调整之前）。
    var activeDockSlots: [DockItem] {
        switch dockSource {
        case .local: dockSlots
        case .server: serverDockSlots ?? DockItem.defaultSlots
        case .defaults: DockItem.defaultSlots
        }
    }

    /// 按服务器模块开关实际显示的 Dock 中间格。
    func dockItems(disabledModules: Set<String>) -> [DockItem] { DockLayout.effective(slots: activeDockSlots, disabledModules: disabledModules) }
    /// 恢复默认：取消本机自定义，重新跟随服务器常用入口（服务器没有时用 App 默认）。
    func resetDock() {
        dockSlots = DockItem.defaultSlots
        dockCustomized = false
    }
    /// 在当前看到的 Dock 上改动，改完即成为本机自定义。
    private func editDock(_ change: (inout [DockItem]) -> Void) {
        var slots = activeDockSlots
        change(&slots)
        guard slots != activeDockSlots || !dockCustomized else { return }
        dockSlots = slots
        dockCustomized = true
    }
    func addDockSlot(_ item: DockItem) {
        guard DockItem.customizable.contains(item), !activeDockSlots.contains(item), activeDockSlots.count < DockItem.maxSlots else { return }
        editDock { $0.append(item) }
    }
    func removeDockSlot(_ item: DockItem) {
        guard activeDockSlots.count > 1, activeDockSlots.contains(item) else { return }
        editDock { $0.removeAll { $0 == item } }
    }
    func moveDockSlotUp(_ item: DockItem) {
        guard let index = activeDockSlots.firstIndex(of: item), index > 0 else { return }
        editDock { $0.swapAt(index, index - 1) }
    }
    private func saveDock() { defaults.set(dockSlots.map(\.rawValue), forKey: Self.dockKey) }

    /// 读到服务器设置后更新「常用入口」（nil = 旧服务器没有该字段，保持现有 Dock 设置）。
    func setServerPrimaryNav(_ paths: [String]?) {
        guard paths != serverPrimaryNav else { return }
        serverPrimaryNav = paths
        guard !server.isEmpty else { return }
        if let paths { defaults.set(paths, forKey: Self.serverPrimaryNavKey + server) }
        else { defaults.removeObject(forKey: Self.serverPrimaryNavKey + server) }
    }

    func setScope(server: String, userID: Int) {
        scope = server + "|" + String(userID) + "|"
        if self.server != server {
            self.server = server
            serverPrimaryNav = server.isEmpty ? nil : defaults.stringArray(forKey: Self.serverPrimaryNavKey + server)
        }
    }
    func isFavoriteAccount(_ id: Int) -> Bool { accountFavorites.contains(scope + String(id)) }
    func isFavoritePlugin(_ key: String) -> Bool { pluginFavorites.contains(scope + key) }
    func toggleAccount(_ id: Int) {
        let key = scope + String(id)
        if !accountFavorites.insert(key).inserted { accountFavorites.remove(key) }
        save()
    }
    func togglePlugin(_ plugin: String) {
        let key = scope + plugin
        if !pluginFavorites.insert(key).inserted { pluginFavorites.remove(key) }
        save()
    }
    private func save() {
        defaults.set(Array(hiddenHomeModules), forKey: "telepilot.home.hidden")
        defaults.set(Array(accountFavorites), forKey: "telepilot.favorites.accounts")
        defaults.set(Array(pluginFavorites), forKey: "telepilot.favorites.plugins")
    }
}
