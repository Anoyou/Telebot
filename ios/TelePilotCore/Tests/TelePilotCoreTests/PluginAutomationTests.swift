import Foundation
import Testing
@testable import TelePilotCore

struct PluginAutomationTests {
    @Test func rulesNeedDeclarationOrExistingRules() {
        let plain: JSONValue = ["key": "plain", "config_schema": ["type": "object", "properties": ["a": ["type": "string"]]]]
        #expect(!PluginAutomation.supportsRules(plain))
        #expect(!PluginAutomation.showsRules(plain, existingRuleCount: 0))
        #expect(PluginAutomation.showsRules(plain, existingRuleCount: 2))
        let rulesMode: JSONValue = ["config_schema": ["type": "object", "x-ui-mode": "rules", "properties": [:]]]
        #expect(PluginAutomation.supportsRules(rulesMode))
        let explicit: JSONValue = ["config_schema": ["type": "object", "x-rule-schema": ["type": "object", "properties": [:]]]]
        #expect(PluginAutomation.supportsRules(explicit))
        let manifest: JSONValue = ["rule_schema": ["type": "object", "properties": ["k": ["type": "string"]]]]
        #expect(PluginAutomation.supportsRules(manifest))
        let nullSchema: JSONValue = ["rule_schema": .null]
        #expect(!PluginAutomation.supportsRules(nullSchema))
    }

    @Test func configJobsNeedActionsOrExistingJobs() {
        let plain: JSONValue = ["key": "plain"]
        #expect(!PluginAutomation.showsConfigJobs(plain, existingJobCount: 0))
        #expect(PluginAutomation.showsConfigJobs(plain, existingJobCount: 1))
        let actions: JSONValue = ["config_actions": [["id": "run"]]]
        #expect(PluginAutomation.showsConfigJobs(actions, existingJobCount: 0))
        let legacy: JSONValue = ["config_schema": ["x-config-actions": [["id": "run"]]]]
        #expect(PluginAutomation.showsConfigJobs(legacy, existingJobCount: 0))
    }

    @Test func countsListShapes() {
        #expect(PluginAutomation.count([["id": 1], ["id": 2]]) == 2)
        #expect(PluginAutomation.count(["items": [["id": 1]]]) == 1)
        #expect(PluginAutomation.count(["jobs": []]) == 0)
        #expect(PluginAutomation.count(nil) == 0)
    }
}
