import Foundation
import Testing
@testable import TelePilotCore

@Suite struct StreamingTextPacerTests {
    private let frame = 1.0 / 30.0

    @Test func nothingToShowReturnsZero() {
        var pacer = StreamingTextPacer()
        let step1 = pacer.advance(backlog: 0, elapsed: frame, finished: false)
        #expect(step1 == 0)
        let step2 = pacer.advance(backlog: 0, elapsed: frame, finished: true)
        #expect(step2 == 0)
    }

    @Test func smallBacklogUsesMinimumRate() {
        var pacer = StreamingTextPacer(minimumRate: 45, targetLag: 0.35)
        // 一秒 30 帧，每帧积压保持 5 个字：按最低速度约 45 字/秒。
        var shown = 0
        for _ in 0..<30 { shown += pacer.advance(backlog: 5, elapsed: frame, finished: false) }
        #expect(shown >= 44 && shown <= 46)
    }

    @Test func largeBacklogSpeedsUp() {
        var slow = StreamingTextPacer()
        var fast = StreamingTextPacer()
        let few = (0..<6).reduce(0) { sum, _ in sum + slow.advance(backlog: 20, elapsed: frame, finished: false) }
        let many = (0..<6).reduce(0) { sum, _ in sum + fast.advance(backlog: 2_000, elapsed: frame, finished: false) }
        #expect(many > few * 10)
    }

    @Test func neverShowsMoreThanBacklog() {
        var pacer = StreamingTextPacer()
        let step3 = pacer.advance(backlog: 3, elapsed: 0.25, finished: false)
        #expect(step3 == 3)
        let step4 = pacer.advance(backlog: 1, elapsed: 10, finished: false)
        #expect(step4 == 1)
    }

    @Test func finishedCatchesUpWithinWindow() {
        var pacer = StreamingTextPacer(finishWindow: 0.3)
        var backlog = 5_000
        var elapsed = 0.0
        while backlog > 0 && elapsed < 1 {
            backlog -= pacer.advance(backlog: backlog, elapsed: frame, finished: true)
            elapsed += frame
        }
        #expect(backlog == 0)
        #expect(elapsed <= 0.3 + frame * 1.5)
    }

    @Test func finishedIsNeverSlowerThanStreaming() {
        var streaming = StreamingTextPacer()
        var finishing = StreamingTextPacer(finishWindow: 10)
        let a = streaming.advance(backlog: 300, elapsed: frame, finished: false)
        let b = finishing.advance(backlog: 300, elapsed: frame, finished: true)
        #expect(b >= a)
    }

    @Test func steadyStreamKeepsBoundedLag() {
        // 服务器每 0.2 秒推 30 个字（150 字/秒）：积压稳定，不会越积越多。
        var reveal = StreamingTextReveal()
        var time = 0.0
        var nextChunk = 0.0
        var maxBacklog = 0
        while time < 6 {
            if time >= nextChunk {
                reveal.append(String(repeating: "字", count: 30))
                nextChunk += 0.2
            }
            _ = reveal.tick(elapsed: frame)
            maxBacklog = max(maxBacklog, reveal.backlogCharacters)
            time += frame
        }
        #expect(maxBacklog < 120)
    }

    @Test func tickIntervalSlowsForLongTail() {
        #expect(StreamingTextPacer.tickInterval(tailUTF8: 100) < StreamingTextPacer.tickInterval(tailUTF8: 50_000))
        #expect(StreamingTextPacer.tickInterval(tailUTF8: 0) <= 1.0 / 25.0)
    }
}

@Suite struct StreamingTextRevealTests {
    private let frame = 1.0 / 30.0

    /// 一直走帧直到追平，返回拼起来的显示文字。
    private func drain(_ reveal: inout StreamingTextReveal, initial: String = "", limit: Int = 10_000) -> String {
        var shown = initial
        var frames = 0
        while !reveal.isCaughtUp && frames < limit {
            shown += reveal.tick(elapsed: frame)
            frames += 1
        }
        return shown
    }

    @Test func concatenatedChunksEqualReceivedText() {
        var reveal = StreamingTextReveal()
        let parts = ["你好，", "这是 **Markdown** 回答。\n\n", "- 列表 👨‍👩‍👧 一\n", "```swift\nlet a = 1\n```\n", "English words here."]
        var shown = ""
        for part in parts {
            reveal.append(part)
            for _ in 0..<3 { shown += reveal.tick(elapsed: frame) }
        }
        reveal.finish()
        shown = drain(&reveal, initial: shown)
        #expect(shown == parts.joined())
        #expect(reveal.revealedText == parts.joined())
    }

    @Test func revealsGraduallyNotAllAtOnce() {
        var reveal = StreamingTextReveal()
        reveal.append(String(repeating: "中文", count: 40))
        let first = reveal.tick(elapsed: frame)
        #expect(!first.isEmpty)
        #expect(first.count < 80)
        #expect(!reveal.isCaughtUp)
    }

    @Test func neverSplitsEmojiOrCJK() {
        var reveal = StreamingTextReveal(pacer: StreamingTextPacer(minimumRate: 30, targetLag: 100))
        let text = "👨‍👩‍👧中文🇨🇳测试"
        reveal.append(text)
        var pieces: [String] = []
        while !reveal.isCaughtUp {
            let piece = reveal.tick(elapsed: frame)
            if !piece.isEmpty { pieces.append(piece) }
        }
        #expect(pieces.joined() == text)
        // 每一片都是完整字符：拼起来的字符数等于各片字符数之和。
        #expect(pieces.reduce(0) { $0 + $1.count } == text.count)
    }

    @Test func asciiRunAppearsWhole() {
        var reveal = StreamingTextReveal(pacer: StreamingTextPacer(minimumRate: 30, targetLag: 100))
        reveal.append("令牌 sk-abcdefghijklmnop 结束")
        var pieces: [String] = []
        while !reveal.isCaughtUp {
            let piece = reveal.tick(elapsed: frame)
            if !piece.isEmpty { pieces.append(piece) }
        }
        // 没有任何一片只露出 Token 的一部分（Token 一定整串出现在同一片里）。
        #expect(pieces.contains { $0.contains("sk-abcdefghijklmnop") })
    }

    @Test func revealAllShowsEverything() {
        var reveal = StreamingTextReveal()
        reveal.append("第一段\n\n第二段很长很长很长")
        let first = reveal.tick(elapsed: frame)
        let rest = reveal.revealAll()
        #expect(first + rest == "第一段\n\n第二段很长很长很长")
        #expect(reveal.isCaughtUp)
        let step5 = reveal.revealAll()
        #expect(step5.isEmpty)
    }

    @Test func finishWithContinuationKeepsPacing() {
        var reveal = StreamingTextReveal()
        reveal.append("你好")
        let shown = reveal.tick(elapsed: frame)
        let matched = reveal.finish(final: "你好，世界")
        #expect(matched)
        #expect(reveal.finished)
        #expect(drain(&reveal, initial: shown) == "你好，世界")
    }

    @Test func finishWithRewrittenTextAsksCallerToReplace() {
        var reveal = StreamingTextReveal()
        reveal.append("草稿内容")
        _ = reveal.tick(elapsed: 0.2)
        let matchedDifferent = reveal.finish(final: "完全不同的最终回答")
        #expect(!matchedDifferent)
        #expect(!reveal.finished)
    }

    @Test func finishedCatchesUpQuickly() {
        var reveal = StreamingTextReveal()
        reveal.append(String(repeating: "长回答", count: 2_000))
        _ = reveal.tick(elapsed: frame)
        reveal.finish()
        var frames = 0
        while !reveal.isCaughtUp { _ = reveal.tick(elapsed: frame); frames += 1 }
        #expect(Double(frames) * frame <= 0.3 + frame * 1.5)
    }

    @Test func resetClearsState() {
        var reveal = StreamingTextReveal()
        reveal.append("abc")
        reveal.finish()
        reveal.reset()
        #expect(reveal.received.isEmpty)
        #expect(reveal.revealedUTF8 == 0)
        #expect(!reveal.finished)
        #expect(reveal.isCaughtUp)
    }

    @Test func appendAfterFinishReturnsToStreaming() {
        var reveal = StreamingTextReveal()
        reveal.append("a")
        reveal.finish()
        reveal.append("b")
        #expect(!reveal.finished)
    }
}
