"""入站消息解析：WS syncPushPackage → 结构化事件 dict。

报文层级（详见 docs/protocol-notes.md）：
- body.syncPushPackage.data[] 每项的 data 字段：明文 JSON 或 base64(MessagePack)
- 解出的消息以数字字符串为 key（"1"/"2"/...），闲鱼侧不同消息类型字段位置不同：
  * 普通聊天：["1"]["10"] 有 reminderContent/senderUserId/senderNick；
    会话 ["1"]["2"]（xxx@goofish），时间 ["1"]["5"]（毫秒），
    真实内容载荷 ["1"]["6"]["3"]["5"]（JSON 串）或 ["1"]["6"]["3"]["1"]（base64）
  * 卡片消息（订单/评价/系统）：["1"]["1"]["1"] 发送者，["1"]["6"]["3"]["2"] 文本
  * 卡片更新：["1"] 为字符串，会话 ["2"]，内容 ["4"]["reminderContent"]
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional

from .msgpack_min import decode_msgpack

# 买家已付款的系统卡片文案（命中即视为 order_paid 事件）
PAID_MARKERS = (
    "[我已付款，等待你发货]",
    "[买家已付款]",
    "[已付款，待发货]",
    "[我已付款，请添加自提信息]",
)
REFUND_MARKERS = ("退款", "售后")


def is_sync_package(message_data: dict) -> bool:
    body = message_data.get("body") if isinstance(message_data, dict) else None
    if not isinstance(body, dict):
        return False
    spp = body.get("syncPushPackage")
    return isinstance(spp, dict) and isinstance(spp.get("data"), list) and len(spp["data"]) > 0


def decode_sync_data(data_field: Any) -> Optional[dict]:
    """解码 data 字段：明文 JSON → base64+MessagePack 双保险。"""
    if isinstance(data_field, dict):
        return data_field
    s = str(data_field or "")
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        padded = s + "=" * (-len(s) % 4)
        decoded = decode_msgpack(base64.b64decode(padded))
        if isinstance(decoded, dict):
            # bytes 值统一转字符串，方便后续 JSON 序列化
            return _strings(decoded)
    except Exception:
        pass
    return None


def _strings(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")
    if isinstance(obj, dict):
        return {k: _strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strings(v) for v in obj]
    return obj


def _strip_chat_id(raw: Any) -> str:
    s = str(raw or "")
    return s.split("@")[0] if "@" in s else s


def _fmt_time(ms: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ms) / 1000))
    except (TypeError, ValueError):
        return time.strftime("%Y-%m-%d %H:%M:%S")


def _find_order_id(message: dict) -> str:
    """在报文中递归找订单号（orderId/bizOrderId/orderIdStr）。

    卡片消息的载荷（["1"]["6"]["3"]["5"] 等）是 JSON 字符串，遍历时需要
    试着把长得像 JSON 的字符串解析出来再找。
    """
    from collections import deque

    def visit(obj: Any) -> Optional[str]:
        queue = deque([obj])
        while queue:
            cur = queue.popleft()
            if isinstance(cur, dict):
                for k in ("bizOrderIdStr", "orderIdStr", "bizOrderId", "orderId"):
                    v = cur.get(k)
                    if v not in ("", None):
                        return str(v).split(".")[0]
                queue.extend(cur.values())
            elif isinstance(cur, list):
                queue.extend(cur)
            elif isinstance(cur, str) and cur[:1] in "{[":
                try:
                    parsed = json.loads(cur)
                except (json.JSONDecodeError, ValueError):
                    continue
                found = visit(parsed)
                if found:
                    return found
        return None

    found = visit(message)
    return found or ""


def _extract_item_id(message: dict) -> str:
    m1 = message.get("1") or {}
    m10 = m1.get("10") if isinstance(m1, dict) else {}

    def _from_json_field(s: Any) -> str:
        try:
            d = json.loads(s) if isinstance(s, str) and s.strip() else {}
            return str(d.get("itemId") or "") if isinstance(d, dict) else ""
        except (json.JSONDecodeError, TypeError):
            return ""

    if isinstance(m10, dict):
        url = str(m10.get("reminderUrl") or "")
        if "itemId=" in url:
            return url.split("itemId=")[1].split("&")[0]
        for field in ("bizTag", "extJson"):
            item = _from_json_field(m10.get(field))
            if item:
                return item
    return ""


def _decode_content_payload(message: dict) -> Optional[dict]:
    """真实内容载荷 ["1"]["6"]["3"]：["5"] 为 JSON 串，["1"] 为 base64(JSON)。"""
    m1 = message.get("1") or {}
    m6 = m1.get("6") if isinstance(m1, dict) else {}
    m63 = m6.get("3") if isinstance(m6, dict) else {}
    if not isinstance(m63, dict):
        return None
    for key in ("5", "1", "2"):
        raw = m63.get(key)
        if not raw:
            continue
        s = str(raw)
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            try:
                obj = json.loads(base64.b64decode(s + "=" * (-len(s) % 4)))
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    return None


def _interpret_content(content: dict) -> tuple[str, List[str]]:
    """从内容载荷提取 (文本, 图片URL列表)。"""
    images: List[str] = []
    text = ""
    text_node = content.get("text")
    if isinstance(text_node, dict) and isinstance(text_node.get("text"), str):
        text = text_node["text"]
    elif isinstance(text_node, str):
        text = text_node

    def _collect(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("picUrl", "imageUrl") and isinstance(v, str) and v:
                    images.append(v)
                elif k in ("picUrls", "imageUriList", "imageUrls") and isinstance(v, list):
                    images.extend(str(x) for x in v if x)
                else:
                    _collect(v)
        elif isinstance(obj, list):
            for x in obj:
                _collect(x)

    _collect(content)
    return text, images


def parse_message(message: dict, my_id: str) -> Optional[Dict[str, Any]]:
    """把解密后的单条消息解析为事件 dict；无法识别返回 None。"""
    m1 = message.get("1")
    if isinstance(m1, str):
        # 卡片更新消息：["1"] 是字符串
        m4 = message.get("4") or {}
        text = str(m4.get("reminderContent") or "") if isinstance(m4, dict) else ""
        sender = str(m4.get("senderUserId") or "") if isinstance(m4, dict) else ""
        name = str(m4.get("reminderTitle") or "系统") if isinstance(m4, dict) else "系统"
        chat_id = _strip_chat_id(message.get("2"))
        ts = _fmt_time(message.get("5"))
    elif isinstance(m1, dict):
        m10 = m1.get("10") or {}
        chat_id = _strip_chat_id(m1.get("2"))
        ts = _fmt_time(m1.get("5"))
        if isinstance(m10, dict) and m10.get("reminderContent"):
            sender = str(m10.get("senderUserId") or "")
            name = str(m10.get("senderNick") or m10.get("reminderTitle") or "系统")
            text = str(m10.get("reminderContent") or "")
        else:
            m11 = m1.get("1") or {}
            sender = _strip_chat_id(m11.get("1")) if isinstance(m11, dict) else ""
            name = "系统"
            m63 = (m1.get("6") or {}).get("3") if isinstance(m1.get("6"), dict) else {}
            text = str(m63.get("2") or "") if isinstance(m63, dict) else ""
    else:
        return None

    images: List[str] = []
    content = _decode_content_payload(message)
    if content:
        c_text, c_images = _interpret_content(content)
        if c_text:
            text = c_text
        images = c_images

    sender = sender or "unknown"
    order_id = _find_order_id(message)
    msg_id = ""
    m10 = (message.get("1") or {}).get("10") if isinstance(message.get("1"), dict) else {}
    if isinstance(m10, dict):
        for field in ("bizTag", "extJson"):
            try:
                d = json.loads(m10.get(field) or "")
                if isinstance(d, dict) and d.get("messageId"):
                    msg_id = str(d["messageId"])
                    break
            except (json.JSONDecodeError, TypeError):
                continue

    kind = "chat"
    if any(marker in text for marker in PAID_MARKERS):
        kind = "order_paid"
    elif any(marker in text for marker in REFUND_MARKERS) and sender in ("", "系统", "unknown"):
        kind = "order_refund"

    return {
        "kind": kind,
        "chat_id": chat_id,
        "sender_id": sender,
        "sender_name": name,
        "text": text,
        "images": images,
        "item_id": _extract_item_id(message),
        "order_id": order_id,
        "msg_time": ts,
        "msg_id": msg_id,
        "is_buyer": sender != my_id and sender != "系统" and sender != "unknown",
        "raw": message,
    }


def sync_package_to_events(message_data: dict, my_id: str) -> List[Dict[str, Any]]:
    """整包 syncPushPackage → 事件列表（解析失败的单条跳过）。"""
    events: List[Dict[str, Any]] = []
    if not is_sync_package(message_data):
        return events
    for item in message_data["body"]["syncPushPackage"]["data"]:
        decoded = decode_sync_data(item.get("data") if isinstance(item, dict) else item)
        if not decoded:
            continue
        ev = parse_message(decoded, my_id)
        if ev:
            events.append(ev)
    return events
