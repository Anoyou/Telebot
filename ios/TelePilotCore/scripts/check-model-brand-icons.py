#!/usr/bin/env python3
"""校验 iOS 模型家族图标（Assets.xcassets/ModelBrands）与 Web 渲染一致，防止第十轮的 logo 错乱回归。

两项检查（纯 Python，macOS 自带 python3 即可运行，iOS 工作流「校验工程与契约同步」执行）：
1. 安全子集：生成的 SVG 只含 <path>，路径只用绝对 M/L/C/Z、坐标之间显式分隔
   （CoreSVG 解析压缩的弧线标志位会失败，见 svg_normalize.py）。
2. 形状一致：用独立的纯 Python 扫描线把生成的 SVG 栅格化成 32×32 覆盖率，与参考覆盖率比较。
   参考覆盖率 model-brand-reference.json 由 resvg（与浏览器同样遵循 SVG 规范的独立渲染器）
   直接渲染 Web 端原始 SVG 得到，并记录源数据摘要；Web 图标改动后摘要不符会提示重新生成。

用法：
  check-model-brand-icons.py                    # 校验（CI）
  check-model-brand-icons.py --update-reference # 重新生成参考覆盖率（需要 pip install resvg-py pillow）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import svg_normalize as N  # noqa: E402

package = Path(__file__).resolve().parents[1]
ios = package.parent
repo = ios.parent
icons_json = repo / "frontend/src/lib/modelBrandIcons.json"
catalog = ios / "TelePilotApp/Assets.xcassets/ModelBrands"
reference_path = Path(__file__).with_name("model-brand-reference.json")
SIZE = 32
# 允许的差异：覆盖率差 > 0.5 的像素不超过 2%（抗锯齿、曲线展平误差远小于此；第九轮错乱的图标差异 > 15%）。
MAX_MISMATCH = 0.02


def source_digest(icon: dict) -> str:
    raw = json.dumps({k: icon.get(k) for k in ("viewBox", "fillRule", "paths")}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def web_svg(icon: dict, size: int) -> str:
    """与 Web `ModelBrandLogo` 相同的结构：根 svg 上设 fill-rule，内部即原始路径片段。"""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{icon["viewBox"]}" width="{size}" height="{size}" '
        f'fill="#000000" fill-rule="{icon.get("fillRule", "nonzero")}">{icon["paths"]}</svg>'
    )


def render_reference(svg: str, size: int = SIZE) -> list[float]:
    import resvg_py  # type: ignore
    from PIL import Image  # type: ignore

    png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    alpha = Image.open(io.BytesIO(bytes(png))).convert("RGBA").getchannel("A")
    return [v / 255 for v in alpha.getdata()]


def encode(coverage: list[float]) -> str:
    return base64.b64encode(bytes(round(v * 255) for v in coverage)).decode()


def decode(text: str) -> list[float]:
    return [v / 255 for v in base64.b64decode(text)]


def mismatch(a: list[float], b: list[float]) -> float:
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 0.5) / len(a)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update-reference", action="store_true")
    args = parser.parse_args()
    icons = json.loads(icons_json.read_text())["icons"]

    if args.update_reference:
        data = {
            "_note": "resvg 渲染 Web 原始 SVG 得到的 32×32 覆盖率（base64，每像素 1 字节），check-model-brand-icons.py 使用",
            "size": SIZE,
            "icons": {
                brand: {"source": source_digest(icon), "coverage": encode(render_reference(web_svg(icon, SIZE)))}
                for brand, icon in sorted(icons.items())
            },
        }
        reference_path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(f"已更新 {reference_path.relative_to(repo)}（{len(icons)} 个图标）")
        return

    reference = json.loads(reference_path.read_text())["icons"]
    errors: list[str] = []
    for brand, icon in sorted(icons.items()):
        svg_path = catalog / f"{brand}.imageset/{brand}.svg"
        if not svg_path.exists():
            errors.append(f"{brand}：缺少 {svg_path.relative_to(repo)}（运行 sync-model-brands.py）")
            continue
        text = svg_path.read_text()
        for problem in N.lint_normalized(text):
            errors.append(f"{brand}：{problem}")
        ref = reference.get(brand)
        if not ref or ref["source"] != source_digest(icon):
            errors.append(f"{brand}：Web 图标已变化，参考覆盖率过期（pip install resvg-py pillow 后运行本脚本 --update-reference）")
            continue
        got = N.rasterize(N.raster_items_from_normalized(text), size=SIZE)
        score = mismatch(got, decode(ref["coverage"]))
        status = "通过" if score <= MAX_MISMATCH else "不一致"
        print(f"{brand:12s} 差异像素 {score * 100:5.2f}%  {status}")
        if score > MAX_MISMATCH:
            errors.append(f"{brand}：生成图标与 Web 渲染差异 {score * 100:.1f}%（上限 {MAX_MISMATCH * 100:.0f}%）")
    stale = sorted(set(reference) - set(icons))
    if stale:
        errors.append(f"参考覆盖率里有已删除的图标：{stale}（--update-reference）")
    if errors:
        raise SystemExit("模型家族图标校验失败：\n" + "\n".join(errors))
    print(f"模型家族图标校验通过（{len(icons)} 个）")


if __name__ == "__main__":
    main()
