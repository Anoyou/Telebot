import Foundation
import Testing
@testable import TelePilotCore

@Suite struct AgentSessionAccountTests {
    @Test func requestedAccountWins() {
        let body = AgentSessionAccount.newSessionBody(requestedAccountID: 4, accountIDs: [1, 4], serverSupportsDefault: true)
        #expect(body == ["account_id": .integer(4)])
    }

    @Test func newServerDecidesSingleAccount() {
        let body = AgentSessionAccount.newSessionBody(requestedAccountID: nil, accountIDs: [9], serverSupportsDefault: true)
        #expect(body == ["default_account": .bool(true)])
    }

    @Test func oldServerSingleAccountFilledByApp() {
        let body = AgentSessionAccount.newSessionBody(requestedAccountID: nil, accountIDs: [9], serverSupportsDefault: false)
        #expect(body == ["default_account": .bool(true), "account_id": .integer(9)])
    }

    @Test func multipleAccountsStayGlobal() {
        let body = AgentSessionAccount.newSessionBody(requestedAccountID: nil, accountIDs: [1, 2], serverSupportsDefault: false)
        #expect(body == ["default_account": .bool(true)])
        let unknown = AgentSessionAccount.newSessionBody(requestedAccountID: nil, accountIDs: nil, serverSupportsDefault: false)
        #expect(unknown == ["default_account": .bool(true)])
    }

    @Test func autoLinkedDetection() {
        let newServer: [String: JSONValue] = ["default_account": .bool(true)]
        #expect(AgentSessionAccount.wasAutoLinked(request: newServer, response: ["account_id": .integer(3), "account_auto_linked": .bool(true)]))
        #expect(!AgentSessionAccount.wasAutoLinked(request: newServer, response: ["account_id": .null, "account_auto_linked": .bool(false)]))
        let oldServer: [String: JSONValue] = ["default_account": .bool(true), "account_id": .integer(3)]
        #expect(AgentSessionAccount.wasAutoLinked(request: oldServer, response: ["account_id": .integer(3)]))
        let explicit: [String: JSONValue] = ["account_id": .integer(3)]
        #expect(!AgentSessionAccount.wasAutoLinked(request: explicit, response: ["account_id": .integer(3)]))
    }
}
