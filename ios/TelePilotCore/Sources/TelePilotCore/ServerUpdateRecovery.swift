import Foundation

/// 服务器更新后「等服务器恢复」的判断（第 21 轮）。
///
/// 更新任务到 100%（或只提交了重启）后，服务会重启一段时间。App 每隔几秒读一次无鉴权的
/// `GET /api/system/version`，按下面的规则判断新版本是否已经在跑，然后自动重新检查更新、刷新状态：
/// - 知道目标提交（任务的 `new_commit` 或检查结果的 `remote_commit`）且服务器报告了提交：两者前缀一致才算恢复；
/// - 否则版本号与更新前不同即算恢复；
/// - 都判断不了（服务器不报告提交、版本号也没变）：连续几次读到正常响应就算恢复（服务已经起来了）。
/// 与 Web `frontend/src/lib/updateRecovery.ts` 规则一致。
public enum ServerUpdateRecovery {
    /// 两次读取的间隔（秒）。
    public static let pollInterval: Double = 3
    /// 等多久还没恢复就提示超时（秒）。
    public static let timeout: Double = 180
    /// 无法比对版本时，连续几次正常响应算恢复。
    public static let healthyStreak = 3

    public struct Baseline: Equatable, Sendable {
        public let version: String?
        public let revision: String?
        public let expectedCommit: String?
        public init(version: String?, revision: String?, expectedCommit: String?) {
            self.version = Self.clean(version)
            self.revision = Self.clean(revision)
            self.expectedCommit = Self.clean(expectedCommit)
        }
        static func clean(_ value: String?) -> String? {
            guard let text = value?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
            return text
        }
    }

    public enum Verdict: Equatable, Sendable {
        /// 新版本已在运行（或服务已稳定恢复）。
        case recovered
        /// 服务有响应，但还是旧版本（重启还没轮到它，或旧容器还在）。
        case stillOld
        /// 服务有响应，暂时判断不了，继续等几次。
        case waiting
    }

    /// 两个提交号是否指同一个提交（长短不同的缩写按前缀比较，至少 7 位）。
    public static func sameCommit(_ lhs: String?, _ rhs: String?) -> Bool {
        guard let a = Baseline.clean(lhs)?.lowercased(), let b = Baseline.clean(rhs)?.lowercased(),
              min(a.count, b.count) >= 7 else { return false }
        return a.hasPrefix(b) || b.hasPrefix(a)
    }

    /// 读到一次正常响应后的判断。`healthyCount` 是包括这一次在内的连续正常响应次数。
    public static func verdict(baseline: Baseline, observedVersion: String?, observedRevision: String?, healthyCount: Int) -> Verdict {
        let revision = Baseline.clean(observedRevision)
        let version = Baseline.clean(observedVersion)
        if let expected = baseline.expectedCommit, let revision {
            return sameCommit(expected, revision) ? .recovered : .stillOld
        }
        if let old = baseline.revision, let revision, !sameCommit(old, revision) { return .recovered }
        if let old = baseline.version, let version, old != version { return .recovered }
        // 判断不了（只是重启、或服务器不报告提交）：服务稳定响应几次后算恢复。
        return healthyCount >= healthyStreak ? .recovered : .waiting
    }
}
