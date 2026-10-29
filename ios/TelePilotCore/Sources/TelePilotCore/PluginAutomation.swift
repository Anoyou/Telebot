import Foundation

/// 插件详情「规则与自动化」分组的显示条件（iOS 0.15.0，与 Web 同一口径）：
/// - 「自动化规则」「复制规则到其他账号」：插件支持规则（manifest `rule_schema`、`config_schema.x-ui-mode == "rules"`、
///   `config_schema.x-rule-schema`）或该账号下已经有规则时才显示；
/// - 「配置任务与进度」：插件声明了配置操作（`config_actions` / `config_schema.x-config-actions`）或该账号已有配置任务时才显示；
/// - 以上都没有、也不支持直通时，整个分组不显示。旧插件按同样规则判断，不做特例。
public enum PluginAutomation {
    public static func supportsRules(_ feature: JSONValue) -> Bool {
        if let declared = feature["rule_schema"], !declared.isNull, declared.objectValue?.isEmpty != true { return true }
        let schema = feature["config_schema"]
        if schema?["x-ui-mode"]?.stringValue == "rules" { return true }
        if let ruleSchema = schema?["x-rule-schema"], ruleSchema.objectValue != nil { return true }
        return false
    }

    public static func configActions(_ feature: JSONValue) -> [JSONValue] {
        if let actions = feature["config_actions"]?.arrayValue, !actions.isEmpty { return actions }
        return feature["config_schema"]?["x-config-actions"]?.arrayValue ?? []
    }

    public static func showsRules(_ feature: JSONValue, existingRuleCount: Int) -> Bool {
        supportsRules(feature) || existingRuleCount > 0
    }

    public static func showsConfigJobs(_ feature: JSONValue, existingJobCount: Int) -> Bool {
        !configActions(feature).isEmpty || existingJobCount > 0
    }

    /// 规则 / 任务列表接口的条数：数组，或 `{items: [...]}` / `{rules: [...]}` / `{jobs: [...]}`。
    public static func count(_ value: JSONValue?) -> Int {
        guard let value else { return 0 }
        if let items = value.arrayValue { return items.count }
        for key in ["items", "rules", "jobs"] {
            if let items = value[key]?.arrayValue { return items.count }
        }
        return 0
    }
}
