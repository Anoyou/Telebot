import Foundation

/// 第 24 轮：Agent 回答上方「思考了 1m 36s ›」那一行的用时。
///
/// 来源优先级（`ReasoningDuration.resolve`）：
/// 1. 服务器记下的真实用时：`usage.stage_timings.reasoning_ms`（服务器 0.116.0-beta.2 起，只在本轮有推理时出现）；
/// 2. 本机流式时计的时（`ReasoningTimer`，按事件的服务器时间戳 `ts`，没有时按收到时间）；
/// 3. 都没有（旧服务器的历史消息）：不显示用时，只写「思考过程」。
public enum ReasoningDuration {
    /// 「1m 36s」「12s」「1h 2m」。不足 1 秒按 1 秒（显示「思考了 0s」没有意义）。
    public static func compact(_ seconds: Double) -> String {
        let total = max(1, Int(seconds.rounded()))
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 { return minutes > 0 ? "\(hours)h \(minutes)m" : "\(hours)h" }
        if minutes > 0 { return secs > 0 ? "\(minutes)m \(secs)s" : "\(minutes)m" }
        return "\(secs)s"
    }

    /// 收起时那一行的文字。
    /// - 正在思考：「正在思考…」，有计时就加「 12s」；
    /// - 已结束且知道用时：「思考了 1m 36s」；不知道用时：「思考过程」。
    public static func title(thinking: Bool, seconds: Double?) -> String {
        if thinking {
            guard let seconds else { return "正在思考…" }
            return "正在思考… " + compact(seconds)
        }
        guard let seconds else { return "思考过程" }
        return "思考了 " + compact(seconds)
    }

    /// 服务器真实用时（秒）：`usage.stage_timings.reasoning_ms`。字段缺失、为空或不是非负数时为 nil。
    public static func serverSeconds(usage: JSONValue?) -> Double? {
        guard let raw = usage?["stage_timings"]?["reasoning_ms"]?.doubleValue, raw >= 0 else { return nil }
        return raw / 1000
    }

    /// 历史消息的用时：服务器值优先，其次本机流式时记下的值（同一个任务 ID）。
    public static func resolve(usage: JSONValue?, local: [String: Double]) -> Double? {
        if let server = serverSeconds(usage: usage) { return server }
        guard let runID = usage?["run_id"]?.stringValue, !runID.isEmpty, let value = local[runID], value > 0 else { return nil }
        return value
    }

    /// 解析服务器事件的 `ts`（Python `datetime.isoformat()`：`2026-11-01T06:14:00.123456+00:00`，小数位数不定）。
    /// 解析不了时返回 nil，调用方改用收到事件的时间。
    public static func timestamp(_ text: String) -> Date? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        var base = trimmed
        var fraction = 0.0
        if let dot = trimmed.firstIndex(of: ".") {
            let afterDot = trimmed[trimmed.index(after: dot)...]
            let digits = afterDot.prefix { $0.isASCII && $0.isNumber }
            if !digits.isEmpty { fraction = Double("0." + digits) ?? 0 }
            base = String(trimmed[..<dot]) + String(afterDot.dropFirst(digits.count))
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: base) else { return nil }
        return date.addingTimeInterval(fraction)
    }
}

/// 流式时的思考计时：收到推理增量开窗，收到正文 / 工具 / 最终回答收窗；多段（工具循环里多次思考）累加。
/// 只做计算，不碰界面，便于单元测试。
public struct ReasoningTimer: Equatable, Sendable {
    /// 已结束的各段累计秒数。
    public private(set) var accumulated: Double = 0
    /// 正在进行的一段从什么时候开始（nil = 当前没在思考）。
    public private(set) var openedAt: Date?
    /// 本轮出现过推理。
    public private(set) var sawReasoning = false

    public init() {}

    public var isThinking: Bool { openedAt != nil }

    /// 收到推理增量。
    public mutating func reasoning(at date: Date) {
        sawReasoning = true
        if openedAt == nil { openedAt = date }
    }

    /// 收到正文增量、工具开始、最终回答或任务结束：结束当前这段思考。
    public mutating func stop(at date: Date) {
        guard let start = openedAt else { return }
        accumulated += max(0, date.timeIntervalSince(start))
        openedAt = nil
    }

    /// 到 `now` 为止的思考用时（正在思考时包含进行中的这段）。没有出现过推理时为 nil。
    public func elapsed(now: Date) -> Double? {
        guard sawReasoning else { return nil }
        let open = openedAt.map { max(0, now.timeIntervalSince($0)) } ?? 0
        return accumulated + open
    }

    public mutating func reset() { self = ReasoningTimer() }

    /// 这个事件对计时的影响：推理增量开窗，正文 / 工具 / 最终回答 / 结束收窗，其它不变（ignore）。
    public enum EventEffect: Equatable, Sendable { case open, close, ignore }

    public static func effect(of eventType: String) -> EventEffect {
        switch eventType {
        case "assistant_reasoning_delta", "reasoning_delta": .open
        case "assistant_delta", "tool_started", "assistant_message", "done", "error", "waiting_input", "waiting_approval": .close
        default: .ignore
        }
    }
}
