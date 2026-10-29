import Foundation

/// 插件原生卡片（`ui.cards`）的块模型，与 Web `frontend/src/lib/pluginCards.ts` 同一套整理规则：
/// 声明（stats / kv / table / list / buttons）+ 卡片数据 → 可直接渲染的模型。SwiftUI 只负责画。
public enum PluginCardTone: String, Sendable, CaseIterable {
    case neutral, success, warning, danger, accent

    public init(_ value: JSONValue?) {
        self = value?.stringValue.flatMap(PluginCardTone.init(rawValue:)) ?? .neutral
    }
}

public struct PluginCardItem: Hashable, Sendable {
    public let label: String
    public let text: String
    public let tone: PluginCardTone
}

public struct PluginCardCell: Hashable, Sendable {
    public let text: String
    public let tone: PluginCardTone
}

public struct PluginCardRow: Hashable, Sendable {
    public let key: String
    public let cells: [PluginCardCell]
}

public struct PluginCardListItem: Hashable, Sendable {
    public let key: String
    public let title: String
    public let subtitle: String
    public let trailing: String
    public let tone: PluginCardTone
}

public struct PluginCardButton: Hashable, Sendable {
    public let id: String
    public let label: String
    public let destructive: Bool
    public let confirm: String
}

public enum PluginCardBlock: Hashable, Sendable {
    case stats(title: String?, items: [PluginCardItem])
    case kv(title: String?, items: [PluginCardItem])
    case table(title: String?, columns: [String], rows: [PluginCardRow], empty: String)
    case list(title: String?, items: [PluginCardListItem], empty: String, hidden: Bool)
    case buttons([PluginCardButton])
    case unsupported(String)

    /// App 实现的布局（单测校验与契约 cards.layouts 一致）。
    public static let supportedLayouts = ["stats", "kv", "table", "list", "buttons"]

    public static func build(_ block: JSONValue, data: JSONValue, now: Date = Date()) -> PluginCardBlock {
        let type = block["type"]?.stringValue ?? ""
        let title = block["title"]?.stringValue
        switch type {
        case "stats", "kv":
            var source = block["items"]?.arrayValue ?? []
            if source.isEmpty { source = PluginCardFormat.value(at: block["items_path"]?.stringValue ?? "items", in: data)?.arrayValue ?? [] }
            let items = source.map { item -> PluginCardItem in
                let value = item["value"] ?? PluginCardFormat.value(at: item["path"]?.stringValue, in: data)
                var text = PluginCardFormat.format(value, format: item["format"]?.stringValue, now: now)
                if let unit = item["unit"]?.stringValue, !unit.isEmpty, value != nil, value?.isNull == false { text += " " + unit }
                let tonePath = item["tone_path"]?.stringValue
                let tone: JSONValue? = tonePath != nil ? PluginCardFormat.value(at: tonePath, in: data) : item["tone"]
                return PluginCardItem(label: item["label"]?.stringValue ?? "", text: text, tone: PluginCardTone(tone))
            }
            return type == "stats" ? .stats(title: title, items: items) : .kv(title: title, items: items)
        case "table":
            let rows = PluginCardFormat.value(at: block["rows_path"]?.stringValue ?? "rows", in: data)?.arrayValue ?? []
            var columns = (block["columns"]?.arrayValue ?? []).compactMap(\.objectValue)
            if columns.isEmpty, let first = rows.first?.objectValue {
                columns = first.keys.sorted().map { ["key": .string($0), "label": .string($0)] }
            }
            let rowKey = block["row_key"]?.stringValue ?? ""
            let built = rows.enumerated().map { index, row -> PluginCardRow in
                let key = rowKey.isEmpty ? String(index) : (row[rowKey].map { PluginCardFormat.format($0, format: nil, now: now) } ?? String(index))
                let cells = columns.map { column -> PluginCardCell in
                    let columnKey = column["key"]?.stringValue ?? ""
                    let toneKey = column["tone_key"]?.stringValue
                    return PluginCardCell(
                        text: PluginCardFormat.format(PluginCardFormat.value(at: columnKey, in: row), format: column["format"]?.stringValue, now: now),
                        tone: PluginCardTone(toneKey.flatMap { row[$0] })
                    )
                }
                return PluginCardRow(key: key, cells: cells)
            }
            let labels = columns.map { $0["label"]?.stringValue ?? $0["key"]?.stringValue ?? "" }
            return .table(title: title, columns: labels, rows: built, empty: block["empty"]?.stringValue ?? "暂无数据")
        case "list":
            let raw = PluginCardFormat.value(at: block["items_path"]?.stringValue ?? "items", in: data)?.arrayValue ?? []
            let titleKey = block["title_key"]?.stringValue ?? "title"
            let subtitleKey = block["subtitle_key"]?.stringValue ?? "subtitle"
            let trailingKey = block["trailing_key"]?.stringValue ?? "trailing"
            let toneKey = block["tone_key"]?.stringValue ?? "tone"
            let items = raw.enumerated().map { index, item in
                PluginCardListItem(
                    key: "\(index)",
                    title: PluginCardFormat.text(item[titleKey]),
                    subtitle: PluginCardFormat.text(item[subtitleKey]),
                    trailing: PluginCardFormat.text(item[trailingKey]),
                    tone: PluginCardTone(item[toneKey])
                )
            }
            return .list(title: title, items: items, empty: block["empty"]?.stringValue ?? "暂无内容",
                         hidden: block["hide_when_empty"]?.boolValue == true && items.isEmpty)
        case "buttons":
            let items = (block["items"]?.arrayValue ?? []).compactMap { item -> PluginCardButton? in
                guard let id = item["id"]?.stringValue, !id.isEmpty else { return nil }
                return PluginCardButton(id: id, label: item["label"]?.stringValue ?? id,
                                        destructive: item["style"]?.stringValue == "destructive",
                                        confirm: item["confirm"]?.stringValue ?? "")
            }
            return .buttons(items)
        default:
            return .unsupported(type)
        }
    }
}

public enum PluginCardFormat {
    public static func value(at path: String?, in data: JSONValue) -> JSONValue? {
        guard let path, !path.isEmpty else { return data }
        var current: JSONValue? = data
        for part in path.split(separator: ".").map(String.init) {
            guard let node = current else { return nil }
            if let array = node.arrayValue {
                guard let index = Int(part), array.indices.contains(index) else { return nil }
                current = array[index]
            } else {
                current = node[part]
            }
        }
        return current
    }

    static func text(_ value: JSONValue?) -> String {
        guard let value, !value.isNull else { return "" }
        return format(value, format: nil, now: Date())
    }

    /// 按契约 formats 格式化；空值显示 "-"。
    public static func format(_ value: JSONValue?, format: String?, now: Date = Date()) -> String {
        guard let value, !value.isNull else { return "-" }
        if let string = value.stringValue, string.isEmpty { return "-" }
        let number = value.doubleValue ?? value.stringValue.flatMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        switch format {
        case "number":
            guard let number else { return plain(value) }
            return number.formatted(.number.locale(Locale(identifier: "zh_CN")))
        case "percent":
            guard let number else { return plain(value) }
            let percent = number * 100
            if percent == 0 { return "0%" }
            return percent >= 10 ? "\(Int(percent.rounded()))%" : String(format: "%.1f%%", percent)
        case "duration_ms":
            guard let number else { return plain(value) }
            if number >= 10_000 { return "\(Int((number / 1000).rounded())) 秒" }
            if number >= 1000 { return String(format: "%.1f 秒", number / 1000) }
            return "\(Int(number.rounded())) 毫秒"
        case "datetime":
            guard let number else { return plain(value) }
            return Date(timeIntervalSince1970: number).formatted(date: .abbreviated, time: .shortened)
        case "relative_time":
            guard let number, number > 0 else { return "-" }
            let diff = max(0, Int((now.timeIntervalSince1970 - number).rounded()))
            if diff < 60 { return "刚刚" }
            if diff < 3600 { return "\(diff / 60) 分钟前" }
            if diff < 86_400 { return "\(diff / 3600) 小时前" }
            return "\(diff / 86_400) 天前"
        default:
            return plain(value)
        }
    }

    static func plain(_ value: JSONValue) -> String {
        switch value {
        case .string(let text): text
        case .integer(let number): String(number)
        case .number(let number): number == number.rounded() && abs(number) < 1e15 ? String(Int64(number)) : String(number)
        case .bool(let flag): flag ? "是" : "否"
        case .null: "-"
        case .array, .object: (try? String(data: value.data(), encoding: .utf8) ?? "") ?? ""
        }
    }
}
