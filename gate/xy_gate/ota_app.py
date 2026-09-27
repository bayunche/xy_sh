"""App 查价通道（第四源）：Android 模拟器（MuMu 12）+ uiautomator2 操作商旅 App。

设计（与 CDP 浏览器通道同构）：
- 模拟器常驻（用户手动启动），gate 通过 adbutils 连 127.0.0.1:16384（MuMu 实例 0）
- uiautomator2 驱动：打开 App → 搜索酒店 → 选日期 → 读价格列表 → 回 JSON
- 控件选择器集中在 _SELECTORS，App 改版只需调这里
- 登录态：用户在模拟器里登录一次（南网 SSO），长期保留在模拟器镜像里

依赖：uiautomator2/adbutils（已在 gate 依赖中）。未连接模拟器时返回友好错误。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

# 赫兹商旅（南方电网，远光软件）
HERTZ_PACKAGE = "com.ygsoft.mup.businesstravelnw"

# MuMu 12 实例 0 的默认 ADB 端口；多实例为 16384+2*n
MUMU_ADB = "127.0.0.1:16384"

# ---- 控件选择器（真机 dump 后校准；App 双周迭代，改版优先改这里）----
_SELECTORS = {
    # 首页酒店入口（文本或 desc）
    "home_hotel_btn": "酒店",
    # 搜索页目的地输入框
    "search_input": 'com.ygsoft.mup.businesstravelnw:id/et_search',
    # 搜索确认
    "search_confirm": "搜索",
    # 价格列表容器里的价格文本（正则）
    "price_pattern": r"[¥￥]\s?([0-9][0-9,]{2,})",
}


class AppProber:
    """模拟器 App 查价。所有方法返回与 web 源同构的 dict（ok/source/reason/...）。"""

    def __init__(self, adb_addr: str = MUMU_ADB, package: str = HERTZ_PACKAGE):
        self.adb_addr = adb_addr
        self.package = package
        self._d = None   # uiautomator2.Device，连接后缓存

    # ---- 连接 ----
    def connect(self, timeout: int = 10) -> Any:
        """连模拟器并返回 u2 device；失败抛异常（含自助指引）。"""
        if self._d is not None:
            return self._d
        import adbutils
        import uiautomator2 as u2

        adb = adbutils.adb
        try:
            adb.connect(self.adb_addr, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"连不上模拟器 ADB（{self.adb_addr}）：{e}。"
                               "请先启动 MuMu 模拟器") from e
        d = u2.connect(self.adb_addr)
        if not d.info.get("screenOn"):
            d.screen_on()
        self._d = d
        return d

    def device_state(self) -> Dict[str, Any]:
        """诊断信息：模拟器/adb/u2 atx-agent/App 安装状态。"""
        out: Dict[str, Any] = {"adb_addr": self.adb_addr}
        try:
            d = self.connect()
            out["connected"] = True
            out["sdk"] = d.device_info.get("version")
        except Exception as e:  # noqa: BLE001
            return {**out, "connected": False, "error": str(e)[:160]}
        try:
            out["app_installed"] = d.app_info(self.package) is not None
        except Exception:  # noqa: BLE001
            out["app_installed"] = False
        return out

    # ---- App 内固定流程 ----
    def _open_app(self, d: Any) -> None:
        d.app_start(self.package)
        d.wait_timeout = 15
        # 等首页出现（酒店入口文本）
        sel = _SELECTORS["home_hotel_btn"]
        if not d(text=sel).wait(timeout=15):
            raise RuntimeError("App 首页未出现「酒店」入口：未登录/改版/卡启动页，"
                               "请在模拟器里人工检查一次")

    def _dump_visible_texts(self, d: Any) -> List[str]:
        """当前屏可见文本（查价兜底解析用 + 调试）。"""
        return [el.get_text() for el in d.xpath("//hierarchy//*").all()
                if (el.get_text() or "").strip()][:200]

    def probe_hertz(self, hotel: str, checkin: str, checkout: str) -> Dict[str, Any]:
        """赫兹商旅查价（MVP：读价格列表；选择器需真机校准后启用）。"""
        base = {"source": "app", "package": self.package}
        try:
            d = self.connect()
        except Exception as e:  # noqa: BLE001
            return {**base, "ok": False, "reason": str(e)[:200]}
        try:
            self._open_app(d)
        except Exception as e:  # noqa: BLE001
            return {**base, "ok": False, "reason": str(e)[:200]}
        # TODO(真机校准)：搜索酒店→选日期→进价格列表（选择器见 _SELECTORS）。
        # 当前 MVP 行为：dump 可见文本 + 正则抽价，返回调试数据。
        texts = self._dump_visible_texts(d)
        prices = []
        pat = re.compile(_SELECTORS["price_pattern"])
        for t in texts:
            m = pat.search(t or "")
            if m:
                prices.append(int(m.group(1).replace(",", "")))
        out = {**base, "ok": bool(prices), "prices": sorted(set(prices))[:12],
               "texts_sample": [t for t in texts if t][:30],
               "note": "MVP 调试模式：首页文本抽取；搜索流程待真机校准选择器"}
        if not prices:
            out["reason"] = "当前页无可见价格（需先走搜索流程或人工定位一次）"
        return out


def probe_app(source: str, hotel: str = "", checkin: str = "",
              checkout: str = "") -> Dict[str, Any]:
    """统一入口（server/CLI 调用）。source: hertz（后续可扩 ctrip-biz 等）。"""
    if source != "hertz":
        return {"ok": False, "source": "app", "reason": f"未知 App 源: {source}"}
    prober = AppProber()
    return prober.probe_hertz(hotel, checkin, checkout)


def app_state() -> Dict[str, Any]:
    return AppProber().device_state()
