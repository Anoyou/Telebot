#!/usr/bin/env python3
"""同步 Web 与 iOS 共用的模型家族映射与图标。

规范来源（Web）：
- frontend/src/lib/modelBrands.json        家族名称、强调色与识别规则
- frontend/src/lib/modelBrandIcons.json    家族单色图标路径（lobe-icons MIT / simple-icons CC0）
- frontend/src/lib/modelBrandCases.json    服务器 238 个模型标识的期望家族（两端单测共用）

生成（iOS）：
- TelePilotCore/Sources/TelePilotCore/model-brands.json          （逐字节复制）
- TelePilotCore/Tests/TelePilotCoreTests/model-brand-cases.json  （逐字节复制）
- TelePilotApp/Assets.xcassets/ModelBrands/<id>.imageset         （规范化矢量 SVG、模板渲染，深浅色随前景色）

`--check` 只校验不写入，iOS 工作流会运行。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svg_normalize  # noqa: E402

package = Path(__file__).resolve().parents[1]
ios = package.parent
repo = ios.parent
lib = repo / "frontend/src/lib"
copies = {
    lib / "modelBrands.json": package / "Sources/TelePilotCore/model-brands.json",
    lib / "modelBrandCases.json": package / "Tests/TelePilotCoreTests/model-brand-cases.json",
}
catalog = ios / "TelePilotApp/Assets.xcassets/ModelBrands"


def render_svg(icon: dict) -> str:
    # 第十轮：不再原样拷贝 Web 的压缩路径（CoreSVG 解析不了紧挨的弧线标志位，图标错乱），
    # 规范化为只含绝对 M/L/C/Z 的安全子集，见 svg_normalize.py；check-model-brand-icons.py 校验形状与 Web 一致。
    return svg_normalize.normalized_svg(icon)


def dump(value: dict) -> str:
    return json.dumps(value, indent=2) + "\n"


def expected_files() -> dict[Path, str]:
    files: dict[Path, str] = {target: source.read_text() for source, target in copies.items()}
    files[catalog / "Contents.json"] = dump({"info": {"author": "xcode", "version": 1}, "properties": {"provides-namespace": True}})
    icons = json.loads((lib / "modelBrandIcons.json").read_text())["icons"]
    for brand, icon in sorted(icons.items()):
        folder = catalog / f"{brand}.imageset"
        files[folder / f"{brand}.svg"] = render_svg(icon)
        files[folder / "Contents.json"] = dump({
            "images": [{"filename": f"{brand}.svg", "idiom": "universal"}],
            "info": {"author": "xcode", "version": 1},
            "properties": {"preserves-vector-representation": True, "template-rendering-intent": "template"},
        })
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = expected_files()
    existing = {path for path in catalog.rglob("*") if path.is_file()} if catalog.exists() else set()
    stale = sorted(existing - set(files))
    if args.check:
        wrong = [str(path.relative_to(repo)) for path, text in files.items() if not path.exists() or path.read_text() != text]
        wrong += [str(path.relative_to(repo)) for path in stale]
        if wrong:
            raise SystemExit("模型家族映射／图标与 Web 不一致，运行 ios/TelePilotCore/scripts/sync-model-brands.py：\n" + "\n".join(wrong))
        return
    for path in stale:
        path.unlink()
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


if __name__ == "__main__":
    main()
