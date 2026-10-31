import Foundation
import Testing
@testable import TelePilotCore

@Suite struct ServerUpdateRecoveryTests {
    @Test func expectedCommitMustMatch() {
        let baseline = ServerUpdateRecovery.Baseline(version: "0.114.0-beta.1", revision: "56246fd0a1b2", expectedCommit: "9a8b7c6d5e4f")
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.114.0-beta.1", observedRevision: "56246fd0a1b2c3d4", healthyCount: 5) == .stillOld)
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.115.0-beta.1", observedRevision: "9a8b7c6d5e4f3a2b", healthyCount: 1) == .recovered)
    }

    @Test func versionChangeCountsWhenNoCommit() {
        let baseline = ServerUpdateRecovery.Baseline(version: "0.114.0-beta.1", revision: nil, expectedCommit: nil)
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.115.0-beta.1", observedRevision: nil, healthyCount: 1) == .recovered)
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.114.0-beta.1", observedRevision: nil, healthyCount: 1) == .waiting)
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.114.0-beta.1", observedRevision: nil, healthyCount: 3) == .recovered)
    }

    @Test func revisionChangeWithoutTarget() {
        let baseline = ServerUpdateRecovery.Baseline(version: "0.114.0", revision: "aaaaaaa1", expectedCommit: " ")
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.114.0", observedRevision: "bbbbbbb2", healthyCount: 1) == .recovered)
        #expect(ServerUpdateRecovery.verdict(baseline: baseline, observedVersion: "0.114.0", observedRevision: "aaaaaaa1ffff", healthyCount: 1) == .waiting)
    }

    @Test func shortCommitsAreNotCompared() {
        #expect(!ServerUpdateRecovery.sameCommit("abc", "abcdef0"))
        #expect(ServerUpdateRecovery.sameCommit("ABCDEF0123", "abcdef0"))
        #expect(!ServerUpdateRecovery.sameCommit(nil, "abcdef0"))
    }
}
