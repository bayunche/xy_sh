"""MessagePack 解码器单测（手工构造字节流，按公开规范断言）。"""
import base64

from xy_gate.msgpack_min import decode_msgpack


def test_fixint_positive_and_negative():
    assert decode_msgpack(b"\x00") == 0
    assert decode_msgpack(b"\x7f") == 127
    assert decode_msgpack(b"\xff") == -1


def test_nil_bool():
    assert decode_msgpack(b"\xc0") is None
    assert decode_msgpack(b"\xc2") is False
    assert decode_msgpack(b"\xc3") is True


def test_fixstr_and_str8():
    assert decode_msgpack(b"\xa3abc") == "abc"
    # str8: 0xd9 len data
    s = "闲鱼测试"
    raw = s.encode("utf-8")
    assert decode_msgpack(b"\xd9" + bytes([len(raw)]) + raw) == s


def test_fixmap_nested():
    # fixmap(2){ "a": 1, "b": fixmap(1){ "c": fixstr "hi" } }
    data = b"\x82\xa1" + b"a" + b"\x01" + b"\xa1" + b"b" + b"\x81\xa1" + b"c" + b"\xa2hi"
    assert decode_msgpack(data) == {"a": 1, "b": {"c": "hi"}}


def test_fixarray():
    assert decode_msgpack(b"\x93\x01\x02\x03") == [1, 2, 3]


def test_uint16_and_array16():
    # uint16 300 = 0x012C
    assert decode_msgpack(b"\xcd\x01\x2c") == 300
    # array16 with 2 elems
    assert decode_msgpack(b"\xdc\x00\x02\x01\x02") == [1, 2]


def test_map16():
    data = b"\xde\x00\x01\xa1k\x01"
    assert decode_msgpack(data) == {"k": 1}


def test_truncated_raises():
    import pytest
    with pytest.raises(ValueError):
        decode_msgpack(b"\xa5ab")   # fixstr 声明 5 字节但只给了 2


def test_real_sync_like_payload():
    """模拟真实 sync 负载：map{ "1": map{ "2": str, "5": uint } }。"""
    inner = (b"\x82"                    # fixmap(2)
             b"\xa1\x32" + b"\xad12345@goofish"   # "2": fixstr(13)
             b"\xa1\x35" + b"\xd1\x1c\x49")       # "5": int16
    payload = b"\x81\xa1\x31" + inner   # map{ "1": inner }
    assert decode_msgpack(payload)["1"]["2"] == "12345@goofish"
