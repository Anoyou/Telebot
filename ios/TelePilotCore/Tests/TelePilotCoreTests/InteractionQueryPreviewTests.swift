import Foundation
import Testing
@testable import TelePilotCore

/// 第 21 轮：玩法查询预览按真实规则渲染「每个玩法的条目模板」。
/// 期望值由服务器 `account_bot_runtime` 的 `_interaction_rule_query_trigger_label` / `_kind_label` / `_limit_label`
/// 对同样三条规则实际运行得出（见 notes/round21/interaction-progress.md），改服务器标签时这里会失败。
@Suite struct InteractionQueryPreviewTests {
    static let ask: JSONValue = [
        "id": "rule-ask", "name": "AI 答疑", "enabled": true, "trigger_mode": "keyword", "module_start_keywords": [],
        "action": "module", "module_key": "content_assistant", "module_action": "ask", "valid_seconds": 600,
        "user_cooldown_seconds": nil, "daily_limit_per_user": nil, "module_prize": nil, "chat_ids": [-1001234567890],
    ]
    static let math: JSONValue = [
        "id": "rule-math", "name": "猜<数>", "enabled": true, "trigger_mode": "both",
        "module_start_keywords": ["开局 num=数字", "a&b", "x", "y", "z", "w"], "action": "math10", "math_prize": 123,
        "valid_seconds": 120, "user_cooldown_seconds": "12h", "daily_limit_per_user": 5, "chat_ids": [],
    ]
    static let bare: JSONValue = ["name": "P", "trigger_mode": "payment", "action": "module", "module_key": "", "valid_seconds": nil]
    static let notice: JSONValue = ["id": "rule-notice", "name": "转账通知", "enabled": true, "action": "notice"]
    static let paused: JSONValue = ["id": "rule-off", "name": "暂停的玩法", "enabled": false, "action": "module", "module_key": "x"]

    @Test func labelsMatchServer() {
        let a = InteractionQueryPreview.itemValues(rule: Self.ask, index: 1)
        #expect(a["trigger"] == "关键词；关键词未配置")
        #expect(a["kind"] == "玩法 <code>content_assistant</code>")
        #expect(a["limit"] == "限时 <code>600</code> 秒")
        let m = InteractionQueryPreview.itemValues(rule: Self.math, index: 2)
        #expect(m["trigger"] == "转账或关键词；关键词：<code>开局 num=数字</code> / <code>a&amp;b</code> / <code>x</code> / <code>y</code> / <code>z</code>；转账通知")
        #expect(m["kind"] == "算数题")
        #expect(m["limit"] == "奖金 <code>123</code>；限时 <code>120</code> 秒；每用户 CD <code>12h</code>；每用户日上限 <code>5</code>")
        #expect(m["name"] == "猜&lt;数&gt;")
        let b = InteractionQueryPreview.itemValues(rule: Self.bare, index: 3)
        #expect(b["trigger"] == "转账；转账通知")
        #expect(b["kind"] == "玩法")
        #expect(b["limit"] == "无限制")
    }

    @Test func modulePrizeHiddenWhenEntryDoesNotDeclareIt() {
        var rule = Self.ask.objectValue ?? [:]
        rule["module_prize"] = 50
        #expect(InteractionQueryPreview.itemValues(rule: .object(rule), index: 1, prizeDeclared: false)["limit"] == "限时 <code>600</code> 秒")
        #expect(InteractionQueryPreview.itemValues(rule: .object(rule), index: 1, prizeDeclared: nil)["limit"] == "奖金 <code>50</code>；限时 <code>600</code> 秒")
        #expect(InteractionQueryPreview.itemValues(rule: .object(rule), index: 1, prizeDeclared: true)["limit"] == "奖金 <code>50</code>；限时 <code>600</code> 秒")
    }

    @Test func composesOnlyEnabledPlayRulesInOrder() {
        let text = InteractionQueryPreview.compose(response: "", item: "• {name}（{index}/{kind}）",
                                                   rules: [Self.notice, Self.ask, Self.paused, Self.math])
        #expect(text == "<b>当前可用联动玩法</b>\n• AI 答疑（1/玩法 <code>content_assistant</code>）\n• 猜&lt;数&gt;（2/算数题）")
        let defaults = InteractionQueryPreview.compose(response: "共 {count} 个，群 {chat_id}\n{items}", item: "", rules: [Self.ask])
        #expect(defaults == "共 1 个，群 -1001234567890\n1. <b>AI 答疑</b>\n触发方式：关键词；关键词未配置")
        #expect(InteractionQueryPreview.renderItem(template: "  {index}. {name}  ", rule: Self.ask, index: 4) == "4. AI 答疑")
    }

    @Test func fallsBackToEmptyMessageWithoutPlayRules() {
        #expect(InteractionQueryPreview.compose(response: "", item: "", empty: "", rules: [Self.notice, Self.paused])
                == TelegramTemplateSamples.defaultQueryEmptyMessage)
        #expect(InteractionQueryPreview.compose(response: "", item: "", empty: " 暂无 ", rules: []) == "暂无")
    }
}
