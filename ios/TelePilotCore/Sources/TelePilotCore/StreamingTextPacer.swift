import Foundation

/// 第 23 轮：Agent 回答的平滑打字效果——节奏器。
///
/// 服务器大约每 0.2 秒推一块文字，原来收到一块就整块显示，看起来一跳一跳。现在客户端把收到的文字放进积压，
/// 每帧按字符显示一部分（类似 ChatGPT）：
/// - 速度自适应：每秒显示 `max(minimumRate, 积压字数 / targetLag)` 个字，积压越多越快，正常情况下显示比收到
///   落后约 `targetLag` 秒，不会越积越多；
/// - 流结束后（`finished`）在 `finishWindow` 秒内把剩下的全部显示完；
/// - 只计算“这一帧显示多少个字”，不碰界面，便于单元测试。
public struct StreamingTextPacer: Equatable, Sendable {
    /// 积压很少时的最低速度（字/秒）。
    public var minimumRate: Double
    /// 流式中：积压大约按这么多秒显示完。
    public var targetLag: Double
    /// 流结束后：最多这么多秒追平全部文字。
    public var finishWindow: Double
    /// 单帧最多按多长时间计算（主线程偶尔卡一下时不至于一下跳出一大段；积压比例会让速度自然追上）。
    public var maxElapsed: Double

    private var carry: Double = 0
    private var finishRemaining: Double?

    public init(minimumRate: Double = 45, targetLag: Double = 0.35, finishWindow: Double = 0.3, maxElapsed: Double = 0.25) {
        self.minimumRate = minimumRate
        self.targetLag = targetLag
        self.finishWindow = finishWindow
        self.maxElapsed = maxElapsed
    }

    public mutating func reset() {
        carry = 0
        finishRemaining = nil
    }

    /// 这一帧应该新显示多少个字符（Character）。
    /// - Parameters:
    ///   - backlog: 已收到但还没显示的字符数。
    ///   - elapsed: 距上一帧的秒数。
    ///   - finished: 流已经结束（收到最终回答或进度流关闭）。
    public mutating func advance(backlog: Int, elapsed: Double, finished: Bool) -> Int {
        guard backlog > 0 else {
            carry = 0
            return 0
        }
        let dt = min(max(elapsed, 0), maxElapsed)
        let streamingRate = max(minimumRate, Double(backlog) / max(targetLag, 0.01))
        if finished {
            let remaining = finishRemaining ?? finishWindow
            let left = remaining - dt
            finishRemaining = left
            // 留一点浮点余量：0.3 − 9 × (1/30) 可能是 1e-17 而不是 0。
            if left <= 1e-6 || remaining <= 1e-6 {
                carry = 0
                return backlog
            }
            // 按剩余时间均摊，且不慢于流式中的速度。
            let exact = max(streamingRate * dt, Double(backlog) * dt / remaining) + carry
            let count = Int(exact.rounded(.down))
            carry = exact - Double(count)
            return min(backlog, count)
        }
        finishRemaining = nil
        let exact = streamingRate * dt + carry
        let count = Int(exact.rounded(.down))
        carry = exact - Double(count)
        return min(backlog, count)
    }

    /// 帧间隔（秒）：最后一块（每帧都要重新解析的部分）不长时约 30 帧/秒；很长（例如一大段代码块）时放慢到约 12 帧/秒，
    /// 避免长回答卡顿——每帧多显示几个字，总速度不变。
    public static func tickInterval(tailUTF8: Int) -> Double {
        tailUTF8 < 2_000 ? 1.0 / 30.0 : 1.0 / 12.0
    }
}

/// 第 23 轮：一段流式回答的“已收到 / 已显示”状态。`received` 是收到的全部文字，`revealedUTF8` 是已经显示到的
/// UTF-8 偏移；每帧 `tick` 返回这一帧新显示的文字，调用方直接拼到显示文字后面（拼起来与 `received` 的前缀逐字节相同）。
///
/// - 按字符（Character）计数，不会切开汉字或 emoji；
/// - 正在显示一个英文单词／一串 ASCII（网址、Token、数字）时一次显示到这串的末尾：英文按词出现，
///   也不会把 Token、密码值一个字一个字露出来（脱敏规则要看到整串才能遮住）；
/// - `finish(final:)`：最终回答是已显示内容的延续时接着平滑追平，不是（服务器改写过）时返回 false，由调用方整段替换。
public struct StreamingTextReveal: Sendable {
    public private(set) var received = ""
    public private(set) var revealedUTF8 = 0
    public private(set) var finished = false
    public var pacer: StreamingTextPacer

    public init(pacer: StreamingTextPacer = StreamingTextPacer()) {
        self.pacer = pacer
    }

    /// 全部显示完了。
    public var isCaughtUp: Bool { revealedUTF8 >= received.utf8.count }

    /// 已显示的文字（测试与调试用；界面自己累积显示文字，不每帧调用）。
    public var revealedText: String { StreamingMarkdownBoundary.slice(received, from: 0, to: revealedUTF8) }

    /// 还没显示的字符数。
    public var backlogCharacters: Int { received[revealIndex...].count }

    private var revealIndex: String.Index {
        received.utf8.index(received.utf8.startIndex, offsetBy: min(revealedUTF8, received.utf8.count))
    }

    public mutating func append(_ delta: String) {
        guard !delta.isEmpty else { return }
        received += delta
        // 流结束后又来了新文字（极少见）：回到流式节奏。
        if finished { finished = false; pacer.reset() }
    }

    /// 流结束。`final` 为最终回答全文：是已显示内容的延续就接着追平（返回 true），否则返回 false（调用方整段替换并 `reset`）。
    @discardableResult
    public mutating func finish(final: String? = nil) -> Bool {
        if let final, final != received {
            guard final.utf8.starts(with: received.utf8.prefix(revealedUTF8)) else { return false }
            received = final
        }
        finished = true
        return true
    }

    public mutating func reset() {
        received = ""
        revealedUTF8 = 0
        finished = false
        pacer.reset()
    }

    /// 立即显示全部（用户滚动、停止生成、切走、减弱动态效果），返回新显示的文字。
    public mutating func revealAll() -> String {
        guard !isCaughtUp else { return "" }
        let chunk = StreamingMarkdownBoundary.slice(received, from: revealedUTF8)
        revealedUTF8 = received.utf8.count
        return chunk
    }

    /// 走一帧，返回这一帧新显示的文字（可能为空）。
    public mutating func tick(elapsed: Double) -> String {
        guard !isCaughtUp else { return "" }
        let start = revealIndex
        let backlog = received[start...].count
        let count = pacer.advance(backlog: backlog, elapsed: elapsed, finished: finished)
        guard count > 0 else { return "" }
        var end = received.index(start, offsetBy: count, limitedBy: received.endIndex) ?? received.endIndex
        end = Self.extendThroughASCIIRun(received, from: end)
        var endOffset = received.utf8.distance(from: received.utf8.startIndex, to: end)
        if endOffset <= revealedUTF8 { endOffset = received.utf8.count }
        let chunk = StreamingMarkdownBoundary.slice(received, from: revealedUTF8, to: endOffset)
        revealedUTF8 = endOffset
        return chunk
    }

    /// `index` 正好落在一串可见 ASCII 字符（英文单词、网址、Token、数字）中间时，延伸到这串的末尾（不超过已收到的文字）。
    static func extendThroughASCIIRun(_ text: String, from index: String.Index) -> String.Index {
        let bytes = text.utf8
        guard index > bytes.startIndex, index < bytes.endIndex else { return index }
        let previous = bytes[bytes.index(before: index)]
        guard isVisibleASCII(previous), isVisibleASCII(bytes[index]) else { return index }
        var cursor = index
        while cursor < bytes.endIndex, isVisibleASCII(bytes[cursor]) {
            cursor = bytes.index(after: cursor)
        }
        return cursor
    }

    private static func isVisibleASCII(_ byte: UInt8) -> Bool { byte >= 0x21 && byte <= 0x7E }
}
