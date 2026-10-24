#!/bin/bash
set -euo pipefail

IOS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${1:-$IOS_ROOT/build/ui-screenshots}"
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"

BOOTED_ID=$(xcrun simctl list devices booted \
  | sed -n 's/.*(\([0-9A-F-]\{36\}\)) (Booted).*/\1/p' \
  | head -n 1 \
  | tr -d '[:space:]')
if [[ -z "$BOOTED_ID" ]]; then
  echo "请先在 Simulator 中启动一台 iPhone。" >&2
  exit 1
fi

RESULT_BUNDLE="$OUTPUT/VisualInspection.xcresult"
if [[ -e "$RESULT_BUNDLE" ]]; then
  echo "结果目录已存在，请换一个输出目录：$RESULT_BUNDLE" >&2
  exit 1
fi

cd "$IOS_ROOT"
xcodegen generate --spec project.yml >/dev/null
xcodebuild test \
  -project TelePilot.xcodeproj \
  -scheme TelePilot \
  -destination "platform=iOS Simulator,id=$BOOTED_ID" \
  -derivedDataPath "$IOS_ROOT/build/DerivedData" \
  -parallel-testing-enabled NO \
  -only-testing:TelePilotUITests/VisualInspectionUITests \
  -resultBundlePath "$RESULT_BUNDLE" \
  -collect-test-diagnostics never \
  CODE_SIGNING_ALLOWED=NO

xcrun xcresulttool export attachments \
  --path "$RESULT_BUNDLE" \
  --output-path "$OUTPUT/attachments" >/dev/null
printf '视觉巡检截图：%s\n' "$OUTPUT/attachments"
