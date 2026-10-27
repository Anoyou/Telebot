#!/usr/bin/env python3
"""为「iOS」工作流挑选一台 iOS 26+ 的 iPhone 模拟器，打印其 UDID（设备名与运行时打到 stderr）。

编译与各 UI 测试分片共用同一套挑选规则，保证运行在同型号、同运行时的模拟器上。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

PREFERRED = ("iPhone 17 Pro", "iPhone 18 Pro", "iPhone 17", None)


def version(runtime: str) -> tuple[int, int]:
    m = re.search(r"iOS-(\d+)-(\d+)", runtime)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def main() -> int:
    raw = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "available", "-j"], check=True, capture_output=True, text=True
    ).stdout
    data = json.loads(raw)["devices"]
    runtimes = sorted((r for r in data if version(r)[0] >= 26), key=version, reverse=True)
    for preferred in PREFERRED:
        for runtime in runtimes:
            for device in data[runtime]:
                if not device.get("isAvailable", True):
                    continue
                name = device["name"]
                if (preferred is None and name.startswith("iPhone")) or name == preferred:
                    print(device["udid"])
                    print(name, runtime, file=sys.stderr)
                    return 0
    print("没有可用的 iOS 26+ iPhone 模拟器", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
