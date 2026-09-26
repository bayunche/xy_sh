"""查价：闲鱼 PC 搜索接口 + 价格统计。

mtop.taobao.idlemtopsearch.pc.search（只读接口，实测只需 sign + 登录 Cookie，
无需 bx-ua 风控参数）。搜索结果项的字段层级多变，按
data.item.main.exContent → data.item.main → item.main → main 逐级兜底。
"""
from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional

from .mtop import MtopClient

SEARCH_API = "mtop.taobao.idlemtopsearch.pc.search"


def _pick_dict(*candidates: Any) -> Dict[str, Any]:
    for c in candidates:
        if isinstance(c, dict) and c:
            return c
    return {}


def _struct_main(item: Dict[str, Any]) -> Dict[str, Any]:
    """结构主节点（clickParam/targetUrl 挂这里）。"""
    candidates = []
    for getter in (
        lambda: item["data"]["item"]["main"],
        lambda: item["item"]["main"],
        lambda: item["main"],
    ):
        try:
            node = getter()
            if isinstance(node, dict) and node:
                candidates.append(node)
        except (KeyError, TypeError):
            continue
    return _pick_dict(*candidates)


def _main_of(item: Dict[str, Any], struct: Dict[str, Any]) -> Dict[str, Any]:
    """内容主节点（title/price/fishTags）：优先 exContent，回退结构主节点。"""
    ex = struct.get("exContent")
    return _pick_dict(ex if isinstance(ex, dict) and ex else None, struct)


def parse_money(price_text: str) -> Optional[float]:
    """'¥1,299' / '1299' / '1.2万' → float；解析失败返回 None。"""
    s = str(price_text or "").replace("¥", "").replace("￥", "").replace(",", "").strip()
    if not s:
        return None
    try:
        if "万" in s:
            return float(re.sub(r"[^\d.]", "", s.replace("万", ""))) * 10000
        return float(s)
    except ValueError:
        m = re.search(r"\d+(?:\.\d+)?", s)
        return float(m.group()) if m else None


def _price_text(main: Dict[str, Any]) -> str:
    parts = main.get("price")
    if isinstance(parts, list):
        return "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
    return str(parts or "")


def _want_count(main: Dict[str, Any], click_args: Dict[str, Any]) -> int:
    fish = main.get("fishTags")
    if isinstance(fish, dict):
        for v in fish.values():
            text = v if not isinstance(v, dict) else v.get("text", "")
            m = re.search(r"(\d+)\s*人想要", str(text))
            if m:
                return int(m.group(1))
    try:
        return int(str(click_args.get("wantNum", 0)))
    except (TypeError, ValueError):
        return 0


def parse_search_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    struct = _struct_main(item)
    if not struct:
        return None
    main = _main_of(item, struct)
    click = _pick_dict(
        (struct.get("clickParam") or {}).get("args") if isinstance(struct.get("clickParam"), dict) else None,
        struct.get("clickParam") if isinstance(struct.get("clickParam"), dict) else None,
        (main.get("clickParam") or {}).get("args") if isinstance(main.get("clickParam"), dict) else None,
        main.get("clickParam") if isinstance(main.get("clickParam"), dict) else None,
        item.get("clickParam") if isinstance(item.get("clickParam"), dict) else None,
    )
    item_id = str(click.get("item_id") or click.get("itemId") or click.get("id") or "")
    price_text = _price_text(main)
    pic = str(main.get("picUrl") or "")
    url = str(main.get("targetUrl") or "").replace("fleamarket://", "https://www.goofish.com/")
    if not url and item_id:
        url = f"https://www.goofish.com/item?id={item_id}"
    return {
        "item_id": item_id,
        "title": str(main.get("title") or "未知标题"),
        "price_text": price_text.replace("当前价", "").strip(),
        "price": parse_money(price_text),
        "want_count": _want_count(main, click),
        "area": str(main.get("area") or ""),
        "seller": str(main.get("userNickName") or ""),
        "url": url,
        "pic_url": (pic if pic.startswith("http") else f"https:{pic}") if pic else "",
    }


def price_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    prices = sorted(p for it in items if (p := it.get("price")) is not None)
    if not prices:
        return {"count": 0}
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0]] * 3
    return {
        "count": len(prices),
        "min": prices[0],
        "p25": round(q[0], 2),
        "median": round(statistics.median(prices), 2),
        "p75": round(q[2], 2),
        "max": prices[-1],
        "mean": round(statistics.fmean(prices), 2),
    }


async def search_items(
    client: MtopClient,
    keyword: str,
    page: int = 1,
    rows: int = 30,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    sort_field: str = "",
    sort_value: str = "",
) -> Dict[str, Any]:
    """搜索 + 解析。返回 {items, stats, has_next_page}。

    sort_field 常用："" 综合；其余排序值以网页端实际参数为准（v1 只默认综合排序，
    价格过滤用 priceRange 过滤器实现，等价于网页端价格筛选）。
    """
    parts = ["quickFilter:filterPersonal;"]
    if price_min is not None or price_max is not None:
        lo = "" if price_min is None else str(int(price_min) if float(price_min).is_integer() else price_min)
        hi = "undefined" if price_max is None else str(int(price_max) if float(price_max).is_integer() else price_max)
        parts.append(f"priceRange:{lo},{hi};")
    data = {
        "pageNumber": page,
        "keyword": keyword,
        "fromFilter": bool(sort_field or len(parts) > 1),
        "rowsPerPage": rows,
        "sortValue": sort_value,
        "sortField": sort_field,
        "customDistance": "",
        "gps": "",
        "propValueStr": {"searchFilter": "".join(parts)},
        "customGps": "",
        "searchReqFromPage": "pcSearch",
        "extraFilterValue": "{}",
        "userPositionJson": "{}",
    }
    res = await client.call(SEARCH_API, "1.0", data, extra_params={
        "spm_cnt": "a21ybx.search.0.0",
        "spm_pre": "a21ybx.home.searchInput.0",
    })
    data_node = ((res.get("data") or {}) if isinstance(res.get("data"), dict) else {})
    raw_list = data_node.get("resultList") or []
    items = [p for raw in raw_list if (p := parse_search_item(raw))]
    return {
        "items": items,
        "stats": price_stats(items),
        "has_next_page": bool((data_node.get("resultInfo") or {}).get("hasNextPage")),
    }
