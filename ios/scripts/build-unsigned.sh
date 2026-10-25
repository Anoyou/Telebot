#!/bin/bash
set -euo pipefail

IOS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${1:-$IOS_ROOT/build}"
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"

# build 号（CFBundleVersion）：CI 通过 TELEPILOT_BUILD_NUMBER 注入单调递增的正整数；
# 未设置时沿用工程里的本地默认值。MARKETING_VERSION 不在这里覆盖，以工程设置为准。
VERSION_ARGS=()
if [ -n "${TELEPILOT_BUILD_NUMBER:-}" ]; then
  if ! [[ "$TELEPILOT_BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
    echo "TELEPILOT_BUILD_NUMBER 必须是正整数，当前为：$TELEPILOT_BUILD_NUMBER" >&2
    exit 1
  fi
  VERSION_ARGS+=("CURRENT_PROJECT_VERSION=$TELEPILOT_BUILD_NUMBER")
  echo "注入 build 号：$TELEPILOT_BUILD_NUMBER"
fi

xcodebuild \
  -project "$IOS_ROOT/TelePilot.xcodeproj" \
  -scheme TelePilot \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$OUTPUT/TelePilot.xcarchive" \
  -derivedDataPath "$OUTPUT/DerivedData" \
  CODE_SIGNING_ALLOWED=NO \
  ${VERSION_ARGS[@]+"${VERSION_ARGS[@]}"} \
  archive

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/Payload"
ditto "$OUTPUT/TelePilot.xcarchive/Products/Applications/TelePilot.app" \
  "$STAGING/Payload/TelePilot.app"
ditto --norsrc --noextattr -c -k --keepParent \
  "$STAGING/Payload" "$OUTPUT/TelePilot-unsigned.ipa"
printf '\n未签名 IPA：%s\n此文件需要你签名后才能安装，不包含证书或描述文件。\n' \
  "$OUTPUT/TelePilot-unsigned.ipa"
