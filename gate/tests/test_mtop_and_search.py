"""签名/查价解析单测。"""
import json
from pathlib import Path

from xy_gate.mtop import generate_sign, find_key_recursive
from xy_gate.search import parse_money, parse_search_item, price_stats

FIXTURES = Path(__file__).parent / "fixtures"


def test_sign_vector():
    # 固定向量：防止签名算法被无意改动
    expected = generate_sign("1700000000000", "abctoken", '{"k":1}')
    import hashlib
    manual = hashlib.md5("abctoken&1700000000000&34839810&{\"k\":1}".encode()).hexdigest()
    assert expected == manual


def test_find_key_recursive():
    obj = {"a": [{"orderId": "123"}, {"x": 1}]}
    assert find_key_recursive(obj, "orderId") == "123"
    assert find_key_recursive(obj, "missing") is None


def test_parse_money():
    assert parse_money("¥1,299") == 1299.0
    assert parse_money("1299") == 1299.0
    assert parse_money("1.2万") == 12000.0
    assert parse_money("当前价¥88") == 88.0
    assert parse_money("") is None
    assert parse_money("面议") is None


def test_parse_search_item_variants():
    # 形态1：data.item.main.exContent
    item1 = {"data": {"item": {"main": {"exContent": {
        "title": "Switch OLED 日版",
        "price": [{"text": "¥"}, {"text": "1,299"}],
        "fishTags": {"tag1": "12人想要"},
        "picUrl": "//img.alicdn.com/x.jpg",
        "clickParam": {"args": {"item_id": "111"}},
    }}}}}
    p1 = parse_search_item(item1)
    assert p1["title"] == "Switch OLED 日版"
    assert p1["price"] == 1299.0
    assert p1["want_count"] == 12
    assert p1["item_id"] == "111"
    assert p1["pic_url"].startswith("https://")

    # 形态2：main 直挂根
    item2 = {"main": {"title": "键盘", "price": [{"text": "99"}],
                      "clickParam": {"args": {"id": "222"}}}}
    p2 = parse_search_item(item2)
    assert p2["price"] == 99.0 and p2["item_id"] == "222"


def test_price_stats():
    items = [{"price": 10.0}, {"price": 20.0}, {"price": 30.0}, {"price": 40.0},
             {"price": None}, {"price": 50.0}]
    stats = price_stats(items)
    assert stats["count"] == 5
    assert stats["min"] == 10.0 and stats["max"] == 50.0
    assert stats["median"] == 30.0
    assert stats["p25"] <= stats["median"] <= stats["p75"]
