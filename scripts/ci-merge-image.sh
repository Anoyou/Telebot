#!/usr/bin/env bash
# 仅在两架构工件完整且 manifest 校验通过后更新发布标签。
set -euo pipefail

sources=()
for arch in amd64 arm64; do
  files=("$RUNNER_TEMP/digests/$ARTIFACT_PREFIX-$arch/"*)
  [[ ${#files[@]} -eq 1 && -f "${files[0]}" ]] || { echo "::error::$arch 必须有且只有一个 digest 工件" >&2; exit 1; }
  digest="${files[0]##*/}"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || { echo "::error::$arch digest 格式无效" >&2; exit 1; }
  sources+=("$IMAGE@sha256:$digest")
done
tags=()
while IFS= read -r tag; do
  [[ -z "$tag" ]] || tags+=(--tag "$tag")
done <<< "$TAGS"
[[ ${#tags[@]} -gt 0 ]] || { echo "::error::没有可发布标签" >&2; exit 1; }
# 先核对待发布 manifest，架构缺失时不能更新 beta / stable 等标签。
docker buildx imagetools create --dry-run "${sources[@]}" > "$RUNNER_TEMP/manifest.json"
jq -e '
  [.manifests[].platform | select(.os == "linux") | .architecture]
  | sort == ["amd64", "arm64"]
' "$RUNNER_TEMP/manifest.json"
docker buildx imagetools create "${tags[@]}" \
  --metadata-file "$RUNNER_TEMP/manifest-metadata.json" "${sources[@]}"
digest="$(jq -er '."containerimage.descriptor".digest' "$RUNNER_TEMP/manifest-metadata.json")"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "::error::manifest digest 格式无效" >&2; exit 1; }
docker buildx imagetools inspect "$IMAGE@$digest" --raw > "$RUNNER_TEMP/manifest.json"
jq -e '
  [.manifests[].platform | select(.os == "linux") | .architecture]
  | sort == ["amd64", "arm64"]
' "$RUNNER_TEMP/manifest.json"
echo "digest=$digest" >> "$GITHUB_OUTPUT"
