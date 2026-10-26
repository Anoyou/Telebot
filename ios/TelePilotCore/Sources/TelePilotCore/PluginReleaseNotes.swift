import Foundation

/// 插件 `CHANGELOG.md` 中的一个版本段落，用于插件商店的「新功能」与版本历史。
public struct PluginRelease: Identifiable, Equatable, Sendable {
    /// 去掉前导 `v` 的版本号，例如 `0.1.13`。
    public var version: String
    /// 段落标题里的日期（`YYYY-MM-DD`），没有写日期时为 nil。
    public var date: String?
    /// 条目文本（保留行内 Markdown，例如反引号代码）。
    public var items: [String]
    public var id: String { version }

    public init(version: String, date: String? = nil, items: [String] = []) {
        self.version = version
        self.date = date
        self.items = items
    }
}

/// 版本号比较：与服务器 `_version_tuple` 一致，只取前三组数字比较，缺位补 0。
public enum PluginVersion {
    public static func components(_ raw: String) -> [Int] {
        var numbers: [Int] = []
        var current = ""
        for character in raw {
            if character.isASCII, character.isNumber {
                current.append(character)
            } else if !current.isEmpty {
                numbers.append(Int(current) ?? 0)
                current = ""
                if numbers.count == 3 { break }
            }
        }
        if !current.isEmpty, numbers.count < 3 { numbers.append(Int(current) ?? 0) }
        while numbers.count < 3 { numbers.append(0) }
        return Array(numbers.prefix(3))
    }

    public static func hasDigits(_ raw: String) -> Bool {
        raw.contains { $0.isASCII && $0.isNumber }
    }

    public static func compare(_ lhs: String, _ rhs: String) -> ComparisonResult {
        for (left, right) in zip(components(lhs), components(rhs)) where left != right {
            return left < right ? .orderedAscending : .orderedDescending
        }
        return .orderedSame
    }

    public static func isNewer(_ candidate: String, than current: String) -> Bool {
        compare(candidate, current) == .orderedDescending
    }
}

/// 解析插件的 `CHANGELOG.md`。兼容常见写法：
/// `## [1.2.0] - 2026-10-01`、`## 0.1.13 (2026-10-26)`、`## v1.0.0 - 2026-01-01`、`## 1.0.0`。
/// 不含数字的段落（例如 `Unreleased`）与没有条目的段落会被忽略。
public enum PluginReleaseNotes {
    public static func parse(_ markdown: String) -> [PluginRelease] {
        var releases: [PluginRelease] = []
        var current: PluginRelease?
        var lastWasItem = false

        func flush() {
            if let release = current, !release.items.isEmpty { releases.append(release) }
            current = nil
        }

        for rawLine in markdown.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("## ") {
                flush()
                current = header(String(line.dropFirst(3)))
                lastWasItem = false
                continue
            }
            if line.hasPrefix("# ") {
                // 一级标题（文件标题）结束当前段落，之后的内容不属于任何版本。
                flush()
                lastWasItem = false
                continue
            }
            guard var release = current else { continue }
            if line.isEmpty {
                lastWasItem = false
            } else if line.hasPrefix("#") {
                // `### 新增` 之类的小标题：不单独成条。
                lastWasItem = false
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("+ ") {
                release.items.append(String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces))
                lastWasItem = true
            } else if lastWasItem, let index = release.items.indices.last {
                release.items[index] += " " + line
            } else {
                release.items.append(line)
                lastWasItem = true
            }
            current = release
        }
        flush()
        return releases
    }

    /// 比 `installed` 新、且不超过 `latest` 的版本段落（按文件中的顺序，通常是从新到旧）。
    public static func releases(_ releases: [PluginRelease], newerThan installed: String?, upTo latest: String? = nil) -> [PluginRelease] {
        releases.filter { release in
            if let installed, !PluginVersion.isNewer(release.version, than: installed) { return false }
            if let latest, PluginVersion.isNewer(release.version, than: latest) { return false }
            return true
        }
    }

    /// 与指定版本号相同的段落。
    public static func release(_ releases: [PluginRelease], for version: String?) -> PluginRelease? {
        guard let version, PluginVersion.hasDigits(version) else { return nil }
        return releases.first { PluginVersion.compare($0.version, version) == .orderedSame }
    }

    static func header(_ text: String) -> PluginRelease? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        var version: String
        var rest: String
        if trimmed.hasPrefix("["), let close = trimmed.firstIndex(of: "]") {
            version = String(trimmed[trimmed.index(after: trimmed.startIndex)..<close])
            rest = String(trimmed[trimmed.index(after: close)...])
        } else {
            let end = trimmed.firstIndex { $0 == " " || $0 == "(" || $0 == "\t" } ?? trimmed.endIndex
            version = String(trimmed[..<end])
            rest = String(trimmed[end...])
        }
        version = version.trimmingCharacters(in: .whitespaces)
        if let first = version.first, first == "v" || first == "V",
           version.dropFirst().first?.isNumber == true {
            version = String(version.dropFirst())
        }
        guard PluginVersion.hasDigits(version) else { return nil }
        var date: String?
        if let range = rest.range(of: #"\d{4}-\d{1,2}-\d{1,2}"#, options: .regularExpression) {
            date = String(rest[range])
        }
        return PluginRelease(version: version, date: date)
    }
}
