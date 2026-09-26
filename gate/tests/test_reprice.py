"""改价闸门单测。"""
from xy_gate.config import (AutoConfirmConfig, RepriceConfig, RobotConfig)
from xy_gate.risk import RiskGate
from xy_gate.store import Store


def make_gate(tmp_path, mode="dry-run", enabled=True, floor_ratio=0.85,
              max_drop=20.0, watchlist=None):
    cfg = RobotConfig(mode=mode, db_path=str(tmp_path / "r.sqlite3"),
                      reprice=RepriceConfig(enabled=enabled, floor_ratio=floor_ratio,
                                            max_drop_pct=max_drop, max_per_day=5,
                                            min_interval_min=10),
                      auto_confirm=AutoConfirmConfig())
    return RiskGate(cfg, Store(tmp_path / "r.sqlite3"), watchlist=watchlist or {})


def test_reprice_disabled(tmp_path):
    gate = make_gate(tmp_path, enabled=False)
    d = gate.check_reprice("i1", current_price=100.0, new_price=90.0)
    assert d.action == "block" and any("enabled=false" in r for r in d.reasons)


def test_reprice_floor(tmp_path):
    gate = make_gate(tmp_path)  # floor = 100*0.85 = 85
    d = gate.check_reprice("i1", current_price=100.0, new_price=84.0)
    assert d.action == "block" and any("低于底价" in r for r in d.reasons)


def test_reprice_watchlist_floor_overrides(tmp_path):
    gate = make_gate(tmp_path, watchlist={"i1": {"floor_price": 60.0}})
    # watchlist 底价 60 覆盖默认 85：默认下 80 会被底价拦，这里应放行（降幅 20% 恰好达上限）
    d_default_block = make_gate(tmp_path / "sub2").check_reprice("i1", 100.0, 80.0)
    assert d_default_block.action == "block" and any("底价" in r for r in d_default_block.reasons)
    d = gate.check_reprice("i1", current_price=100.0, new_price=80.0)
    assert d.action == "dryrun"


def test_reprice_drop_cap_and_direction(tmp_path):
    gate = make_gate(tmp_path)  # floor 85
    d = gate.check_reprice("i1", current_price=100.0, new_price=100.0)  # 现价100 floor85
    # 新价100：floor 85 ≤ 100 ✓，但高于现价？等于现价不算涨 → 允许 dryrun
    assert d.action == "dryrun"
    d2 = gate.check_reprice("i2", current_price=100.0, new_price=120.0)
    assert d2.action == "block" and any("只允许降价" in r for r in d2.reasons)


def test_reprice_interval_and_daily_cap(tmp_path):
    gate = make_gate(tmp_path, watchlist={"i1": {"floor_price": 10.0}})
    gate.record_reprice("i1", 100.0, 90.0, "dryrun", "ok")
    d = gate.check_reprice("i1", current_price=100.0, new_price=95.0)
    assert d.action == "block" and any("间隔不足" in r for r in d.reasons)
    # 其他商品不受该间隔限制，但受每日次数限制
    for i in range(5):
        gate.record_reprice(f"other{i}", 100.0, 90.0, "dryrun", "ok")
    d2 = gate.check_reprice("fresh", current_price=100.0, new_price=90.0)
    assert d2.action == "block" and any("上限" in r for r in d2.reasons)


def test_floor_price_of_priority(tmp_path):
    gate = make_gate(tmp_path, watchlist={"i1": {"floor_price": 66.0}})
    assert gate.floor_price_of("i1", 100.0) == 66.0
    assert gate.floor_price_of("unknown", 100.0) == 85.0
    assert gate.floor_price_of("unknown", None) is None
