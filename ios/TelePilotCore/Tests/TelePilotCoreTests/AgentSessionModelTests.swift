import Foundation
import Testing
@testable import TelePilotCore

@Suite struct AgentSessionModelTests {
    let models: [JSONValue] = [
        ["provider_id": 2, "provider_name": "Grok", "model": "grok-4.20-fast"],
        ["provider_id": 3, "provider_name": "OpenAI", "model": "gpt-5.1"],
    ]

    @Test func selectionFromIndex() {
        #expect(AgentSessionModel.selection(models: models, index: -1) == ["mode": "auto"])
        #expect(AgentSessionModel.selection(models: models, index: 5) == ["mode": "auto"])
        #expect(AgentSessionModel.selection(models: models, index: 1) == ["mode": "pinned", "provider_id": 3, "model": "gpt-5.1"])
    }

    @Test func indexFromSelection() {
        #expect(AgentSessionModel.index(of: nil, in: models) == -1)
        #expect(AgentSessionModel.index(of: ["mode": "auto"], in: models) == -1)
        #expect(AgentSessionModel.index(of: ["mode": "pinned", "provider_id": 3, "model": "gpt-5.1"], in: models) == 1)
        // 服务器返回的数字可能是浮点
        #expect(AgentSessionModel.index(of: ["mode": "pinned", "provider_id": .number(2), "model": "grok-4.20-fast"], in: models) == 0)
    }

    @Test func deletedModelFallsBackToAuto() {
        #expect(AgentSessionModel.index(of: ["mode": "pinned", "provider_id": 9, "model": "deleted"], in: models) == -1)
        #expect(AgentSessionModel.index(of: ["mode": "pinned", "provider_id": 3, "model": "gpt-5.1"], in: []) == -1)
        #expect(AgentSessionModel.index(of: ["mode": "pinned"], in: models) == -1)
    }

    @Test func serverValueWinsOverLocal() {
        let local: JSONValue = ["mode": "pinned", "provider_id": 2, "model": "grok-4.20-fast"]
        let server: JSONValue = ["model_selection": ["mode": "pinned", "provider_id": 3, "model": "gpt-5.1"]]
        #expect(AgentSessionModel.restored(serverRow: server, serverSupports: true, local: local)?["model"] == "gpt-5.1")
        // 服务器没值（null）或旧服务器：用本机
        #expect(AgentSessionModel.restored(serverRow: ["model_selection": nil], serverSupports: true, local: local) == local)
        #expect(AgentSessionModel.restored(serverRow: server, serverSupports: false, local: local) == local)
        #expect(AgentSessionModel.restored(serverRow: [:], serverSupports: true, local: nil) == nil)
    }

    @Test func localStoreBySession() throws {
        let suite = "AgentSessionModelTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = AgentSessionModel.LocalStore(defaults: defaults, scope: "server.1")
        let pinned: JSONValue = ["mode": "pinned", "provider_id": 3, "model": "gpt-5.1"]
        store.save("a", pinned)
        #expect(store.load("a") == pinned)
        #expect(store.load("b") == nil)
        // 另一个服务器 / 用户互不影响
        #expect(AgentSessionModel.LocalStore(defaults: defaults, scope: "server.2").load("a") == nil)
        store.save("a", ["mode": "auto"])
        #expect(store.load("a") == nil)
        store.save("a", pinned)
        store.remove("a")
        #expect(store.load("a") == nil)
    
    }

    @Test func removeAllClearsEveryScope() throws {
        let suite = "AgentSessionModelTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let pinned: JSONValue = ["mode": "pinned", "provider_id": 3, "model": "gpt-5.1"]
        AgentSessionModel.LocalStore(defaults: defaults, scope: "s1").save("a", pinned)
        AgentSessionModel.LocalStore(defaults: defaults, scope: "s2").save("b", pinned)
        defaults.set("x", forKey: "other.key")
        AgentSessionModel.LocalStore.removeAll(defaults: defaults)
        #expect(AgentSessionModel.LocalStore(defaults: defaults, scope: "s1").load("a") == nil)
        #expect(AgentSessionModel.LocalStore(defaults: defaults, scope: "s2").load("b") == nil)
        #expect(defaults.string(forKey: "other.key") == "x")
    }
}
