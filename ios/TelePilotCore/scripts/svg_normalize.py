"""把 Web 端的模型家族 SVG 片段规范化为 Xcode 资源目录（CoreSVG）稳定支持的子集。

背景（第十轮真机问题）：lobe-icons 的路径是压缩写法，弧线命令的两个标志位与后续坐标紧挨着
（例如 `a.503.503 0 00-.975 0`、`0 011.943.173`）。浏览器按 SVG 规范把 `00`、`01` 拆成两个标志位，
而 Xcode 资源目录把 SVG 转矢量时使用的 CoreSVG 对这种写法解析失败，从出错处起丢弃后面的路径段：
Google Gemma 只剩一个小三角，Poolside 图形破碎，智谱的圆点也被截断。

本模块不依赖第三方库，CI（macOS 自带 python3）可直接运行：
- 解析全部路径命令（M L H V C S Q T A Z 及其相对形式，含压缩的弧线标志位与 `.5.5` 连写小数）；
- 全部转换为绝对坐标的 M / L / C / Z：二次曲线升为三次，弧线按 SVG 规范 F.6.5 转为三次贝塞尔；
- `<ellipse>` / `<circle>` / `<rect>` / `<polygon>` / `<polyline>` 转为路径；
- 每段数字之间都有显式分隔符，统一保留 3 位小数；
- 保留 `fill-opacity` / `opacity`；`clip-rule` 只对 clipPath 生效，单独出现时无意义，丢弃；
- 遇到不认识的元素或属性（transform、clip-path、渐变、描边等）直接报错，避免静默生成错误图标。
"""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree

SVG_NS = "http://www.w3.org/2000/svg"
ALLOWED_ATTRS = {
    "path": {"d", "fill-opacity", "opacity", "clip-rule", "fill-rule", "fill"},
    "ellipse": {"cx", "cy", "rx", "ry", "fill-opacity", "opacity", "clip-rule", "fill-rule", "fill"},
    "circle": {"cx", "cy", "r", "fill-opacity", "opacity", "clip-rule", "fill-rule", "fill"},
    "rect": {"x", "y", "width", "height", "rx", "ry", "fill-opacity", "opacity", "clip-rule", "fill-rule", "fill"},
    "polygon": {"points", "fill-opacity", "opacity", "clip-rule", "fill-rule", "fill"},
    "polyline": {"points", "fill-opacity", "opacity", "clip-rule", "fill-rule", "fill"},
}
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
KAPPA = 0.5522847498307936  # 4/3*(sqrt(2)-1)


class SVGError(ValueError):
    pass


# ---------------------------------------------------------------- 解析路径

class _Scanner:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def skip(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n,":
            self.i += 1

    def done(self) -> bool:
        self.skip()
        return self.i >= len(self.s)

    def peek_command(self) -> str | None:
        self.skip()
        if self.i < len(self.s) and self.s[self.i].isalpha():
            return self.s[self.i]
        return None

    def command(self) -> str:
        c = self.peek_command()
        if c is None:
            raise SVGError(f"位置 {self.i} 期望路径命令：{self.s[self.i:self.i + 20]!r}")
        self.i += 1
        return c

    def number(self) -> float:
        self.skip()
        m = NUMBER.match(self.s, self.i)
        if not m:
            raise SVGError(f"位置 {self.i} 期望数字：{self.s[self.i:self.i + 20]!r}")
        self.i = m.end()
        return float(m.group(0))

    def flag(self) -> int:
        """弧线标志位只有一个字符（0 或 1），后面可以紧跟下一个数字。"""
        self.skip()
        if self.i < len(self.s) and self.s[self.i] in "01":
            self.i += 1
            return int(self.s[self.i - 1])
        raise SVGError(f"位置 {self.i} 期望弧线标志位 0/1：{self.s[self.i:self.i + 20]!r}")

    def has_number(self) -> bool:
        self.skip()
        return self.i < len(self.s) and NUMBER.match(self.s, self.i) is not None


def _arc_to_cubics(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2):
    """SVG 规范 F.6.5／F.6.6：端点参数化的椭圆弧 → 若干段三次贝塞尔（每段 ≤ 90°）。"""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [("L", (x2, y2))]
    phi = math.radians(phi_deg % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        length = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, dot / length)))
        return -a if ux * vy - uy * vx < 0 else a

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    segments = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / segments
    t = 4 / 3 * math.tan(step / 4)
    out = []

    def point(theta):
        ct, st = math.cos(theta), math.sin(theta)
        return (cx + rx * ct * cos_p - ry * st * sin_p, cy + rx * ct * sin_p + ry * st * cos_p)

    def deriv(theta):
        ct, st = math.cos(theta), math.sin(theta)
        return (-rx * st * cos_p - ry * ct * sin_p, -rx * st * sin_p + ry * ct * cos_p)

    theta = theta1
    for k in range(segments):
        p0 = point(theta)
        d0 = deriv(theta)
        theta_next = theta + step
        p3 = point(theta_next) if k < segments - 1 else (x2, y2)
        d3 = deriv(theta_next)
        c1 = (p0[0] + t * d0[0], p0[1] + t * d0[1])
        c2 = (p3[0] - t * d3[0], p3[1] - t * d3[1])
        out.append(("C", (c1[0], c1[1], c2[0], c2[1], p3[0], p3[1])))
        theta = theta_next
    return out


def parse_path(d: str) -> list[tuple[str, tuple[float, ...]]]:
    """解析任意合法的 SVG 路径数据，返回只含绝对 M / L / C / Z 的段列表。"""
    sc = _Scanner(d)
    out: list[tuple[str, tuple[float, ...]]] = []
    cx = cy = 0.0  # 当前点
    sx = sy = 0.0  # 子路径起点
    last_ctrl = None  # 上一段三次曲线的第二控制点（S 用）
    last_qctrl = None  # 上一段二次曲线的控制点（T 用）
    cmd = None
    while not sc.done():
        nxt = sc.peek_command()
        if nxt is not None:
            cmd = sc.command()
        elif cmd is None:
            raise SVGError("路径数据必须以命令开头")
        elif cmd in "Zz":
            raise SVGError("Z 之后出现了多余的数字")
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if c == "M":
            x, y = sc.number() + ox, sc.number() + oy
            out.append(("M", (x, y)))
            cx, cy, sx, sy = x, y, x, y
            cmd = "l" if rel else "L"  # M 后面的坐标对按 L 处理
            last_ctrl = last_qctrl = None
            continue
        if c == "Z":
            out.append(("Z", ()))
            cx, cy = sx, sy
            last_ctrl = last_qctrl = None
            if sc.has_number():
                raise SVGError("Z 之后出现了多余的数字")
            continue
        if out and out[-1][0] == "Z":
            out.append(("M", (cx, cy)))  # Z 后直接画线：显式补一个 M，避免依赖渲染器的隐式子路径
        if c == "L":
            x, y = sc.number() + ox, sc.number() + oy
            out.append(("L", (x, y)))
            cx, cy = x, y
            last_ctrl = last_qctrl = None
        elif c == "H":
            x = sc.number() + ox
            out.append(("L", (x, cy)))
            cx = x
            last_ctrl = last_qctrl = None
        elif c == "V":
            y = sc.number() + (cy if rel else 0.0)
            out.append(("L", (cx, y)))
            cy = y
            last_ctrl = last_qctrl = None
        elif c == "C":
            x1, y1 = sc.number() + ox, sc.number() + oy
            x2, y2 = sc.number() + ox, sc.number() + oy
            x, y = sc.number() + ox, sc.number() + oy
            out.append(("C", (x1, y1, x2, y2, x, y)))
            last_ctrl, last_qctrl = (x2, y2), None
            cx, cy = x, y
        elif c == "S":
            x1, y1 = (2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1]) if last_ctrl else (cx, cy)
            x2, y2 = sc.number() + ox, sc.number() + oy
            x, y = sc.number() + ox, sc.number() + oy
            out.append(("C", (x1, y1, x2, y2, x, y)))
            last_ctrl, last_qctrl = (x2, y2), None
            cx, cy = x, y
        elif c in "QT":
            if c == "Q":
                qx, qy = sc.number() + ox, sc.number() + oy
            else:
                qx, qy = (2 * cx - last_qctrl[0], 2 * cy - last_qctrl[1]) if last_qctrl else (cx, cy)
            x, y = sc.number() + ox, sc.number() + oy
            c1 = (cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy))
            c2 = (x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y))
            out.append(("C", (c1[0], c1[1], c2[0], c2[1], x, y)))
            last_ctrl, last_qctrl = None, (qx, qy)
            cx, cy = x, y
        elif c == "A":
            rx, ry, rot = sc.number(), sc.number(), sc.number()
            large, sweep = sc.flag(), sc.flag()
            x, y = sc.number() + ox, sc.number() + oy
            out.extend(_arc_to_cubics(cx, cy, rx, ry, rot, large, sweep, x, y))
            cx, cy = x, y
            last_ctrl = last_qctrl = None
        else:
            raise SVGError(f"不支持的路径命令 {cmd}")
        if not sc.has_number() and sc.peek_command() is None and not sc.done():
            raise SVGError(f"位置 {sc.i} 无法解析：{d[sc.i:sc.i + 20]!r}")
    return out


# ---------------------------------------------------------------- 形状 → 路径

def _ellipse(cx, cy, rx, ry):
    k = KAPPA
    return [
        ("M", (cx + rx, cy)),
        ("C", (cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry)),
        ("C", (cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy)),
        ("C", (cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry)),
        ("C", (cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy)),
        ("Z", ()),
    ]


def _num(el, name, default=0.0):
    value = el.get(name)
    return float(value) if value is not None else default


def element_segments(el) -> list[tuple[str, tuple[float, ...]]]:
    tag = el.tag.split("}")[-1]
    if tag == "path":
        return parse_path(el.get("d", ""))
    if tag == "ellipse":
        return _ellipse(_num(el, "cx"), _num(el, "cy"), _num(el, "rx"), _num(el, "ry"))
    if tag == "circle":
        r = _num(el, "r")
        return _ellipse(_num(el, "cx"), _num(el, "cy"), r, r)
    if tag == "rect":
        x, y, w, h = _num(el, "x"), _num(el, "y"), _num(el, "width"), _num(el, "height")
        if el.get("rx") or el.get("ry"):
            raise SVGError("暂不支持圆角 rect，请改写为 path")
        return [("M", (x, y)), ("L", (x + w, y)), ("L", (x + w, y + h)), ("L", (x, y + h)), ("Z", ())]
    if tag in ("polygon", "polyline"):
        values = [float(v) for v in NUMBER.findall(el.get("points", ""))]
        pts = list(zip(values[::2], values[1::2]))
        segs = [("M", pts[0])] + [("L", p) for p in pts[1:]]
        return segs + ([("Z", ())] if tag == "polygon" else [])
    raise SVGError(f"不支持的元素 <{tag}>")


# ---------------------------------------------------------------- 输出

def fmt(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_segments(segs) -> str:
    parts = []
    for c, args in segs:
        if c == "Z":
            parts.append("Z")
        else:
            coords = [f"{fmt(args[i])},{fmt(args[i + 1])}" for i in range(0, len(args), 2)]
            parts.append(c + " " + " ".join(coords))
    return " ".join(parts)


def parse_fragment(paths: str):
    """解析 Web 图标 JSON 里的 `paths` 片段，逐个元素返回 (段列表, 属性)。"""
    root = ElementTree.fromstring(f'<g xmlns="{SVG_NS}">{paths}</g>')
    items = []
    for el in root:
        tag = el.tag.split("}")[-1]
        if tag not in ALLOWED_ATTRS:
            raise SVGError(f"不支持的元素 <{tag}>（需要扩展 svg_normalize.py）")
        unknown = set(el.attrib) - ALLOWED_ATTRS[tag]
        if unknown:
            raise SVGError(f"<{tag}> 含不支持的属性 {sorted(unknown)}（需要扩展 svg_normalize.py）")
        fill = el.get("fill")
        if fill not in (None, "currentColor", "#000", "#000000", "black"):
            raise SVGError(f"<{tag}> 使用了单色模板不支持的填充 {fill!r}")
        if len(el):
            raise SVGError(f"<{tag}> 不应包含子元素")
        items.append((element_segments(el), dict(el.attrib)))
    return items


def normalized_svg(icon: dict) -> str:
    """Web 图标定义 → 规范化 SVG 文本（资源目录用）。"""
    if icon.get("viewBox", "0 0 24 24") != "0 0 24 24":
        raise SVGError("目前只支持 viewBox 0 0 24 24")
    rule = icon.get("fillRule", "nonzero")
    body = []
    for segs, attrs in parse_fragment(icon["paths"]):
        extra = ""
        for name in ("fill-opacity", "opacity"):
            if name in attrs:
                extra += f' {name}="{fmt(float(attrs[name]))}"'
        own_rule = attrs.get("fill-rule", rule)
        body.append(f'<path fill="#000000" fill-rule="{own_rule}"{extra} d="{format_segments(segs)}"/>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="0 0 24 24" width="24" height="24">'
        + "".join(body)
        + "</svg>\n"
    )


SAFE_PATH = re.compile(r"^M [-\d.]+,[-\d.]+(?: (?:[MLC](?: [-\d.]+,[-\d.]+)+|Z))*$")


def lint_normalized(text: str) -> list[str]:
    """校验生成的 SVG 只用安全子集：<svg> 下只有 <path>，路径只含绝对 M/L/C/Z、坐标显式分隔。"""
    problems = []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        return [f"XML 解析失败：{error}"]
    if root.tag != f"{{{SVG_NS}}}svg" or root.get("viewBox") != "0 0 24 24":
        problems.append("根元素必须是 viewBox 0 0 24 24 的 <svg>")
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if el is root:
            continue
        if tag != "path":
            problems.append(f"出现了 <{tag}>")
            continue
        if set(el.attrib) - {"fill", "fill-rule", "fill-opacity", "opacity", "d"}:
            problems.append(f"<path> 含多余属性 {sorted(set(el.attrib))}")
        if not SAFE_PATH.match(el.get("d", "")):
            problems.append("路径数据不在安全子集内（只允许绝对 M/L/C/Z、空格与逗号分隔）")
    return problems


def segments_to_polygons(segs, tolerance_steps: int = 16):
    """把 M/L/C/Z 段展平成多边形（每段三次曲线均匀取点），供纯 Python 栅格化。"""
    polys, cur = [], []
    x = y = 0.0
    for c, args in segs:
        if c == "M":
            if len(cur) > 1:
                polys.append(cur)
            cur = [args]
            x, y = args
        elif c == "L":
            cur.append(args)
            x, y = args
        elif c == "C":
            x1, y1, x2, y2, x3, y3 = args
            for i in range(1, tolerance_steps + 1):
                t = i / tolerance_steps
                mt = 1 - t
                cur.append((
                    mt ** 3 * x + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t ** 3 * x3,
                    mt ** 3 * y + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t ** 3 * y3,
                ))
            x, y = x3, y3
        elif c == "Z":
            if len(cur) > 1:
                polys.append(cur)
            cur = [cur[0]] if cur else []
            if cur:
                x, y = cur[0]
    if len(cur) > 1:
        polys.append(cur)
    return polys


def rasterize(items, size: int = 32, supersample: int = 4) -> list[float]:
    """纯 Python 扫描线栅格化（每像素 supersample² 个采样点），返回 size×size 覆盖率（0–1，已乘不透明度）。

    items: [(段列表, fill_rule, opacity)]。多个元素按“源上方”叠加（单色，取 1-(1-a)(1-b)）。
    """
    n = size * supersample
    scale = n / 24.0
    coverage = [0.0] * (size * size)
    for segs, rule, opacity in items:
        polys = [[(px * scale, py * scale) for px, py in poly] for poly in segments_to_polygons(segs)]
        edges = []
        for poly in polys:
            for (xa, ya), (xb, yb) in zip(poly, poly[1:] + poly[:1]):
                if ya != yb:
                    edges.append((xa, ya, xb, yb))
        hits = [0] * (size * size)
        for row in range(n):
            sy = row + 0.5
            crossings = []
            for xa, ya, xb, yb in edges:
                if (ya <= sy < yb) or (yb <= sy < ya):
                    xi = xa + (sy - ya) * (xb - xa) / (yb - ya)
                    crossings.append((xi, 1 if yb > ya else -1))
            if not crossings:
                continue
            crossings.sort()
            winding = 0
            for idx, (xi, direction) in enumerate(crossings[:-1]):
                winding += direction
                inside = (winding % 2 == 1) if rule == "evenodd" else (winding != 0)
                if not inside:
                    continue
                x_end = crossings[idx + 1][0]
                start = max(0, math.ceil(xi - 0.5))
                stop = min(n - 1, math.floor(x_end - 0.5))
                base = (row // supersample) * size
                for col in range(start, stop + 1):
                    hits[base + col // supersample] += 1
        total = supersample * supersample
        for i, h in enumerate(hits):
            if h:
                a = opacity * h / total
                coverage[i] = 1 - (1 - coverage[i]) * (1 - a)
    return coverage


def raster_items_from_normalized(text: str):
    root = ElementTree.fromstring(text)
    items = []
    for el in root.iter(f"{{{SVG_NS}}}path"):
        opacity = float(el.get("fill-opacity", "1")) * float(el.get("opacity", "1"))
        items.append((parse_path(el.get("d", "")), el.get("fill-rule", "nonzero"), opacity))
    return items
