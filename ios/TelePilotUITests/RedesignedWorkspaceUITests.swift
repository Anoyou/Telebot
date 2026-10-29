import XCTest

@MainActor
final class RedesignedWorkspaceUITests: XCTestCase {
    private func launch(tab: String) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        // 0.18.0 起 AI 默认在「更多」里；这组用例把 AI 放回 Dock，直接从底部进入。
        app.launchArguments = ["--ui-testing", "--ui-test-dock=overview,plugins,ai", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons[tab].waitForExistence(timeout: 45))
        app.tabBars.buttons[tab].tap()
        return app
    }

    func testAIProviderAndModelWorkflow() {
        let app = launch(tab: "AI")
        let providers = app.buttons["供应商"].firstMatch
        XCTAssertTrue(providers.waitForExistence(timeout: 10))
        providers.tap()
        let provider = app.buttons["ai.provider.1"]
        XCTAssertTrue(provider.waitForExistence(timeout: 10))
        provider.tap()
        XCTAssertTrue(app.buttons["ai.provider.keys"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["ai.provider.models"].exists)
        app.buttons["ai.provider.models"].tap()
        XCTAssertTrue(app.buttons["ai.models.save"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["ai.models.save"].isEnabled)
        XCTAssertTrue(app.staticTexts["gpt-5.4-mini"].exists)
    }

    func testAIModelCapabilitiesSyncAndNativeQuickTest() {
        let app = launch(tab: "AI")
        let providers = app.buttons["供应商"].firstMatch
        XCTAssertTrue(providers.waitForExistence(timeout: 10))
        providers.tap()
        let provider = app.buttons["ai.provider.1"]
        XCTAssertTrue(provider.waitForExistence(timeout: 10))
        provider.tap()
        let models = app.buttons["ai.provider.models"]
        XCTAssertTrue(models.waitForExistence(timeout: 5))
        models.tap()
        let sync = app.buttons["ai.models.sync"]
        XCTAssertTrue(sync.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "多模态（图片）")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "能力未知")).firstMatch.exists)
        sync.tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "新增 1 个")).firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["gpt-5.4-nano"].firstMatch.exists)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let quick = app.buttons["ai.provider.quicktest"]
        for _ in 0..<5 where !quick.isHittable { app.swipeUp() }
        XCTAssertTrue(quick.waitForExistence(timeout: 5))
        quick.tap()
        XCTAssertTrue(app.navigationBars["单个模型快速测试"].waitForExistence(timeout: 5))
        let run = app.buttons["ai.quicktest.run"]
        XCTAssertTrue(run.waitForExistence(timeout: 5))
        run.tap()
        XCTAssertTrue(app.staticTexts["模型回复"].waitForExistence(timeout: 5))
    }

    func testAILivenessConfirmsBeforeStartingAndOffersRetry() {
        let app = launch(tab: "AI")
        let liveness = app.buttons["ai.liveness.patrol"].firstMatch
        XCTAssertTrue(liveness.waitForExistence(timeout: 10))
        liveness.tap()
        XCTAssertTrue(app.navigationBars["全部供应商巡检"].waitForExistence(timeout: 10))
        let start = app.buttons["ai.liveness.start"]
        for _ in 0..<5 where !start.isHittable { app.swipeUp() }
        XCTAssertTrue(start.waitForExistence(timeout: 10))
        XCTAssertTrue(start.isEnabled)
        start.tap()
        let confirm = app.buttons["确认并开始"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(app.staticTexts["执行进度"].waitForExistence(timeout: 10))
        let retry = app.buttons["ai.liveness.retryFailed"]
        for _ in 0..<5 where !retry.exists { app.swipeUp() }
        XCTAssertTrue(retry.waitForExistence(timeout: 10))
        for _ in 0..<5 where !retry.isHittable { app.swipeUp() }
        retry.tap()
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "1 个模型各发送一次真实请求")).firstMatch.exists)
        let cancel = app.buttons["取消"].firstMatch
        if cancel.exists && cancel.isHittable { cancel.tap() }
        else { app.otherElements["PopoverDismissRegion"].firstMatch.tap() }
    }

    func testAIGlobalSettingsIsNativeForm() {
        let app = launch(tab: "AI")
        let entry = app.buttons["ai.settings.global"]
        for _ in 0..<6 where !entry.exists { app.swipeUp() }
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        let save = app.buttons["ai.settings.save"]
        for _ in 0..<6 where !save.exists { app.swipeUp() }
        XCTAssertTrue(save.waitForExistence(timeout: 10))
        XCTAssertFalse(save.isEnabled)
    }

    func testAICommandEnablementShowsAliasesAndAccounts() {
        let app = launch(tab: "AI")
        let entry = app.buttons["ai.settings.commands"]
        for _ in 0..<6 where !entry.exists { app.swipeUp() }
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        let command = app.buttons["ai.command.11"]
        XCTAssertTrue(command.waitForExistence(timeout: 10))
        command.tap()
        XCTAssertTrue(app.staticTexts["指令别名"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts[".a"].exists)
    }
}
