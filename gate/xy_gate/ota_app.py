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

import ctypes
import os
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


class NemuBackend:
    """网易官方外部渲染接口（MAA 同款，external_renderer_ipc.dll）：
    截图直读渲染帧（无遮挡/无需窗口可见）+ 原生点击/按键/中文输入。"""

    MUMU_ROOT = r"C:\Program Files\Netease\MuMu Player 12"

    def __init__(self, mumu_root: str = None):
        root = mumu_root or self.MUMU_ROOT
        dll_path = None
        for cand in (os.path.join(root, "nx_main", "sdk", "external_renderer_ipc.dll"),
                     os.path.join(root, "nx_device", "12.0", "shell", "sdk",
                                  "external_renderer_ipc.dll")):
            if os.path.exists(cand):
                dll_path = cand
                break
        if not dll_path:
            raise RuntimeError("external_renderer_ipc.dll 未找到")
        self.dll = ctypes.CDLL(dll_path)
        self.dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        self.dll.nemu_connect.restype = ctypes.c_void_p
        self.dll.nemu_get_display_id.argtypes = [ctypes.c_void_p]
        self.dll.nemu_get_display_id.restype = ctypes.c_int
        self.dll.nemu_capture_display.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_void_p]
        self.dll.nemu_input_event_touch_down.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.nemu_input_event_touch_up.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.nemu_input_event_key_down.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self.dll.nemu_input_event_key_up.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self.dll.nemu_input_text.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        self.dll.nemu_disconnect.argtypes = [ctypes.c_void_p]
        self.handle = self.dll.nemu_connect(root, 0)
        if not self.handle:
            raise RuntimeError("nemu_connect 失败（MuMu 未运行/路径不对）")
        self.display_id = self.dll.nemu_get_display_id(self.handle)

    def close(self):
        if getattr(self, "handle", None):
            self.dll.nemu_disconnect(self.handle)
            self.handle = None

    def capture(self):
        """返回 PIL RGB 图像（已垂直翻转，正向画面）。"""
        from PIL import Image
        W, H = ctypes.c_int(0), ctypes.c_int(0)
        r = self.dll.nemu_capture_display(self.handle, self.display_id, 0,
                                          ctypes.byref(W), ctypes.byref(H), None)
        if r != 0 or not W.value:
            raise RuntimeError(f"nemu 探测尺寸失败: {r}")
        buf = (ctypes.c_ubyte * (W.value * H.value * 4))()
        r = self.dll.nemu_capture_display(self.handle, self.display_id,
                                          W.value * H.value * 4,
                                          ctypes.byref(W), ctypes.byref(H), buf)
        if r != 0:
            raise RuntimeError(f"nemu 捕获失败: {r}")
        img = Image.frombytes("RGBA", (W.value, H.value), bytes(buf), "raw", "RGBA", 0, 1)
        return img.transpose(Image.FLIP_TOP_BOTTOM).convert("RGB")

    def tap(self, x: int, y: int) -> None:
        self.dll.nemu_input_event_touch_down(self.handle, self.display_id, x, y)
        self.dll.nemu_input_event_touch_up(self.handle, self.display_id, x, y)

    def key(self, code: int) -> None:
        self.dll.nemu_input_event_key_down(self.handle, self.display_id, code)
        self.dll.nemu_input_event_key_up(self.handle, self.display_id, code)

    def text(self, s: str) -> None:
        """原生输入（支持中文，UTF-8）。"""
        self.dll.nemu_input_text(self.handle, self.display_id, s.encode("utf-8"))

class RpaClient:
    """纯 ADB RPA 基元：dump / tap / text / screen / launch。"""

    def __init__(self, adb_addr: str = MUMU_ADB, shots_dir: str = "../data/emulator"):
        self.adb_addr = adb_addr
        self._dev = None
        self._nemu = None
        self.shots_dir = Path(shots_dir)

    @property
    def nemu(self):
        """网易官方接口（截图无遮挡/中文输入）；失败返回 None 走 adb 兜底。"""
        if self._nemu is None:
            try:
                self._nemu = NemuBackend()
            except Exception:
                self._nemu = False
        return self._nemu or None

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

    # ---- 操作（nemu 官方接口优先，adb 兜底）----
    def tap(self, x: int, y: int) -> None:
        n = self.nemu
        if n:
            n.tap(x, y)
        else:
            self.dev.shell(f"input tap {x} {y}")

    def tap_text(self, text: str, settle: float = 1.5) -> bool:
        pos = self.find(text)
        if not pos:
            return False
        self.tap(*pos)
        time.sleep(settle)
        return True

    def text_input(self, s: str) -> None:
        """输入（nemu 接口支持中文；adb 兜底仅 ASCII）。"""
        n = self.nemu
        if n:
            n.text(s)
        else:
            self.dev.shell("input text " + re.sub(r"[^0-9A-Za-z@._-]", "", s))

    def back(self) -> None:
        n = self.nemu
        if n:
            n.key(4)
        else:
            self.dev.shell("input keyevent 4")

    def launch(self, pkg: str = HERTZ_PACKAGE) -> None:
        pid = self.dev.shell(f"pidof {pkg}").strip()
        if not pid:
            self.dev.shell(f"am start -n {HERTZ_MAIN}")
            time.sleep(12)

    # ---- Windows 层截图（adb screencap 抓不到 MuMu 的 GPU 渲染）----
    def screen(self, name: str = "rpa_screen.png") -> Optional[Path]:
        n = self.nemu
        if n:
            try:
                self.shots_dir.mkdir(parents=True, exist_ok=True)
                path = self.shots_dir / name
                n.capture().save(path)
                return path
            except Exception:
                pass   # 落到 Windows 层抓取
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

        # EnumWindows 回调拿不到 hwnd 本体，重找一次拿句柄并置顶（防遮挡）
        hwnd_mu = None
        pairs = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb2(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == "MuMu模拟器":
                    rc = wt.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rc))
                    if rc.right - rc.left > 300:
                        pairs.append((hwnd, (rc.left, rc.top, rc.right, rc.bottom)))
            return True

        user32.EnumWindows(cb2, 0)
        if not pairs:
            return None
        hwnd_mu, target = pairs[0]
        # 最小化则还原，然后置顶到前台（屏幕级抓取必须窗口可见）
        user32.ShowWindow(hwnd_mu, 9)   # SW_RESTORE
        user32.SetForegroundWindow(hwnd_mu)
        user32.SetWindowPos(hwnd_mu, -1, 0, 0, 0, 0, 0x0003)   # HWND_TOPMOST, NOMOVE|NOSIZE
        time.sleep(0.6)
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / name
        ImageGrab.grab(bbox=target, all_screens=True).save(path)
        user32.SetWindowPos(hwnd_mu, -2, 0, 0, 0, 0, 0x0003)   # 取消 TOPMOST
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

    # ---- 已校准的完整查价流程（2026-09-30 实测走通）----
    def _tap_text_container(self, text: str, settle: float = 2.0) -> bool:
        """点文本的可点击祖先容器（Weex 文本本身常 clickable=false）。"""
        root = self.dump()
        nodes = list(root.iter("node"))

        def paths(node, anc):
            for c in node:
                t = (c.attrib.get("text") or "").strip()
                ch = anc + [c]
                if t == text:
                    yield ch
                yield from paths(c, ch)

        for chain in paths(root, []):
            for a in reversed(chain[:-1]):
                if a.attrib.get("clickable") == "true":
                    b = a.attrib.get("bounds") or ""
                    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
                    if m and m.group(1) != "0":
                        x = (int(m.group(1)) + int(m.group(3))) // 2
                        y = (int(m.group(2)) + int(m.group(4))) // 2
                        if 0 < y < 1850:
                            self.tap(x, y)
                            time.sleep(settle)
                            return True
            break
        return False

    def _at_page(self, frag: str) -> bool:
        return any(frag in (t or "") for t in self.texts() if (t or "").startswith("pages/"))

    def _goto_hotel_search(self) -> bool:
        """确保停在 book-hotel 搜索页（从首页进入；处理'仅查询'弹层）。"""
        for _ in range(4):
            if self._at_page("book-hotel/book-hotel"):
                self.tap_text("仅查询", settle=1.5)   # 关掉申请单弹层（无则忽略）
                return True
            self.dev.shell("input swipe 540 700 540 1500 300")   # 复位滚动到顶部（下拉）
            time.sleep(1)
            self._tap_text_container("酒店预订", settle=4)
            time.sleep(2)
        return self._at_page("book-hotel/book-hotel")

    def _select_city(self, city: str) -> bool:
        for attempt in range(2):
            # 城市字段：直点文本或点容器双保险
            if not self.tap_text("请选择城市", settle=5):
                self._tap_text_container("请选择城市", settle=6)
            # 轮询等城市页渲染（H5 慢，实测 5-8s）
            ts = []
            for _ in range(8):
                time.sleep(1.2)
                ts = self.texts()
                if any(t == "热门城市" for t in ts):
                    break
            if any(t == city for t in ts) and any(t == "热门城市" for t in ts):
                if self.tap_text(city, settle=3):
                    time.sleep(1)
                    if not any(t == "热门城市" for t in self.texts()):
                        return True   # 城市页已关=选择成功
            elif any((t or "").strip() == city for t in ts):
                return True           # 字段已是目标城市
            self.back(); time.sleep(1.5)
        return False

    def _select_dates(self, checkin: str, checkout: str) -> bool:
        """checkin/checkout: YYYY-MM-DD。日历多月滚动式，格子=节日+日+标记。"""
        import datetime as dt
        ci = dt.date.fromisoformat(checkin)
        co = dt.date.fromisoformat(checkout)
        # 点日期字段开日历：优先当前显示的入住日（MM月DD日），否则"今天"
        opened = False
        for n in self.dump().iter("node"):
            t = (n.attrib.get("text") or "").strip()
            if re.match(r"^\d{2}月\d{2}日$", t):
                pos = _center(n.attrib.get("bounds") or "")
                if pos != (0, 0):
                    self.tap(*pos); time.sleep(3); opened = True
                    break
        if not opened and not self.tap_text("今天", settle=3):
            return False
        ym = f"{ci.year}年{ci.month}月"
        root = self.dump()
        for _ in range(5):
            y = self._text_y(root, ym)
            if y is not None and 100 < y < 1500:
                break
            self.dev.shell("input swipe 540 1500 540 700 400")
            time.sleep(1.2)
            root = self.dump()
        else:
            return False
        y_end = self._text_y(root, f"{co.year}年{co.month}月") or 99999
        if not self._tap_day_in(root, ci.day, y, y_end):
            return False
        time.sleep(1)
        root2 = self.dump()
        y2 = self._text_y(root2, ym) or y
        y2_end = self._text_y(root2, f"{co.year}年{co.month}月") or 99999
        return self._tap_day_in(root2, co.day, y2, y2_end)

    def _text_y(self, root, txt: str):
        for n in root.iter("node"):
            if (n.attrib.get("text") or "").strip() == txt:
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
                if m:
                    return (int(m.group(2)) + int(m.group(4))) // 2
        return None

    def _tap_day_in(self, root, day: int, y_top: int, y_bottom: int) -> bool:
        pat = re.compile(r"^(?:[\u4e00-\u9fa5]{0,4})?" + str(day) + r"(?:入住|离店|在店)?$")
        for n in root.iter("node"):
            t = (n.attrib.get("text") or "").strip()
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
            if t and m and pat.match(t) and n.attrib.get("clickable") == "true":
                cy = (int(m.group(2)) + int(m.group(4))) // 2
                if y_top < cy < y_bottom and 0 < cy < 1850:
                    x = (int(m.group(1)) + int(m.group(3))) // 2
                    self.tap(x, cy)
                    return True
        return False

    def _read_hotel_list(self) -> List[Dict[str, Any]]:
        """读列表页价格行（滚动合并）；名称与价格按序配对。"""
        out: Dict[str, Dict[str, Any]] = {}
        all_texts: List[str] = []
        for _ in range(3):
            ts = self.texts()
            all_texts.extend(ts)
            for t in ts:
                t = (t or "").strip()
                pm = re.search(r"[¥￥]\s*([0-9][0-9,]*\.?[0-9]*)\s*起", t)
                if pm and t not in out:
                    out[t] = {"price_text": t,
                              "price": round(float(pm.group(1).replace(",", "")))}
            self.dev.shell("input swipe 540 1500 540 700 300")
            time.sleep(1.5)
        items = list(out.values())
        names = [t for t in all_texts
                 if t and ("酒店" in t or "公寓" in t) and 4 < len(t) < 30
                 and "关键字" not in t and "协议" not in t]
        for i, name in enumerate(dict.fromkeys(names)):
            if i < len(items):
                items[i]["name"] = name
        return items

    def probe(self, hotel: str = "", checkin: str = "", checkout: str = "",
              nights: int = 1, city: str = "杭州") -> Dict[str, Any]:
        """完整查价流程：登录检测→搜索页→城市→日期→查询→协议价列表。"""
        base = {"source": "app", "package": HERTZ_PACKAGE}
        try:
            self.launch()
        except Exception as e:  # noqa: BLE001
            return {**base, "ok": False, "reason": f"模拟器不可用: {str(e)[:120]}"}
        time.sleep(3)
        need = self._require_login(self.texts())
        if need:
            return need
        # 状态归位：连 back 回首页根（消除上轮残留浮层/子页）
        for _ in range(5):
            if self._at_page("index-travel") and not any(
                    t in ("历史记录", "选择日期") for t in self.texts()):
                break
            self.back(); time.sleep(1.2)
        if not self._goto_hotel_search():
            return {**base, "ok": False, "reason": "未能进入酒店搜索页（App 改版或卡顿）"}
        # 清理遮挡浮层（历史记录/日历）
        for _ in range(2):
            ts = self.texts()
            if any(t in ("历史记录", "选择日期") for t in ts):
                self.back(); time.sleep(1.5)
            else:
                break
        # 城市已是目标则跳过（字段显示城市名而非"请选择城市"）
        already = any((t or "").strip() == city for t in self.texts())
        if not already and not self._select_city(city):
            return {**base, "ok": False, "reason": f"城市选择失败: {city}（支持热门城市直点，其他待字母索引校准）"}
        if checkin and checkout:
            import re as _re9
            cur = [t.strip() for t in self.texts() if t and _re9.match(r"^[0-9]{1,2}月[0-9]{2}日$", t.strip())]
            want_ci = "%02d月%02d日" % (int(checkin[5:7]), int(checkin[8:10]))
            want_co = "%02d月%02d日" % (int(checkout[5:7]), int(checkout[8:10]))
            if want_ci in cur and want_co in cur:
                pass   # 日期已是目标
            elif not self._select_dates(checkin, checkout):
                return {**base, "ok": False, "reason": f"日期选择失败: {checkin}~{checkout}"}
        if not self.tap_text("查询", settle=8):
            return {**base, "ok": False, "reason": "查询按钮未找到"}
        time.sleep(3)
        items = self._read_hotel_list()
        matched = [i for i in items if hotel[:2] in (i.get("name") or "")] if hotel else []
        target = matched[0] if matched else None
        shot = self.screen("hertz_list.png")
        out = {**base, "ok": bool(items),
               "city": city, "checkin": checkin, "checkout": checkout,
               "items": items[:15],
               "target": target,
               "lowest": min((i["price"] for i in items), default=None),
               "screenshot": str(shot) if shot else None,
               "note": "南网协议价列表（'符合差标'为差旅标准过滤标签）；target 未命中看 items 全列表"}
        if not items:
            out["reason"] = "列表无价格（无房/加载慢/App 改版）"
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
