import XCTest

@MainActor
final class ManagementUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["更多"].waitForExistence(timeout: 45))
        app.tabBars.buttons["更多"].tap()
        return app
    }

    /// 服务器更新面板以 medium 高度的 sheet 呈现，iOS 26 起内联导航栏是悬浮在列表之上的玻璃层，
    /// 列表行可以滚到导航栏下面：此时元素仍 exists/isHittable，但点击中心会落在导航栏上而丢失
    /// （iOS 27 上“应用更新”曾停在 y≈420–490、导航栏 430–482 处，点击后确认弹窗没有出现）。
    /// 这里按帧判断：只有目标整行落在导航栏下沿与列表下沿之间才算到位；否则按目标与可见区中线的
    /// 距离做“按住—拖动—停留”的受控拖动（无惯性），目标被导航栏遮住时向下拖、在下方或尚未加载时向上拖。
    private func scrollUpdateSheet(to element: XCUIElement, in app: XCUIApplication, attempts: Int = 14) {
        let sheet = app.otherElements["update.sheet"]
        let list = sheet.collectionViews.firstMatch
        XCTAssertTrue(list.waitForExistence(timeout: 10))
        let bar = sheet.navigationBars.firstMatch
        for _ in 0..<attempts {
            let listFrame = list.frame
            let top = (bar.exists ? max(listFrame.minY, bar.frame.maxY) : listFrame.minY) + 4
            let bottom = listFrame.maxY - 4
            let span = bottom - top
            guard span > 40 else { return }
            var dy = -span * 0.5
            if element.exists {
                let frame = element.frame
                if frame.minY >= top && frame.maxY <= bottom {
                    if element.isHittable { return }
                    Thread.sleep(forTimeInterval: 0.3)
                    continue
                }
                dy = (top + span / 2) - frame.midY
                dy = max(-span * 0.6, min(span * 0.6, dy))
                if abs(dy) < 24 { dy = dy < 0 ? -24 : 24 }
            }
            let origin = list.coordinate(withNormalizedOffset: CGVector(dx: 0, dy: 0))
            let startY = dy < 0 ? bottom - span * 0.15 : top + span * 0.15
            let start = origin.withOffset(CGVector(dx: listFrame.width / 2, dy: startY - listFrame.minY))
            let end = start.withOffset(CGVector(dx: 0, dy: dy))
            start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.4)
        }
    }

    /// 断言目标整行位于 sheet 可见区（导航栏下沿以下），点击不会被悬浮导航栏截走。
    private func assertVisibleInUpdateSheet(_ element: XCUIElement, in app: XCUIApplication,
                                            file: StaticString = #filePath, line: UInt = #line) {
        let sheet = app.otherElements["update.sheet"]
        let bar = sheet.navigationBars.firstMatch
        XCTAssertTrue(element.exists, "\(element) 不存在", file: file, line: line)
        XCTAssertTrue(element.isHittable, "\(element) 不可点", file: file, line: line)
        if bar.exists {
            XCTAssertGreaterThanOrEqual(element.frame.minY, bar.frame.maxY,
                                        "\(element) 仍被导航栏遮挡", file: file, line: line)
        }
    }

    func testTelegramBindingRequiresCredentialsBeforeSendingCode() {
        let app = launch()
        let accounts = app.buttons["more.accounts"]
        XCTAssertTrue(accounts.waitForExistence(timeout: 10))
        accounts.tap()
        app.buttons["account.add"].tap()
        XCTAssertTrue(app.secureTextFields["API Hash"].waitForExistence(timeout: 10))
        let send = app.buttons["发送验证码"]
        for _ in 0..<4 where !send.isHittable { app.swipeUp() }
        XCTAssertTrue(send.exists)
        XCTAssertFalse(send.isEnabled)
        XCTAssertFalse(app.textFields["验证码"].exists)
    }

    func testLedgerResetRequiresExactPhraseBeforeConfirmation() {
        let app = launch()
        app.buttons["资金台账"].tap()
        let reset = app.buttons["重置资金台账"]
        XCTAssertTrue(app.descendants(matching: .any)["ledger.screen"].firstMatch.waitForExistence(timeout: 10))
        for _ in 0..<12 where !(reset.exists && reset.isHittable) { app.swipeUp() }
        reset.tap()
        let field = app.textFields["确认口令"]
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        app.buttons["management.submit"].tap()
        XCTAssertTrue(app.staticTexts["请输入确认口令：RESET_LEDGER。"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["确认重置资金台账"].exists)
    }

    func testRestoreCannotCommitWithoutPreviewingAFile() {
        let app = launch()
        app.buttons["备份与维护"].tap()
        app.buttons["配置导入与导出"].tap()
        let choose = app.buttons["选择文件"]
        for _ in 0..<8 where !choose.isHittable { app.swipeUp() }
        XCTAssertTrue(choose.exists)
        XCTAssertFalse(app.buttons["management.file.confirm"].exists)
        XCTAssertFalse(app.buttons["预览导入影响"].isEnabled)
    }

    /// “关于 → App 更新日志”读取随 App 打包的 ios/CHANGELOG.md，fixture 模式下不连服务器也能显示，
    /// 并标出当前安装的版本。
    func testAboutShowsBundledAppChangelog() {
        let app = launch()
        let about = app.buttons["more.about"]
        // “更多”顶部的全局紧急停止说明较长，“关于与更新”可能在首屏之外；List 懒加载，先滚动再等待。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(about.exists && about.isHittable) { app.swipeUp() }
        XCTAssertTrue(about.waitForExistence(timeout: 5))
        about.tap()
        let entry = app.buttons["about.appChangelog"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        for _ in 0..<4 where !entry.isHittable { app.swipeUp() }
        entry.tap()
        XCTAssertTrue(app.navigationBars["App 更新日志"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.descendants(matching: .any)["changelog.unavailable"].exists)
        XCTAssertTrue(app.descendants(matching: .any)["changelog.list"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["changelog.current"].firstMatch.waitForExistence(timeout: 5))
        let oldest = app.staticTexts["首个原生客户端与 APNs"]
        for _ in 0..<12 where !oldest.exists { app.swipeUp() }
        XCTAssertTrue(oldest.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "App 更新日志"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testServerUpdateRequiresConfirmationBeforeApplying() {
        let app = launch()
        let about = app.buttons["more.about"]
        // “更多”顶部的全局紧急停止说明较长，“关于与更新”可能在首屏之外；List 懒加载，先滚动再等待。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(about.exists && about.isHittable) { app.swipeUp() }
        XCTAssertTrue(about.waitForExistence(timeout: 5))
        about.tap()
        XCTAssertTrue(app.staticTexts["App 版本"].waitForExistence(timeout: 10))
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        open.tap()
        XCTAssertTrue(app.staticTexts["发现新版本"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["0.105.0-beta.1"].exists)
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
        assertVisibleInUpdateSheet(apply, in: app)
        apply.tap()
        XCTAssertTrue(app.buttons["update.confirm"].firstMatch.waitForExistence(timeout: 10))
        let cancel = app.buttons["取消"].firstMatch
        if cancel.exists && cancel.isHittable { cancel.tap() }
        else { app.otherElements["PopoverDismissRegion"].firstMatch.tap() }
        XCTAssertFalse(app.otherElements["update.applied"].exists)
        XCTAssertFalse(app.staticTexts["更新任务已启动"].exists)
    }

    func testRuntimeLogCredentialsAreMaskedByDefault() {
        let app = launch()
        app.buttons["日志与诊断"].tap()
        let runtime = app.buttons["logs.kind.runtime"]
        for _ in 0..<8 where !(runtime.exists && runtime.isHittable) { app.swipeUp() }
        XCTAssertTrue(runtime.waitForExistence(timeout: 10))
        runtime.tap()
        let message = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "界面日志测试")).firstMatch
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "sk-proj-THIS_IS_A_FAKE_SECRET_FOR_UI_TEST_123456")).firstMatch.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "原生日志默认脱敏"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testServerUpdateReceiptSurvivesClosingPanel() {
        let app = launch()
        let about = app.buttons["more.about"]
        // “更多”顶部的全局紧急停止说明较长，“关于与更新”可能在首屏之外；List 懒加载，先滚动再等待。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<6 where !(about.exists && about.isHittable) { app.swipeUp() }
        XCTAssertTrue(about.waitForExistence(timeout: 5))
        about.tap()
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        XCTAssertTrue(open.waitForExistence(timeout: 10))
        open.tap()
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
        XCTAssertTrue(apply.waitForExistence(timeout: 10))
        assertVisibleInUpdateSheet(apply, in: app)
        apply.tap()
        let confirm = app.buttons["update.confirm"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        confirm.tap()
        // 提交后面板内直接显示更新进度（只读轮询），不再跳转到原始 JSON 任务页。
        let progress = app.descendants(matching: .any)["update.progress"]
        XCTAssertTrue(progress.waitForExistence(timeout: 10))
        app.buttons["update.done"].tap()
        XCTAssertTrue(open.waitForExistence(timeout: 5))
        open.tap()
        // 重新打开时从本地收据恢复同一个任务继续跟踪，期间不显示“应用更新”，不能重复提交。
        XCTAssertTrue(app.descendants(matching: .any)["update.progress"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["update.apply"].exists)
        let applied = app.descendants(matching: .any)["update.applied"]
        XCTAssertTrue(applied.waitForExistence(timeout: 30))
        XCTAssertFalse(app.buttons["update.apply"].exists)
    }

    func testLogDetailExplainsMessageFailureAndOpensTimeline() {
        let app = launch()
        app.buttons["日志与诊断"].tap()
        let messages = app.buttons["logs.kind.messages"]
        for _ in 0..<8 where !(messages.exists && messages.isHittable) { app.swipeUp() }
        XCTAssertTrue(messages.waitForExistence(timeout: 10))
        messages.tap()
        let failure = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "处理失败")).firstMatch
        XCTAssertTrue(failure.waitForExistence(timeout: 10))
        failure.tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "建议下一步")).firstMatch.waitForExistence(timeout: 10))
        let timeline = app.buttons["logs.openTrace"]
        for _ in 0..<8 where !(timeline.exists && timeline.isHittable) { app.swipeUp() }
        timeline.tap()
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "插件开始执行")).firstMatch.waitForExistence(timeout: 10))
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "日志处理时间线"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testLedgerShowsSummaryAndCharts() {
        let app = launch()
        app.buttons["资金台账"].tap()
        let net = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "净额")).firstMatch
        XCTAssertTrue(net.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "待补付")).firstMatch.exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "资金台账汇总与图表"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
