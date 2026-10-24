#!/bin/bash
set -euo pipefail

IOS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${1:-$IOS_ROOT/build}"
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"

xcodebuild \
  -project "$IOS_ROOT/TelePilot.xcodeproj" \
  -scheme TelePilot \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "$OUTPUT/TelePilot.xcarchive" \
  -derivedDataPath "$OUTPUT/DerivedData" \
  CODE_SIGNING_ALLOWED=NO \
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
