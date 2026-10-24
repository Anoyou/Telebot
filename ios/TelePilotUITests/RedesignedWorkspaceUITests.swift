import XCTest

@MainActor
final class RedesignedWorkspaceUITests: XCTestCase {
    private func launch(tab: String) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons[tab].waitForExistence(timeout: 15))
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

    func testAILivenessConfirmsBeforeStartingAndOffersRetry() {
        let app = launch(tab: "AI")
        let liveness = app.buttons["模型批量测活"].firstMatch
        XCTAssertTrue(liveness.waitForExistence(timeout: 10))
        liveness.tap()
        XCTAssertTrue(app.navigationBars["模型测活"].waitForExistence(timeout: 10))
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
