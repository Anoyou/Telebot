import XCTest

@MainActor
final class ManagementUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["更多"].waitForExistence(timeout: 15))
        app.tabBars.buttons["更多"].tap()
        return app
    }

    /// 服务器更新面板以 medium 高度的 sheet 呈现。对整个 App 快速上滑会带惯性滚过目标行，
    /// 甚至在惯性未停时点击而丢失点击；这里只在 sheet 内的列表上慢速逐段上滑，直到目标可点。
    private func scrollUpdateSheet(to element: XCUIElement, in app: XCUIApplication, attempts: Int = 10) {
        let list = app.otherElements["update.sheet"].collectionViews.firstMatch
        XCTAssertTrue(list.waitForExistence(timeout: 10))
        for _ in 0..<attempts where !(element.exists && element.isHittable) {
            list.swipeUp(velocity: .slow)
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

    func testServerUpdateRequiresConfirmationBeforeApplying() {
        let app = launch()
        let about = app.buttons["more.about"]
        XCTAssertTrue(about.waitForExistence(timeout: 10))
        for _ in 0..<4 where !about.isHittable { app.swipeUp() }
        about.tap()
        XCTAssertTrue(app.staticTexts["App 版本"].waitForExistence(timeout: 10))
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        open.tap()
        XCTAssertTrue(app.staticTexts["发现新版本"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["0.105.0-beta.1"].exists)
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
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
        XCTAssertTrue(about.waitForExistence(timeout: 10))
        for _ in 0..<4 where !about.isHittable { app.swipeUp() }
        about.tap()
        let open = app.buttons["update.open"]
        for _ in 0..<4 where !open.isHittable { app.swipeUp() }
        XCTAssertTrue(open.waitForExistence(timeout: 10))
        open.tap()
        let apply = app.buttons["update.apply"]
        scrollUpdateSheet(to: apply, in: app)
        XCTAssertTrue(apply.waitForExistence(timeout: 10))
        apply.tap()
        let confirm = app.buttons["update.confirm"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 10))
        confirm.tap()
        let job = app.buttons["update.job"]
        scrollUpdateSheet(to: job, in: app)
        XCTAssertTrue(job.waitForExistence(timeout: 10))
        app.buttons["update.done"].tap()
        XCTAssertTrue(open.waitForExistence(timeout: 5))
        open.tap()
        // 面板新增“更新通道”分区后内容更长：滚到“更新结果”时，上方操作分区的行已被列表回收，
        // 因此先在操作分区确认“应用更新”已隐藏、“允许下次更新”可用，再继续滚到升级任务。
        let allowNext = app.buttons["update.allowNext"]
        scrollUpdateSheet(to: allowNext, in: app)
        XCTAssertTrue(allowNext.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["update.apply"].exists)
        scrollUpdateSheet(to: job, in: app)
        XCTAssertTrue(job.waitForExistence(timeout: 10))
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
