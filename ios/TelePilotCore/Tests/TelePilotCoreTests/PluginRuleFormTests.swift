import Foundation
import Testing
@testable import TelePilotCore

/// 通用规则表单（契约 2）：与 Web `pluginRules.test.ts` 同样的用例。
struct PluginRuleFormTests {
    static let autoReply: JSONValue = [
        "type": "object", "x-ui-mode": "rules",
        "x-rule-summary": ["match", "patterns", "scope", "missing"],
        "x-dry-run": ["sample_fields": ["message", "chat_type", "bogus"], "title": "试运行", "description": "不会真的发送。"],
        "properties": ["max_per_hour": ["type": "integer"]],
        "x-rule-schema": ["type": "object", "required": ["patterns"], "properties": [
            "match": ["type": "string", "enum": ["keyword", "regex"], "enumNames": ["关键词", "正则"], "default": "keyword"],
            "scope": ["type": "string", "enum": ["private", "group_specific"], "enumNames": ["仅私聊", "指定群"]],
            "group_ids": ["type": "array", "x-ui-widget": "textarea", "items": ["type": "string"],
                          "x-ui-visible-if": ["field": "scope", "equals": "group_specific"]],
            "patterns": ["type": "array", "x-ui-widget": "textarea", "items": ["type": "string"]]
        ]]
    ]

    @Test func ruleSchemaPrefersDeclaredRuleSchema() {
        #expect(PluginRuleForm.isRulesMode(Self.autoReply))
        #expect(PluginRuleForm.hasExplicitRuleSchema(Self.autoReply))
        #expect(PluginRuleForm.ruleSchema(Self.autoReply)?["properties"]?["patterns"] != nil)
        #expect(!PluginRuleForm.configFieldsAreRuleFields(Self.autoReply))
    }

    @Test func wholeConfigSchemaIsRuleSchemaWhenOmitted() {
        let autorepeat: JSONValue = ["type": "object", "x-ui-mode": "rules", "properties": ["target_chat_id": ["type": "integer"]]]
        #expect(PluginRuleForm.ruleSchema(autorepeat) == autorepeat)
        #expect(PluginRuleForm.configFieldsAreRuleFields(autorepeat))
        let plain: JSONValue = ["type": "object", "properties": ["reply": ["type": "string"]]]
        #expect(PluginRuleForm.ruleSchema(plain) == nil)
        #expect(!PluginRuleForm.configFieldsAreRuleFields(plain))
    }

    @Test func visibleIfFollowsSiblingValue() {
        let field = Self.autoReply["x-rule-schema"]?["properties"]?["group_ids"]
        #expect(!PluginRuleForm.isVisible(field, values: ["scope": "private"]))
        #expect(!PluginRuleForm.isVisible(field, values: [:]))
        #expect(PluginRuleForm.isVisible(field, values: ["scope": "group_specific"]))
        let inList: JSONValue = ["x-ui-visible-if": ["field": "n", "in": [1, 2]]]
        #expect(PluginRuleForm.isVisible(inList, values: ["n": "2"]))
        #expect(!PluginRuleForm.isVisible(inList, values: ["n": 3]))
        let notIn: JSONValue = ["x-ui-visible-if": ["field": "mode", "not_in": ["off"]]]
        #expect(PluginRuleForm.isVisible(notIn, values: ["mode": "on"]))
        #expect(!PluginRuleForm.isVisible(notIn, values: ["mode": "off"]))
        // 声明不合法：一律显示
        #expect(PluginRuleForm.isVisible(["x-ui-visible-if": "scope"], values: [:]))
        #expect(PluginRuleForm.isVisible(["x-ui-visible-if": ["field": "scope"]], values: [:]))
    }

    @Test func summaryKeysAndValues() {
        let schema = PluginRuleForm.ruleSchema(Self.autoReply) ?? .null
        let keys = PluginRuleForm.summaryKeys(ruleSchema: schema, declared: Self.autoReply["x-rule-summary"])
        #expect(keys == ["match", "patterns", "scope"])
        let props = schema["properties"]
        #expect(PluginRuleForm.formatValue(props?["match"], "regex") == "正则")
        #expect(PluginRuleForm.formatValue(props?["patterns"], ["你好", "hello"]) == "你好、hello")
        #expect(PluginRuleForm.formatValue(nil, nil) == "—")
        #expect(PluginRuleForm.formatValue(nil, .bool(true)) == "是")
        #expect(PluginRuleForm.formatValue(nil, 300) == "300")
        let fallback = PluginRuleForm.summaryKeys(ruleSchema: ["properties": ["a": ["type": "string"], "b": ["type": "string", "x-ui-hidden": true], "c": ["type": "string"]]], declared: nil)
        #expect(fallback == ["a", "c"])
    }

    @Test func dryRunSpecDefaultsAndDisable() {
        let spec = PluginRuleForm.dryRunSpec(Self.autoReply["x-dry-run"])
        #expect(spec.enabled)
        #expect(spec.sampleFields == ["message", "chat_type"])
        #expect(spec.description == "不会真的发送。")
        #expect(PluginRuleForm.dryRunSpec(nil).sampleFields == ["message", "chat_id"])
        #expect(!PluginRuleForm.dryRunSpec(.bool(false)).enabled)
        #expect(PluginRuleForm.parseChatID(" -1001234 ") == -1001234)
        #expect(PluginRuleForm.parseChatID("abc") == nil)
        #expect(PluginRuleForm.parseChatID("") == nil)
    }

    @Test func lineListKeepsCommas() {
        let field = Self.autoReply["x-rule-schema"]?["properties"]?["patterns"] ?? .null
        #expect(PluginRuleForm.isLineList(field))
        #expect(PluginRuleForm.splitLines("^签到$\n\n\\d{1,3}\n a,b ", itemType: "string") == ["^签到$", "\\d{1,3}", "a,b"])
        #expect(PluginRuleForm.splitLines("-1001\nx", itemType: "integer") == [-1001, "x"])
        #expect(PluginRuleForm.joinLines(["a", 1]) == "a\n1")
    }
}
