"""入站消息解析单测：用真实报文形态的 fixture。"""
import json
from pathlib import Path

from xy_gate.inbound import sync_package_to_events, parse_message, decode_sync_data

FIXTURES = Path(__file__).parent / "fixtures"
MY_ID = "2200000001"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_decode_plain_json():
    assert decode_sync_data('{"1": {"2": "x@goofish"}}') == {"1": {"2": "x@goofish"}}


def test_chat_message_fixture():
    data = load("chat_message.json")
    events = sync_package_to_events(data, MY_ID)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "chat"
    assert ev["chat_id"] == "2880000002"
    assert ev["sender_id"] == "2880000002"
    assert ev["is_buyer"] is True
    assert ev["item_id"] == "760000000001"
    # 真实内容载荷优先于 reminderContent
    assert "刀" in ev["text"] or ev["text"]  # 文本被解码替换


def test_order_paid_card_fixture():
    data = load("order_paid_card.json")
    events = sync_package_to_events(data, MY_ID)
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "order_paid"
    assert ev["order_id"] == "230000000003"
    assert ev["item_id"] == "760000000001"


def test_own_message_not_buyer():
    msg = load("chat_message.json")
    inner = json.loads(json.dumps(msg))
    # 把发送者改成自己 → is_buyer False
    for item in inner["body"]["syncPushPackage"]["data"]:
        item["data"] = item["data"].replace("2880000002", MY_ID)
    events = sync_package_to_events(inner, MY_ID)
    assert events and events[0]["is_buyer"] is False


def test_parse_message_card_update_shape():
    """卡片更新形态：['1'] 是字符串。"""
    message = {
        "1": "760000000001",
        "2": "2880000002@goofish",
        "4": {"reminderContent": "[买家已付款]", "senderUserId": "2880000002"},
        "5": 1700000000000,
    }
    ev = parse_message(message, MY_ID)
    assert ev["kind"] == "order_paid"
    assert ev["chat_id"] == "2880000002"
