"""风控闸门单测。"""
from pathlib import Path

from xy_gate.config import RobotConfig, AutoConfirmConfig, ReplyConfig
from xy_gate.risk import RiskGate
from xy_gate.store import Store


def make_gate(tmp_path, mode="dry-run", confirm_enabled=True, max_amount=100.0,
              quiet=None, whitelist=(), cooldown=3):
    cfg = RobotConfig(mode=mode, db_path=str(tmp_path / "t.sqlite3"),
                      auto_confirm=AutoConfirmConfig(enabled=confirm_enabled,
                                                      max_amount_cny=max_amount,
                                                      item_whitelist=list(whitelist),
                                                      cooldown_minutes=cooldown),
                      reply=ReplyConfig(quiet_start=quiet[0] if quiet else None,
                                        quiet_end=quiet[1] if quiet else None))
    return RiskGate(cfg, Store(tmp_path / "t.sqlite3"))


def test_confirm_blocked_when_disabled(tmp_path):
    gate = make_gate(tmp_path, confirm_enabled=False)
    d = gate.check_confirm("o1", amount=10)
    assert d.action == "block" and any("enabled=false" in r for r in d.reasons)


def test_confirm_dryrun_when_enabled(tmp_path):
    gate = make_gate(tmp_path, mode="dry-run", confirm_enabled=True)
    d = gate.check_confirm("o2", item_id="i1", buyer_id="b1", amount=30)
    assert d.action == "dryrun"


def test_confirm_live_allows(tmp_path):
    gate = make_gate(tmp_path, mode="live", confirm_enabled=True)
    d = gate.check_confirm("o3", amount=99.9)
    assert d.action == "allow"


def test_confirm_amount_cap(tmp_path):
    gate = make_gate(tmp_path, mode="live", max_amount=100)
    d = gate.check_confirm("o4", amount=100.01)
    assert d.action == "block" and any("超过上限" in r for r in d.reasons)


def test_confirm_whitelist(tmp_path):
    gate = make_gate(tmp_path, mode="live", whitelist=["777"])
    assert gate.check_confirm("o5", item_id="777").action == "allow"
    d = gate.check_confirm("o6", item_id="888")
    assert d.action == "block" and any("白名单" in r for r in d.reasons)


def test_confirm_duplicate_cooldown(tmp_path):
    gate = make_gate(tmp_path, mode="live")
    gate.store.record_confirm("o7", "", "", 10, "allow", "ok", False)
    d = gate.check_confirm("o7")
    assert d.action == "block" and any("重复确认" in r for r in d.reasons)


def test_quiet_hours_cross_midnight(tmp_path):
    gate = make_gate(tmp_path, quiet=("23:30", "08:00"))
    from datetime import datetime
    assert gate.in_quiet_hours(datetime(2026, 9, 18, 23, 31)) is True
    assert gate.in_quiet_hours(datetime(2026, 9, 18, 7, 59)) is True
    assert gate.in_quiet_hours(datetime(2026, 9, 18, 12, 0)) is False


def test_keyword_rule_match_and_item_scope(tmp_path):
    gate = make_gate(tmp_path)
    gate.cfg.reply.keyword_rules = [
        {"match": ["怎么发货"], "reply": "拍下秒发~"},
        {"match": ["在吗"], "reply": "限品回复", "item_id": "999"},
    ]
    assert gate.match_keyword_rule("请问怎么发货呀")["reply"] == "拍下秒发~"
    assert gate.match_keyword_rule("在吗", item_id="999")["reply"] == "限品回复"
    assert gate.match_keyword_rule("在吗", item_id="111") is None
    assert gate.match_keyword_rule("你好") is None
