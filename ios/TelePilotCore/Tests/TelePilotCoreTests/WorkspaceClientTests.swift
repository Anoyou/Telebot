import Foundation
import Testing
@testable import TelePilotCore

private actor StreamEvents {
    var values: [JSONValue] = []
    func append(_ value: JSONValue) { values.append(value) }
}

extension ClientTests {
    @Test func jsonPreservesTelegramIDsBooleansAndScalars() throws {
        let payload = try JSONValue(data: Data("{\"id\":9007199254740993,\"ok\":true,\"zero\":0,\"one\":1,\"value\":null}".utf8))
        #expect(payload["id"]?.intValue == 9_007_199_254_740_993)
        #expect(payload["zero"]?.boolValue == nil)
        #expect(payload["one"]?.intValue == 1)
        #expect(payload["ok"]?.boolValue == true)
        #expect(try JSONValue(data: payload.data()) == payload)
        #expect(try JSONValue(data: JSONValue.string("scalar").data()) == .string("scalar"))
    }

    @Test func formCatalogResolvesActualRequiredFields() throws {
        let operation = try #require(APIContractCatalog.bundled.operation(path: "/api/accounts/42", method: "PATCH"))
        #expect(operation.path == "/api/accounts/{aid}")
        #expect(operation.requestSchema["properties"]?["notes"] != nil)
        let login = try #require(APIContractCatalog.bundled.operation(path: "/api/accounts/login/start", method: "POST"))
        #expect(login.requestSchema["properties"]?["phone"] != nil)
        #expect(APIContractCatalog.bundled.operation(path: "/api/unavailable", method: "POST") == nil)
    }

    @Test func rawWritesShareCsrfAndNeverReplayServerFailures() async throws {
        let (client, script, _) = try setup([csrfReply, MockReply(status: 503)])
        do {
            _ = try await client.request(path: "/api/system/kill-switch", method: "POST", body: ["enabled": true])
            Issue.record("Expected uncertain outcome")
        } catch let error as APIError { #expect(error.isOutcomeUncertain) }
        #expect(script.requests.count == 2)
        #expect(script.requests[1].value(forHTTPHeaderField: "X-CSRF-Token") == "csrf-one")
        #expect(try body(script.requests[1])["enabled"] as? Bool == true)
    }

    @Test func rawBodyAndUploadSupportFilesBeyondOld64KiBLimit() async throws {
        let (client, script, _) = try setup([csrfReply, MockReply(body: "{\"ok\":true}")])
        let file = Data(repeating: 65, count: 100_000)
        #expect(try await client.upload(path: "/api/system/import-config/preview", file: file,
                                       filename: "backup.json", fields: ["preview_signature": "test"])["ok"]?.boolValue == true)
        #expect(script.requests.count == 2)
        #expect(script.requests[1].value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
    }

    @Test func rawPathsRejectTraversalAndForeignOrigins() async throws {
        let (client, script, _) = try setup([])
        for path in ["https://evil.example/api/accounts", "//evil.example/api/accounts",
                     "/api/../secret", "/api/%2e%2e/secret", "/api/accounts#fragment"] {
            await #expect(throws: APIError.self) { try await client.request(path: path) }
        }
        #expect(script.requests.isEmpty)
    }

    @Test func successfulMalformedMutationIsUncertain() async throws {
        let (client, _, _) = try setup([csrfReply, MockReply(body: "<html>gateway</html>")])
        do {
            _ = try await client.request(path: "/api/plugins/example/reload", method: "POST")
            Issue.record("Expected uncertain outcome")
        } catch let error as APIError { #expect(error.isOutcomeUncertain) }
    }

    @Test func noContentDeleteSucceedsWithoutInventingResponse() async throws {
        let (client, _, _) = try setup([csrfReply, MockReply(status: 204, body: "")])
        #expect(try await client.request(path: "/api/apns/devices/installation", method: "DELETE") == .null)
    }

    @Test func durableStreamDecodesUnicodeAndStopsAtDone() async throws {
        let wire = "{\"type\":\"assistant_delta\",\"delta\":\"原生助手\",\"seq\":1}\n{\"type\":\"done\",\"ok\":true,\"seq\":2}\n{\"type\":\"unexpected\"}\n"
        let (client, script, _) = try setup([MockReply(headers: ["Content-Type": "application/x-ndjson"], body: wire)])
        let events = StreamEvents()
        try await client.stream(path: "/api/system-agent/runs/run-one/stream?after_seq=0") { await events.append($0) }
        #expect(await events.values.count == 2)
        #expect(await events.values.first?["delta"]?.stringValue == "原生助手")
        #expect(script.requests.count == 1)
        #expect(script.requests.first?.httpMethod == "GET")
    }

    @Test func durableStreamWithoutDoneRequiresReconciliation() async throws {
        let (client, _, _) = try setup([MockReply(headers: ["Content-Type": "application/x-ndjson"],
                                                  body: "{\"type\":\"heartbeat\"}\n")])
        await #expect(throws: APIError.self) {
            try await client.stream(path: "/api/system-agent/runs/run-one/stream") { _ in }
        }
    }
}
