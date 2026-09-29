"""App 查价通道（第四源）：MuMu 模拟器 + 纯 ADB RPA（MAA 式，零注入）。

实测结论（2026-09-30）：
- 该 App 加固壳会检测 uiautomator2 的 atx-agent 常驻注入并自杀——**不能用 u2 连接**；
- 渲染正常（Weex），但 adb screencap 抓不到 GPU 合成层（白屏）——截图走 Windows 层
  （GetWindowRect + ImageGrab，MuMu 窗口需可见不可最小化）；
- 布局侦察用 `uiautomator dump`（系统工具，一次性退出，非常驻）实测可用；
- 点击 `input tap`、输入 `input text`（ASCII；中文输入后续按搜索页实际形态解决）。

流程：dump 布局 → 按文本找坐标 → input tap → （必要时）Windows 层截图/OCR 复核。
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERTZ_PACKAGE = "com.ygsoft.mup.businesstravelnw"
HERTZ_MAIN = f"{HERTZ_PACKAGE}/com.ygsoft.tphone.MainActivity"
MUMU_ADB = "127.0.0.1:16384"
PRICE_RE = re.compile(r"[¥￥]\s*([0-9][0-9,]{2,})")
LOGIN_HINTS = ("请输入手机号", "请输入密码", "请输入验证码", "账号密码登录", "登录")


def _center(bounds: str) -> Tuple[int, int]:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return (0, 0)
    x1, y1, x2, y2 = map(int, m.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


class RpaClient:
    """纯 ADB RPA 基元：dump / tap / text / screen / launch。"""

    def __init__(self, adb_addr: str = MUMU_ADB, shots_dir: str = "../data/emulator"):
        self.adb_addr = adb_addr
        self._dev = None
        self.shots_dir = Path(shots_dir)

    @property
    def dev(self):
        if self._dev is None:
            import adbutils
            adbutils.adb.connect(self.adb_addr, timeout=8)
            self._dev = adbutils.adb.device(self.adb_addr)
        return self._dev

    # ---- 布局（一次性 dump，非常驻）----
    def dump(self) -> ET.Element:
        self.dev.shell("uiautomator dump /sdcard/rpa_ui.xml")
        xml = self.dev.shell("cat /sdcard/rpa_ui.xml")
        xml = xml[xml.find("<?xml"):] if "<?xml" in xml else xml
        return ET.fromstring(xml)

    def texts(self) -> List[str]:
        return [n.attrib.get("text") for n in self.dump().iter("node")
                if (n.attrib.get("text") or "").strip()]

    def find(self, text: str) -> Optional[Tuple[int, int]]:
        """按精确/前缀文本找第一个可点元素中心坐标。"""
        for n in self.dump().iter("node"):
            t = (n.attrib.get("text") or "").strip()
            if t == text or (len(text) >= 4 and t.startswith(text)):
                b = n.attrib.get("bounds") or ""
                if b:
                    return _center(b)
        return None

    # ---- 操作 ----
    def tap(self, x: int, y: int) -> None:
        self.dev.shell(f"input tap {x} {y}")

    def tap_text(self, text: str, settle: float = 1.5) -> bool:
        pos = self.find(text)
        if not pos:
            return False
        self.tap(*pos)
        time.sleep(settle)
        return True

    def text_input(self, s: str) -> None:
        """ASCII 输入（手机号/验证码/数字）。中文不支持（adb input 限制）。"""
        self.dev.shell("input text " + re.sub(r"[^0-9A-Za-z@._-]", "", s))

    def back(self) -> None:
        self.dev.shell("input keyevent 4")

    def launch(self, pkg: str = HERTZ_PACKAGE) -> None:
        pid = self.dev.shell(f"pidof {pkg}").strip()
        if not pid:
            self.dev.shell(f"am start -n {HERTZ_MAIN}")
            time.sleep(12)

    # ---- Windows 层截图（adb screencap 抓不到 MuMu 的 GPU 渲染）----
    def screen(self, name: str = "rpa_screen.png") -> Optional[Path]:
        import ctypes
        import ctypes.wintypes as wt
        from PIL import ImageGrab

        user32 = ctypes.windll.user32
        target = None

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(hwnd, _lp):
            nonlocal target
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == "MuMu模拟器":
                rc = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rc))
                w, h = rc.right - rc.left, rc.bottom - rc.top
                if w > 300:          # 主窗口（多开器等小窗忽略）
                    target = (rc.left, rc.top, rc.right, rc.bottom)
            return True

        user32.EnumWindows(cb, 0)
        if not target:
            return None
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / name
        ImageGrab.grab(bbox=target, all_screens=True).save(path)
        return path


class HertzApp(RpaClient):
    """赫兹商旅查价流程（流程节点按登录后界面逐步校准）。"""

    def state(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"adb_addr": self.adb_addr}
        try:
            out["connected"] = True
            out["android"] = self.dev.shell("getprop ro.build.version.release").strip()
            out["app_pid"] = self.dev.shell(f"pidof {HERTZ_PACKAGE}").strip() or ""
            out["app_installed"] = bool(self.dev.shell(
                f"pm list packages | grep {HERTZ_PACKAGE}").strip())
            ts = self.texts()
            joined = " ".join(ts)
            out["at_login"] = any(h in joined for h in LOGIN_HINTS)
            out["screen_texts"] = ts[:12]
        except Exception as e:  # noqa: BLE001
            out["connected"] = False
            out["error"] = str(e)[:160]
        return out

    def _require_login(self, texts: List[str]) -> Optional[Dict[str, Any]]:
        joined = " ".join(texts)
        if any(h in joined for h in LOGIN_HINTS):
            return {"ok": False, "source": "app", "reason": "needs_login",
                    "hint": "模拟器里的赫兹商旅未登录：请在 MuMu 窗口中人工登录一次"
                            "（南网 SSO），登录态会保留在模拟器里"}
        return None

    def probe(self, hotel: str = "", checkin: str = "", checkout: str = "",
              nights: int = 1) -> Dict[str, Any]:
        """查价 MVP：启动→校验登录→进入酒店列表读价（流程待登录后校准）。"""
        base = {"source": "app", "package": HERTZ_PACKAGE}
        try:
            self.launch()
        except Exception as e:  # noqa: BLE001
            return {**base, "ok": False, "reason": f"模拟器不可用: {str(e)[:120]}"}
        time.sleep(3)
        texts = self.texts()
        need = self._require_login(texts)
        if need:
            return need
        # 已登录：抽取当前屏价格（后续校准"酒店搜索→日期→列表"流程后启用）
        prices = sorted({int(m.replace(",", ""))
                         for t in texts for m in PRICE_RE.findall(t or "")})
        shot = self.screen("hertz_probe.png")
        out = {**base, "ok": bool(prices), "prices": prices[:12],
               "texts_sample": texts[:30], "screenshot": str(shot) if shot else None,
               "note": "MVP：当前屏价格抽取；搜索/日期流程待登录后校准"}
        if not prices:
            out["reason"] = "当前页无可见价格（需校准搜索流程）"
        return out


def probe_app(source: str, hotel: str = "", checkin: str = "",
              checkout: str = "") -> Dict[str, Any]:
    """统一入口（server/CLI 调用）。"""
    if source != "hertz":
        return {"ok": False, "source": "app", "reason": f"未知 App 源: {source}"}
    app = HertzApp()
    if hotel and checkin and checkout:
        import datetime as _dt
        ci = _dt.date.fromisoformat(checkin)
        co = _dt.date.fromisoformat(checkout)
        return app.probe(hotel, checkin, checkout, (co - ci).days)
    return app.state()


def app_state() -> Dict[str, Any]:
    return HertzApp().state()
