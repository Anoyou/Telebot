import Foundation

/// Keeps reasoning already received for a turn when a compatible server omits it
/// from its final/history packet. Matching is by scoped IDs, never answer text.
/// The caller supplies redacted text; server-provided reasoning stays authoritative.
public struct ReasoningHistory: Sendable {
    private struct Snapshot: Sendable {
        let text: String
        let runID: String?
    }
    private var entries: [String: Snapshot] = [:]
    private var order: [String] = []

    public init() {}

    private func key(scope: String, kind: String, id: String?) -> String? {
        guard let id, !id.isEmpty else { return nil }
        return scope + "\u{0}" + kind + ":" + id
    }

    public mutating func remember(scope: String, runID: String?, messageID: String?, text: String) {
        for key in [key(scope: scope, kind: "run", id: runID), key(scope: scope, kind: "message", id: messageID)].compactMap({ $0 }) {
            if text.isEmpty {
                entries.removeValue(forKey: key)
                order.removeAll { $0 == key }
            } else {
                if entries[key] == nil { order.append(key) }
                entries[key] = Snapshot(text: text, runID: runID)
            }
        }
        // At most 200 turns with both IDs. Older history normally comes from the server.
        while order.count > 400 { entries.removeValue(forKey: order.removeFirst()) }
    }

    public func restoring(_ message: JSONValue, scope: String) -> JSONValue {
        guard message["role"]?.stringValue == "assistant",
              var object = message.objectValue,
              var content = message["content"]?.objectValue,
              (content["reasoning"]?.stringValue ?? "").isEmpty else { return message }
        let keys = [key(scope: scope, kind: "message", id: message["id"]?.stringValue ?? message["id"]?.intValue.map(String.init)),
                    key(scope: scope, kind: "run", id: message["usage"]?["run_id"]?.stringValue)]
        let runID = message["usage"]?["run_id"]?.stringValue
        let snapshot = keys.compactMap({ $0 }).compactMap({ entries[$0] }).first { item in
            guard let previousRun = item.runID, let runID else { return true }
            return previousRun == runID
        }
        guard let snapshot else { return message }
        content["reasoning"] = .string(snapshot.text)
        object["content"] = .object(content)
        return .object(object)
    }
}
