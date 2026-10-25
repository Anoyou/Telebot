import Foundation

/// iOS App 自身更新日志（`ios/CHANGELOG.md`）的解析结果。
///
/// 文件随 App 打包，按 Keep a Changelog 约定书写：
/// `## [版本] - 日期 · 级别 · 主题`，可选的 `> 对应服务器：…` 说明行，`### 分类` 与 `- 条目`。
public struct AppChangelogRelease: Identifiable, Equatable, Sendable {
    public struct Group: Identifiable, Equatable, Sendable {
        public var title: String
        public var items: [String]
        public var id: String { title }
    }

    public var version: String
    public var date: String?
    public var level: String?
    public var title: String?
    public var note: String?
    public var groups: [Group]
    public var id: String { version }
    public var isUnreleased: Bool { version.caseInsensitiveCompare("Unreleased") == .orderedSame }
    public var itemCount: Int { groups.reduce(0) { $0 + $1.items.count } }
}

public enum AppChangelog {
    /// 解析 Markdown；只保留有条目的版本段落（空的 Unreleased 会被省略）。
    public static func parse(_ markdown: String) -> [AppChangelogRelease] {
        var releases: [AppChangelogRelease] = []
        var current: AppChangelogRelease?
        var lastItemLine = false

        func flush() {
            if var release = current {
                release.groups.removeAll { $0.items.isEmpty }
                if release.itemCount > 0 { releases.append(release) }
            }
            current = nil
        }

        for rawLine in markdown.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("## ") {
                flush()
                current = header(String(line.dropFirst(3)))
                lastItemLine = false
                continue
            }
            // 先取出副本修改再写回，避免对可选值 `current` 的重叠访问。
            guard var release = current else { continue }
            if line.hasPrefix("### ") {
                release.groups.append(.init(title: String(line.dropFirst(4)).trimmingCharacters(in: .whitespaces), items: []))
                lastItemLine = false
            } else if line.hasPrefix("> ") {
                let text = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                release.note = release.note.map { "\($0) \(text)" } ?? text
                lastItemLine = false
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                if release.groups.isEmpty { release.groups.append(.init(title: "变化", items: [])) }
                let text = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                release.groups[release.groups.count - 1].items.append(text)
                lastItemLine = true
            } else if line.isEmpty {
                lastItemLine = false
            } else if lastItemLine, let groupIndex = release.groups.indices.last,
                      let itemIndex = release.groups[groupIndex].items.indices.last {
                // 条目的续行：拼接到上一条。
                release.groups[groupIndex].items[itemIndex] += line
            }
            current = release
        }
        flush()
        return releases
    }

    private static func header(_ text: String) -> AppChangelogRelease {
        var version = text
        var rest = ""
        if text.hasPrefix("["), let close = text.firstIndex(of: "]") {
            version = String(text[text.index(after: text.startIndex)..<close])
            rest = String(text[text.index(after: close)...])
        }
        rest = rest.trimmingCharacters(in: .whitespaces)
        if rest.hasPrefix("-") { rest = String(rest.dropFirst()).trimmingCharacters(in: .whitespaces) }
        let parts = rest.components(separatedBy: "·").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        return AppChangelogRelease(
            version: version.trimmingCharacters(in: .whitespaces),
            date: parts.first,
            level: parts.count > 2 ? parts[1] : nil,
            title: parts.count > 2 ? parts[2...].joined(separator: " · ") : (parts.count == 2 ? parts[1] : nil),
            note: nil,
            groups: []
        )
    }
}
