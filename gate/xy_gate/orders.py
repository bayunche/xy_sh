"""订单与确认交易（卖家侧）。

- 已卖出订单列表：mtop.taobao.idle.trade.merchant.sold.get（queryCode: ALL / NOT_SHIP）
- 订单详情：mtop.idle.web.trade.order.detail（返回原始 JSON，字段随闲鱼版本浮动，
  大脑可直接读原始结构）
- 确认交易（无需邮寄/虚拟发货）：mtop.taobao.idle.logistic.consign.dummy
  data={"orderId":..., "tradeText":"", "picList":[], "newUnconsign":true}
- 小刀（砍价成交）订单免拼：mtop.idle.groupon.activity.seller.freeshipping
  data={"bizOrderId":"...", "itemId":<int>, "buyerId":<int>}
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .mtop import MtopClient, MtopError, find_key_recursive

SOLD_API = "mtop.taobao.idle.trade.merchant.sold.get"
DETAIL_API = "mtop.idle.web.trade.order.detail"
CONSIGN_DUMMY_API = "mtop.taobao.idle.logistic.consign.dummy"
FREESHIPPING_API = "mtop.idle.groupon.activity.seller.freeshipping"

SELLER_REFERER = "https://seller.goofish.com/"


class FreeShippingError(MtopError):
    """免拼确认失败。"""


def _ret_str(res: Dict[str, Any]) -> str:
    ret = res.get("ret") or [""]
    return str(ret[0]) if ret else ""


async def sold_orders(client: MtopClient, page: int = 1, rows: int = 20, query_code: str = "ALL") -> Dict[str, Any]:
    """已卖出订单。v1 不做深度字段解析（闲鱼该接口结构随版本变动大），
    返回 {success, orders(尽量提取的常用字段), raw(data 节点原文)}。"""
    res = await client.call(SOLD_API, "1.0", {
        "pageNumber": page,
        "rowsPerPage": rows,
        "orderIds": "",
        "queryCode": query_code,
        "orderSearchParam": "{}",
    }, referer=SELLER_REFERER, extra_headers={"idle_site_biz_code": "COMMONPRO"})
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    orders_raw = find_key_recursive(data, "orders", "orderList", "list") or []
    orders = []
    if isinstance(orders_raw, list):
        for o in orders_raw:
            if not isinstance(o, dict):
                continue
            orders.append({
                "order_id": str(find_key_recursive(o, "bizOrderIdStr", "orderIdStr", "bizOrderId", "orderId") or ""),
                "item_id": str(find_key_recursive(o, "itemId") or ""),
                "buyer_id": str(find_key_recursive(o, "buyerUserId", "buyerId") or ""),
                "price": find_key_recursive(o, "actualPayFee", "payFee", "price"),
                "status_text": str(find_key_recursive(o, "statusText", "orderStatusText") or ""),
                "title": str(find_key_recursive(o, "itemTitle", "title") or ""),
            })
    return {"success": "SUCCESS" in _ret_str(res), "orders": orders, "raw": data}


async def order_detail(client: MtopClient, order_id: str) -> Dict[str, Any]:
    """订单详情（原始返回；金额字段位置随版本浮动，由调用方/大脑自行读取）。"""
    res = await client.call(DETAIL_API, "1.0", {"orderId": str(order_id)},
                            extra_params={"spm_cnt": "a21ybx.order-detail.0.0"},
                            referer=SELLER_REFERER)
    return {"success": "SUCCESS" in _ret_str(res), "raw": res.get("data") or {}}


async def confirm_consign(
    client: MtopClient, order_id: str, trade_text: str = "",
) -> Dict[str, Any]:
    """确认交易：无需邮寄发货（consign.dummy）。已发货视为幂等成功。

    ⚠️ 写操作：必须先过 risk.RiskGate.check_confirm；dry-run 模式下由
    daemon 层拦截，本函数不做闸门判断（CLI/HTTP 会再校验一次）。
    """
    data_val = json.dumps({"orderId": str(order_id), "tradeText": trade_text or "",
                           "picList": [], "newUnconsign": True},
                          ensure_ascii=False, separators=(",", ":"))
    res = await client.call(CONSIGN_DUMMY_API, "1.0", data_val, referer=SELLER_REFERER)
    ret = _ret_str(res)
    if "SUCCESS" in ret or "SUCCESS::调用成功" in ret:
        return {"success": True, "order_id": order_id, "message": ret}
    if "ORDER_ALREADY_DELIVERY" in ret or "已发货成功" in ret:
        return {"success": True, "order_id": order_id, "already_delivered": True, "message": ret}
    raise MtopError(f"确认发货失败: {ret}")


async def agree_freeshipping(client: MtopClient, order_id: str, item_id: str, buyer_id: str) -> Dict[str, Any]:
    """小刀（砍价成交）订单免拼确认。itemId/buyerId 必须是数字串。"""
    data_val = json.dumps({"bizOrderId": str(order_id), "itemId": int(item_id),
                           "buyerId": int(buyer_id)}, separators=(",", ":"))
    res = await client.call(FREESHIPPING_API, "1.0", data_val, referer=SELLER_REFERER)
    ret = _ret_str(res)
    if "SUCCESS" in ret or "ORDER_ALREADY_DELIVERY" in ret or "已发货成功" in ret:
        return {"success": True, "order_id": order_id, "message": ret}
    raise FreeShippingError(f"免拼确认失败: {ret}")
