import SwiftUI
import Observation

enum DockItem: String, CaseIterable, Codable, Identifiable {
    case overview, plugins, ai, more, agent
    var id: String { rawValue }
    var title: String {
        switch self {
        case .overview: "概览"
        case .plugins: "插件"
        case .ai: "AI"
        case .more: "更多"
        case .agent: "Agent"
        }
    }
    var symbol: String {
        switch self {
        case .overview: "square.grid.2x2"
        case .plugins: "puzzlepiece.extension"
        case .ai: "cpu"
        case .more: "ellipsis.circle"
        case .agent: "sparkles"
        }
    }
}

@MainActor @Observable
final class WorkspacePreferences {
    var hiddenHomeModules: Set<String> { didSet { save() } }
    private(set) var accountFavorites: Set<String>
    private(set) var pluginFavorites: Set<String>
    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private var scope = ""

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        hiddenHomeModules = Set(defaults.stringArray(forKey: "telepilot.home.hidden") ?? [])
        accountFavorites = Set(defaults.stringArray(forKey: "telepilot.favorites.accounts") ?? [])
        pluginFavorites = Set(defaults.stringArray(forKey: "telepilot.favorites.plugins") ?? [])
    }

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
