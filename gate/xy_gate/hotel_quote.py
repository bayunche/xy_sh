"""代订酒店：算价、比价缓存、报价闸门、人工补价。

铁律：查价/报价自动；真实下单与付款永远人工；成本超过转人工阈值时
禁止 agent 自行报价（Decision=block，如实转告）。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .config import RobotConfig
from .risk import Decision
from .store import Store


def calc_quote(cost: float, cfg: RobotConfig) -> Dict:
    """成本 → 阶梯加价报价（价格取整到元）。"""
    pct = cfg.hotel_quotes.margin_pct(cost)
    return {"cost": cost, "margin_pct": pct, "price": round(cost * (1 + pct / 100))}


class HotelQuoteGate:
    def __init__(self, cfg: RobotConfig, store: Store):
        self.cfg = cfg
        self.store = store
        self.store._ensure_hotel_quotes_table()

    # ── 比价缓存（酒店+日期+晚数+房型 → 各源报价）─────────────────

    def cache_get(self, hotel: str, date: str, nights: int, room: str) -> Optional[List[Dict]]:
        row = self.store.hotel_quote_cache_get(hotel, date, nights, room)
        if not row:
            return None
        ts, quotes = row
        if time.time() - ts >= self.cfg.hotel_quotes.cache_ttl_min * 60:
            return None
        return quotes

    def cache_set(self, hotel: str, date: str, nights: int, room: str,
                  quotes: List[Dict]) -> None:
        self.store.hotel_quote_cache_set(hotel, date, nights, room, quotes)

    # ── 报价闸门 ─────────────────────────────────────────────────────

    def check(self, chat_id: str, cost: float) -> Decision:
        reasons: List[str] = []
        if cost > self.cfg.hotel_quotes.manual_threshold_cny:
            reasons.append(
                f"成本 ¥{cost} 超过转人工阈值 ¥{self.cfg.hotel_quotes.manual_threshold_cny}，"
                "需人工核价后才能给客户报价")
        count = self._queries_this_hour(chat_id)
        if count >= self.cfg.hotel_quotes.max_queries_per_hour:
            reasons.append(
                f"比价过于频繁：本小时已 {count} 次（上限 {self.cfg.hotel_quotes.max_queries_per_hour}），"
                "请改用缓存价或让客户稍后再试")
        if reasons:
            return Decision("block", reasons)
        q = calc_quote(cost, self.cfg)
        return Decision("allow", [f"成本 ¥{cost}，加价 {q['margin_pct']}% → 报价 ¥{q['price']}"])

    def note_query(self, chat_id: str) -> None:
        """记录一次全量比价（限频计数；agent 真去查 OTA 前调用）。"""
        key = f"hq_count:{time.strftime('%Y-%m-%d %H')}:{chat_id}"
        self.store.kv_set(key, str(self._queries_this_hour(chat_id) + 1))

    def _queries_this_hour(self, chat_id: str) -> int:
        key = f"hq_count:{time.strftime('%Y-%m-%d %H')}:{chat_id}"
        try:
            return int(self.store.kv_get(key) or 0)
        except (TypeError, ValueError):
            return 0

    # ── 人工补价（会员/协议价成本，报价时优先采用）─────────────────

    def cost_get(self, hotel: str) -> Optional[float]:
        v = self.store.kv_get(f"hotel_cost:{hotel.strip()}")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def cost_set(self, hotel: str, price: float) -> None:
        self.store.kv_set(f"hotel_cost:{hotel.strip()}", str(price))
        self.store.add_event("hotel_cost", "", "", f"人工补价：{hotel} → ¥{price}")
