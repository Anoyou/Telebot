import XCTest
import UIKit

@MainActor
final class WorkspaceUITests: XCTestCase {
    private func launch(_ extra: [String] = []) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"] + extra
        app.launch()
        return app
    }

    private func openPluginMarket(_ app: XCUIApplication) {
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugins.discover"].tap()
        let entry = app.buttons["plugins.market.entry"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
    }

    /// 插件市场（iOS 0.17.0 / 服务器 0.113.0）：更新角标、一键全部更新（服务器并行，结果逐个显示）、已自动回滚提示、来源管理。
    func testPluginMarketUpdatesAllAndManagesSources() {
        let app = launch()
        openPluginMarket(app)
        let count = app.staticTexts["plugins.market.updateCount"]
        XCTAssertTrue(count.waitForExistence(timeout: 15))
        XCTAssertEqual(count.label, "1 个插件可更新")
        XCTAssertTrue(app.descendants(matching: .any)["plugins.market.rolledBack.dice_party"].firstMatch.exists)
        let action = app.buttons["plugins.market.action.content_assistant"]
        XCTAssertTrue(action.waitForExistence(timeout: 5))
        XCTAssertEqual(action.label, "更新")
        XCTAssertEqual(app.buttons["plugins.market.action.weather_digest"].label, "获取")

        app.buttons["plugins.market.updateAll"].tap()
        let confirm = app.buttons.matching(NSPredicate(format: "label == %@ AND identifier != %@", "全部更新", "plugins.market.updateAll")).firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(app.descendants(matching: .any)["plugins.market.result.content_assistant"].firstMatch.waitForExistence(timeout: 15))
        let updated = NSPredicate(format: "label == %@", "所有已安装插件都是最新的")
        expectation(for: updated, evaluatedWith: count)
        waitForExpectations(timeout: 10)

        let sources = app.buttons["plugins.market.sources"]
        XCTAssertTrue(reveal(sources, in: app))
        sources.tap()
        XCTAssertTrue(app.switches["plugins.market.source.auto.official"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["plugins.market.source.delete.official"].exists)
        let url = app.textFields["plugins.market.source.url"]
        XCTAssertTrue(reveal(url, in: app))
        url.tap()
        url.typeText("https://example.com/registry.json")
        let add = app.buttons["plugins.market.source.add"]
        XCTAssertTrue(reveal(add, in: app))
        add.tap()
        XCTAssertTrue(app.descendants(matching: .any)["plugins.market.source.src-fixture"].firstMatch.waitForExistence(timeout: 10))
    }

    /// 旧服务器没有插件市场接口：页面提示更新服务器，“发现”页的其它安装方式不受影响。
    func testPluginMarketAsksToUpdateOldServer() {
        let app = launch(["--ui-test-market-unsupported"])
        openPluginMarket(app)
        XCTAssertTrue(app.descendants(matching: .any)["plugins.market.unsupported"].firstMatch.waitForExistence(timeout: 15))
    }

    func testDockKeepsPrimarySectionsAndAgentEntry() {
        let app = launch()
        // 工作流「预热模拟器」也跑这个用例：通过 TEST_RUNNER_TELEPILOT_WARMUP_TIMEOUT 放宽首次冷启动的等待，
        // 一次等到 App 起来，而不是 45 秒超时后反复重跑（每次重跑另有 30–50 秒 xcodebuild 开销）。
        let timeout = ProcessInfo.processInfo.environment["TELEPILOT_WARMUP_TIMEOUT"].flatMap(TimeInterval.init) ?? 45
        XCTAssertTrue(app.tabBars.buttons["概览"].waitForExistence(timeout: timeout))
        XCTAssertTrue(app.tabBars.buttons["插件"].exists)
        XCTAssertTrue(app.tabBars.buttons["AI"].exists)
        XCTAssertTrue(app.tabBars.buttons["更多"].exists)
        XCTAssertTrue(app.tabBars.buttons["Agent"].exists)
    }

    /// 第十一轮：冷启动有会话时先显示上次的概览快照并标“更新于 …”；断网启动（连核对会话都失败）也留在主界面，
    /// 不误落登录页。第一次启动完整刷新后保存快照，第二次断网启动读取它。
    func testColdStartShowsSnapshotWhenOffline() {
        continueAfterFailure = false
        let base = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN", "--ui-test-snapshot"]
        let first = XCUIApplication()
        first.launchArguments = base
        first.launch()
        let status = first.descendants(matching: .any)["overview.status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 45))
        let loaded = NSPredicate(format: "label CONTAINS %@ OR label CONTAINS %@", "服务运行中", "部分状态未能读取")
        expectation(for: loaded, evaluatedWith: status)
        waitForExpectations(timeout: 30)
        // 快照在后台写盘。
        sleep(2)
        first.terminate()

        let second = XCUIApplication()
        second.launchArguments = base + ["--ui-test-snapshot-reuse", "--ui-test-offline", "--ui-test-offline-start"]
        second.launch()
        let cached = second.descendants(matching: .any)["overview.status"].firstMatch
        XCTAssertTrue(cached.waitForExistence(timeout: 45))
        XCTAssertTrue(cached.label.contains("上次读取的状态"), cached.label)
        XCTAssertTrue(cached.label.contains("更新于"), cached.label)
        XCTAssertTrue(second.tabBars.buttons["概览"].exists)
        XCTAssertFalse(second.textFields["login.server"].exists)
        let attachment = XCTAttachment(screenshot: second.screenshot())
        attachment.name = "断网冷启动显示快照"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    func testMoreSectionKeepsAccountsAndSettingsReachable() {
        let app = launch()
        let more = app.tabBars.buttons["更多"]
        XCTAssertTrue(more.waitForExistence(timeout: 45))
        more.tap()
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["more.settings"].exists)
        XCTAssertTrue(app.buttons["more.search"].exists)
    }

    /// 页面级分段控件：插件中心（ScrollView）、发现、已安装（List）与 AI 概览、供应商、用量（List）同一位置、
    /// 同一尺寸，切换分页不跳动；并且不被分组列表的卡片圆角裁切。
    ///
    /// 只比 frame 查不出裁切（第五轮 frame 完全一致，但真机上发现、已安装两端被切掉），
    /// 所以再比截图：分段控件最外侧几个点宽的竖条在“该端分段未选中”的两个分页之间应当逐像素一致，
    /// 并与插件中心（ScrollView，不会被裁切）的同一竖条一致。
    func testSegmentedHeadersShareFrameAndAreNotClipped() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        // 分段控件下方不再常驻搜索框：搜索在导航栏左上角。
        XCTAssertFalse(app.searchFields.firstMatch.exists)
        XCTAssertTrue(app.buttons["plugins.search"].exists)
        let center = settledHeader(app)
        app.buttons["plugins.discover"].tap()
        XCTAssertTrue(app.buttons["plugin.discover.row.weather_digest"].waitForExistence(timeout: 15))
        let discover = settledHeader(app)
        app.buttons["plugins.installed"].tap()
        XCTAssertTrue(app.buttons["plugins.checkUpdates"].waitForExistence(timeout: 10))
        let installed = settledHeader(app)

        app.tabBars.buttons["AI"].tap()
        XCTAssertTrue(app.segmentedControls.buttons["概览"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["ai.search"].exists)
        let overview = settledHeader(app)
        app.segmentedControls.buttons["供应商"].tap()
        let providers = settledHeader(app)
        app.segmentedControls.buttons["用量"].tap()
        let usage = settledHeader(app)

        let all = [("插件中心", center), ("发现", discover), ("已安装", installed), ("AI 概览", overview), ("AI 供应商", providers), ("AI 用量", usage)]
        let message = all.map { "\($0.0) \($0.1.frame)" }.joined(separator: "；")
        for (name, header) in all {
            XCTAssertEqual(header.frame.minX, center.frame.minX, accuracy: 1, "\(name)：\(message)")
            XCTAssertEqual(header.frame.width, center.frame.width, accuracy: 1, "\(name)：\(message)")
            XCTAssertEqual(header.frame.height, center.frame.height, accuracy: 1, "\(name)：\(message)")
            XCTAssertEqual(header.frame.minY, center.frame.minY, accuracy: 2, "\(name)：\(message)")
        }
        // 右端（第三段未选中）：插件中心 ↔ 发现 ↔ AI 概览 ↔ AI 供应商。
        let right: (Header) -> CGRect = { h in CGRect(x: h.frame.maxX - 7, y: h.frame.minY - 2, width: 9, height: h.frame.height + 4) }
        // 左端（第一段未选中）：发现 ↔ 已安装 ↔ AI 供应商 ↔ AI 用量。
        let left: (Header) -> CGRect = { h in CGRect(x: h.frame.minX - 2, y: h.frame.minY - 2, width: 9, height: h.frame.height + 4) }
        for (name, other) in [("发现", discover), ("AI 概览", overview), ("AI 供应商", providers)] {
            let diff = PixelDiff.mean(center.shot, right(center), other.shot, right(other))
            XCTAssertLessThan(diff, 4, "分段控件右端与插件中心不一致（被裁切？）：\(name) 平均差 \(diff)")
        }
        for (name, other) in [("已安装", installed), ("AI 供应商", providers), ("AI 用量", usage)] {
            let diff = PixelDiff.mean(discover.shot, left(discover), other.shot, left(other))
            XCTAssertLessThan(diff, 4, "分段控件左端与发现页不一致（被裁切？）：\(name) 平均差 \(diff)")
        }
    }

    private struct Header {
        let frame: CGRect
        let shot: XCUIScreenshot
    }

    /// 等切换动画结束后读取分段控件的 frame 并截图。
    private func settledHeader(_ app: XCUIApplication) -> Header {
        let control = app.segmentedControls.firstMatch
        XCTAssertTrue(control.waitForExistence(timeout: 10))
        var frame = control.frame
        for _ in 0..<10 {
            Thread.sleep(forTimeInterval: 0.25)
            let next = control.frame
            if next == frame { break }
            frame = next
        }
        Thread.sleep(forTimeInterval: 0.4)
        return Header(frame: control.frame, shot: XCUIScreen.main.screenshot())
    }

    /// 页面级搜索统一为导航栏左上角的放大镜：点按展开系统搜索栏并可直接输入，作用于当前分页；取消即收起并清空。
    func testPageSearchUsesLeadingToolbarButton() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        let button = app.buttons["plugins.search"]
        XCTAssertEqual(button.label, "搜索")
        // 左上角：在屏幕左半边、导航栏高度内。
        XCTAssertLessThan(button.frame.midX, app.frame.midX)
        XCTAssertLessThan(button.frame.maxY, app.segmentedControls.firstMatch.frame.minY)
        button.tap()
        let field = app.searchFields.firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        type("自动", into: field, app: app)
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["plugin.card.content_assistant"].exists)
        // 同一个搜索作用于切换后的分页。
        app.buttons["plugins.installed"].tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "plugin.installed.row.auto_reply")).firstMatch.waitForExistence(timeout: 10)
                      || app.staticTexts["自动回复"].waitForExistence(timeout: 2))
        tapCancel(app)
        XCTAssertFalse(app.searchFields.firstMatch.waitForExistence(timeout: 2))
        app.buttons["plugins.center"].tap()
        XCTAssertTrue(app.buttons["plugin.card.content_assistant"].waitForExistence(timeout: 10))

        app.tabBars.buttons["AI"].tap()
        let ai = app.buttons["ai.search"]
        XCTAssertTrue(ai.waitForExistence(timeout: 10))
        XCTAssertLessThan(ai.frame.midX, app.frame.midX)
        ai.tap()
        XCTAssertTrue(app.searchFields.firstMatch.waitForExistence(timeout: 5))
        tapCancel(app)
        XCTAssertFalse(app.searchFields.firstMatch.waitForExistence(timeout: 2))

        app.tabBars.buttons["更多"].tap()
        let more = app.buttons["more.search"]
        XCTAssertTrue(more.waitForExistence(timeout: 10))
        XCTAssertLessThan(more.frame.midX, app.frame.midX)
        more.tap()
        let moreField = app.searchFields.firstMatch
        XCTAssertTrue(moreField.waitForExistence(timeout: 5))
        type("日常", into: moreField, app: app)
        XCTAssertTrue(app.buttons["search.account.1"].waitForExistence(timeout: 10))
        tapCancel(app)
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 5))
    }

    private func type(_ text: String, into field: XCUIElement, app: XCUIApplication) {
        // 展开后系统搜索栏应已聚焦；个别系统版本上首次聚焦较慢，未出现键盘时再点一下。
        if !app.keyboards.firstMatch.waitForExistence(timeout: 2) { field.tap() }
        field.typeText(text)
    }

    /// 点系统搜索栏的“取消”。切换分页等操作会让搜索栏失去焦点，系统此时可能收起“取消”按钮：先点回搜索栏。
    private func tapCancel(_ app: XCUIApplication) {
        let field = app.searchFields.firstMatch
        guard field.exists else { return }
        let names = ["取消", "Cancel", "关闭", "Close"]
        func cancelButton() -> XCUIElement? {
            names.lazy.map { app.buttons[$0].firstMatch }.first { $0.exists && $0.isHittable }
        }
        if cancelButton() == nil { field.tap(); _ = app.buttons["取消"].firstMatch.waitForExistence(timeout: 3) }
        guard let cancel = cancelButton() else {
            XCTFail("找不到搜索栏的取消按钮：" + app.buttons.allElementsBoundByIndex.prefix(30).map(\.label).joined(separator: " | "))
            return
        }
        cancel.tap()
    }

    func testPluginScreenUsesTwoColumnCards() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["plugins.category"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["plugins.accountPicker"].firstMatch.exists)
        // 原「插件管理」拆成 App Store 式的「发现」与「已安装」。
        app.buttons["plugins.installed"].tap()
        XCTAssertTrue(app.buttons["plugins.checkUpdates"].waitForExistence(timeout: 10))
        app.buttons["plugins.discover"].tap()
        XCTAssertTrue(app.buttons["plugin.discover.row.weather_digest"].waitForExistence(timeout: 15))
        // 其他安装方式在列表底部：懒加载列表要先滚到元素再断言。
        XCTAssertTrue(reveal(app.buttons["plugins.repositories"], in: app))
        XCTAssertTrue(app.buttons["plugins.installFromFile"].exists)
    }

    func testPluginStoreUpdatesShowNotesAndUpdateIndividually() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugins.installed"].tap()
        // 待更新区在最上：真实的可更新数、全部更新、版本 x → y 与可折叠的「新功能」（来自仓库 CHANGELOG）。
        XCTAssertTrue(app.buttons["plugins.updateAll"].waitForExistence(timeout: 10))
        let card = app.buttons["plugin.update.row.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 5))
        XCTAssertTrue(card.label.contains("v2.1.0 → v2.2.0"))
        XCTAssertTrue(app.descendants(matching: .any)["plugin.update.notes.content_assistant"].firstMatch.waitForExistence(timeout: 20))
        // 最近已更新（来自安装历史）。
        XCTAssertTrue(reveal(app.buttons["plugin.recent.row.forward_guard"], in: app))
        app.swipeDown()
        app.swipeDown()

        let update = app.buttons["plugin.update.action.content_assistant"]
        XCTAssertTrue(update.waitForExistence(timeout: 5))
        update.tap()
        // 更新成功后离开待更新区，出现在「最近已更新」，并显示真实的新版本。
        XCTAssertTrue(app.staticTexts["所有插件均为最新版本"].waitForExistence(timeout: 15))
        XCTAssertFalse(app.buttons["plugin.update.row.content_assistant"].exists)
        let recent = app.buttons["plugin.recent.row.content_assistant"]
        XCTAssertTrue(reveal(recent, in: app))
        XCTAssertTrue(recent.label.contains("v2.2.0"))
    }

    func testPluginStoreDiscoverInstallsAfterConfirmation() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugins.discover"].tap()
        // 已安装的显示「打开」，未安装的显示「获取」。
        let installed = app.buttons["plugin.discover.action.content_assistant"]
        XCTAssertTrue(installed.waitForExistence(timeout: 15))
        XCTAssertEqual(installed.label, "更新")
        let get = app.buttons["plugin.discover.action.dice_party"]
        XCTAssertTrue(reveal(get, in: app))
        XCTAssertEqual(get.label, "获取")
        get.tap()
        let confirm = app.buttons["安装"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        let open = app.buttons["plugin.discover.action.dice_party"]
        XCTAssertTrue(open.waitForExistence(timeout: 10))
        let opened = NSPredicate(format: "label == %@", "打开")
        expectation(for: opened, evaluatedWith: open)
        waitForExpectations(timeout: 10)
    }

    func testPluginProductPageShowsInfoWhatsNewAndConfirmsUninstall() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugins.installed"].tap()
        let card = app.buttons["plugin.update.row.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        let primary = app.buttons["plugin.product.primary"]
        XCTAssertTrue(primary.waitForExistence(timeout: 10))
        XCTAssertEqual(primary.label, "更新")
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "最低版本")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["TelePilot 社区"].exists)
        XCTAssertTrue(app.staticTexts["新功能"].waitForExistence(timeout: 20))
        // 停用与卸载在底部危险区，确认框挂在触发按钮上。
        let uninstall = app.buttons["plugin.product.uninstall"]
        XCTAssertTrue(reveal(uninstall, in: app, maxSwipes: 10))
        uninstall.tap()
        // 只弹出确认，不会直接卸载。
        XCTAssertTrue(app.buttons["卸载"].waitForExistence(timeout: 5))
        let cancel = app.buttons["取消"]
        if cancel.exists {
            cancel.tap()
        } else {
            app.navigationBars.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        }
        XCTAssertTrue(app.buttons["plugin.product.uninstall"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["plugin.product.globalToggle"].exists)
    }

    /// 懒加载列表里屏幕外的行不在辅助功能树中：上滑直到元素可点，再断言。
    /// 插件自带页面（iOS 0.15.0，Plugin API 1.4）：插件详情「插件页面与操作」打开隔离的 WKWebView 页面，
    /// 页面经 Bridge 读配置、调用插件操作（与 Web 同一协议），不能联网；菜单项需确认后才调用。
    func testPluginPageBridgeAndMenuAction() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let pluginCard = app.buttons["plugin.card.widget_gallery"]
        XCTAssertTrue(reveal(pluginCard, in: app))
        pluginCard.tap()

        let pageEntry = app.buttons["plugin.page.panel"]
        XCTAssertTrue(reveal(pageEntry, in: app, maxSwipes: 10))
        XCTAssertTrue(pageEntry.label.contains("控件面板"), pageEntry.label)
        pageEntry.tap()
        let webView = app.webViews.firstMatch
        XCTAssertTrue(webView.waitForExistence(timeout: 15))
        XCTAssertTrue(webView.staticTexts["你好 · widget_gallery"].waitForExistence(timeout: 15))
        XCTAssertTrue(webView.staticTexts["联网被阻止"].waitForExistence(timeout: 10))
        XCTAssertTrue(webView.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "主题：")).firstMatch.exists)
        let run = webView.buttons["调用插件操作"]
        XCTAssertTrue(run.waitForExistence(timeout: 5))
        run.tap()
        XCTAssertTrue(webView.staticTexts["已完成：示例摘要"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 菜单项：先确认，再调用
        let action = app.buttons["plugin.menuAction.summarize"]
        XCTAssertTrue(reveal(action, in: app))
        action.tap()
        let confirm = app.buttons["确定"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        let result = app.descendants(matching: .any)["plugin.menuAction.result"].firstMatch
        XCTAssertTrue(result.waitForExistence(timeout: 10))
        XCTAssertTrue(result.label.contains("摘要已生成"), result.label)
    }

    /// 「规则与自动化」只显示插件支持的入口（iOS 0.15.0）：普通插件没有「自动化规则」「配置任务与进度」，
    /// 声明了规则模式的插件才有「自动化规则」「复制规则到其他账号」。
    func testPluginAutomationShowsOnlySupportedEntries() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let widgetCard = app.buttons["plugin.card.widget_gallery"]
        XCTAssertTrue(reveal(widgetCard, in: app))
        widgetCard.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 10))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 10))
        Thread.sleep(forTimeInterval: 1.5)
        for _ in 0..<6 { app.swipeUp() }
        XCTAssertFalse(app.buttons["plugin.automation.rules"].exists)
        XCTAssertFalse(app.buttons["plugin.automation.copyRules"].exists)
        XCTAssertFalse(app.buttons["plugin.automation.jobs"].exists)
        XCTAssertFalse(app.staticTexts["规则与自动化"].exists)
        app.navigationBars.buttons.element(boundBy: 0).tap()

        let ruleCard = app.buttons["plugin.card.rule_gallery"]
        XCTAssertTrue(reveal(ruleCard, in: app))
        ruleCard.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 10))
        app.buttons["账号配置"].tap()
        let rules = app.buttons["plugin.automation.rules"]
        XCTAssertTrue(reveal(rules, in: app))
        XCTAssertTrue(app.buttons["plugin.automation.copyRules"].exists)
        XCTAssertFalse(app.buttons["plugin.automation.jobs"].exists)
    }

    /// iOS 0.15.0 命名：「更多」里「App 设置」（0.16.0 起图标 apps.iphone）、管理模块「服务器设置」（server.rack）；
    /// 0.16.1：紧急停止说明（ⓘ + 文字）并入红色高亮行、替换原状态文字，高亮区外不再有说明；点 ⓘ 在原生 popover 里看完整三段。
    func testRenamedSettingsAndEmergencyStopInfo() {
        let app = launch()
        app.tabBars.buttons["更多"].tap()
        let settings = app.buttons["more.settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        XCTAssertTrue(settings.label.contains("App 设置"), settings.label)
        let summary = app.staticTexts["more.emergencyStop.summary"]
        XCTAssertTrue(summary.waitForExistence(timeout: 5))
        XCTAssertEqual(summary.label, "停止全部消息处理，数据和管理台不受影响")
        // 原状态文字与高亮区外的说明行都不再显示。
        XCTAssertFalse(app.staticTexts["未开启 · 账号与 Bot 正常运行"].exists)
        XCTAssertFalse(app.staticTexts["more.emergencyStop.footer"].exists)
        XCTAssertEqual(app.staticTexts.matching(NSPredicate(format: "label == %@", "停止全部消息处理，数据和管理台不受影响")).count, 1)
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "停止什么：")).firstMatch.exists)
        // 状态仍由右侧按钮的无障碍值明确给出。
        let toggle = app.buttons["more.emergencyStop"]
        XCTAssertTrue(toggle.exists)
        XCTAssertEqual(toggle.value as? String, "未开启 · 账号与 Bot 正常运行")
        let title = app.staticTexts["more.emergencyStop.title"]
        XCTAssertTrue(title.exists)
        let info = app.buttons["more.emergencyStop.info"]
        XCTAssertTrue(info.exists)
        XCTAssertEqual(info.label, "紧急停止说明")
        // 说明在红色高亮行内：紧贴标题下方、与右侧按钮同一行；ⓘ 在文字左边。
        XCTAssertGreaterThanOrEqual(summary.frame.minY, title.frame.maxY - 2, "说明应在标题下方：\(summary.frame) / \(title.frame)")
        XCTAssertLessThan(summary.frame.minY - title.frame.maxY, 14, "说明应紧贴标题（同一高亮行）：\(summary.frame) / \(title.frame)")
        XCTAssertTrue(toggle.frame.midY > title.frame.minY && toggle.frame.midY < summary.frame.maxY + 8, "右侧按钮应与说明同在高亮行：\(toggle.frame)")
        XCTAssertLessThanOrEqual(info.frame.midX, summary.frame.minX, "ⓘ 应在说明文字左边")
        XCTAssertGreaterThanOrEqual(info.frame.midX, title.frame.minX - 4, "ⓘ 应与标题左对齐（在高亮行内）")
        XCTAssertLessThanOrEqual(summary.frame.maxX, toggle.frame.minX, "说明不应压到右侧按钮")
        info.tap()
        let details = app.descendants(matching: .any)["more.emergencyStop.details"].firstMatch
        XCTAssertTrue(details.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["停止什么"].exists)
        XCTAssertTrue(app.staticTexts["不影响"].exists)
        XCTAssertTrue(app.staticTexts["如何恢复"].exists)
        // popover 而不是 sheet：点 popover 外收起。状态栏区域的点按不会传给 popover 的收起手势，
        // 所以按 popover 实际位置选一处内容区空白（在它上方或下方）。
        let window = app.windows.firstMatch.frame
        let popover = details.frame
        let outsideY: CGFloat = popover.minY > window.height * 0.35 ? 0.2 : 0.9
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: outsideY)).tap()
        XCTAssertTrue(details.waitForNonExistence(timeout: 5))

        settings.tap()
        XCTAssertTrue(app.navigationBars["App 设置"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let server = app.buttons["服务器设置"]
        XCTAssertTrue(reveal(server, in: app))
    }

    /// 0.16.1：开启紧急停止后，高亮行内明确显示“已停止”和恢复方式；确认弹窗文案不变；解除后恢复说明。
    func testEmergencyStopEnabledStateShowsStoppedAndResume() {
        let app = launch()
        app.tabBars.buttons["更多"].tap()
        let toggle = app.buttons["more.emergencyStop"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 10))
        let summary = app.staticTexts["more.emergencyStop.summary"]
        XCTAssertTrue(summary.waitForExistence(timeout: 5))
        XCTAssertEqual(summary.label, "停止全部消息处理，数据和管理台不受影响")
        toggle.tap()
        let stop = app.buttons["立即紧急停止"].firstMatch
        XCTAssertTrue(stop.waitForExistence(timeout: 5))
        stop.tap()
        let stopped = NSPredicate(format: "label == %@", "已停止全部消息处理，点“解除”恢复")
        expectation(for: stopped, evaluatedWith: summary)
        waitForExpectations(timeout: 10)
        XCTAssertEqual(toggle.value as? String, "已开启 · 账号进程与 Bot 已停止")
        XCTAssertTrue(app.buttons["more.emergencyStop.info"].exists, "开启时仍可点 ⓘ 查看说明")

        toggle.tap()
        let resume = app.buttons["解除紧急停止"].firstMatch
        XCTAssertTrue(resume.waitForExistence(timeout: 5))
        resume.tap()
        let normal = NSPredicate(format: "label == %@", "停止全部消息处理，数据和管理台不受影响")
        expectation(for: normal, evaluatedWith: summary)
        waitForExpectations(timeout: 10)
        XCTAssertEqual(toggle.value as? String, "未开启 · 账号与 Bot 正常运行")
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication, maxSwipes: Int = 8) -> Bool {
        var swipes = 0
        while !(element.exists && element.isHittable) && swipes < maxSwipes {
            app.swipeUp()
            swipes += 1
        }
        return element.waitForExistence(timeout: 3)
    }

    /// 插件声明式 UI（iOS 0.14.0）：plugin.overview 原生卡片（宿主「运行统计」、插件卡、失败只影响本卡），
    /// 以及契约里 iOS 新补的控件与「此控件需要更新 App」只读提示。
    func testPluginDeclarativeWidgetsAndCards() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let pluginCard = app.buttons["plugin.card.widget_gallery"]
        XCTAssertTrue(reveal(pluginCard, in: app))
        pluginCard.tap()

        // 卡片 DSL：运行统计（stats + table）在插件详情顶部
        XCTAssertTrue(app.staticTexts["运行统计"].waitForExistence(timeout: 10))
        let eventRow = app.descendants(matching: .any)["plugin.uicard.row.on_event"].firstMatch
        XCTAssertTrue(eventRow.waitForExistence(timeout: 10))
        XCTAssertTrue(eventRow.label.contains("事件处理"), eventRow.label)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "122")).firstMatch.exists)
        // 插件自己的卡（action 数据源）正常显示；另一张读取超时的卡只在本卡显示「已停止工作 · 重试」
        let failed = app.descendants(matching: .any)["plugin.uicard.failed"].firstMatch
        XCTAssertTrue(reveal(failed, in: app))
        XCTAssertTrue(failed.label.contains("已停止工作"), failed.label)
        XCTAssertTrue(app.buttons["plugin.uicard.retry.broken"].exists)
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "42")).firstMatch.exists)

        // 配置控件
        app.swipeDown(); app.swipeDown()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        let configList = app.buttons["plugin.config.configList.回复规则"]
        XCTAssertTrue(configList.waitForExistence(timeout: 10))
        XCTAssertTrue(configList.label.contains("1 项"), configList.label)
        XCTAssertTrue(reveal(app.buttons["plugin.config.allowedPeers.生效会话"], in: app))
        XCTAssertTrue(reveal(app.buttons["plugin.config.multiSelect.生效星期"], in: app))
        XCTAssertTrue(reveal(app.buttons["plugin.config.secretList.Token 池"], in: app))
        let unknown = app.descendants(matching: .any)["plugin.config.unknownWidget"].firstMatch
        XCTAssertTrue(reveal(unknown, in: app))
        XCTAssertTrue(unknown.label.contains("此控件需要更新 App"), unknown.label)
        // 未知控件不能编辑：没有对应的文本框
        XCTAssertFalse(app.textFields["主题色"].exists)

        // 密钥列表只显示遮罩
        app.buttons["plugin.config.secretList.Token 池"].tap()
        let saved = app.descendants(matching: .any)["plugin.config.secretList.item.0"].firstMatch
        XCTAssertTrue(saved.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "abcd···wxyz")).firstMatch.exists)
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 对象列表：每项一行摘要，点开编辑
        app.swipeDown(); app.swipeDown(); app.swipeDown()
        XCTAssertTrue(configList.waitForExistence(timeout: 5))
        configList.tap()
        let rule = app.buttons["plugin.config.configList.item.0"]
        XCTAssertTrue(rule.waitForExistence(timeout: 5))
        XCTAssertTrue(rule.label.contains("问候"), rule.label)
        XCTAssertTrue(rule.label.contains("你好 → 你好呀"), rule.label)
    }

    /// 通用规则页（插件 UI 契约 2，iOS 0.14.1）：规则列表按 x-rule-summary 显示摘要；试运行按 x-dry-run 的样例字段；
    /// 规则表单里「指定群 ID」按 x-ui-visible-if 只在作用范围为「指定群」时出现，模式每行一项。
    func testPluginRulesFormVisibleIfAndDryRun() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let pluginCard = app.buttons["plugin.card.rule_gallery"]
        XCTAssertTrue(reveal(pluginCard, in: app))
        pluginCard.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 10))
        app.buttons["账号配置"].tap()
        // 规则模式且声明了 x-rule-schema：顶层没有账号配置字段
        let rulesLink = app.buttons["自动化规则"]
        XCTAssertTrue(reveal(rulesLink, in: app))
        rulesLink.tap()

        let row = app.descendants(matching: .any).matching(identifier: "plugin.rule.row").firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        XCTAssertTrue(row.label.contains("指定群问候"), row.label)
        XCTAssertTrue(row.label.contains("关键词"), row.label)
        XCTAssertTrue(row.label.contains("你好、hello"), row.label)
        XCTAssertTrue(row.label.contains("指定群"), row.label)

        // 新建：默认作用范围「仅私聊」，不显示「指定群 ID」；模式是每行一项的文本框
        app.buttons["添加"].tap()
        let createConfig = app.buttons["management.object.config"]
        XCTAssertTrue(reveal(createConfig, in: app))
        createConfig.tap()
        XCTAssertTrue(app.descendants(matching: .any)["management.lineList.patterns"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any)["management.lineList.group_ids"].firstMatch.exists)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 已有规则（作用范围「指定群」）：编辑时显示「指定群 ID」
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()
        let edit = app.buttons["编辑"]
        XCTAssertTrue(reveal(edit, in: app))
        edit.tap()
        let editConfig = app.buttons["management.object.config"]
        XCTAssertTrue(reveal(editConfig, in: app))
        editConfig.tap()
        XCTAssertTrue(app.descendants(matching: .any)["management.lineList.group_ids"].firstMatch.waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 试运行：填样例消息后运行，显示命中与将输出的内容
        let dryRun = app.buttons["plugin.rule.dryRun"]
        XCTAssertTrue(reveal(dryRun, in: app))
        dryRun.tap()
        let message = app.descendants(matching: .any)["plugin.rule.dryRun.message"].firstMatch
        XCTAssertTrue(message.waitForExistence(timeout: 5))
        message.tap()
        message.typeText("hello there")
        XCTAssertTrue(app.descendants(matching: .any)["plugin.rule.dryRun.chatType"].firstMatch.exists)
        app.buttons["plugin.rule.dryRun.submit"].tap()
        let matched = app.descendants(matching: .any)["plugin.rule.dryRun.matched"].firstMatch
        XCTAssertTrue(matched.waitForExistence(timeout: 10))
        XCTAssertTrue(matched.label.contains("命中：是"), matched.label)
        let output = app.descendants(matching: .any)["plugin.rule.dryRun.output"].firstMatch
        XCTAssertTrue(reveal(output, in: app))
        XCTAssertTrue(output.label.contains("你好 张三"), output.label)
    }

    func testPluginAccountStateIsSeparateFromGlobalMaintenance() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugin.card.auto_reply"].tap()
        XCTAssertTrue(app.buttons["plugin.account.toggle"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["账号运行详情"].exists)
        app.buttons["plugin.account.toggle"].tap()
        let confirm = app.buttons["确认停用"]
        if !confirm.waitForExistence(timeout: 5) {
            app.buttons["plugin.account.toggle"].tap()
            XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        }
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(app.buttons["在此账号启用该插件"].firstMatch.waitForExistence(timeout: 10))
        let serverMaintenance = app.buttons["plugin.detail.serverMaintenance"]
        if !serverMaintenance.waitForExistence(timeout: 3) { app.swipeUp() }
        XCTAssertTrue(serverMaintenance.waitForExistence(timeout: 5))
        serverMaintenance.tap()
        XCTAssertTrue(app.buttons["plugin.global.toggle"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["卸载插件"].exists == false)
        XCTAssertTrue(app.buttons["重新加载到所选账号"].exists)
    }

    func testPluginConfigSubpageReturnsToConfigurationTab() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        app.buttons["plugin.card.auto_reply"].tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        XCTAssertTrue(app.buttons["plugin.config.toolbarReview"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        // 返回后仍停留在“账号配置”，而不是跳回“概况”。
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
    }

    func testPluginConfigUsesNativeProviderAndModelPickers() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let card = app.buttons["plugin.card.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        // x-ui-widget=llm-provider-select / llm-model-select 渲染为原生选择器，而不是手输文本框。
        let provider = app.descendants(matching: .any)["plugin.config.select.固定 TelePilot Provider（可选）"].firstMatch
        XCTAssertTrue(provider.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["plugin.config.select.TelePilot 模型覆盖（可选）"].firstMatch.exists)
        XCTAssertFalse(app.textFields["固定 TelePilot Provider（可选）"].exists)
        // 统一模型列表：Provider 选择器保留，但标注随模型自动填写。
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "随模型自动填写")).firstMatch.waitForExistence(timeout: 5))
    }

    func testPluginConfigFollowsSectionsAndMarksOnlyRequiredFields() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let card = app.buttons["plugin.card.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()
        // 基础分组（“AI 模型”）留在主页，组标题位于组内第一项上方。
        let aiGroup = app.descendants(matching: .any)["plugin.config.group.AI 模型"].firstMatch
        XCTAssertTrue(aiGroup.waitForExistence(timeout: 10))
        let provider = app.descendants(matching: .any)["plugin.config.select.固定 TelePilot Provider（可选）"].firstMatch
        XCTAssertTrue(provider.waitForExistence(timeout: 5))
        XCTAssertLessThan(aiGroup.frame.minY, provider.frame.minY)
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.telepilot_provider"].exists)
        // 其他分组各自成为一行子页面入口，行上显示摘要；必填未填的分组有提示。
        // 表单是懒加载列表，屏幕外的行不在辅助功能树里：滚到“更多设置”最后一行（消息模板）可点为止，
        // 保证三个子页面入口都已渲染，而不是第一个入口刚露头就停下。
        let triggerPage = app.buttons["plugin.config.page.触发方式"]
        let templatePage = app.buttons["plugin.config.page.消息模板"]
        var swipes = 0
        while !(templatePage.exists && templatePage.isHittable) && swipes < 8 {
            app.swipeUp()
            swipes += 1
        }
        XCTAssertTrue(templatePage.waitForExistence(timeout: 5))
        XCTAssertTrue(triggerPage.exists)
        XCTAssertTrue(triggerPage.isHittable)
        XCTAssertTrue(triggerPage.label.contains("有必填项未填"))
        let ptPage = app.buttons["plugin.config.page.PT 站点数据源"]
        XCTAssertTrue(ptPage.exists)
        XCTAssertTrue(ptPage.label.contains("未启用"))
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.bot_username"].exists)

        triggerPage.tap()
        XCTAssertTrue(app.navigationBars["触发方式"].waitForExistence(timeout: 5))
        // 写着“可选”、有默认值的字段不标“必填”；只有没有默认值的“目标群组 ID”是必填。
        let required = app.descendants(matching: .any)["plugin.config.required.target_chat_id"].firstMatch
        XCTAssertTrue(required.waitForExistence(timeout: 5))
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.bot_username"].exists)
        XCTAssertFalse(app.descendants(matching: .any)["plugin.config.required.enable_guest"].exists)
        // 可选字段只有一个控件：不再出现“是否提交此项”的同名开关。
        let guest = app.switches.matching(NSPredicate(format: "label == %@", "访客模式触发"))
        XCTAssertEqual(guest.count, 1)
        // 子页面里修改同一份草稿，回到主页统一检查并保存。
        guest.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.92, dy: 0.5)).tap()
        app.navigationBars["触发方式"].buttons.element(boundBy: 0).tap()
        let review = app.buttons["plugin.config.toolbarReview"]
        XCTAssertTrue(review.waitForExistence(timeout: 5))
        XCTAssertTrue(review.isEnabled)
        // 有未保存的修改时离开要确认。
        let back = app.buttons["plugin.config.back"]
        XCTAssertTrue(back.waitForExistence(timeout: 5))
        back.tap()
        let discard = app.buttons["放弃修改"]
        XCTAssertTrue(discard.waitForExistence(timeout: 5))
        discard.tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
    }
}

/// 截图像素比对：两张截图里各取一个矩形（单位为点、尺寸相同），返回 RGB 平均绝对差（0–255）。
enum PixelDiff {
    static func mean(_ lhs: XCUIScreenshot, _ lhsRect: CGRect, _ rhs: XCUIScreenshot, _ rhsRect: CGRect) -> Double {
        guard let a = Bitmap(lhs.image), let b = Bitmap(rhs.image) else { return .infinity }
        let scale = a.scale
        let width = Int((lhsRect.width * scale).rounded())
        let height = Int((lhsRect.height * scale).rounded())
        guard width > 0, height > 0 else { return .infinity }
        let ax = Int((lhsRect.minX * scale).rounded()), ay = Int((lhsRect.minY * scale).rounded())
        let bx = Int((rhsRect.minX * scale).rounded()), by = Int((rhsRect.minY * scale).rounded())
        var total = 0.0
        var count = 0
        for row in 0..<height {
            for column in 0..<width {
                guard let p = a.pixel(ax + column, ay + row), let q = b.pixel(bx + column, by + row) else { continue }
                total += Double(abs(Int(p.0) - Int(q.0)) + abs(Int(p.1) - Int(q.1)) + abs(Int(p.2) - Int(q.2))) / 3
                count += 1
            }
        }
        return count == 0 ? .infinity : total / Double(count)
    }

    private struct Bitmap {
        let bytes: [UInt8]
        let width: Int
        let height: Int
        let scale: CGFloat

        init?(_ image: UIImage) {
            guard let cg = image.cgImage else { return nil }
            width = cg.width
            height = cg.height
            scale = image.size.width > 0 ? CGFloat(cg.width) / image.size.width : image.scale
            var buffer = [UInt8](repeating: 0, count: width * height * 4)
            let drawn = buffer.withUnsafeMutableBytes { raw -> Bool in
                guard let context = CGContext(data: raw.baseAddress, width: cg.width, height: cg.height, bitsPerComponent: 8,
                                              bytesPerRow: cg.width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
                context.draw(cg, in: CGRect(x: 0, y: 0, width: cg.width, height: cg.height))
                return true
            }
            guard drawn else { return nil }
            bytes = buffer
        }

        func pixel(_ x: Int, _ y: Int) -> (UInt8, UInt8, UInt8)? {
            guard x >= 0, y >= 0, x < width, y < height else { return nil }
            let index = (y * width + x) * 4
            return (bytes[index], bytes[index + 1], bytes[index + 2])
        }
    }
}
