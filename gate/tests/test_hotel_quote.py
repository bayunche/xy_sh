"""代订酒店：配置/算价/缓存/闸门 单测。"""
from xy_gate.config import HotelQuoteConfig, RobotConfig, load_robot_config, render_robot_yaml


def test_hotel_config_defaults():
    hq = RobotConfig().hotel_quotes
    assert hq.tiers == [[500, 10.0], [1500, 8.0]]
    assert hq.manual_threshold_cny == 2500
    assert hq.cache_ttl_min == 30
    assert hq.max_queries_per_hour == 3
    # margin_pct：超出显式档位的走默认 5%
    assert hq.margin_pct(300) == 10.0
    assert hq.margin_pct(1000) == 8.0
    assert hq.margin_pct(2000) == 5.0


def test_hotel_config_yaml_roundtrip(tmp_path):
    raw = (
        "mode: dry-run\n"
        "hotel_quotes:\n"
        "  tiers: [[800, 12], [2000, 6]]\n"
        "  manual_threshold_cny: 3000\n"
        "  cache_ttl_min: 15\n"
        "  max_queries_per_hour: 5\n"
    )
    (tmp_path / "robot.yaml").write_text(raw, encoding="utf-8")
    cfg = load_robot_config(config_dir=tmp_path)
    assert cfg.hotel_quotes.tiers == [[800.0, 12.0], [2000.0, 6.0]]
    assert cfg.hotel_quotes.manual_threshold_cny == 3000
    assert cfg.hotel_quotes.cache_ttl_min == 15
    assert cfg.hotel_quotes.max_queries_per_hour == 5
    assert cfg.hotel_quotes.margin_pct(500) == 12.0
    out = render_robot_yaml(cfg)
    assert "hotel_quotes:" in out and "manual_threshold_cny: 3000.0" in out


# ── 报价核心 ────────────────────────────────────────────────────────
from xy_gate.hotel_quote import calc_quote, HotelQuoteGate
from xy_gate.store import Store


def test_calc_quote_tiers():
    cfg = RobotConfig()
    assert calc_quote(300, cfg) == {"cost": 300, "margin_pct": 10.0, "price": 330.0}
    mid = calc_quote(1000, cfg)
    assert mid["margin_pct"] == 8.0 and mid["price"] == 1080.0
    high = calc_quote(2000, cfg)
    assert high["margin_pct"] == 5.0 and high["price"] == 2100.0


def _make_gate(tmp_path, threshold=2500, ttl=30, per_hour=3):
    cfg = RobotConfig(db_path=str(tmp_path / "hq.sqlite3"))
    cfg.hotel_quotes.manual_threshold_cny = threshold
    cfg.hotel_quotes.cache_ttl_min = ttl
    cfg.hotel_quotes.max_queries_per_hour = per_hour
    return HotelQuoteGate(cfg, Store(tmp_path / "hq.sqlite3"))


def test_gate_threshold_and_manual_cost(tmp_path):
    g = _make_gate(tmp_path)
    assert g.check("c1", 2400).action == "allow"
    d = g.check("c1", 2600)
    assert d.action == "block" and any("人工" in r for r in d.reasons)
    g.cost_set("杭州开元名都", 600)
    assert g.cost_get("杭州开元名都") == 600
    assert g.cost_get("未知酒店") is None


def test_gate_rate_limit(tmp_path):
    g = _make_gate(tmp_path, per_hour=2)
    g.note_query("c1")
    g.note_query("c1")
    d = g.check("c1", 100)
    assert d.action == "block" and any("频繁" in r for r in d.reasons)
    assert g.check("c2", 100).action == "allow"   # 其他会话不受影响


def test_gate_allow_reason_has_quote(tmp_path):
    g = _make_gate(tmp_path)
    d = g.check("c1", 500)
    assert d.action == "allow" and "550" in d.reasons[0]   # 500×10%


def test_cache_ttl(tmp_path):
    g = _make_gate(tmp_path, ttl=0)   # 0 分钟 → 立即过期
    g.cache_set("开元名都", "2026-10-01", 2, "", [{"source": "ctrip", "price": 500}])
    assert g.cache_get("开元名都", "2026-10-01", 2, "") is None
    g2 = _make_gate(tmp_path / "b", ttl=30)
    g2.cache_set("开元名都", "2026-10-01", 2, "", [{"source": "ctrip", "price": 500}])
    got = g2.cache_get("开元名都", "2026-10-01", 2, "")
    assert got and got[0]["price"] == 500
