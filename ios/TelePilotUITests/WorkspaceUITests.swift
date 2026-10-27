import XCTest
import UIKit

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
        // 工作流「预热模拟器」也跑这个用例：通过 TEST_RUNNER_TELEPILOT_WARMUP_TIMEOUT 放宽首次冷启动的等待，
        // 一次等到 App 起来，而不是 45 秒超时后反复重跑（每次重跑另有 30–50 秒 xcodebuild 开销）。
        let timeout = ProcessInfo.processInfo.environment["TELEPILOT_WARMUP_TIMEOUT"].flatMap(TimeInterval.init) ?? 45
        XCTAssertTrue(app.tabBars.buttons["概览"].waitForExistence(timeout: timeout))
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
