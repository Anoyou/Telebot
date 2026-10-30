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
        case .logs: "日志与诊断"
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
    /// 用户自选的 Dock 中间格（只存在本机，所有服务器共用）。实际显示还要经过 `DockLayout.effective` 按模块开关调整。
    var dockSlots: [DockItem] { didSet { saveDock() } }
    private(set) var accountFavorites: Set<String>
    private(set) var pluginFavorites: Set<String>
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var scope = ""

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        hiddenHomeModules = Set(defaults.stringArray(forKey: "telepilot.home.hidden") ?? [])
        accountFavorites = Set(defaults.stringArray(forKey: "telepilot.favorites.accounts") ?? [])
        pluginFavorites = Set(defaults.stringArray(forKey: "telepilot.favorites.plugins") ?? [])
        #if DEBUG
        // 界面测试：默认每次从默认 Dock 开始，避免上一个用例的自定义影响；`--ui-test-dock=a,b` 指定初始 Dock，
        // `--ui-test-dock-keep` 保留上次启动保存的值（验证持久化）。
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--ui-testing") {
            if let seeded = arguments.first(where: { $0.hasPrefix("--ui-test-dock=") }) {
                let items = seeded.dropFirst("--ui-test-dock=".count).split(separator: ",").compactMap { DockItem(rawValue: String($0)) }
                defaults.set(items.map(\.rawValue), forKey: Self.dockKey)
            } else if !arguments.contains("--ui-test-dock-keep") {
                defaults.removeObject(forKey: Self.dockKey)
            }
        }
        #endif
        let saved = (defaults.stringArray(forKey: Self.dockKey) ?? []).compactMap(DockItem.init(rawValue:))
        dockSlots = saved.isEmpty ? DockItem.defaultSlots : DockLayout.normalized(saved)
    }

    static let dockKey = "telepilot.dock.slots"

    /// 按服务器模块开关实际显示的 Dock 中间格。
    func dockItems(disabledModules: Set<String>) -> [DockItem] { DockLayout.effective(slots: dockSlots, disabledModules: disabledModules) }
    func resetDock() { dockSlots = DockItem.defaultSlots }
    func addDockSlot(_ item: DockItem) {
        guard DockItem.customizable.contains(item), !dockSlots.contains(item), dockSlots.count < DockItem.maxSlots else { return }
        dockSlots.append(item)
    }
    func removeDockSlot(_ item: DockItem) {
        guard dockSlots.count > 1 else { return }
        dockSlots.removeAll { $0 == item }
    }
    func moveDockSlotUp(_ item: DockItem) {
        guard let index = dockSlots.firstIndex(of: item), index > 0 else { return }
        dockSlots.swapAt(index, index - 1)
    }
    private func saveDock() { defaults.set(dockSlots.map(\.rawValue), forKey: Self.dockKey) }

    func setScope(server: String, userID: Int) { scope = server + "|" + String(userID) + "|" }
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
