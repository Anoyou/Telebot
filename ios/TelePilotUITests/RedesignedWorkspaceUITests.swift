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

    func testAILivenessRequiresPreviewBeforeStarting() {
        let app = launch(tab: "AI")
        let liveness = app.buttons["模型批量测活"].firstMatch
        XCTAssertTrue(liveness.waitForExistence(timeout: 10))
        liveness.tap()
        XCTAssertTrue(app.navigationBars["模型测活"].waitForExistence(timeout: 10))
        let preview = app.buttons["ai.liveness.preview"]
        for _ in 0..<3 where !preview.exists { app.swipeUp() }
        XCTAssertTrue(preview.waitForExistence(timeout: 10))
        XCTAssertEqual(preview.label, "预览测试范围与消耗")
        XCTAssertFalse(app.buttons["ai.liveness.start"].exists)
        preview.tap()
        XCTAssertTrue(preview.waitForExistence(timeout: 5))
        let start = app.buttons["ai.liveness.start"]
        for _ in 0..<5 where !start.isHittable { app.swipeUp() }
        XCTAssertTrue(start.waitForExistence(timeout: 5))
        start.tap()
        let startAction = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "开始测活")).firstMatch
        XCTAssertTrue(startAction.waitForExistence(timeout: 5))
        XCTAssertTrue(startAction.isEnabled)
        startAction.tap()
        XCTAssertTrue(app.staticTexts["执行进度"].waitForExistence(timeout: 10))
    }
}
