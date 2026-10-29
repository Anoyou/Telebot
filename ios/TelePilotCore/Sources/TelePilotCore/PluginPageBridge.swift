import Foundation

/// 插件页面 Bridge 协议 v1（与 Web `frontend/src/lib/pluginPageBridge.ts` 同一套，契约见
/// `schemas/plugin-ui-widgets.json` → `pages.bridge`）：
/// 页面 → 宿主 `{tp:"telepilot", v:1, id, method, params}`；宿主 → 页面 `{tp, v, id, ok, result | error:{code, message}}`；
/// 宿主事件 `{tp, v, event:"theme", data:{scheme}}`。宿主只做格式校验与转发，权限由服务器逐次校验。
public enum PluginPageBridge {
    public static let protocolVersion: Int64 = 1
    public static let maxParamsBytes = 32_768

    public struct Request: Equatable, Sendable {
        public let id: String
        public let method: String
        public let params: JSONValue
    }

    /// WKScriptMessage.body（NSDictionary 等）→ JSONValue；不是 JSON 对象时返回 nil。
    public static func decode(any body: Any) -> JSONValue? {
        guard JSONSerialization.isValidJSONObject(body),
              let data = try? JSONSerialization.data(withJSONObject: body) else { return nil }
        return try? JSONValue(data: data)
    }

    public static func parse(_ message: JSONValue?) -> Request? {
        guard let message, message["tp"]?.stringValue == "telepilot", message["v"]?.intValue == Int(protocolVersion),
              let id = message["id"]?.stringValue, isValidID(id),
              let method = message["method"]?.stringValue, isValidMethod(method) else { return nil }
        let params = message["params"] ?? .object([:])
        guard params.objectValue != nil else { return nil }
        if let data = try? params.data(), data.count > maxParamsBytes { return nil }
        return Request(id: id, method: method, params: params)
    }

    public static func ok(_ id: String, _ result: JSONValue) -> JSONValue {
        ["tp": "telepilot", "v": .integer(protocolVersion), "id": .string(id), "ok": true, "result": result]
    }

    public static func error(_ id: String, code: String, message: String) -> JSONValue {
        ["tp": "telepilot", "v": .integer(protocolVersion), "id": .string(id), "ok": false,
         "error": ["code": .string(code), "message": .string(message)]]
    }

    /// 页面只能在 tp-plugin: / about: 内导航，http(s)、tel、mailto 等一律拦下。
    public static func allowsNavigation(scheme: String) -> Bool {
        ["tp-plugin", "about"].contains(scheme.lowercased())
    }

    static func isValidID(_ id: String) -> Bool {
        let extra: Set<UInt32> = [95, 46, 58, 45] // _ . : -
        return !id.isEmpty && id.unicodeScalars.count <= 64 && id.unicodeScalars.allSatisfy { scalar in
            let v = scalar.value
            return (48...57).contains(v) || (65...90).contains(v) || (97...122).contains(v) || extra.contains(v)
        }
    }

    static func isValidMethod(_ method: String) -> Bool {
        let scalars = Array(method.unicodeScalars)
        guard let first = scalars.first, scalars.count <= 64, (97...122).contains(first.value) else { return false }
        return scalars.allSatisfy { scalar in
            let v = scalar.value
            return (97...122).contains(v) || (48...57).contains(v) || v == 95 || v == 46
        }
    }
}
