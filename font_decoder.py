"""学习通测验页字体加密解码（字形哈希 → 真字）。

字体表来自开源项目 nekro_chaoxing_study 的 font_map_table.json，
运行时随 exe 一起打包。依赖 fontTools。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import unicodedata
from io import BytesIO

try:
    from fontTools.ttLib import TTFont
except ImportError:  # pragma: no cover
    TTFont = None

_TABLE = None


def _resource_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _table() -> dict:
    global _TABLE
    if _TABLE is None:
        path = os.path.join(_resource_dir(), "font_map_table.json")
        try:
            with open(path, encoding="utf-8") as handle:
                _TABLE = json.load(handle)
        except (OSError, json.JSONDecodeError):
            _TABLE = {}
    return _TABLE


def has_encryption(html: str) -> bool:
    return 'id="cxSecretStyle"' in html or "id='cxSecretStyle'" in html


def _hash_glyph(glyph) -> str:
    if glyph.numberOfContours <= 0:
        return ""
    parts = []
    last = 0
    for index in range(glyph.numberOfContours):
        end = glyph.endPtsOfContours[index]
        for point in range(last, end + 1):
            x, y = glyph.coordinates[point]
            flag = glyph.flags[point] & 0x01
            parts.append(f"{x}{y}{flag}")
        last = end + 1
    return hashlib.md5("".join(parts).encode()).hexdigest()


def make_decoder(html: str):
    """从页面里提取加密字体，返回 text -> text 的解码函数；失败返回 None。"""
    if TTFont is None or not has_encryption(html):
        return None
    style = re.search(r"<style[^>]*id=[\"']cxSecretStyle[\"'][^>]*>(.*?)</style>", html, re.S)
    if not style:
        return None
    encoded = re.search(r"base64,([^)'\"]+)", style.group(1))
    if not encoded:
        return None
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", encoded.group(1)))
        font = TTFont(BytesIO(raw), lazy=False)
        glyph_map = {}
        for name in font.getGlyphOrder():
            if name.startswith("uni"):
                value = _hash_glyph(font["glyf"].glyphs[name])
                if value:
                    glyph_map[name] = value
    except Exception:  # noqa: BLE001
        return None
    if not glyph_map:
        return None
    reverse = {}
    for name, value in _table().items():
        reverse.setdefault(value, name)

    def decode(text: str) -> str:
        result = []
        for char in text:
            glyph_name = glyph_map.get(f"uni{ord(char):X}")
            original = reverse.get(glyph_name) if glyph_name else None
            if original:
                if original.startswith("uni"):
                    try:
                        result.append(chr(int(original[3:], 16)))
                        continue
                    except ValueError:
                        pass
                elif len(original) == 1:
                    result.append(original)
                    continue
            result.append(char)
        text = "".join(result)
        fixed = []
        for char in text:
            code = ord(char)
            if 0x2E80 <= code <= 0x2FDF or 0xF900 <= code <= 0xFAFF:
                fixed.append(unicodedata.normalize("NFKC", char))
            else:
                fixed.append(char)
        return "".join(fixed)

    return decode
