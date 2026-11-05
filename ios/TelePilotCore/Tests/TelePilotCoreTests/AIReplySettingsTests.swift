import XCTest
@testable import TelePilotCore

final class AIReplySettingsTests: XCTestCase {
    func testOnlyChangedSupportedFieldsAreSent() {
        let original = AIReplySettingsDraft(.object([
            "ai_reply_style": .string("inline"),
            "ai_plugin_reply_via_bot": .bool(true),
            "unrelated_setting": .string("keep"),
        ]))
        var draft = original
        draft.preferBot = false
        draft.streamEdit = true // 旧服务器没有这个字段，不能把本地默认值写进去。
        XCTAssertEqual(draft.patch(from: original), ["ai_plugin_reply_via_bot": .bool(false)])
    }

    func testUnknownReplyStyleIsPreservedWhenOtherFieldChanges() {
        let original = AIReplySettingsDraft(.object([
            "ai_reply_style": .string("future_mode"),
            "ai_layout_divider_before": .bool(true),
        ]))
        var draft = original
        draft.dividerBefore = false
        XCTAssertEqual(draft.replyStyle, "future_mode")
        XCTAssertEqual(draft.patch(from: original), ["ai_layout_divider_before": .bool(false)])
    }

    func testMissingOrMalformedValuesRemainUnsupported() {
        let original = AIReplySettingsDraft(.object(["ai_stream_edit": .string("false")]))
        var draft = original
        draft.streamEdit = true
        draft.preferBot = false
        XCTAssertTrue(original.supportedKeys.isEmpty)
        XCTAssertTrue(draft.patch(from: original).isEmpty)
    }

    func testResponseMustConfirmTheActualSubmittedValues() {
        let patch: [String: JSONValue] = ["ai_plugin_reply_via_bot": .bool(false)]
        XCTAssertFalse(AIReplySettingsDraft(.object([:])).confirms(patch))
        XCTAssertFalse(AIReplySettingsDraft(.object(["ai_plugin_reply_via_bot": .bool(true)])).confirms(patch))
        XCTAssertTrue(AIReplySettingsDraft(.object(["ai_plugin_reply_via_bot": .bool(false)])).confirms(patch))
    }

    func testUnchangedSettingsDoNotProduceAWrite() {
        let draft = AIReplySettingsDraft(.object([
            "ai_reply_style": .string("normal"), "ai_inline_delete_command": .bool(false),
            "ai_plugin_reply_via_bot": .bool(false), "ai_stream_edit": .bool(true),
            "ai_layout_divider_before": .bool(false), "ai_layout_divider_after": .bool(false),
            "ai_native_divider": .bool(false), "ai_inline_ask_enabled": .bool(false),
            "ai_inline_ask_show_question": .bool(false),
        ]))
        XCTAssertEqual(draft.supportedKeys.count, 9)
        XCTAssertTrue(draft.patch(from: draft).isEmpty)
    }
}
