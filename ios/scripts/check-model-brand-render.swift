// 用 macOS 系统的 SVG 渲染（NSImage → CoreSVG，与 Xcode 资源目录转换矢量图标是同一套解析器）实际渲染
// iOS 模型家族图标，与 resvg 渲染 Web 原始 SVG 的参考覆盖率（ios/TelePilotCore/scripts/model-brand-reference.json）比对。
//
// - 生成的资源（Assets.xcassets/ModelBrands/<id>.imageset/<id>.svg）差异超过 2% 即失败；
// - 同时渲染 Web 原始 SVG（第九轮直接拷贝进资源目录的写法），只记录差异（LOGO-RECORD），用于确认根因；
// - 若这台 macOS 的 NSImage 不能读取 SVG（全部读取失败），给出警告并跳过，形状一致性仍由
//   check-model-brand-icons.py 的纯 Python 校验保证。
//
// 用法：swift ios/scripts/check-model-brand-render.swift <仓库根目录>
import AppKit
import Foundation

struct Reference: Decodable {
    struct Item: Decodable { let source: String; let coverage: String }
    let size: Int
    let icons: [String: Item]
}

struct Icons: Decodable {
    struct Icon: Decodable { let viewBox: String; let paths: String; let fillRule: String? }
    let icons: [String: Icon]
}

let root = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : FileManager.default.currentDirectoryPath)
let reference = try JSONDecoder().decode(Reference.self, from: Data(contentsOf: root.appendingPathComponent("ios/TelePilotCore/scripts/model-brand-reference.json")))
let icons = try JSONDecoder().decode(Icons.self, from: Data(contentsOf: root.appendingPathComponent("frontend/src/lib/modelBrandIcons.json")))
let catalog = root.appendingPathComponent("ios/TelePilotApp/Assets.xcassets/ModelBrands")
let size = reference.size
let maxMismatch = 0.02

/// 把 SVG 数据画进 size×size 的 RGBA 位图，返回每像素不透明度（行优先、从上到下，与参考一致）。
func render(_ data: Data) -> [Double]? {
    guard let image = NSImage(data: data), image.isValid, image.size.width > 0 else { return nil }
    guard let context = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: size * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
    image.draw(in: NSRect(x: 0, y: 0, width: size, height: size), from: .zero, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    guard let buffer = context.data else { return nil }
    let pixels = buffer.bindMemory(to: UInt8.self, capacity: size * size * 4)
    return (0..<(size * size)).map { Double(pixels[$0 * 4 + 3]) / 255 }
}

func mismatch(_ a: [Double], _ b: [Double]) -> Double {
    Double(zip(a, b).filter { abs($0 - $1) > 0.5 }.count) / Double(a.count)
}

func decode(_ text: String) -> [Double] {
    (Data(base64Encoded: text) ?? Data()).map { Double($0) / 255 }
}

var failures: [String] = []
var unreadable: [String] = []
var checked = 0
for brand in icons.icons.keys.sorted() {
    guard let item = reference.icons[brand] else { failures.append("\(brand)：缺少参考覆盖率"); continue }
    let expected = decode(item.coverage)
    let assetURL = catalog.appendingPathComponent("\(brand).imageset/\(brand).svg")
    guard let assetData = try? Data(contentsOf: assetURL) else { failures.append("\(brand)：缺少资源 \(assetURL.path)"); continue }
    guard let asset = render(assetData) else { unreadable.append(brand); continue }
    checked += 1
    let assetDiff = mismatch(asset, expected)
    // 第九轮的写法：Web 原始路径片段原样放进 <g fill-rule=…>（只做记录，不判失败）。
    let icon = icons.icons[brand]!
    let legacy = "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"\(icon.viewBox)\" width=\"24\" height=\"24\"><g fill=\"#000000\" fill-rule=\"\(icon.fillRule ?? "nonzero")\">\(icon.paths.replacingOccurrences(of: "currentColor", with: "#000000"))</g></svg>"
    let legacyText = render(Data(legacy.utf8)).map { String(format: "%.1f%%", mismatch($0, expected) * 100) } ?? "无法读取"
    print(String(format: "LOGO-RECORD %@ 资源差异 %.2f%%（第九轮原始写法 %@）", brand, assetDiff * 100, legacyText))
    if assetDiff > maxMismatch {
        failures.append(String(format: "%@：CoreSVG 渲染与 Web 差异 %.1f%%", brand, assetDiff * 100))
    }
}

if checked == 0 {
    print("::warning::这台 macOS 的 NSImage 无法读取 SVG，跳过 CoreSVG 渲染比对（形状一致性由 check-model-brand-icons.py 校验）")
    exit(0)
}
if !unreadable.isEmpty { failures.append("NSImage 无法读取：\(unreadable.joined(separator: ", "))") }
if !failures.isEmpty {
    print("模型家族图标 CoreSVG 渲染校验失败：\n" + failures.joined(separator: "\n"))
    exit(1)
}
print("模型家族图标 CoreSVG 渲染校验通过（\(checked) 个）")
