"""纯 Python AES-128-CBC（仅加密）。

超星学习通的网页登录接口要求对账号和密码做 AES-128-CBC 加密再 base64，
这里用标准实现，不依赖 pycryptodome / cryptography，保证零安装。
"""

from __future__ import annotations

import base64
import hashlib

SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)

RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _gmul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _expand_key(key: bytes) -> list[list[int]]:
    words = [list(key[i * 4:(i + 1) * 4]) for i in range(4)]
    for i in range(4, 44):
        temp = words[i - 1][:]
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // 4 - 1]
        words.append([words[i - 4][j] ^ temp[j] for j in range(4)])
    return [sum(words[r * 4:(r + 1) * 4], []) for r in range(11)]


def _shift_rows(s: list[int]) -> list[int]:
    out = [0] * 16
    for c in range(4):
        for r in range(4):
            out[4 * c + r] = s[4 * ((c + r) & 3) + r]
    return out


def _mix_columns(s: list[int]) -> list[int]:
    out = [0] * 16
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c:4 * c + 4]
        out[4 * c + 0] = _gmul(a0, 2) ^ _gmul(a1, 3) ^ a2 ^ a3
        out[4 * c + 1] = a0 ^ _gmul(a1, 2) ^ _gmul(a2, 3) ^ a3
        out[4 * c + 2] = a0 ^ a1 ^ _gmul(a2, 2) ^ _gmul(a3, 3)
        out[4 * c + 3] = _gmul(a0, 3) ^ a1 ^ a2 ^ _gmul(a3, 2)
    return out


class AES128:
    """AES-128 单块加密。状态按列优先排列，与标准 test vector 对齐。"""

    def __init__(self, key: bytes):
        if len(key) != 16:
            raise ValueError("AES-128 key must be 16 bytes")
        self.round_keys = _expand_key(key)

    def encrypt_block(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("block must be 16 bytes")
        s = [b ^ k for b, k in zip(block, self.round_keys[0])]
        for rnd in range(1, 10):
            s = [SBOX[b] for b in s]
            s = _shift_rows(s)
            s = _mix_columns(s)
            s = [b ^ k for b, k in zip(s, self.round_keys[rnd])]
        s = [SBOX[b] for b in s]
        s = _shift_rows(s)
        s = [b ^ k for b, k in zip(s, self.round_keys[10])]
        return bytes(s)


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    aes = AES128(key)
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        block = bytes(x ^ y for x, y in zip(data[i:i + 16], prev))
        prev = aes.encrypt_block(block)
        out += prev
    return bytes(out)


# 超星网页登录使用的固定密钥（与页面脚本一致）
CHAOXING_AES_KEY = b"u2oh6Vu^HWe4_AES"


def encrypt_login(text: str) -> str:
    """把账号/密码加密成超星登录接口需要的 base64 串。"""
    raw = aes_cbc_encrypt(CHAOXING_AES_KEY, CHAOXING_AES_KEY, text.encode("utf-8"))
    return base64.b64encode(raw).decode("ascii")


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _selftest() -> None:
    # FIPS-197 附录 C.1
    key = bytes(range(16))
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    ct = AES128(key).encrypt_block(pt)
    assert ct.hex() == "69c4e0d86a7b0430d8cdb78070b4c55a", ct.hex()

    # NIST SP 800-38A AES-128-CBC 第一块
    key2 = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv2 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pt2 = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    out = aes_cbc_encrypt(key2, iv2, pt2)
    assert out[:16].hex() == "7649abac8119b246cee98e9b12e9197d", out[:16].hex()

    # 登录加密输出长度（base64，16 字节块）
    sample = encrypt_login("test")
    assert len(base64.b64decode(sample)) == 16
    print("crypto selftest OK")


if __name__ == "__main__":
    _selftest()
