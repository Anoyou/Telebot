import Foundation
import Testing
@testable import TelePilotCore

@Suite struct AppChangelogTests {
    @Test func parsesReleasesGroupsAndNotes() {
        let markdown = """
        # iOS App 更新日志

        - 前言里的列表不属于任何版本。

        ## [Unreleased]

        ## [0.7.0] - 2026-10-25 · minor（次版本） · 新主题

        > 对应服务器：Beta

        ### 新增

        - 第一条 `代码`
          续行
        - 第二条

        ### 修复

        - 修复一条

        ## [0.6.1] - 2026-10-20 · patch（补丁版本） · 修复
        ### 修复
        - 只有一条
        """
        let releases = AppChangelog.parse(markdown)
        #expect(releases.map(\.version) == ["0.7.0", "0.6.1"])
        let first = releases[0]
        #expect(first.date == "2026-10-25")
        #expect(first.level == "minor（次版本）")
        #expect(first.title == "新主题")
        #expect(first.note == "对应服务器：Beta")
        #expect(first.groups.map(\.title) == ["新增", "修复"])
        #expect(first.groups[0].items == ["第一条 `代码`续行", "第二条"])
        #expect(first.itemCount == 3)
        #expect(!first.isUnreleased)
    }

    @Test func keepsUnreleasedWhenItHasItems() {
        let releases = AppChangelog.parse("## [Unreleased]\n### 新增\n- 开发中\n## [0.1.0] - 2026-01-01\n- 无分类条目\n")
        #expect(releases.map(\.version) == ["Unreleased", "0.1.0"])
        #expect(releases[0].isUnreleased)
        #expect(releases[1].groups.first?.title == "变化")
        #expect(releases[1].title == nil)
    }

    /// 仓库里的 ios/CHANGELOG.md 必须能被解析，且最新正式版本与工程 MARKETING_VERSION 一致。
    @Test func bundledChangelogMatchesProjectVersion() throws {
        let iosRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let markdown = try String(contentsOf: iosRoot.appendingPathComponent("CHANGELOG.md"), encoding: .utf8)
        let project = try String(contentsOf: iosRoot.appendingPathComponent("project.yml"), encoding: .utf8)
        let releases = AppChangelog.parse(markdown).filter { !$0.isUnreleased }
        #expect(!releases.isEmpty)
        let marketing = project.split(separator: "\n")
            .first { $0.contains("MARKETING_VERSION:") }
            .map { $0.split(separator: ":")[1].trimmingCharacters(in: CharacterSet(charactersIn: " \"")) }
        #expect(releases.first?.version == marketing)
        for release in releases {
            #expect(release.date != nil, "\(release.version) 缺少日期")
            #expect(release.note?.hasPrefix("对应服务器") == true, "\(release.version) 缺少“对应服务器”说明")
        }
    }
}
