#!/bin/sh
set -eu
PACKAGE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
OUTPUT="$PACKAGE/Sources/TelePilotCore/Generated"
if [ "${1:-}" = "--check" ]; then
  python3 "$PACKAGE/scripts/sync-openapi.py" --check
  python3 "$PACKAGE/scripts/sync-contract.py" --check
  OUTPUT="$(mktemp -d)"
  trap 'rm -rf "$OUTPUT"' EXIT HUP INT TERM
elif [ "$#" -eq 0 ]; then
  python3 "$PACKAGE/scripts/sync-openapi.py"
  python3 "$PACKAGE/scripts/sync-contract.py"
else
  echo "Usage: sh scripts/generate.sh [--check]" >&2
  exit 2
fi
swift run --package-path "$PACKAGE/Codegen" swift-openapi-generator generate \
  "$PACKAGE/Sources/TelePilotCore/openapi.json" \
  --config "$PACKAGE/Sources/TelePilotCore/openapi-generator-config.yaml" \
  --output-directory "$OUTPUT"
if [ "${1:-}" = "--check" ]; then
  diff -ru "$PACKAGE/Sources/TelePilotCore/Generated" "$OUTPUT"
fi
