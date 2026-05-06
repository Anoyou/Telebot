// 生成 PWA 占位图标：Telegram 蓝色实心方块 + 中间一个白色 "U"。
// 不依赖任何第三方库，纯 Node 内建 zlib + Buffer 写 PNG。
// 用法：node scripts/gen-placeholder-icons.mjs
// 之后想换真图标：把 1024x1024 源图放到 frontend/public/，覆盖下面 4 个文件即可。
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = resolve(__dirname, "../frontend/public");

// ---------- PNG 编码（RGBA 8bit） ----------
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = (CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8)) >>> 0;
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const tb = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([tb, data])), 0);
  return Buffer.concat([len, tb, data, crc]);
}

function encodePng(width, height, pixels /* Uint8Array, RGBA */) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type = RGBA
  // ihdr[10..12] = 0
  const rowBytes = width * 4;
  const rowLen = rowBytes + 1;
  const raw = Buffer.alloc(rowLen * height);
  const src = Buffer.from(pixels.buffer, pixels.byteOffset, pixels.byteLength);
  for (let y = 0; y < height; y++) {
    raw[y * rowLen] = 0; // filter: None
    src.copy(raw, y * rowLen + 1, y * rowBytes, (y + 1) * rowBytes);
  }
  const idat = deflateSync(raw);
  return Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------- 像素生成 ----------
const BG = [0x26, 0xa6, 0xde, 0xff]; // Telegram 蓝
const FG = [0xff, 0xff, 0xff, 0xff]; // 白色

/**
 * 渲染一个 size×size 的 RGBA 缓冲：
 *  - safeZone=true  →  内容在中央 80%（maskable 安全区）
 *  - safeZone=false →  内容铺满（普通图标）
 *  - rounded=true   →  iOS 风格的圆角（apple-touch-icon iOS 自己也会做圆角，这里只是好看一点）
 */
function renderIcon(size, { safeZone = false, rounded = false } = {}) {
  const px = new Uint8Array(size * size * 4);
  const cx = size / 2;
  const cy = size / 2;
  const radius = rounded ? size * 0.22 : 0; // 圆角半径
  const contentScale = safeZone ? 0.8 : 1.0;
  const inner = size * contentScale;
  const innerStart = (size - inner) / 2;
  const innerEnd = innerStart + inner;

  // 字母 "U" 的简单几何描述：
  //   两根竖线 + 底部半圆。基于 inner 区域定义。
  const ux0 = innerStart + inner * 0.28;
  const ux1 = innerStart + inner * 0.72;
  const uTop = innerStart + inner * 0.28;
  const uBottomY = innerStart + inner * 0.66; // 半圆中心
  const uStroke = inner * 0.13;
  const uArcR = (ux1 - ux0) / 2;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let color = [0, 0, 0, 0]; // 默认透明（圆角外）

      // 圆角矩形遮罩
      let inside = true;
      if (rounded) {
        const corner =
          (x < radius && y < radius && Math.hypot(radius - x, radius - y) > radius) ||
          (x > size - radius && y < radius && Math.hypot(x - (size - radius), radius - y) > radius) ||
          (x < radius && y > size - radius && Math.hypot(radius - x, y - (size - radius)) > radius) ||
          (x > size - radius && y > size - radius &&
            Math.hypot(x - (size - radius), y - (size - radius)) > radius);
        if (corner) inside = false;
      }

      if (inside) {
        // safeZone 外面用背景色填充（maskable 要求图标本身也铺满 safezone 之外）
        color = BG;

        // 在 inner 区域内画 "U"
        if (x >= innerStart && x <= innerEnd && y >= innerStart && y <= innerEnd) {
          // 左右竖线（从 uTop 到 uBottomY 的笔画矩形）
          const inLeftBar =
            x >= ux0 - uStroke / 2 && x <= ux0 + uStroke / 2 && y >= uTop && y <= uBottomY;
          const inRightBar =
            x >= ux1 - uStroke / 2 && x <= ux1 + uStroke / 2 && y >= uTop && y <= uBottomY;
          // 底部半圆环：以 ((ux0+ux1)/2, uBottomY) 为圆心
          const dx = x - (ux0 + ux1) / 2;
          const dy = y - uBottomY;
          const d = Math.hypot(dx, dy);
          const inArc = dy >= 0 && d >= uArcR - uStroke / 2 && d <= uArcR + uStroke / 2;

          if (inLeftBar || inRightBar || inArc) color = FG;
        }
      }

      const off = (y * size + x) * 4;
      px[off] = color[0];
      px[off + 1] = color[1];
      px[off + 2] = color[2];
      px[off + 3] = color[3];
    }
  }
  return px;
}

// ---------- 输出 ----------
mkdirSync(PUBLIC_DIR, { recursive: true });

const outputs = [
  { name: "pwa-192x192.png", size: 192, opts: {} },
  { name: "pwa-512x512.png", size: 512, opts: {} },
  // maskable：内容收缩到 80% 安全区，外圈仍然是品牌色，被裁圆/裁方都好看
  { name: "pwa-maskable-512x512.png", size: 512, opts: { safeZone: true } },
  // apple-touch-icon：iOS 会自己加圆角，这里仍画方形 + 自带圆角避免 iOS 旧版本不裁
  { name: "apple-touch-icon.png", size: 180, opts: { rounded: true } },
];

for (const { name, size, opts } of outputs) {
  const px = renderIcon(size, opts);
  const buf = encodePng(size, size, px);
  const path = resolve(PUBLIC_DIR, name);
  writeFileSync(path, buf);
  console.log(`✓ ${name} (${size}×${size}, ${buf.length} bytes)`);
}
