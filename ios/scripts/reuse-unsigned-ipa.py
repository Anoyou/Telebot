#!/usr/bin/env python3
"""「iOS 未签名 IPA」复用：把 PR 上已全绿、同一文件树构建的未签名 IPA 改写 build 号后发布。

只做两件事，其余字节原样保留（未签名 IPA 没有代码签名，改 Info.plist 不会破坏任何签名）：
1. 核对 IPA 里 TelePilot.app/Info.plist 的 CFBundleShortVersionString 等于 ios/project.yml 的 MARKETING_VERSION；
2. 把 CFBundleVersion 改写为本次工作流的 build 号（TELEPILOT_BUILD_NUMBER = run_number，严格递增），
   ZIP 内其它条目（含权限位、时间戳）逐条复制。

用法：reuse-unsigned-ipa.py <输入 ipa> <输出 ipa> <build 号> <期望版本>
输出一行 JSON：{"version": ..., "build": ..., "original_build": ...}
"""
from __future__ import annotations

import json
import plistlib
import re
import sys
import zipfile

PLIST = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")


def main() -> None:
    source, target, build, expected_version = sys.argv[1:5]
    if not re.fullmatch(r"[1-9][0-9]*", build):
        raise SystemExit(f"build 号必须是正整数：{build}")
    with zipfile.ZipFile(source) as zin:
        names = [info.filename for info in zin.infolist() if PLIST.match(info.filename)]
        if len(names) != 1:
            raise SystemExit(f"IPA 里应恰好有一个 Payload/*.app/Info.plist，实际 {names}")
        plist_name = names[0]
        raw = zin.read(plist_name)
        fmt = plistlib.FMT_BINARY if raw.startswith(b"bplist") else plistlib.FMT_XML
        info = plistlib.loads(raw)
        version = info.get("CFBundleShortVersionString")
        if version != expected_version:
            raise SystemExit(f"IPA 版本 {version} 与工程 MARKETING_VERSION {expected_version} 不一致")
        original_build = info.get("CFBundleVersion")
        info["CFBundleVersion"] = build
        patched = plistlib.dumps(info, fmt=fmt, sort_keys=False)
        with zipfile.ZipFile(target, "w") as zout:
            for item in zin.infolist():
                data = patched if item.filename == plist_name else zin.read(item.filename)
                zout.writestr(item, data, compress_type=item.compress_type)
    with zipfile.ZipFile(target) as check:
        if check.testzip() is not None:
            raise SystemExit("改写后的 IPA 校验失败")
        info = plistlib.loads(check.read(plist_name))
        if info.get("CFBundleVersion") != build or info.get("CFBundleShortVersionString") != expected_version:
            raise SystemExit("改写后的 Info.plist 不符合预期")
    print(json.dumps({"version": version, "build": build, "original_build": original_build}, ensure_ascii=False))


if __name__ == "__main__":
    main()
