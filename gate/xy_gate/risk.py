"""风控闸门：确定性代码把关，提示词只是建议层。

闸门族：
- 回复类：静默时段、每小时上限、同一会话大脑 job 冷却
- 确认交易类：总开关、干跑、商品白/黑名单、买家黑名单、金额上限、重复确认冷却
- 改价类：总开关、底价（watchlist.floor_price 或挂价×floor_ratio）、
  单次降幅上限、只降不涨、每日次数、同商品间隔

所有写路径（daemon 自动流、CLI、HTTP API）都必须先过这里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple

from .config import RobotConfig, _load_watchlist
from .store import Store


@dataclass
class Decision:
    action: str                 # allow | dryrun | block
    reasons: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.action in ("allow", "dryrun")

    def to_dict(self) -> dict:
        return {"action": self.action, "reasons": self.reasons}


def _parse_hhmm(s: Optional[str]) -> Optional[dt_time]:
    if not s:
        return None
    try:
        h, m = str(s).split(":")
        return dt_time(int(h), int(m))
    except (ValueError, TypeError):
        return None


class RiskGate:
    def __init__(self, cfg: RobotConfig, store: Store,
                 watchlist: Optional[Dict[str, Dict]] = None):
        self.cfg = cfg
        self.store = store
        wl_path = Path(cfg.watchlist_path)
        if not wl_path.is_absolute():
            from .brain import REPO_ROOT
            wl_path = REPO_ROOT / wl_path
        self.watchlist = watchlist if watchlist is not None else _load_watchlist(wl_path)

    # ── 回复类 ────────────────────────────────────────────────────────

    def in_quiet_hours(self, now: Optional[datetime] = None) -> bool:
        start = _parse_hhmm(self.cfg.reply.quiet_start)
        end = _parse_hhmm(self.cfg.reply.quiet_end)
        if not start or not end:
            return False
        now = now or datetime.now()
        t = now.time()
        if start <= end:
            return start <= t <= end
        return t >= start or t <= end   # 跨午夜，如 23:30-08:00

    def check_reply(self, chat_id: str) -> Decision:
        reasons: List[str] = []
        if self.in_quiet_hours():
            reasons.append(f"静默时段 {self.cfg.reply.quiet_start}-{self.cfg.reply.quiet_end}，不自动回复")
        sent = self.store.count_replies_last_hour()
        if sent >= self.cfg.reply.max_per_hour:
            reasons.append(f"本小时自动回复已达上限 {self.cfg.reply.max_per_hour}")
        if reasons:
            return Decision("block", reasons)
        return Decision("allow")

    # ── 确认交易类 ────────────────────────────────────────────────────

    def check_confirm(
        self,
        order_id: str,
        item_id: str = "",
        buyer_id: str = "",
        amount: Optional[float] = None,
        is_bargain: bool = False,
    ) -> Decision:
        ac = self.cfg.auto_confirm
        reasons: List[str] = []
        block = reasons.append

        if not order_id:
            block("缺少订单号")
        if not ac.enabled:
            block("auto_confirm.enabled=false（总开关未开，需人工确认交易）")
        if ac.item_blacklist and item_id in ac.item_blacklist:
            block(f"商品 {item_id} 在黑名单")
        if ac.item_whitelist and item_id and item_id not in ac.item_whitelist:
            block(f"商品 {item_id} 不在白名单")
        if buyer_id and buyer_id in ac.buyer_blacklist:
            block(f"买家 {buyer_id} 在黑名单")
        if amount is not None and amount > ac.max_amount_cny:
            block(f"订单金额 ¥{amount} 超过上限 ¥{ac.max_amount_cny}")
        if order_id:
            last = self.store.confirm_last_time(order_id)
            if last is not None:
                block(f"订单 {order_id} 已有确认记录（{last}），重复确认需人工")

        if reasons:
            return Decision("block", reasons)

        if not self.cfg.live:
            return Decision("dryrun", ["mode=dry-run：模拟确认（记录台账，不调真实 API）"])
        return Decision("allow", [
            f"金额 ¥{amount if amount is not None else '未知'} ≤ ¥{ac.max_amount_cny}" if amount is not None else "金额未知（未阻断，请人工核对）",
        ] + (["小刀订单：先免拼再确认"] if is_bargain else []))

    # ── 关键词规则 ───────────────────────────────────────────────────

    def match_keyword_rule(self, text: str, item_id: str = "") -> Optional[dict]:
        """命中即返回规则 dict（含 reply 文案）；未命中返回 None。"""
        for rule in self.cfg.reply.keyword_rules:
            if rule.get("item_id") and str(rule["item_id"]) != str(item_id):
                continue
            if any(kw in text for kw in rule.get("match", [])):
                return rule
        return None

    # ── 改价闸门（议价成交 / 审计调价共用）─────────────────────────

    def floor_price_of(self, item_id: str, listed_price: Optional[float]) -> Optional[float]:
        """议价底价：watchlist.floor_price 优先，否则挂价 × floor_ratio。"""
        wl = self.watchlist.get(str(item_id)) or {}
        if wl.get("floor_price") is not None:
            return float(wl["floor_price"])
        if listed_price is not None:
            return round(listed_price * self.cfg.reprice.floor_ratio, 2)
        return None

    def check_reprice(self, item_id: str, current_price: Optional[float],
                      new_price: float) -> Decision:
        """自动改价四关：总开关 → 底价 → 降幅 → 频次/间隔。"""
        rp = self.cfg.reprice
        reasons: List[str] = []
        block = reasons.append

        if not rp.enabled:
            block("reprice.enabled=false（自动改价总开关未开）")
        floor = self.floor_price_of(item_id, current_price)
        if floor is not None and new_price < floor:
            block(f"新价 ¥{new_price} 低于底价 ¥{floor}")
        if current_price is not None and current_price > 0:
            drop_pct = (current_price - new_price) / current_price * 100
            if drop_pct > rp.max_drop_pct:
                block(f"降幅 {drop_pct:.1f}% 超过单次上限 {rp.max_drop_pct}%")
            if new_price > current_price:
                block(f"新价 ¥{new_price} 高于现价 ¥{current_price}（只允许降价方向）")
        # 频次与间隔（台账 kv）
        today = datetime.now().strftime("%Y-%m-%d")
        day_count = int(self.store.kv_get(f"reprice_count:{today}") or 0)
        if day_count >= rp.max_per_day:
            block(f"今日已改价 {day_count} 次，达到上限 {rp.max_per_day}")
        last = self.store.kv_get(f"reprice_last:{item_id}")
        if last and (time.time() - float(last)) < rp.min_interval_min * 60:
            block(f"商品 {item_id} 改价间隔不足 {rp.min_interval_min} 分钟")

        if reasons:
            return Decision("block", reasons)
        if not self.cfg.live:
            return Decision("dryrun", ["mode=dry-run：模拟改价（记录台账，不调真实 API）"])
        return Decision("allow", [f"底价 ¥{floor} ≤ ¥{new_price}" if floor is not None
                                  else "无底价数据（watchlist 未配置该商品）"])

    def record_reprice(self, item_id: str, old_price: Optional[float],
                       new_price: float, decision: str, result: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        self.store.kv_set(f"reprice_last:{item_id}", str(time.time()))
        self.store.kv_set(f"reprice_count:{today}",
                          str(int(self.store.kv_get(f"reprice_count:{today}") or 0) + 1))
        self.store.add_event("reprice", "", item_id,
                             f"{item_id}: ¥{old_price} → ¥{new_price} [{decision}] {result[:120]}")

    # ── 大脑 job 冷却 ─────────────────────────────────────────────────

    def check_brain_job(self, chat_id: str) -> Tuple[bool, str]:
        last = self.store.kv_get(f"brain_last:{chat_id}")
        if last:
            if time.time() - float(last) < self.cfg.brain.per_chat_cooldown_sec:
                remaining = int(self.cfg.brain.per_chat_cooldown_sec - (time.time() - float(last)))
                return False, f"该会话大脑冷却中（剩 {remaining}s），本次跳过"
        return True, ""
