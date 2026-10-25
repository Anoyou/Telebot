import Foundation
import Testing
@testable import TelePilotCore

/// 与 Web `configFieldOrder.test.ts` 同一组用例：字段按声明顺序 + x-ui-order 排序，按 x-ui-section 分组。
struct PluginConfigLayoutTests {
    // 模拟 JSONB 打乱后的 properties：顺序只能靠 x-ui-property-order 还原。
    static let schema: JSONValue = [
        "type": "object",
        "x-ui-property-order": ["enable_guest", "bot_username", "telepilot_model", "max_tokens", "help_template", "reset_template", "removed_field"],
        "properties": [
            "telepilot_model": ["type": "string", "title": "模型", "default": "", "x-ui-section": "AI 模型"],
            "bot_username": ["type": "string", "title": "交互 Bot 用户名（兜底，可选）", "default": "", "x-ui-section": "触发方式"],
            "enable_guest": ["type": "boolean", "title": "访客模式触发", "default": true, "x-ui-section": "触发方式"],
            "max_tokens": ["type": "integer", "title": "最大输出 Token", "default": 800],
            "reset_template": ["type": "string", "title": "重置记忆确认", "default": "", "x-ui-order": 74],
            "help_template": ["type": "string", "title": "帮助提示", "default": "", "x-ui-order": 73],
            "late_field": ["type": "string", "title": "未记录顺序的字段"]
        ],
        "required": []
    ]

    @Test func declaredOrderFollowsPropertyOrderThenKeys() {
        #expect(PluginConfigLayout.declaredOrder(schema: Self.schema)
                == ["enable_guest", "bot_username", "telepilot_model", "max_tokens", "help_template", "reset_template", "late_field"])
    }

    @Test func uiOrderIsStableAndDefaultsToZero() {
        #expect(PluginConfigLayout.orderedKeys(schema: Self.schema)
                == ["enable_guest", "bot_username", "telepilot_model", "max_tokens", "late_field", "help_template", "reset_template"])
        #expect(PluginConfigLayout.orderedKeys(schema: Self.schema, keys: ["max_tokens", "enable_guest"]) == ["enable_guest", "max_tokens"])
    }

    @Test func groupsFollowSectionsThenBasicThenTemplates() {
        let keys = PluginConfigLayout.orderedKeys(schema: Self.schema)
        let groups = PluginConfigLayout.groups(schema: Self.schema, orderedKeys: keys)
        #expect(groups == [
            .init(title: "触发方式", keys: ["enable_guest", "bot_username"]),
            .init(title: "AI 模型", keys: ["telepilot_model"]),
            .init(title: "其他设置", keys: ["max_tokens", "late_field"]),
            .init(title: "消息模板", keys: ["help_template", "reset_template"])
        ])
    }

    @Test func schemaWithoutSectionsKeepsOneUntitledGroup() {
        let schema: JSONValue = ["properties": ["b": ["type": "string"], "a": ["type": "string"]], "x-ui-property-order": ["b", "a"]]
        #expect(PluginConfigLayout.groups(schema: schema, orderedKeys: PluginConfigLayout.orderedKeys(schema: schema))
                == [.init(title: nil, keys: ["b", "a"])])
    }

    @Test func smallSchemasStayOnOnePage() {
        let keys = PluginConfigLayout.orderedKeys(schema: Self.schema)
        let groups = PluginConfigLayout.groups(schema: Self.schema, orderedKeys: keys)
        // 只有 7 个字段：不拆子页面。
        #expect(PluginConfigLayout.pagePlan(schema: Self.schema, groups: groups) == .init(inline: groups, pages: []))
    }

    @Test func longSchemasMoveNonBasicSectionsToSubpages() {
        var properties: [String: JSONValue] = [:]
        var order: [JSONValue] = []
        func add(_ key: String, _ section: String?, inline: Bool? = nil) {
            var spec: [String: JSONValue] = ["type": "string", "default": ""]
            if let section { spec["x-ui-section"] = .string(section) }
            if let inline { spec["x-ui-section-inline"] = .bool(inline) }
            properties[key] = .object(spec)
            order.append(.string(key))
        }
        add("telepilot_model", "模型与能力"); add("enable_web_search", "模型与能力")
        add("enable_guest", "触发方式", inline: true); add("trigger_keywords", "触发方式")
        add("pt_enabled", "PT 站点数据源"); add("pt_site_url", "PT 站点数据源"); add("pt_cookie", "PT 站点数据源")
        add("access_mode", "使用者与限额"); add("per_user_daily_limit", "使用者与限额")
        add("max_tokens", nil)
        add("help_template", nil); add("reset_template", nil)
        let schema: JSONValue = ["properties": .object(properties), "x-ui-property-order": .array(order)]
        let groups = PluginConfigLayout.groups(schema: schema, orderedKeys: PluginConfigLayout.orderedKeys(schema: schema))
        let plan = PluginConfigLayout.pagePlan(schema: schema, groups: groups)
        #expect(plan.inline.map(\.title) == ["模型与能力", "触发方式", "其他设置"])
        #expect(plan.pages.map(\.title) == ["PT 站点数据源", "使用者与限额", "消息模板"])
        #expect(plan.pages.first?.keys == ["pt_enabled", "pt_site_url", "pt_cookie"])
    }
}

