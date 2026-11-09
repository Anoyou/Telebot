import Testing
@testable import TelePilotCore

@Suite struct ReasoningHistoryTests {
    @Test func restoresNumericMessageIDWhenFinalHistoryOmitsReasoning() {
        var history = ReasoningHistory()
        history.remember(scope: "server/session", runID: "run-1", messageID: "42", text: "先核对再汇总")
        let message: JSONValue = ["id": 42, "role": "assistant", "content": ["text": "完成"]]
        let restored = history.restoring(message, scope: "server/session")
        #expect(restored["content"]?["reasoning"]?.stringValue == "先核对再汇总")
        #expect(restored["content"]?["text"]?.stringValue == "完成")
        #expect(message["content"]?["reasoning"] == nil)
    }

    @Test func usesRunIDWhenTerminalPacketHasNoMessageID() {
        var history = ReasoningHistory()
        history.remember(scope: "s", runID: "run-1", messageID: nil, text: "思考摘要")
        let message: JSONValue = ["id": 42, "role": "assistant", "content": ["text": "回答", "reasoning": ""], "usage": ["run_id": "run-1"]]
        #expect(history.restoring(message, scope: "s")["content"]?["reasoning"]?.stringValue == "思考摘要")
        #expect(history.restoring(message, scope: "other") == message)
    }

    @Test func serverReasoningWinsAndUserMessagesAreNeverFilled() {
        var history = ReasoningHistory()
        history.remember(scope: "s", runID: "run-1", messageID: "42", text: "缓存摘要")
        let server: JSONValue = ["id": 42, "role": "assistant", "content": ["text": "回答", "reasoning": "完整服务端摘要"]]
        #expect(history.restoring(server, scope: "s") == server)
        let user: JSONValue = ["id": 42, "role": "user", "content": ["text": "问题"]]
        #expect(history.restoring(user, scope: "s") == user)
    }

    @Test func regenerationCannotInheritThePreviousRunsReasoning() {
        var history = ReasoningHistory()
        history.remember(scope: "s", runID: "old", messageID: "42", text: "旧摘要")
        let next: JSONValue = ["id": 42, "role": "assistant", "content": ["text": "新回答"], "usage": ["run_id": "new"]]
        #expect(history.restoring(next, scope: "s") == next)
        history.remember(scope: "s", runID: "new", messageID: "42", text: "新摘要")
        #expect(history.restoring(next, scope: "s")["content"]?["reasoning"]?.stringValue == "新摘要")
        history.remember(scope: "s", runID: "new", messageID: "42", text: "")
        #expect(history.restoring(next, scope: "s") == next)
    }

    @Test func identicalAnswersDoNotMatchUnrelatedMessages() {
        var history = ReasoningHistory()
        history.remember(scope: "s", runID: "one", messageID: "41", text: "第一轮摘要")
        let other: JSONValue = ["id": 42, "role": "assistant", "content": ["text": "完成"], "usage": ["run_id": "two"]]
        #expect(history.restoring(other, scope: "s") == other)
        #expect(history.restoring(.null, scope: "s") == .null)
    }
}
