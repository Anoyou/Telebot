import Foundation

/// The owner-facing history and final stream event restore server credential placeholders.
/// Apply this at every display, copy and share boundary while retaining the original request.
enum AssistantRedaction {
    private struct Rule: Sendable {
        let expression: NSRegularExpression
        let replacement: String
        init(_ pattern: String, _ replacement: String = "••••••") {
            expression = try! NSRegularExpression(pattern: pattern)
            self.replacement = replacement
        }
    }

    private static let rules: [Rule] = [
        Rule(#"(?s)-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?(?:-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|$)"#),
        Rule(#"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@"#, "$1••••••@"),
        Rule(#"(?i)((?:https?://)?api\.telegram\.org/bot)[^/\s\"']+"#, "$1••••••"),
        Rule(#"(?i)(https://api(?:\.sandbox)?\.push\.apple\.com/3/device/)[0-9a-f]+"#, "$1••••••"),
        Rule(#"(?i)\b(?:sk-ant-|sk-proj-|sk-or-|sk-|xai-|gsk_|AIza|ghp_|gho_|github_pat_|hf_)[A-Za-z0-9_\-]*"#),
        Rule(#"\b[0-9]{6,12}:[A-Za-z0-9_-]{20,}\b"#),
        Rule(#"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"#),
        Rule(#"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._+/=\-]+"#, "$1••••••"),
        Rule(#"(?i)((?:[\"']?)(?:[a-z0-9.-]+[_-])*(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer[_-]?token|bot[_-]?token|token|password|passwd|secret(?:[_-]?key)?|credentials?|cookie|proxy[_-]?(?:user|pass)|session[_-]?string|totp|[a-z0-9_.-]+_enc)(?:[\"']?)\s*[:=]\s*)(?:[\"'][^\"'\r\n]*[\"']|(?:bearer|basic)\s+[^\r\n,;}\]]+|[^\s,;}\]&]+)"#, "$1••••••"),
        Rule(#"((?:密码|口令|密钥|令牌|验证码)\s*[:：=]\s*)(?:[\"'][^\"'\r\n]*[\"']|[^\s,，;；。]+)"#, "$1••••••")
    ]

    static func text(_ value: String) -> String {
        rules.reduce(value) { output, rule in
            rule.expression.stringByReplacingMatches(in: output, range: NSRange(output.startIndex..., in: output), withTemplate: rule.replacement)
        }
    }

    static func containsCredential(_ value: String) -> Bool { text(value) != value }
}
