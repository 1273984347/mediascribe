#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断 whisper.cpp 官方预构建 WASM（main.js 内嵌的 wasm 二进制）的类型段。

用途：定位 Chrome 报 ``CompileError: WebAssembly.instantiate(): unknown type
form: 106`` 的确切原因 —— 逐个函数类型打印 form 字节，找出 Chrome 不认的那个。

背景（2026-10-23 实测）：官方预构建的 ``whisper.wasm`` 在 Chrome 155 上无法
实例化；Node（同一 V8）报 ``section was shorter than expected size``。
本脚本从 JS 字符串里还原原始二进制并解析类型段，以确定是**类型形式不被支持**
（WASM GC 提案等）还是**二进制被截断**。

用法::

    python diag_wasm.py [path/to/main.js]
"""

from __future__ import annotations

import pathlib
import sys


def extract_wasm(js: bytes) -> bytes:
    """从 main.js 里把 ``binaryDecode('...')`` 的 JS 字符串还原成原始 wasm 二进制。"""
    idx = js.find(b"binaryDecode('")
    if idx < 0:
        raise SystemExit("未找到 binaryDecode('...')")
    i = js.index(b"'", idx) + 1
    out = bytearray()
    while i < len(js):
        c = js[i : i + 1]
        if c == b"\\":
            nxt = js[i + 1 : i + 2]
            if nxt == b"x":
                out.append(int(js[i + 2 : i + 4], 16))
                i += 4
                continue
            if nxt == b"n":
                out.append(0x0A)
                i += 2
                continue
            if nxt == b"r":
                out.append(0x0D)
                i += 2
                continue
            if nxt == b"t":
                out.append(0x09)
                i += 2
                continue
            if nxt == b"'":
                out.append(0x27)
                i += 2
                continue
            if nxt == b"\\":
                out.append(0x5C)
                i += 2
                continue
            out.append(js[i + 1])
            i += 2
            continue
        if c == b"'":
            break
        out += c
        i += 1
    return bytes(out)


def read_uleb(data: bytes, pos: int) -> tuple[int, int]:
    """读一个 ULEB128 无符号整数，返回 (值, 新位置)。"""
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def main() -> int:
    path = pathlib.Path(
        sys.argv[1] if len(sys.argv) > 1 else r"D:\MediaScribe\output\bench\shell\b5-app\main.js"
    )
    js = path.read_bytes()
    wasm = extract_wasm(js)
    print(
        f"[wasm] 提取到 {len(wasm):,} 字节, magic={wasm[:4]!r}, version={int.from_bytes(wasm[4:8], 'little')}"
    )

    pos = 8
    type_forms: dict[int, int] = {}
    while pos < len(wasm):
        sec_id = wasm[pos]
        pos += 1
        size, pos = read_uleb(wasm, pos)
        body = wasm[pos : pos + size]
        if sec_id == 1:  # type section
            p = 0
            count, p = read_uleb(body, p)
            print(f"[wasm] 类型段: {count} 个类型")
            for idx in range(count):
                form = body[p]
                type_forms[form] = type_forms.get(form, 0) + 1
                p += 1
                if form == 0x60:  # func type
                    np_, p = read_uleb(body, p)
                    p += np_
                    nr, p = read_uleb(body, p)
                    p += nr
                else:
                    # 非函数类型（GC 提案的 struct/array/sub/rec 等）
                    print(
                        f"    类型[{idx}] form=0x{form:02X} ({form})  <== Chrome 不认识的类型形式"
                    )
                    break
        pos += size
    print(f"[wasm] 类型 form 统计: { {hex(k): v for k, v in type_forms.items()} }")
    if 0x60 not in type_forms or len(type_forms) > 1:
        print(
            "[wasm] 结论: 该二进制含非 0x60 (func) 的类型形式 —— 需要 Chrome 尚未默认开启的 "
            "WASM 提案 (GC/exnref 等)，故报 'unknown type form'"
        )
    else:
        print("[wasm] 结论: 类型段均为标准 func 类型 —— 报错来自别处 (见 section 解析)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
