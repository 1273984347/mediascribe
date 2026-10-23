#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 Tauri 需要的最小图标集 (纯标准库, 无第三方依赖)。

产出:
    icons/32x32.png, icons/128x128.png, icons/128x128@2x.png
    icons/icon.ico   —— 经典 BMP/DIB 格式 (非 PNG-in-ICO), 保证 winresource 能解析

为什么自带生成器: Tauri 在 Windows 上构建时会把 bundle.icon 的 .ico 嵌进 exe 资源,
图标缺失会让构建在更早的阶段就失败, 从而掩盖真正的阻断原因 (缺 MSVC 链接器)。
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def _png(width: int, height: int, pixel) -> bytes:
    """生成 RGBA PNG。pixel(x, y) -> (r, g, b, a)。"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _brand_pixel(x: int, y: int, size: int):
    """MediaScribe 标记色: 深蓝底 + 白色 'M' 形笔触 (极简, 只为产出合法图标)。"""
    fx = x / max(size - 1, 1)
    fy = y / max(size - 1, 1)
    # 深蓝底
    r, g, b = 0x1F, 0x3A, 0x6E
    # 中间一道白色斜线
    if 0.30 <= fx <= 0.70:
        t = abs(fy - (0.75 - 0.55 * (fx - 0.30) / 0.40))
        if t < 0.13:
            r, g, b = 0xF5, 0xF7, 0xFA
    return (r, g, b, 255)


def _ico_bmp(size: int, pixel) -> bytes:
    """单张 size x size 的 BMP/DIB 图像数据 (BITMAPINFOHEADER + BGRA + AND mask)。"""
    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        size,          # biWidth
        size * 2,      # biHeight (XOR + AND 合并)
        1,             # biPlanes
        32,            # biBitCount
        0,             # biCompression = BI_RGB
        0,             # biSizeImage (0 = 可算)
        0, 0, 0, 0,
    )
    xor = bytearray()
    for y in range(size - 1, -1, -1):          # BMP 自下而上
        for x in range(size):
            r, g, b, a = pixel(x, y, size)
            xor.extend((b, g, r, a))
    and_stride = ((size + 31) // 32) * 4         # 1bpp, 4 字节对齐
    and_mask = bytearray(and_stride * size)
    return bytes(header) + bytes(xor) + bytes(and_mask)


def write_ico(path: Path, sizes=(16, 32, 48, 256)) -> None:
    images = [_ico_bmp(s, _brand_pixel) for s in sizes]
    offset = 6 + 16 * len(images)
    entries = bytearray()
    for size, data in zip(sizes, images, strict=False):
        dim = 0 if size >= 256 else size
        entries.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + bytes(entries) + b"".join(images))


def main() -> int:
    out = Path(__file__).resolve().parent / "icons"
    out.mkdir(parents=True, exist_ok=True)
    for size, name in ((32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png")):
        (out / name).write_bytes(_png(size, size, _brand_pixel))
    write_ico(out / "icon.ico")
    made = sorted(p.name for p in out.iterdir())
    print(f"[icons] wrote {out}: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
