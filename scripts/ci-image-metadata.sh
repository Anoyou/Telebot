#!/usr/bin/env bash
# 两个原生构建任务与 manifest 合并任务共用同一套发布标签校验。
set -euo pipefail

owner_lc="$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')"
image="ghcr.io/${owner_lc}/telepilot-${SERVICE}"
version="$(python - <<'PY'
import re
from pathlib import Path
text = Path("backend/app/__init__.py").read_text()
print(re.search(r'__version__ = "([^"]+)"', text).group(1))
PY
)"
stable_pattern='^[0-9]+\.[0-9]+\.[0-9]+$'
prerelease_pattern='^[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta|rc)\.[0-9]+$'
{
  echo "image=$image"
  echo "tags<<EOF"
  echo "${image}:sha-${REVISION}"
  if [[ "$REF_TYPE" == "tag" ]]; then
    tag_version="${REF_NAME#v}"
    if [[ "$tag_version" != "$version" ]]; then
      echo "Tag ${REF_NAME} 与代码版本 ${version} 不一致" >&2
      exit 1
    fi
    echo "${image}:${version}"
    if [[ "$version" =~ $stable_pattern ]]; then
      echo "${image}:stable"
    elif [[ "$version" =~ $prerelease_pattern ]]; then
      echo "${image}:beta"
    else
      echo "不支持的发布版本格式：${version}" >&2
      exit 1
    fi
  elif [[ "$REF_NAME" == "main" ]]; then
    if [[ ! "$version" =~ $stable_pattern ]]; then
      echo "main 只允许稳定版本镜像，当前为 ${version}" >&2
      exit 1
    fi
    echo "${image}:stable"
  elif [[ "$REF_NAME" == "Beta" ]]; then
    if [[ "$version" =~ $prerelease_pattern ]]; then
      echo "${image}:beta"
    elif [[ "$version" =~ $stable_pattern ]]; then
      echo "Beta 正在同步稳定发布检查点，仅保留提交 SHA 标签" >&2
    else
      echo "不支持的 Beta 版本格式：${version}" >&2
      exit 1
    fi
  fi
  echo "EOF"
} >> "$GITHUB_OUTPUT"
