import Foundation
import Testing
@testable import TelePilotCore

@Suite struct StreamingMarkdownBoundaryTests {
    private func end(_ text: String, from start: Int = 0) -> Int {
        StreamingMarkdownBoundary.stableEnd(in: text, from: start)
    }

    @Test func splitsAfterBlankLine() {
        let text = "段落一\n\n段落二"
        #expect(end(text) == "段落一\n\n".utf8.count)
        // 继续从边界往后找：最后一段还没有空行，不再前进。
        #expect(end(text, from: "段落一\n\n".utf8.count) == "段落一\n\n".utf8.count)
        #expect(StreamingMarkdownBoundary.slice(text, from: "段落一\n\n".utf8.count) == "段落二")
        #expect(StreamingMarkdownBoundary.slice(text, from: 0, to: "段落一\n\n".utf8.count) == "段落一\n\n")
    }

    @Test func usesLastBlankLine() {
        let text = "a\n\nb\n\nc\n"
        #expect(end(text) == "a\n\nb\n\n".utf8.count)
    }

    @Test func neverSplitsInsideCodeFence() {
        #expect(end("```swift\nlet a = 1\n\nlet b = 2\n") == 0)
        let closed = "```\na\n\nb\n```\n\n后文"
        #expect(end(closed) == "```\na\n\nb\n```\n\n".utf8.count)
    }

    @Test func neverSplitsInsidePrivateKey() {
        let open = "-----BEGIN PRIVATE KEY-----\nAAAA\n\nBBBB\n"
        #expect(end(open) == 0)
        let closed = open + "-----END PRIVATE KEY-----\n\n后文"
        #expect(end(closed) == closed.utf8.count - "后文".utf8.count)
    }

    @Test func keepsLabelAndValueTogether() {
        #expect(end("密码：\n\nabc") == 0)
        #expect(end("token =\n\nabc\n\n") == "token =\n\nabc\n\n".utf8.count)
        #expect(end("Authorization: Bearer\n\nxyz") == 0)
    }

    @Test func ignoresIncompleteLastLineAndBadStart() {
        #expect(end("abc") == 0)
        #expect(end("a\n\n", from: 99) == 3)
        #expect(end("", from: 0) == 0)
        #expect(end("a\r\n\r\nb") == "a\r\n\r\n".utf8.count)
    }
}
