"""极简 MessagePack 解码器（仅解码，闲鱼 sync 包负载解压用）。

闲鱼 WS syncPushPackage.data 的负载是 base64(MessagePack)。闲鱼侧只使用
MessagePack 规范的一个子集，这里按公开规范实现常用格式：
fixint/fixmap/fixarray/fixstr、nil/bool、bin/str 8-32、array/map 16-32、
uint/int 8-64、float 32/64。未知格式字节抛 ValueError。
"""
from __future__ import annotations

import struct
from typing import Any, List


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("MessagePack 数据不完整（读到末尾）")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]


def _decode(r: _Reader) -> Any:
    b = r.u8()
    if b <= 0x7F:                       # 正 fixint
        return b
    if b >= 0xE0:                       # 负 fixint
        return b - 0x100
    if 0x80 <= b <= 0x8F:               # fixmap
        return _map(r, b & 0x0F)
    if 0x90 <= b <= 0x9F:               # fixarray
        return _array(r, b & 0x0F)
    if 0xA0 <= b <= 0xBF:               # fixstr
        return r.take(b & 0x1F).decode("utf-8", errors="ignore")
    if b == 0xC0:
        return None
    if b == 0xC2:
        return False
    if b == 0xC3:
        return True
    if b == 0xC4:
        return r.take(r.u8())           # bin8
    if b == 0xC5:
        return r.take(r.u16())          # bin16
    if b == 0xC6:
        return r.take(r.u32())          # bin32
    if b == 0xCA:
        return struct.unpack(">f", r.take(4))[0]
    if b == 0xCB:
        return struct.unpack(">d", r.take(8))[0]
    if b == 0xCC:
        return r.u8()
    if b == 0xCD:
        return r.u16()
    if b == 0xCE:
        return r.u32()
    if b == 0xCF:
        return r.u64()
    if b == 0xD0:
        return struct.unpack(">b", r.take(1))[0]
    if b == 0xD1:
        return struct.unpack(">h", r.take(2))[0]
    if b == 0xD2:
        return struct.unpack(">i", r.take(4))[0]
    if b == 0xD3:
        return struct.unpack(">q", r.take(8))[0]
    if b == 0xD9:
        return r.take(r.u8()).decode("utf-8", errors="ignore")
    if b == 0xDA:
        return r.take(r.u16()).decode("utf-8", errors="ignore")
    if b == 0xDB:
        return r.take(r.u32()).decode("utf-8", errors="ignore")
    if b == 0xDC:
        return _array(r, r.u16())
    if b == 0xDD:
        return _array(r, r.u32())
    if b == 0xDE:
        return _map(r, r.u16())
    if b == 0xDF:
        return _map(r, r.u32())
    raise ValueError(f"未支持的 MessagePack 格式字节: 0x{b:02x}")


def _map(r: _Reader, size: int) -> dict:
    out: dict = {}
    for _ in range(size):
        key = _decode(r)
        out[key if isinstance(key, (str, int)) else str(key)] = _decode(r)
    return out


def _array(r: _Reader, size: int) -> List[Any]:
    return [_decode(r) for _ in range(size)]


def decode_msgpack(data: bytes) -> Any:
    r = _Reader(data)
    value = _decode(r)
    return value
