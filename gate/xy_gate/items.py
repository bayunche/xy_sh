"""商品运营（卖侧）：在售列表 / 改价 / 下架 / 评价 / 能力探测。

权限地图（2026-09 实测，详见 docs/protocol-notes.md）：
- 个人版在售列表 mtop.idle.web.xyh.item.list —— 无需鱼小铺，普通账号可用 ✅
- 卖家平台系（改价 info.update / 下架 batch.offline / 订单 sold.get）——
  需要账号开通鱼小铺（卖家工作台），未开通时返回 FAIL_BIZ_IDLE_USER_UNAUTHORIZED
- 自动评价 mtop.taobao.idle.rate.create —— 需已完成订单的 tradeId，权限未验证
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .mtop import MtopClient, MtopError

# 个人版（www.goofish.com，无需鱼小铺）
MY_ITEMS_API = "mtop.idle.web.xyh.item.list"

# 卖家平台系（需鱼小铺）
SELLER_PRICE_API = "mtop.alibaba.idle.seller.pc.item.info.update"
SELLER_OFFLINE_API = "mtop.alibaba.idle.seller.pc.item.batch.offline"
SELLER_LIST_API = "mtop.alibaba.idle.seller.pc.common.item.search"
SELLER_REFERER = "https://seller.goofish.com/?site=COMMONPRO"
SELLER_HEADERS = {"idle_site_biz_code": "COMMONPRO", "idle_user_group_member_id": ""}

RATE_API = "mtop.taobao.idle.rate.create"

# 未开通鱼小铺时卖家平台系的标志性错误
FISH_SHOP_MARKERS = ("FAIL_BIZ_IDLE_USER_UNAUTHORIZED", "PERMISSION_EXCEPTION")


def _ret_str(res: Dict[str, Any]) -> str:
    ret = res.get("ret") or [""]
    return str(ret[0]) if ret else ""


def _needs_fish_shop(ret: str) -> bool:
    return any(m in ret for m in FISH_SHOP_MARKERS)


class FishShopRequiredError(MtopError):
    """账号未开通鱼小铺（卖家工作台），该接口族不可用。"""


async def my_items(client: MtopClient, user_id: str, page: int = 1,
                   rows: int = 20) -> Dict[str, Any]:
    """个人版在售商品列表（含标题/挂价/主图，分页）。

    经实测：groupId 必须带参考项目的魔数 '58877261' 且 defaultGroup=True
    （groupId 传空串会稳定返回空 cardList）；pageSize 上限 20，超过会**静默
    返回空**；偶发空返回时重试一次。
    """

    rows = min(rows, 20)   # 服务端上限，超过静默变空

    def _parse(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for card in data.get("cardList") or []:
            cd = (card.get("cardData") or {})
            dp = cd.get("detailParams") or {}
            price_text = str(dp.get("soldPrice") or "")
            try:
                price = float(price_text)
            except (TypeError, ValueError):
                price = None
            items.append({
                "item_id": str(card.get("id") or dp.get("itemId") or ""),
                "title": str(dp.get("title") or ""),
                "price": price,
                "price_text": price_text,
                "post_info": str(dp.get("postInfo") or ""),
                "pic_url": str(dp.get("picUrl") or ""),
            })
        return items

    payload = {"needGroupInfo": True, "pageNumber": page, "pageSize": rows,
               "groupName": "在售", "groupId": "58877261", "defaultGroup": True,
               "userId": user_id}
    res = await client.call(MY_ITEMS_API, "1.0", payload,
                            extra_params={"spm_cnt": "a21ybx.im.0.0",
                                          "spm_pre": "a21ybx.collection.menu.1"})
    data = res.get("data") or {}
    items = _parse(data)
    if not items:   # 服务端偶发空返回，重试一次
        res = await client.call(MY_ITEMS_API, "1.0", payload,
                                extra_params={"spm_cnt": "a21ybx.im.0.0",
                                              "spm_pre": "a21ybx.collection.menu.1"})
        data = res.get("data") or {}
        items = _parse(data)
    return {"success": True, "items": items,
            "total": data.get("totalCount"), "has_next": bool(data.get("nextPage"))}


async def seller_items(client: MtopClient, page: int = 1, rows: int = 20) -> Dict[str, Any]:
    """卖家平台商品列表（需鱼小铺；含库存与状态）。"""
    try:
        res = await client.call(SELLER_LIST_API, "1.0", {
            "pageNo": page, "pageSize": rows, "bizType": "commonPro",
            "searchRequest": "{}", "itemStatus": "0",
        }, extra_params={"spm_cnt": "a21107h.42826273.0.0", "needLoginPC": "true"},
           extra_headers=SELLER_HEADERS, referer=SELLER_REFERER)
    except MtopError as e:
        raise FishShopRequiredError(str(e)) if _needs_fish_shop(str(e)) else e
    biz = ((res.get("data") or {}).get("data") or {})
    items = [{
        "item_id": str(r.get("itemId") or ""), "title": str(r.get("title") or ""),
        "price": r.get("reservePrice"), "quantity": r.get("quantity"),
        "status": r.get("itemStatusDesc") or r.get("itemStatus"),
    } for r in biz.get("itemSearchResponseList") or [] if isinstance(r, dict)]
    return {"success": True, "items": items, "has_next": bool(biz.get("hasNextPage"))}


async def update_price(client: MtopClient, item_id: str, price_yuan: float,
                       quantity: int = 1) -> Dict[str, Any]:
    """改价（单规格，单位元；需鱼小铺）。多规格走 itemSkuListStr，v1 不做。

    ⚠️ 写操作：必须先过 risk.check_reprice。
    """
    data = {"itemId": str(item_id), "quantity": int(quantity), "price": price_yuan}
    try:
        res = await client.call(SELLER_PRICE_API, "1.0", data,
                                extra_headers=SELLER_HEADERS, referer=SELLER_REFERER)
    except MtopError as e:
        raise FishShopRequiredError(str(e)) if _needs_fish_shop(str(e)) else e
    inner = res.get("data") if isinstance(res.get("data"), dict) else {}
    ok = str(inner.get("code") or "").lower() == "success" or inner.get("data") is True
    if ok:
        return {"success": True, "item_id": item_id, "price": price_yuan}
    raise MtopError(f"改价业务失败: {json.dumps(inner, ensure_ascii=False)[:300]}")


async def offline_items(client: MtopClient, item_ids: List[str]) -> Dict[str, Any]:
    """批量下架（需鱼小铺）。⚠️ 写操作。"""
    ids = ",".join(str(i) for i in item_ids if str(i).strip())
    if not ids:
        raise MtopError("缺少 item_ids")
    try:
        res = await client.call(SELLER_OFFLINE_API, "1.0", {"itemIds": ids},
                                extra_headers=SELLER_HEADERS, referer=SELLER_REFERER)
    except MtopError as e:
        raise FishShopRequiredError(str(e)) if _needs_fish_shop(str(e)) else e
    return {"success": "SUCCESS" in _ret_str(res), "item_ids": ids,
            "ret": _ret_str(res)}


async def rate_buyer(client: MtopClient, trade_id: str, feedback: str) -> Dict[str, Any]:
    """自动评价（好评）。⚠️ 写操作；tradeId 为已完成订单的交易号。"""
    res = await client.call(RATE_API, "4.0", {
        "tradeId": str(trade_id), "rate": 1,
        "feedback": feedback or "不错的买家，交易愉快", "createOrAppend": 0,
    })
    return {"success": "SUCCESS" in _ret_str(res), "ret": _ret_str(res)}


async def capability_probe(client: MtopClient, user_id: str) -> Dict[str, Any]:
    """探测各接口族可用性（全部只读或空参探测，不改任何数据）。

    返回 {family: {ok, reason}}，未开鱼小铺的族会给出开通指引。
    """
    out: Dict[str, Any] = {}

    async def probe(name: str, fn) -> None:
        try:
            r = await fn()
            out[name] = {"ok": bool(r.get("success")), "detail": r.get("ret", "")}
        except FishShopRequiredError as e:
            out[name] = {"ok": False, "needs_fish_shop": True, "detail": str(e)[:120]}
        except MtopError as e:
            out[name] = {"ok": False, "detail": str(e)[:120]}
        except Exception as e:  # noqa: BLE001
            out[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"[:120]}

    async def _probe_im():
        r = await client.call("mtop.taobao.idlemessage.pc.login.token", "1.0",
                              {"appKey": "444e9908a51d1cb236a27862abc769c9",
                               "deviceId": "probe"})
        return {"success": "SUCCESS" in _ret_str(r), "ret": _ret_str(r)}

    await probe("im_token", _probe_im)

    async def _probe_search():
        r = await client.call("mtop.taobao.idlemtopsearch.pc.search", "1.0",
                              {"pageNumber": 1, "keyword": "能力探测", "rowsPerPage": 1,
                               "sortValue": "", "sortField": "", "customDistance": "",
                               "gps": "", "customGps": "",
                               "propValueStr": {"searchFilter": "quickFilter:filterPersonal;"},
                               "searchReqFromPage": "pcSearch", "extraFilterValue": "{}",
                               "userPositionJson": "{}"},
                              extra_params={"spm_cnt": "a21ybx.search.0.0"})
        return {"success": "SUCCESS" in _ret_str(r), "ret": _ret_str(r)}

    await probe("search", _probe_search)
    await probe("my_items", lambda: my_items(client, user_id, rows=1))
    await probe("seller_items", lambda: seller_items(client, rows=1))
    async def _probe_orders():
        r = await client.call(
            "mtop.taobao.idle.trade.merchant.sold.get", "1.0",
            {"pageNumber": 1, "rowsPerPage": 1, "orderIds": "", "queryCode": "ALL",
             "orderSearchParam": "{}"}, referer="https://seller.goofish.com/",
            extra_headers={"idle_site_biz_code": "COMMONPRO"},
            extra_params={"type": "json", "valueType": "string"})
        return {"success": "SUCCESS" in _ret_str(r), "ret": _ret_str(r)}

    await probe("orders", _probe_orders)
    return {"account": user_id, "capabilities": out}
