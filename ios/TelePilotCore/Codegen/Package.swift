// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TelePilotCodegen",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.14.2"),
    ]
)
