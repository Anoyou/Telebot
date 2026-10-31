import Foundation

/// Agent 每个会话记住上次选择的本轮模型（第 22 轮）。
///
/// - 新服务器（`features.agent_session_model_selection`）把选择存在会话的 `model_selection` 上，
///   Web 与 iOS 一致、换设备也记得；改选择时 `PATCH` 会话，发送时服务器也会顺带记住。
/// - 旧服务器或服务器上还没有值时，退回本机按会话 id 记的值（`LocalStore`）。
/// - 新会话默认「自动」；所选模型已被删除 / 停用（不在模型清单里）时回落「自动」，不报错。
public enum AgentSessionModel {
    public static let featureKey = "agent_session_model_selection"
    public static let auto: JSONValue = ["mode": "auto"]

    /// 模型清单（`capabilities.model_matrix` 过滤后）下标 → 运行请求里的 `model_selection`；越界为自动。
    public static func selection(models: [JSONValue], index: Int) -> JSONValue {
        guard models.indices.contains(index), let provider = models[index]["provider_id"],
              let model = models[index]["model"] else { return auto }
        return ["mode": "pinned", "provider_id": provider, "model": model]
    }

    /// 选择 → 模型清单下标；自动、格式不对、或所选模型已不在清单里时为 -1（自动）。
    public static func index(of selection: JSONValue?, in models: [JSONValue]) -> Int {
        guard let selection, selection["mode"]?.stringValue == "pinned",
              let provider = selection["provider_id"]?.intValue,
              let model = selection["model"]?.stringValue, !model.isEmpty else { return -1 }
        return models.firstIndex { $0["provider_id"]?.intValue == provider && $0["model"]?.stringValue == model } ?? -1
    }

    /// 打开会话时要恢复的选择：服务器值优先（服务器支持且有值），否则本机记的值，都没有为 nil（自动）。
    public static func restored(serverRow: JSONValue?, serverSupports: Bool, local: JSONValue?) -> JSONValue? {
        if serverSupports, let value = serverRow?["model_selection"], value["mode"]?.stringValue != nil { return value }
        return local
    }

    /// 本机按会话 id 记忆（旧服务器兜底）。按服务器地址 + 用户分开存，最多保留 `limit` 个会话。
    public struct LocalStore {
        public static let keyPrefix = "telepilot.assistant.sessionModel."
        public let defaults: UserDefaults
        public let key: String
        public var limit = 200

        public init(defaults: UserDefaults = .standard, scope: String) {
            self.defaults = defaults
            self.key = Self.keyPrefix + scope
        }

        private func read() -> [String: JSONValue] {
            guard let data = defaults.data(forKey: key),
                  let value = try? JSONDecoder().decode([String: JSONValue].self, from: data) else { return [:] }
            return value
        }

        public func load(_ sessionID: String) -> JSONValue? { read()[sessionID] }

        /// 记下选择；自动时删除该会话的记录（缺省就是自动）。
        public func save(_ sessionID: String, _ selection: JSONValue) {
            var all = read()
            if selection["mode"]?.stringValue == "pinned" {
                all[sessionID] = selection
            } else {
                all.removeValue(forKey: sessionID)
            }
            if all.count > limit {
                // 超出上限时随便丢掉多余的（只影响很久没打开的旧会话回落自动）。
                for extra in all.keys.sorted().prefix(all.count - limit) where extra != sessionID { all.removeValue(forKey: extra) }
            }
            if let data = try? JSONEncoder().encode(all) { defaults.set(data, forKey: key) }
        }

        /// 清掉所有服务器 / 用户的本机记忆（界面测试每次启动从空白开始）。
        public static func removeAll(defaults: UserDefaults = .standard) {
            for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(keyPrefix) { defaults.removeObject(forKey: key) }
        }

        public func remove(_ sessionID: String) {
            var all = read()
            guard all.removeValue(forKey: sessionID) != nil else { return }
            if let data = try? JSONEncoder().encode(all) { defaults.set(data, forKey: key) }
        }
    }
}
