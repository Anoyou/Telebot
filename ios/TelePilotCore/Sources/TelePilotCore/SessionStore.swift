import Foundation
import Security

struct StoredSession: Codable, Sendable {
    let token: String
    let expiresAt: Date
}

protocol SessionStore: Sendable {
    func load(origin: String) throws -> StoredSession?
    func save(_ session: StoredSession, origin: String) throws
    func clear(origin: String) throws
}

struct KeychainSessionStore: SessionStore {
    private func query(_ origin: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "TelePilot.Native.Session",
         kSecAttrAccount as String: origin,
         kSecAttrSynchronizable as String: false]
    }

    func load(origin: String) throws -> StoredSession? {
        var query = query(origin)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw APIError.secureStorage }
        guard let session = try? JSONDecoder().decode(StoredSession.self, from: data) else {
            try clear(origin: origin)
            return nil
        }
        return session
    }

    func save(_ session: StoredSession, origin: String) throws {
        let data = try JSONEncoder().encode(session)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        let status = SecItemUpdate(query(origin) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query(origin)
            item.merge(attributes) { _, new in new }
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw APIError.secureStorage }
        } else if status != errSecSuccess { throw APIError.secureStorage }
    }

    func clear(origin: String) throws {
        let status = SecItemDelete(query(origin) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw APIError.secureStorage }
    }
}
