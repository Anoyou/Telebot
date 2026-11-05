import Foundation

/// AI 回复设置的草稿。只写服务器明确提供、且用户实际改动过的字段。
public struct AIReplySettingsDraft: Equatable, Sendable {
    public var replyStyle = "normal"
    public var deleteCommand = true
    public var preferBot = true
    public var streamEdit = false
    public var dividerBefore = true
    public var dividerAfter = true
    public var nativeDivider = true
    public var inlineAsk = true
    public var showQuestion = true
    public private(set) var supportedKeys: Set<String> = []

    public static let keys = [
        "ai_reply_style", "ai_inline_delete_command", "ai_plugin_reply_via_bot", "ai_stream_edit",
        "ai_layout_divider_before", "ai_layout_divider_after", "ai_native_divider",
        "ai_inline_ask_enabled", "ai_inline_ask_show_question",
    ]

    public init() {}

    public init(_ response: JSONValue) {
        if let value = response["ai_reply_style"]?.stringValue {
            replyStyle = value
            supportedKeys.insert("ai_reply_style")
        }
        for key in Self.keys where key != "ai_reply_style" {
            if response[key]?.boolValue != nil { supportedKeys.insert(key) }
        }
        deleteCommand = response["ai_inline_delete_command"]?.boolValue ?? true
        preferBot = response["ai_plugin_reply_via_bot"]?.boolValue ?? true
        streamEdit = response["ai_stream_edit"]?.boolValue ?? false
        dividerBefore = response["ai_layout_divider_before"]?.boolValue ?? true
        dividerAfter = response["ai_layout_divider_after"]?.boolValue ?? true
        nativeDivider = response["ai_native_divider"]?.boolValue ?? true
        inlineAsk = response["ai_inline_ask_enabled"]?.boolValue ?? true
        showQuestion = response["ai_inline_ask_show_question"]?.boolValue ?? true
    }

    public var values: [String: JSONValue] {
        [
            "ai_reply_style": .string(replyStyle),
            "ai_inline_delete_command": .bool(deleteCommand),
            "ai_plugin_reply_via_bot": .bool(preferBot),
            "ai_stream_edit": .bool(streamEdit),
            "ai_layout_divider_before": .bool(dividerBefore),
            "ai_layout_divider_after": .bool(dividerAfter),
            "ai_native_divider": .bool(nativeDivider),
            "ai_inline_ask_enabled": .bool(inlineAsk),
            "ai_inline_ask_show_question": .bool(showQuestion),
        ]
    }

    public func patch(from original: Self) -> [String: JSONValue] {
        let previous = original.values
        return values.filter { original.supportedKeys.contains($0.key) && previous[$0.key] != $0.value }
    }

    /// PATCH 返回的是最新设置。缺字段或回传值不一致时，不能把写入结果显示成已确认。
    public func confirms(_ patch: [String: JSONValue]) -> Bool {
        let current = values
        return patch.allSatisfy { supportedKeys.contains($0.key) && current[$0.key] == $0.value }
    }
}
