import Foundation
import Testing
@testable import TelePilotCore

@Suite struct PluginMarketInfoTests {
    // 分段构造，避免一个大字面量拖慢类型推断。
    private static func makeFull() -> JSONValue {
        var object: [String: JSONValue] = ["key": "todo", "kind": "automation", "summary": "待办提醒"]
        object["name"] = ["zh": "待办提醒", "en": "todo"]
        object["description"] = "  支持自然语言时间  "
        object["usage"] = "发送 {prefix}todo 明天 9 点 开会"
        object["commands"] = .array([.string("todo"), .string("undo"), .string("")])
        object["release_notes"] = "- 新增重复提醒"
        object["released_at"] = "2026-10-30"
        object["homepage"] = "https://github.com/Anoyou/telebot-plugins/tree/main/todo"
        object["license"] = "MIT"
        object["icon_url"] = "javascript:alert(1)"
        object["screenshots"] = .array([.string("https://example.com/a.png"), .string("http://example.com/b.png"), .string("not a url")])
        object["summary_i18n"] = ["zh": "待办提醒", "en": "Reminders"]
        object["channel"] = "stable"
        let stable: JSONValue = ["name": "stable", "ref": "main", "version": "1.0.0"]
        let beta: JSONValue = ["name": "beta", "ref": "beta", "version": .null]
        object["available_channels"] = .array([stable, beta])
        object["update_version"] = "1.1.0"
        object["tags"] = .array([.string("效率")])
        return .object(object)
    }

    private let full = PluginMarketInfoTests.makeFull()

    @Test func decodesDetailFieldsAndFiltersUnsafeLinks() {
        let details = PluginMarketDetails(full)
        #expect(details.description == "支持自然语言时间")
        #expect(details.commands == ["todo", "undo"])
        #expect(details.releaseNotes == "- 新增重复提醒")
        #expect(details.releasedAt == "2026-10-30")
        #expect(details.homepage?.host == "github.com")
        #expect(details.license == "MIT")
        #expect(details.iconURL == nil)
        #expect(details.screenshots.map(\.absoluteString) == ["https://example.com/a.png"])
        #expect(details.summaryEnglish == "Reminders")
        #expect(details.channel == "stable")
        #expect(details.channels.map(\.name) == ["stable", "beta"])
        #expect(details.channel(named: "beta")?.version == nil)
        #expect(details.hasMultipleChannels)
        #expect(details.hasUsagePreview)
        #expect(details.updateVersion == "1.1.0")
    }

    @Test func oldServerCatalogHasNoDetailsAndHidesChannels() {
        let legacy: JSONValue = ["key": "todo", "summary": "待办提醒", "channels": ["stable": "main"]]
        let details = PluginMarketDetails(legacy)
        #expect(details.description == nil)
        #expect(details.commands.isEmpty)
        #expect(details.screenshots.isEmpty)
        #expect(details.homepage == nil)
        #expect(details.channels.isEmpty)
        #expect(!details.hasMultipleChannels)
        #expect(!details.hasUsagePreview)
        let single = PluginMarketDetails(["available_channels": [["name": "stable", "version": "1.0.0"]]])
        #expect(!single.hasMultipleChannels)
    }

    @Test func kindCountsKeepStoreOrderAndPutUnknownLast() {
        let counts = PluginMarketInfo.kindCounts(["utility", "zz", "interactive", "automation", "interactive", "aa"])
        #expect(counts.map(\.kind) == ["interactive", "automation", "utility", "aa", "zz"])
        #expect(counts.first?.count == 2)
        #expect(PluginMarketInfo.kindCounts([]).isEmpty)
    }

    @Test func searchCoversDescriptionAndCommands() {
        #expect(PluginMarketInfo.matches(full, query: "自然语言"))
        #expect(PluginMarketInfo.matches(full, query: "UNDO"))
        #expect(PluginMarketInfo.matches(full, query: "效率"))
        #expect(PluginMarketInfo.matches(full, query: "  "))
        #expect(!PluginMarketInfo.matches(full, query: "天气"))
    }

    @Test func plainLanguageTexts() {
        #expect(PluginMarketInfo.permissionDescription("payout")?.contains("游戏币") == true)
        #expect(PluginMarketInfo.permissionDescription("brand_new") == nil)
        #expect(PluginMarketInfo.channelLabel("beta") == "测试版")
        #expect(PluginMarketInfo.channelLabel("stable") == "稳定版")
        #expect(PluginMarketInfo.channelLabel("nightly") == "nightly")
        #expect(PluginMarketInfo.channelLabel(nil) == "")
        #expect(PluginMarketInfo.renderUsage("发送 {prefix}todo 与 {prefix}undo") == "发送 ,todo 与 ,undo")
        #expect(PluginMarketInfo.renderUsage(nil) == "")
        #expect(PluginMarketInfo.needsInstallConfirmation(trust: "unreviewed"))
        #expect(PluginMarketInfo.needsInstallConfirmation(trust: nil))
        #expect(!PluginMarketInfo.needsInstallConfirmation(trust: "official"))
        #expect(!PluginMarketInfo.needsInstallConfirmation(trust: "community"))
        #expect(PluginMarketInfo.trustDescription("official").contains("官方"))
        #expect(PluginMarketInfo.trustDescription("whatever") == PluginMarketInfo.trustDescription("unreviewed"))
    }

    @Test func channelDowngradeMatchesWeb() {
        #expect(PluginMarketInfo.isChannelDowngrade(installed: "1.1.0-beta.1", target: "1.0.0"))
        #expect(!PluginMarketInfo.isChannelDowngrade(installed: "1.0.0", target: "1.1.0-beta.1"))
        #expect(PluginMarketInfo.isChannelDowngrade(installed: "1.0.0", target: "1.0.0-beta.1"))
        #expect(PluginMarketInfo.isChannelDowngrade(installed: "1.0.0-beta.2", target: "1.0.0-beta.1"))
        #expect(!PluginMarketInfo.isChannelDowngrade(installed: "1.0.0-beta.1", target: "1.0.0"))
        #expect(!PluginMarketInfo.isChannelDowngrade(installed: "1.0.0", target: nil))
        #expect(!PluginMarketInfo.isChannelDowngrade(installed: "1.0", target: "1.0.0"))
    }

    @Test func requestBodiesOnlySendChannelWhenNeeded() {
        let stable = PluginMarketInfo.installBody(sourceID: "official", key: "todo", channel: "stable")
        #expect(stable["channel"] == nil)
        #expect(stable["key"]?.stringValue == "todo")
        let none = PluginMarketInfo.installBody(sourceID: "official", key: "todo", channel: nil)
        #expect(none["channel"] == nil)
        let beta = PluginMarketInfo.installBody(sourceID: "official", key: "todo", channel: "beta")
        #expect(beta["channel"]?.stringValue == "beta")
        let body = PluginMarketInfo.channelBody(key: "todo", channel: "beta", sourceID: "official")
        #expect(body["source_id"]?.stringValue == "official")
        #expect(PluginMarketInfo.channelBody(key: "todo", channel: "stable", sourceID: nil)["source_id"] == nil)
    }
}
