import XCTest

@MainActor
final class WorkspaceUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        return app
    }

    func testDockKeepsPrimarySectionsAndAgentEntry() {
        let app = launch()
        XCTAssertTrue(app.tabBars.buttons["概览"].waitForExistence(timeout: 45))
        XCTAssertTrue(app.tabBars.buttons["插件"].exists)
        XCTAssertTrue(app.tabBars.buttons["AI"].exists)
        XCTAssertTrue(app.tabBars.buttons["更多"].exists)
        XCTAssertTrue(app.tabBars.buttons["Agent"].exists)
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

    func testPluginScreenUsesTwoColumnCards() {
        let app = launch()
        let plugins = app.tabBars.buttons["插件"]
        XCTAssertTrue(plugins.waitForExistence(timeout: 45))
        plugins.tap()
        XCTAssertTrue(app.buttons["plugin.card.auto_reply"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["plugins.category"].exists)
        XCTAssertTrue(app.buttons["plugins.accountPicker"].exists)
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
    private func reveal(_ element: XCUIElement, in app: XCUIApplication, maxSwipes: Int = 8) -> Bool {
        var swipes = 0
        while !(element.exists && element.isHittable) && swipes < maxSwipes {
            app.swipeUp()
            swipes += 1
        }
        return element.waitForExistence(timeout: 3)
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
