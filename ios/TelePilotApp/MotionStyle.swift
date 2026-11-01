import SwiftUI
import UIKit

/// 第 24 轮：全 App 统一的动效规则（见 `ios/DESIGN.md`「动效」）。
///
/// - 克制、系统风格：状态切换用 `.snappy`，展开折叠用 `.smooth`，淡入淡出 0.2 秒；不做弹跳、旋转入场或循环装饰动画。
/// - 系统「减弱动态效果」打开时：去掉位移与弹簧，只保留淡入淡出（`reducedFade`）；数字滚动换成淡变。
/// - 界面测试（`--ui-testing`）中一律不加动画：XCTest 要等界面空闲才查找元素，动画只会让测试变慢变脆。
enum Motion {
    nonisolated static let disabledForTests = ProcessInfo.processInfo.arguments.contains("--ui-testing")

    /// 状态切换（开关、选中、复制成功、错误提示出现）。
    static var snappy: Animation { .snappy(duration: 0.25) }
    /// 展开 / 折叠（思考过程、预览、详情）。
    static var smooth: Animation { .smooth(duration: 0.3) }
    /// 减弱动态效果时代替位移动画的淡变。
    static var reducedFade: Animation { .easeInOut(duration: 0.2) }

    /// 实际使用的动画：界面测试为 nil（无动画）；减弱动态效果时换成淡变（`fadeOnly` 为假时连淡变也不要，
    /// 用于只有位移、没有透明度变化的改动，如列表重排）。
    static func animation(_ base: Animation, reduceMotion: Bool, fadeOnly: Bool = true) -> Animation? {
        if disabledForTests { return nil }
        if reduceMotion { return fadeOnly ? reducedFade : nil }
        return base
    }

    /// 出现 / 消失：从 `edge` 方向轻移 + 淡入；减弱动态效果时只淡入。
    static func transition(edge: Edge = .top, reduceMotion: Bool) -> AnyTransition {
        if disabledForTests { return .identity }
        if reduceMotion { return .opacity }
        return .move(edge: edge).combined(with: .opacity)
    }

    /// 包一层 `withAnimation`，直接读系统「减弱动态效果」（不需要在视图里声明环境值）。
    @MainActor static func perform(_ base: Animation = Motion.snappy, fadeOnly: Bool = true, _ body: () -> Void) {
        perform(base, reduceMotion: UIAccessibility.isReduceMotionEnabled, fadeOnly: fadeOnly, body)
    }

    /// 包一层 `withAnimation`：视图里已有 `accessibilityReduceMotion` 环境值时传进来。
    @MainActor static func perform(_ base: Animation = Motion.snappy, reduceMotion: Bool, fadeOnly: Bool = true, _ body: () -> Void) {
        if let animation = animation(base, reduceMotion: reduceMotion, fadeOnly: fadeOnly) {
            withAnimation(animation, body)
        } else {
            body()
        }
    }
}

/// `.motionAnimation(value:)`：值变化时按统一规则做动画（自动读取「减弱动态效果」）。
private struct MotionAnimationModifier<Value: Equatable>: ViewModifier {
    let base: Animation
    let value: Value
    let fadeOnly: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @ViewBuilder func body(content: Content) -> some View {
        // 界面测试中完全不挂（连 `.animation(nil, value:)` 也不挂，避免改动别处原有的动画事务）。
        if Motion.disabledForTests {
            content
        } else {
            content.animation(Motion.animation(base, reduceMotion: reduceMotion, fadeOnly: fadeOnly), value: value)
        }
    }
}

/// `.motionTransition(edge:)`：出现 / 消失的统一过渡。
private struct MotionTransitionModifier: ViewModifier {
    let edge: Edge
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @ViewBuilder func body(content: Content) -> some View {
        if Motion.disabledForTests {
            content
        } else {
            content.transition(Motion.transition(edge: edge, reduceMotion: reduceMotion))
        }
    }
}

/// 数字变化：滚动式数字（`numericText`）；减弱动态效果时淡变；界面测试中不变。
private struct NumericMotionModifier<Value: Equatable>: ViewModifier {
    let value: Value
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @ViewBuilder func body(content: Content) -> some View {
        if Motion.disabledForTests {
            content
        } else {
            content
                .contentTransition(reduceMotion ? .opacity : .numericText())
                .animation(Motion.animation(Motion.snappy, reduceMotion: reduceMotion), value: value)
        }
    }
}

/// SF Symbol 换图（如复制 → 对勾）：系统 `replace` 效果；减弱动态效果时系统自动降级为淡变。
private struct SymbolSwapModifier<Value: Equatable>: ViewModifier {
    let value: Value

    @ViewBuilder func body(content: Content) -> some View {
        if Motion.disabledForTests {
            content
        } else {
            content
                .contentTransition(.symbolEffect(.replace))
                .animation(Motion.snappy, value: value)
        }
    }
}

extension View {
    /// 值变化时按统一规则做动画（尊重减弱动态效果，界面测试中关闭）。
    func motionAnimation<Value: Equatable>(_ base: Animation = Motion.snappy, value: Value, fadeOnly: Bool = true) -> some View {
        modifier(MotionAnimationModifier(base: base, value: value, fadeOnly: fadeOnly))
    }

    /// 出现 / 消失：轻移 + 淡入（减弱动态效果时只淡入）。
    func motionTransition(edge: Edge = .top) -> some View {
        modifier(MotionTransitionModifier(edge: edge))
    }

    /// 数字滚动（`contentTransition(.numericText())`）。
    func motionNumeric<Value: Equatable>(value: Value) -> some View {
        modifier(NumericMotionModifier(value: value))
    }

    /// SF Symbol 换图（复制 → 对勾等）。
    func motionSymbolSwap<Value: Equatable>(value: Value) -> some View {
        modifier(SymbolSwapModifier(value: value))
    }
}
