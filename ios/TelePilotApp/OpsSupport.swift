import SwiftUI
import UIKit
import TelePilotCore

/// 日志、台账与概览共用的小工具：时间解析、金额格式、查询拼接与复制诊断信息。
/// 所有展示和复制出口都经过 AssistantRedaction，避免凭据进入剪贴板。
enum OpsFormat {
    nonisolated(unsafe) private static let fractionalISO: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    nonisolated(unsafe) private static let plainISO = ISO8601DateFormatter()

    /// 解析后端 ISO 时间。兼容微秒精度、`+00:00` 时区与不带时区的时间（按 UTC）。
    static func date(_ raw: String?) -> Date? {
        guard var text = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        if text.count == 10, let parsedDay = Self.day(text) { return parsedDay }
        if let range = text.range(of: #"\.[0-9]+"#, options: .regularExpression) {
            let digits = String(text[range].dropFirst().prefix(3))
            text.replaceSubrange(range, with: "." + digits.padding(toLength: 3, withPad: "0", startingAt: 0))
        }
        let hasZone = text.hasSuffix("Z") || text.hasSuffix("z")
            || text.range(of: #"[+-][0-9]{2}:?[0-9]{2}$"#, options: .regularExpression) != nil
        if !hasZone { text += "Z" }
        text = text.replacingOccurrences(of: " ", with: "T")
        return fractionalISO.date(from: text) ?? plainISO.date(from: text)
    }

    static func date(_ value: JSONValue?) -> Date? {
        if let text = value?.stringValue { return date(text) }
        if let seconds = value?.doubleValue, seconds > 0 { return Date(timeIntervalSince1970: seconds) }
        return nil
    }

    /// `2026-10-24` 这类按日分桶键，按本机日历解析为当天零点，图表日期才不会跨日。
    static func day(_ key: String) -> Date? {
        let parts = key.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return Calendar.current.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    static func time(_ date: Date?) -> String {
        guard let date else { return "时间未记录" }
        if Calendar.current.isDateInToday(date) { return "今天 " + date.formatted(date: .omitted, time: .shortened) }
        if Calendar.current.isDateInYesterday(date) { return "昨天 " + date.formatted(date: .omitted, time: .shortened) }
        return date.formatted(date: .abbreviated, time: .shortened)
    }

    static func fullTime(_ date: Date?) -> String {
        guard let date else { return "时间未记录" }
        return date.formatted(date: .numeric, time: .standard)
    }

    static func relative(_ date: Date?) -> String {
        guard let date else { return "时间未记录" }
        if abs(date.timeIntervalSinceNow) < 60 { return "刚刚" }
        return date.formatted(.relative(presentation: .named))
    }

    static func duration(milliseconds: Int?) -> String? {
        guard let milliseconds else { return nil }
        if milliseconds < 1_000 { return "\(milliseconds) 毫秒" }
        let seconds = Double(milliseconds) / 1_000
        if seconds < 60 { return String(format: "%.1f 秒", seconds) }
        return "\(Int(seconds) / 60) 分 \(Int(seconds) % 60) 秒"
    }

    static func decimal(_ value: JSONValue?) -> Decimal {
        if let text = value?.stringValue, let parsed = Decimal(string: text.trimmingCharacters(in: .whitespaces), locale: Locale(identifier: "en_US_POSIX")) {
            return parsed
        }
        if let number = value?.doubleValue { return Decimal(number) }
        return 0
    }

    static func double(_ value: Decimal) -> Double { NSDecimalNumber(decimal: value).doubleValue }

    static func amount(_ value: Decimal, signed: Bool = false) -> String {
        let text = value.formatted(.number.precision(.fractionLength(0...8)))
        return signed && value > 0 ? "+" + text : text
    }

    static func integer(_ value: Int) -> String { value.formatted(.number.grouping(.automatic)) }

    /// 列表接口没有总数，达到上限只能说明至少有这么多条。
    static func sampledCount(_ value: Int, limit: Int) -> String {
        integer(value) + (value >= limit ? "+" : "")
    }

    /// 只保留 ASCII 安全字符，其余（含中文、`+`、空格）全部百分号编码。
    static func path(_ base: String, _ items: [(String, String?)]) -> String {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~:")
        let parts = items.compactMap { key, value -> String? in
            guard let value, !value.isEmpty, let encoded = value.addingPercentEncoding(withAllowedCharacters: allowed) else { return nil }
            return key + "=" + encoded
        }
        return parts.isEmpty ? base : base + "?" + parts.joined(separator: "&")
    }

    static func iso(_ date: Date) -> String { date.ISO8601Format() }

    static func text(_ value: JSONValue?) -> String? {
        guard let value, !value.isNull else { return nil }
        if let text = value.stringValue {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : AssistantRedaction.text(trimmed)
        }
        if let integer = value.intValue { return String(integer) }
        if let number = value.doubleValue { return String(number) }
        if let bool = value.boolValue { return bool ? "是" : "否" }
        return nil
    }

    static func errorMessage(_ error: Error) -> String {
        if let error = error as? APIError {
            if case .server(let status, _) = error, status == 403 || status == 409 || status == 423 {
                return "服务器上这个功能模块未启用或被暂停（HTTP \(status)）。可在“服务器设置 › 平台能力开关”中检查。"
            }
            return error.localizedDescription
        }
        return "暂时无法读取，请检查网络后下拉重试。"
    }

    /// 复制给他人或助手做诊断的文本。逐行脱敏后写入剪贴板。
    @MainActor static func copyForDiagnosis(_ lines: [String]) {
        let header = "TelePilot 诊断信息（\(Date.now.formatted(date: .numeric, time: .standard))）"
        UIPasteboard.general.string = AssistantRedaction.text(([header] + lines).joined(separator: "\n"))
    }
}

enum OpsFetch {
    @MainActor static func result(_ model: AppModel, _ path: String, enabled: Bool = true) async -> Result<JSONValue, Error> {
        guard enabled else { return .success(.array([])) }
        do { return .success(try await model.request(path: path)) }
        catch { return .failure(error) }
    }
}

/// 统一的严重程度：颜色 + 图标 + 文字三者同时表达，满足“不能只靠颜色区分”。
enum OpsSeverity: Int, Comparable, CaseIterable, Sendable {
    case debug, info, success, warning, error

    static func < (lhs: OpsSeverity, rhs: OpsSeverity) -> Bool { lhs.rawValue < rhs.rawValue }

    var title: String {
        switch self {
        case .debug: "调试"
        case .info: "信息"
        case .success: "正常"
        case .warning: "告警"
        case .error: "错误"
        }
    }
    var symbol: String {
        switch self {
        case .debug: "ant"
        case .info: "info.circle.fill"
        case .success: "checkmark.circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        }
    }
    var color: Color {
        switch self {
        case .debug: .secondary
        case .info: .blue
        case .success: .green
        case .warning: .orange
        case .error: .red
        }
    }

    static func runtime(_ raw: String?) -> OpsSeverity {
        switch (raw ?? "").lowercased() {
        case "debug", "trace": .debug
        case "warn", "warning": .warning
        case "error", "critical", "fatal", "exception": .error
        default: .info
        }
    }
}

struct OpsSeverityIcon: View {
    let severity: OpsSeverity
    var body: some View {
        Image(systemName: severity.symbol)
            .foregroundStyle(severity.color)
            .font(.body)
            .frame(width: 26)
            .accessibilityLabel(severity.title)
    }
}

/// 小号状态标签：文字 + 图标 + 颜色。
struct OpsBadge: View {
    let title: String
    var symbol: String
    var color: Color = .secondary
    var body: some View {
        Label(title, systemImage: symbol)
            .font(.caption.weight(.medium))
            .labelStyle(.titleAndIcon)
            .foregroundStyle(color)
            .lineLimit(1)
    }
}

/// 概览与台账的数字卡片，内容区不叠加玻璃，仅用系统分组背景。
struct OpsMetricTile: View {
    let title: String
    let value: String
    var symbol: String
    var color: Color = .primary
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(title, systemImage: symbol)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Text(value)
                .font(.title3.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let detail {
                Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
        .padding(10)
        .background(Color(uiColor: .tertiarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

struct OpsMetricGrid<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 10)], spacing: 10) { content }
            .padding(.vertical, 4)
    }
}

/// 一个字段一行的可读详情：标签在上，值在下，可选中复制。
struct OpsFieldRow: View {
    let title: String
    let value: String?
    var symbol: String?
    var monospaced = false

    var body: some View {
        if let value, !value.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                if let symbol {
                    Label(title, systemImage: symbol).font(.caption).foregroundStyle(.secondary)
                } else {
                    Text(title).font(.caption).foregroundStyle(.secondary)
                }
                Text(value)
                    .font(monospaced ? .callout.monospaced() : .body)
                    .textSelection(.enabled)
            }
            .padding(.vertical, 1)
            .accessibilityElement(children: .combine)
        }
    }
}
