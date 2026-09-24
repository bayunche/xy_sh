"""ota.py 单测：Python 侧逻辑（结果折算/名称比对/城市映射/汇率）。

JS 提取器由 E2E 第 17 步真浏览器验证（fixture 见 data/ota-research/）。
"""
from pathlib import Path

import pytest

from xy_gate.ota import (
    OtaConfig,
    OtaProber,
    _LY_CITY_IDS,
    _name_mismatch,
    _split_date,
    twd_to_cny,
)


def _prober(tmp_path: Path) -> OtaProber:
    return OtaProber(OtaConfig(), tmp_path)


# ---- _trip_result：CNY 直显 ----
def test_trip_result_cny(tmp_path):
    p = _prober(tmp_path)
    data = {"blocks": [{
        "currency": "CNY", "per_night": 519, "total": 1100,
        "room": "Deluxe Twin Room",
        "breakfast": "Includes 2 great breakfasts",
        "cancel": "Non-refundable"}]}
    out = p._trip_result(data, "https://x/")
    assert out["ok"] is True
    assert out["per_night_cny"] == 519
    assert out["total_cny"] == 1100
    assert out["note"] == ""            # CNY 无需折算说明
    assert out["room"] == "Deluxe Twin Room"


# ---- _trip_result：TWD 折算 ----
def test_trip_result_twd(tmp_path):
    p = _prober(tmp_path)
    data = {"blocks": [{
        "currency": "TWD", "per_night": 2608, "total": 5216,
        "room": "豪華雙床房", "breakfast": "含 2 客豐盛早餐",
        "cancel": "不可退款"}]}
    out = p._trip_result(data, "https://x/")
    assert out["per_night_cny"] == round(2608 * 0.225)
    assert out["total_cny"] == round(5216 * 0.225)
    assert "折算" in out["note"]


# ---- 名称比对：中文 expect vs 英文标题不算 mismatch ----
def test_name_mismatch_cjk_expect_en_title():
    assert _name_mismatch("杭州开元名都大酒店",
                          "New Century Grand Hotel Hangzhou") is False
    assert _name_mismatch("杭州开元名都大酒店", "宁波某酒店") is True  # 前2字都不匹配
    assert _name_mismatch("", "whatever") is False
    assert _name_mismatch("Grand Hotel", "New Century Grand Hotel") is False


# ---- 城市映射 ----
def test_ly_city_ids_cover():
    assert _LY_CITY_IDS["杭州"] == "383"
    assert len(_LY_CITY_IDS) >= 20


# ---- 汇率工具 ----
def test_twd_to_cny():
    assert twd_to_cny("TWD2,608 與 TWD 5,216", 0.225) == [587, 1174]


# ---- 日期格式 ----
def test_split_date():
    assert _split_date("2026-10-01") == "20261001"
