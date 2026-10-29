import Foundation
import Testing
@testable import TelePilotCore

/// 与 Web `pluginPageBridge.test.ts` 同样的用例。
struct PluginPageBridgeTests {
    @Test func parsesOnlyProtocolV1Requests() {
        let ok = PluginPageBridge.parse(["tp": "telepilot", "v": 1, "id": "r1", "method": "config.get"])
        #expect(ok == PluginPageBridge.Request(id: "r1", method: "config.get", params: .object([:])))
        #expect(PluginPageBridge.parse(["tp": "telepilot", "v": 1, "id": "r2", "method": "action.invoke", "params": ["id": "x"]])?.params == ["id": "x"])
        #expect(PluginPageBridge.parse(["tp": "telepilot", "v": 2, "id": "r1", "method": "ready"]) == nil)
        #expect(PluginPageBridge.parse(["tp": "other", "v": 1, "id": "r1", "method": "ready"]) == nil)
        #expect(PluginPageBridge.parse(["tp": "telepilot", "v": 1, "id": "", "method": "ready"]) == nil)
        #expect(PluginPageBridge.parse(["tp": "telepilot", "v": 1, "id": "r1", "method": "Bad Method"]) == nil)
        #expect(PluginPageBridge.parse(["tp": "telepilot", "v": 1, "id": "r1", "method": "ready", "params": [1]]) == nil)
        let big = JSONValue.string(String(repeating: "x", count: 40_000))
        #expect(PluginPageBridge.parse(["tp": "telepilot", "v": 1, "id": "r1", "method": "ready", "params": ["big": big]]) == nil)
        #expect(PluginPageBridge.parse(JSONValue.string("hello")) == nil)
    }

    @Test func decodesScriptMessageBodies() {
        let body: [String: Any] = ["tp": "telepilot", "v": NSNumber(value: 1), "id": "r3", "method": "ready", "params": [String: Any]()]
        #expect(PluginPageBridge.parse(PluginPageBridge.decode(any: body))?.method == "ready")
        #expect(PluginPageBridge.decode(any: "text") == nil)
    }

    @Test func repliesAndNavigation() {
        #expect(PluginPageBridge.ok("r1", ["a": 1]) == ["tp": "telepilot", "v": 1, "id": "r1", "ok": true, "result": ["a": 1]])
        #expect(PluginPageBridge.error("r1", code: "X", message: "失败")["error"]?["code"] == "X")
        #expect(PluginPageBridge.allowsNavigation(scheme: "tp-plugin"))
        #expect(PluginPageBridge.allowsNavigation(scheme: "about"))
        #expect(!PluginPageBridge.allowsNavigation(scheme: "https"))
        #expect(!PluginPageBridge.allowsNavigation(scheme: "javascript"))
    }
}
