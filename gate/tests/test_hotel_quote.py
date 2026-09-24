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
