import Foundation

public struct APIContractCatalog: Sendable {
    public struct Operation: Sendable {
        public let path: String
        public let method: String
        public let requestSchema: JSONValue
        public let parameters: [JSONValue]
        public let summary: String
        public let contentType: String?
    }
    private let document: JSONValue
    public static let bundled = APIContractCatalog()

    public init() {
        if let url = Bundle.module.url(forResource: "contract", withExtension: "json"),
           let data = try? Data(contentsOf: url), let root = try? JSONValue(data: data) {
            document = root
        } else { document = .object([:]) }
    }

    public func operation(path: String, method: String) -> Operation? {
        let cleanPath = path.split(separator: "?", maxSplits: 1).first.map(String.init) ?? path
        let paths = document["paths"]?.objectValue ?? [:]
        let template: String?
        if paths[cleanPath] != nil { template = cleanPath }
        else {
            let components = cleanPath.split(separator: "/")
            template = paths.keys.sorted().first { candidate in
                let parts = candidate.split(separator: "/")
                return parts.count == components.count && zip(parts, components).allSatisfy {
                    ($0.0.hasPrefix("{") && $0.0.hasSuffix("}")) || $0.0 == $0.1
                }
            }
        }
        guard let template, let value = paths[template]?[method.lowercased()] else { return nil }
        let content = value["requestBody"]?["content"]?.objectValue ?? [:]
        let contentType = content["application/json"] != nil ? "application/json" : content.keys.sorted().first
        let schema = contentType.flatMap { content[$0]?["schema"] } ?? .object([:])
        return Operation(path: template, method: method.uppercased(), requestSchema: resolve(schema),
                         parameters: (value["parameters"]?.arrayValue ?? []).map { resolve($0) },
                         summary: value["summary"]?.stringValue ?? "", contentType: contentType)
    }

    public func resolve(_ schema: JSONValue, depth: Int = 0) -> JSONValue {
        guard depth < 12 else { return schema }
        if let ref = schema["$ref"]?.stringValue {
            let name = ref.split(separator: "/").last.map(String.init) ?? ""
            if let target = document["components"]?["schemas"]?[name] { return resolve(target, depth: depth + 1) }
        }
        switch schema {
        case .object(let fields): return .object(fields.mapValues { resolve($0, depth: depth + 1) })
        case .array(let values): return .array(values.map { resolve($0, depth: depth + 1) })
        default: return schema
        }
    }
}
