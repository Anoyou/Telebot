import Foundation

enum PrivacyFormatting {
    static func phone(_ value: String) -> String {
        guard value.count > 4 else { return "••••" }
        return "•••• " + value.suffix(4)
    }
}
