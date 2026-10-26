import Foundation
import Testing
@testable import TelePilotCore

@Suite struct PluginReleaseNotesTests {
    @Test func parsesParenthesizedDatesUsedByTelePilotPlugins() {
        let markdown = """
        # 更新日志

        ## 0.1.13 (2026-10-26)
        - 配置表单：可选字段不再标必填，
          留空时仍按默认值处理。

        ## 0.1.12 (2026-08-15)
        - 聊天回复改走 `MessageOps`。
        - 第二条

        ## 0.1.11
        普通段落也算一条。
        """
        let releases = PluginReleaseNotes.parse(markdown)
        #expect(releases.map(\.version) == ["0.1.13", "0.1.12", "0.1.11"])
        #expect(releases[0].date == "2026-10-26")
        #expect(releases[0].items == ["配置表单：可选字段不再标必填， 留空时仍按默认值处理。"])
        #expect(releases[1].items.count == 2)
        #expect(releases[2].date == nil)
        #expect(releases[2].items == ["普通段落也算一条。"])
    }

    @Test func parsesKeepAChangelogHeadersAndSkipsUnreleased() {
        let markdown = """
        ## [Unreleased]
        - 还没发布

        ## [1.2.0] - 2026-10-01
        ### 新增
        - 新按钮
        ### 修复
        * 修复崩溃

        ## v1.1.0 - 2026-09-01
        - 旧版本

        ## [1.0.0] - 2026-08-01
        """
        let releases = PluginReleaseNotes.parse(markdown)
        #expect(releases.map(\.version) == ["1.2.0", "1.1.0"])
        #expect(releases[0].items == ["新按钮", "修复崩溃"])
        #expect(releases[1].date == "2026-09-01")
    }

    @Test func filtersReleasesBetweenInstalledAndLatest() {
        let releases = [
            PluginRelease(version: "1.3.0", items: ["未来"]),
            PluginRelease(version: "1.2.0", items: ["新"]),
            PluginRelease(version: "1.1.1", items: ["补丁"]),
            PluginRelease(version: "1.1.0", items: ["当前"]),
        ]
        let pending = PluginReleaseNotes.releases(releases, newerThan: "1.1.0", upTo: "1.2.0")
        #expect(pending.map(\.version) == ["1.2.0", "1.1.1"])
        #expect(PluginReleaseNotes.releases(releases, newerThan: nil).count == 4)
        #expect(PluginReleaseNotes.release(releases, for: "v1.1.0")?.items == ["当前"])
        #expect(PluginReleaseNotes.release(releases, for: nil) == nil)
    }

    @Test func comparesVersionsLikeServer() {
        #expect(PluginVersion.isNewer("0.1.10", than: "0.1.9"))
        #expect(PluginVersion.compare("1.0", "1.0.0") == .orderedSame)
        #expect(PluginVersion.compare("0.73.0-beta.15", "0.73.0") == .orderedSame)
        #expect(!PluginVersion.isNewer("1.0.0", than: "1.0.1"))
        #expect(PluginVersion.components("v2.10.3.4") == [2, 10, 3])
    }
}
