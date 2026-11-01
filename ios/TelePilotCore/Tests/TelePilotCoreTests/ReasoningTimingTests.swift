import Foundation
import Testing
@testable import TelePilotCore

@Suite struct ReasoningTimingTests {
    @Test func compactFormatsLikeCodex() {
        #expect(ReasoningDuration.compact(96) == "1m 36s")
        #expect(ReasoningDuration.compact(12.4) == "12s")
        #expect(ReasoningDuration.compact(0.2) == "1s")
        #expect(ReasoningDuration.compact(60) == "1m")
        #expect(ReasoningDuration.compact(3720) == "1h 2m")
        #expect(ReasoningDuration.compact(7200) == "2h")
    }

    @Test func titleCoversThinkingKnownAndUnknown() {
        #expect(ReasoningDuration.title(thinking: true, seconds: nil) == "正在思考…")
        #expect(ReasoningDuration.title(thinking: true, seconds: 5) == "正在思考… 5s")
        #expect(ReasoningDuration.title(thinking: false, seconds: 96) == "思考了 1m 36s")
        #expect(ReasoningDuration.title(thinking: false, seconds: nil) == "思考过程")
    }

    @Test func serverSecondsReadsStageTimings() {
        let usage: JSONValue = ["run_id": "r1", "stage_timings": ["total_ms": 120_000, "reasoning_ms": 96_000]]
        #expect(ReasoningDuration.serverSeconds(usage: usage) == 96)
        let old: JSONValue = ["stage_timings": ["total_ms": 120_000, "first_token_ms": 800]]
        #expect(ReasoningDuration.serverSeconds(usage: old) == nil)
        #expect(ReasoningDuration.serverSeconds(usage: nil) == nil)
        let negative: JSONValue = ["stage_timings": ["reasoning_ms": -5]]
        #expect(ReasoningDuration.serverSeconds(usage: negative) == nil)
    }

    @Test func resolvePrefersServerThenLocalByRunID() {
        let local = ["r1": 42.0]
        let server: JSONValue = ["run_id": "r1", "stage_timings": ["reasoning_ms": 96_000]]
        #expect(ReasoningDuration.resolve(usage: server, local: local) == 96)
        let oldServer: JSONValue = ["run_id": "r1"]
        #expect(ReasoningDuration.resolve(usage: oldServer, local: local) == 42)
        let other: JSONValue = ["run_id": "r2"]
        #expect(ReasoningDuration.resolve(usage: other, local: local) == nil)
        #expect(ReasoningDuration.resolve(usage: nil, local: local) == nil)
    }

    @Test func timestampParsesPythonIsoformat() throws {
        let micro = try #require(ReasoningDuration.timestamp("2026-11-01T06:14:00.123456+00:00"))
        let whole = try #require(ReasoningDuration.timestamp("2026-11-01T06:14:00+00:00"))
        #expect(abs(micro.timeIntervalSince(whole) - 0.123456) < 0.000_01)
        let zulu = try #require(ReasoningDuration.timestamp("2026-11-01T06:14:01.5Z"))
        #expect(abs(zulu.timeIntervalSince(whole) - 1.5) < 0.000_01)
        #expect(ReasoningDuration.timestamp("") == nil)
        #expect(ReasoningDuration.timestamp("Sat 2026-10-24 4:40 PM") == nil)
    }

    @Test func timerAccumulatesSegmentsAndSkipsTools() {
        let t0 = Date(timeIntervalSince1970: 1_000)
        var timer = ReasoningTimer()
        #expect(timer.elapsed(now: t0) == nil)
        timer.reasoning(at: t0)
        timer.reasoning(at: t0.addingTimeInterval(3))
        #expect(timer.isThinking)
        #expect(timer.elapsed(now: t0.addingTimeInterval(5)) == 5)
        timer.stop(at: t0.addingTimeInterval(10))
        #expect(!timer.isThinking)
        // 工具执行期间（10–30 秒）不计。
        #expect(timer.elapsed(now: t0.addingTimeInterval(30)) == 10)
        timer.reasoning(at: t0.addingTimeInterval(30))
        timer.stop(at: t0.addingTimeInterval(36))
        #expect(timer.elapsed(now: t0.addingTimeInterval(99)) == 16)
        // 重复收窗无影响；时间倒退不产生负数。
        timer.stop(at: t0.addingTimeInterval(200))
        #expect(timer.elapsed(now: t0) == 16)
        timer.reset()
        #expect(timer == ReasoningTimer())
    }

    @Test func eventEffects() {
        #expect(ReasoningTimer.effect(of: "assistant_reasoning_delta") == .open)
        #expect(ReasoningTimer.effect(of: "reasoning_delta") == .open)
        #expect(ReasoningTimer.effect(of: "assistant_delta") == .close)
        #expect(ReasoningTimer.effect(of: "tool_started") == .close)
        #expect(ReasoningTimer.effect(of: "assistant_message") == .close)
        #expect(ReasoningTimer.effect(of: "heartbeat") == .ignore)
        #expect(ReasoningTimer.effect(of: "model_attempt") == .ignore)
    }
}
