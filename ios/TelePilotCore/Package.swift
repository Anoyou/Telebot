// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TelePilotCore",
    platforms: [.iOS("26.0"), .macOS(.v14)],
    products: [.library(name: "TelePilotCore", targets: ["TelePilotCore"])],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-runtime", from: "1.8.0"),
        .package(url: "https://github.com/apple/swift-openapi-urlsession", from: "1.1.0"),
        .package(url: "https://github.com/apple/swift-http-types", from: "1.3.0"),
    ],
    targets: [
        .target(name: "TelePilotCore", dependencies: [
            .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
            .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            .product(name: "HTTPTypes", package: "swift-http-types"),
        ], exclude: ["openapi.json", "openapi-generator-config.yaml"], resources: [.process("contract.json"), .process("model-brands.json"), .process("plugin-ui-widgets.json")]),
        .testTarget(name: "TelePilotCoreTests", dependencies: ["TelePilotCore"], resources: [.process("model-brand-cases.json"), .process("telegram-html-cases.json")]),
    ]
)
