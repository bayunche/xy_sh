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
MUMU_ADB = os.environ.get("XY_APP_ADB", "127.0.0.1:16384")   # MuMu 实例0；mac=MuMu Pro 默认同号段
PRICE_RE = re.compile(r"[¥￥]\s*([0-9][0-9,]{2,})")
LOGIN_HINTS = ("请输入手机号", "请输入密码", "请输入验证码", "账号密码登录", "登录")


def _norm(s: str) -> str:
    """OCR 比较归一化：去全部空白（WinRT 汉字间插空格）+ 剥前导杂符
    （OCR 常给行加 "-" / "·" 前缀，如 "-10月01日"）。"""
    s = re.sub(r"\s+", "", s or "")
    return re.sub(r"^[\-—·:：,。'']+", "", s)


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

    _winsdk_engine_cached = None   # 进程内 WinRT OCR 引擎（懒加载；None=未试，False=不可用）
    _vision_ocr_cached = None      # macOS Vision OCR（懒加载；None=未试，False=不可用）
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
        """按精确/前缀文本找第一个**可见**元素中心坐标。

        隐藏节点（bounds=[0,0][0,0]，虚拟化列表未渲染/历史残留）必须跳过——
        实测城市页的历史记录里藏着同名文本，点到 (0,0) 造成静默失败。"""
        for n in self.dump().iter("node"):
            t = (n.attrib.get("text") or "").strip()
            if t == text or (len(text) >= 4 and t.startswith(text)):
                b = n.attrib.get("bounds") or ""
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
                if not m:
                    continue
                x1, y1, x2, y2 = map(int, m.groups())
                if x2 > x1 and y2 > y1:          # 退化 bounds=隐藏，跳过
                    return ((x1 + x2) // 2, (y1 + y2) // 2)
        return None

    def visible_nodes(self):
        """[（文本, 可点, 中心坐标, y中心)…] 只含屏幕内真实渲染的节点。"""
        out = []
        for n in self.dump().iter("node"):
            t = (n.attrib.get("text") or "").strip()
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
            if not (t and m):
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            if x2 > x1 and y2 > y1 and 0 < (y1 + y2) // 2 < 1850:
                out.append((t, n.attrib.get("clickable") == "true",
                            ((x1 + x2) // 2, (y1 + y2) // 2), (y1 + y2) // 2))
        return out

    def fg_visible_nodes(self):
        """前台页面子树的可见节点：背景页同名节点（如历史记录里的城市/价格）
        会造成误点误读，一切交互定位都应基于本方法。"""
        _, scope = self._fg_scope()
        out = []
        for n in scope.iter("node"):
            t = (n.attrib.get("text") or "").strip()
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
            if not (t and m):
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            if x2 > x1 and y2 > y1 and 0 < (y1 + y2) // 2 < 1850:
                out.append((t, n.attrib.get("clickable") == "true",
                            ((x1 + x2) // 2, (y1 + y2) // 2), (y1 + y2) // 2))
        return out

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

    # ---- 截图存档（nemu → adb screencap → Windows 层抓屏兜底）----
    def screen(self, name: str = "rpa_screen.png") -> Optional[Path]:
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / name
        n = self.nemu
        if n:
            try:
                n.capture().save(path)
                return path
            except Exception:
                pass
        try:
            self._adb_screenshot().save(path)   # mac（MuMu Pro/真机）主通道
            return path
        except Exception:
            pass
        import sys as _sys
        if _sys.platform != "win32":
            return None
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

    # ---- 像素层真相（dump 分不清前后台页：覆盖页节点也带真实 bounds）----
    @classmethod
    def _vision_ocr_cached(cls):
        """macOS Vision OCR（pyobjc，zh-Hans，0.1-0.4s/张）——WinRT OCR 的
        mac 对应物。None=未试，False=不可用（非 mac / 未装 pyobjc）。"""
        if cls._vision_ocr_cached is None:
            try:
                import Vision  # noqa: F401  (pyobjc-framework-Vision)
                from Foundation import NSURL  # noqa: F401
                cls._vision_ocr_cached = True
            except Exception:
                cls._vision_ocr_cached = False
        return cls._vision_ocr_cached

    def _ocr_vision(self, png: Path, img_w: int, img_h: int) -> Optional[List[Dict[str, Any]]]:
        import Vision
        from Foundation import NSURL
        if not self._vision_ocr_cached():
            return None
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
            NSURL.fileURLWithPath_(str(png)), None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLanguages_(["zh-Hans", "en-US"])
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"Vision OCR 失败: {err}")
        out = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)
            if not cand:
                continue
            top = cand[0]
            bb = obs.boundingBox()   # 归一化坐标，原点在**左下**
            x = bb.origin.x * img_w
            w = bb.size.width * img_w
            h = bb.size.height * img_h
            y = (1.0 - bb.origin.y - bb.size.height) * img_h   # 转左上原点
            out.append({"text": str(top.string()), "x": int(x), "y": int(y),
                        "w": int(w), "h": int(h)})
        return out

    @classmethod
    def _winsdk_engine(cls):
        """进程内 WinRT OCR（winsdk 包，同 ocr.ps1 引擎但免 PowerShell 进程
        拉起：实测 0.28s vs ~2s，一次 probe 30-45 次 OCR 是最大耗时项）。"""
        if cls._winsdk_engine_cached is None:
            try:
                from winsdk.windows.media.ocr import OcrEngine
                from winsdk.windows.globalization import Language
                cls._winsdk_engine_cached = OcrEngine.try_create_from_language(
                    Language("zh-CN")) or False
            except Exception:
                cls._winsdk_engine_cached = False
        return cls._winsdk_engine_cached or None

    def _ocr_winsdk(self, png: Path) -> Optional[List[Dict[str, Any]]]:
        import asyncio
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile, FileAccessMode
        eng = self._winsdk_engine()
        if not eng:
            return None

        async def _run():
            sf = await StorageFile.get_file_from_path_async(str(png))
            stream = await sf.open_async(FileAccessMode.READ)
            dec = await BitmapDecoder.create_async(stream)
            bmp = await dec.get_software_bitmap_async()
            res = await eng.recognize_async(bmp)
            out = []
            for line in res.lines:
                ws = list(line.words)
                if not ws:
                    continue
                x1 = min(w.bounding_rect.x for w in ws)
                y1 = min(w.bounding_rect.y for w in ws)
                x2 = max(w.bounding_rect.x + w.bounding_rect.width for w in ws)
                y2 = max(w.bounding_rect.y + w.bounding_rect.height for w in ws)
                out.append({"text": line.text, "x": int(x1), "y": int(y1),
                            "w": int(x2 - x1), "h": int(y2 - y1)})
            return out

        return asyncio.run(_run())

    def _adb_screenshot(self):
        """adb screencap 截图（mac 主通道：MuMu Pro Mac / 真机）。

        MuMu **Windows** 的 screencap 抓不到 GPU 合成层（返回白图）——检测到
        近似单色帧即抛错提示走 nemu，避免下游 OCR 拿到空结果瞎猜。"""
        img = self.dev.screenshot()   # adbutils 内置（自动处理 \r\n），自带 adb 二进制
        small = img.convert("L").resize((54, 96))
        px = list(small.getdata())
        mean = sum(px) / len(px)
        var = sum((p - mean) ** 2 for p in px) / len(px)
        if var < 30:
            raise RuntimeError(
                "adb screencap 返回空白/单色帧——MuMu Windows 的 GPU 合成层限制"
                "（Windows 上截图必须走 nemu 通道）；真机/MuMu Pro Mac 不应出现此错")
        return img

    def _capture(self):
        """设备渲染帧：nemu 官方接口（Windows）→ adb screencap（mac/真机）。"""
        n = self.nemu
        if n:
            return n.capture()
        return self._adb_screenshot()

    def ocr(self, region=None, scale: int = 1) -> List[Dict[str, Any]]:
        """设备截图 + OCR → [{text, x, y, w, h, cx, cy}…]。

        坐标=设备像素（截图与 tap 同一坐标系）。region 裁剪 + scale 放大可救
        小字/高亮背景格子。OCR 后端链：winsdk(Win) → Vision(mac) → ocr.ps1
        (Win 兜底；JSON 落文件，stdout 编码不可靠)。"""
        import json as _json
        import subprocess
        import sys as _sys
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        img = self._capture()
        if region:
            img = img.crop(region)
        if scale > 1:
            from PIL import Image
            img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        png = self.shots_dir / "_ocr_tmp.png"
        img.save(png)
        data = None
        try:
            data = self._ocr_winsdk(png)
        except Exception:
            data = None
        if data is None:
            try:
                data = self._ocr_vision(png, img.width, img.height)
            except Exception:
                data = None
        if data is None and _sys.platform == "win32":
            ocr_ps1 = Path(__file__).resolve().parent.parent / "tools" / "ocr.ps1"
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(ocr_ps1), str(png)],
                capture_output=True, timeout=40)
            raw = (self.shots_dir / "_ocr_tmp.png.ocr.json").read_text("utf-8-sig").strip()
            data = _json.loads(raw) if raw and raw != "[]" else []
        if data is None:
            raise RuntimeError("无可用 OCR 后端（Windows: winsdk/ocr.ps1；macOS: "
                               "pip install pyobjc-framework-Vision）")
        ox, oy = (region[0], region[1]) if region else (0, 0)
        for d in data:
            d["x"] = ox + d["x"] // scale
            d["y"] = oy + d["y"] // scale
            d["w"] = d["w"] // scale
            d["h"] = d["h"] // scale
            d["cx"] = d["x"] + d["w"] // 2
            d["cy"] = d["y"] + d["h"] // 2
        return data

    def ocr_texts(self, region=None) -> List[str]:
        return [_norm(d["text"]) for d in self.ocr(region)]

    def ocr_find(self, text: str) -> Optional[Tuple[int, int]]:
        """按文本找 OCR 行中心：归一化后先精确匹配，无则包含匹配取最短行
        （"查询"不能命中"仅查询"这种长行）。"""
        t = _norm(text)
        data = self.ocr()
        hits = [d for d in data if _norm(d["text"]) == t]
        if not hits:
            hits = sorted((d for d in data if t in _norm(d["text"])),
                          key=lambda d: len(_norm(d["text"])))
        return (hits[0]["cx"], hits[0]["cy"]) if hits else None

    def ocr_tap(self, text: str, settle: float = 1.5, dy: int = 0,
                dx: int = 0) -> bool:
        """点 OCR 文本行中心；dy/dx 偏移用于点文本上方/旁边的真实触控目标
        （如首页菜单的图标在标签上方 ~110px，标签本身不响应点击）。"""
        pos = self.ocr_find(text)
        if not pos:
            return False
        self.tap(pos[0] + dx, pos[1] + dy)
        time.sleep(settle)
        return True

    def screen_state(self) -> str:
        """按像素判前台页（DOM 分不清覆盖页；OCR 文本特征从具体到一般）。"""
        s = "".join(self.ocr_texts())
        if any(_norm(h) in s for h in LOGIN_HINTS):
            return "login"
        if "热门城市" in s:
            return "city"
        if "选择日期" in s:
            return "calendar"
        if "凌晨" in s and "提示" in s and "入住" in s:
            return "wee-hours"   # 凌晨查当天入住弹窗（盖在搜索页上，须先判）
        if "出差申请" in s and "仅查询" in s:
            return "apply-overlay"
        if "查询" in s and ("请选择城市" in s or "关键字" in s):
            return "search"
        if "酒店预订" in s and ("机票预订" in s or "火车预订" in s):
            return "home"
        if "¥" in s or "￥" in s or "起" in s:
            return "list"
        return "other"

    def _dismiss_wee_hours(self) -> bool:
        """凌晨（~0-6点）查当天入住弹「是否凌晨入住」提示——点「否」保持
        当天入住（点「是」会把入住日期改成前一天，破坏查询口径）。

        OCR 常漏读/误读弹窗单字按钮（实测"否"整行漏读、"是"→"疋"），
        dump 兜底找可点的"否"。"""
        if self.ocr_tap("否", settle=1.5):
            return True
        for n in self.dump().iter("node"):
            if (n.attrib.get("text") or "").strip() == "否" \
                    and n.attrib.get("clickable") == "true":
                pos = _center(n.attrib.get("bounds") or "")
                if pos != (0, 0):
                    self.tap(*pos)
                    time.sleep(1.5)
                    return True
        return False


class HertzApp(RpaClient):
    """赫兹商旅查价流程（流程节点按登录后界面逐步校准）。"""

    def state(self) -> Dict[str, Any]:
        import sys as _sys
        out: Dict[str, Any] = {"adb_addr": self.adb_addr,
                               "platform": _sys.platform}
        try:
            out["connected"] = True
            out["android"] = self.dev.shell("getprop ro.build.version.release").strip()
            out["app_pid"] = self.dev.shell(f"pidof {HERTZ_PACKAGE}").strip() or ""
            out["app_installed"] = bool(self.dev.shell(
                f"pm list packages | grep {HERTZ_PACKAGE}").strip())
            out["ocr_backend"] = ("winsdk" if self._winsdk_engine()
                                  else "vision" if self._vision_ocr_cached() else "ps1/无")
            ts = self.texts()
            joined = " ".join(ts)
            out["at_login"] = any(h in joined for h in LOGIN_HINTS)
            out["screen_texts"] = ts[:12]
        except Exception as e:  # noqa: BLE001
            out["connected"] = False
            out["error"] = str(e)[:160]
            if _sys.platform == "darwin":
                out["hint"] = ("mac 需要：MuMu Player Pro（Apple Silicon，装「赫兹商旅」"
                               "App 并人工登录一次）保持运行；连接地址可用环境变量 "
                               "XY_APP_ADB 覆盖（默认 127.0.0.1:16384）")
            else:
                out["hint"] = "Windows 需要：MuMu 模拟器 12 保持运行（ADB 16384）"
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

    def _fg_scope(self):
        """（前台页名, 前台页节点）。Weex 页面栈叠加在 dump 里=多份 pages/* 节点，
        **文档序最后一层**才是前台；返回栈里的同名旧页会造成误判。"""
        root = self.dump()
        last = None
        for n in root.iter("node"):
            t = (n.attrib.get("text") or "").strip()
            if t.startswith("pages/"):
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
                if m and int(m.group(3)) > int(m.group(1)):
                    last = (t, n)
        return last if last else ("", root)

    def _fg_page_name(self) -> str:
        return self._fg_scope()[0]

    def _at_page(self, frag: str) -> bool:
        return frag in self._fg_page_name()

    def ensure_search_ready(self, timeout_s: int = 30) -> bool:
        """搜索页就绪：像素判定无弹层遮挡（出差申请弹层/城市浮层）。"""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.screen_state()
            if st == "search":
                return True
            if st == "apply-overlay":
                self.ocr_tap("仅查询", settle=1.5)   # 出差申请弹层的跳过钮
                continue
            if st == "wee-hours":
                self._dismiss_wee_hours()
                continue
            if st == "city":
                self.back(); time.sleep(1.2)
                continue
            if st == "other" and "出差申请" in "".join(self.ocr_texts()):
                # 弹层在但"仅查询"没露出（折叠/半屏）→ 点一次，不行滚一下再点
                if not self.ocr_tap("仅查询", settle=1.5):
                    self.dev.shell("input swipe 540 1300 540 1000 300")
                    time.sleep(1)
                continue
            time.sleep(1)
        return False

    def _goto_hotel_search(self) -> bool:
        """确保停在酒店搜索页（像素判定；从首页进入；处理'仅查询'弹层）。"""
        for _ in range(4):
            st = self.screen_state()
            if st == "search":
                return True
            if st == "apply-overlay":
                self.ocr_tap("仅查询", settle=2.0)
                continue
            if st in ("city", "calendar"):
                self.back(); time.sleep(1.2)
                continue
            # home / other：点"酒店预订"**图标**（真实触控目标=标签上方 ~110px
            # 的 147x147 图标 TextView，标签本身不响应）；**不要先下拉复位**
            # （冷启后下拉会触发首页刷新动画，吃掉后续点击）。
            if self.ocr_tap("酒店预订", settle=3.5, dy=-110):
                time.sleep(1)
                continue
            self.dev.shell("input swipe 540 700 540 1500 300")
            time.sleep(1.2)
            self._tap_text_container("酒店预订", settle=4)
        return self.screen_state() == "search"

    def wait_state(self, target: str, timeout_s: float = 18,
                   poll: float = 1.2) -> str:
        """等屏幕进入目标状态；中途自动清"出差申请"弹层（它在流程里会反复
        弹出：进搜索页、点城市字段、点日期字段后都可能再弹）。"""
        deadline = time.time() + timeout_s
        st = self.screen_state()
        while st != target and time.time() < deadline:
            if st == "apply-overlay":
                self.ocr_tap("仅查询", settle=2.0)
            elif st == "wee-hours":
                self._dismiss_wee_hours()
            else:
                time.sleep(poll)
            st = self.screen_state()
        return st

    def _city_field_ocr(self) -> Optional[Tuple[str, int, int]]:
        """搜索页城市字段的（归一化文本, cx, cy）：OCR 行里 y 560~760、左半屏
        的普通文本（排除右侧"当前位置"提示）。新会话默认=定位城市（实测鞍山）。"""
        for d in self.ocr():
            t = _norm(d["text"])
            if 560 < d["cy"] < 760 and d["cx"] < 640 and t \
                    and "位置" not in t and t not in ("当前位置",):
                return (t, d["cx"], d["cy"])
        return None

    def _select_city(self, city: str) -> bool:
        """选城市（像素闭环 + dump 兜底）：点字段开城市页 → 点城市 → OCR 复核字段。

        城市格子里有 OCR 硬盲区（部分格子样式导致整格漏识别，实测杭州），
        OCR 找不到时用 dump 同名节点按 y 最靠上兜底——背景搜索页"历史记录"
        的同名节点在最底部（y>1400）被 y 范围排掉，误点由字段级验证兜住。"""
        city_n = _norm(city)
        for attempt in range(3):
            field = self._city_field_ocr()
            if field and field[0] == city_n:
                return True                       # 字段已是目标城市
            if self.screen_state() != "city":
                if field:
                    self.tap(field[1], field[2])  # 点字段本身开城市页
                else:
                    self.ocr_tap("请选择城市", settle=5)
            # 等城市页（H5 慢 5-8s；点字段可能再次触发申请单弹层，wait_state 会清）
            st = self.wait_state("city", timeout_s=18)
            if st != "city":
                self.back(); time.sleep(1.5)
                continue
            time.sleep(2.0)   # H5 出标题后仍在重排，点早一拍=点到错位内容
            pos = self.ocr_find(city)
            if not pos:
                cands = []
                for n in self.dump().iter("node"):
                    if (n.attrib.get("text") or "").strip() == city:
                        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                                     n.attrib.get("bounds") or "")
                        if m:
                            x1, y1, x2, y2 = map(int, m.groups())
                            cy = (y1 + y2) // 2
                            if x2 > x1 and 300 < cy < 1400:
                                cands.append(((x1 + x2) // 2, cy))
                pos = min(cands, key=lambda p: p[1]) if cands else None
            if pos:
                self.tap(*pos)
                time.sleep(2)
                field = self._city_field_ocr()
                if field and field[0] == city_n:
                    return True                   # 字段已变=真成功
            self.back(); time.sleep(1.5)
        return False

    def _select_dates(self, checkin: str, checkout: str) -> bool:
        """选日期（像素开合验证 + dump 格子定位）。

        日历=多月连滚（手风琴+虚拟化），格子文本"节日+日"或"日+标记"
        （如"国庆节1"/"30入住"），选入住→选离店自动关闭，无确认按钮。"""
        import datetime as dt
        ci = dt.date.fromisoformat(checkin)
        co = dt.date.fromisoformat(checkout)
        want_ci = "%02d月%02d日" % (ci.month, ci.day)   # 日期栏格式带前导零
        want_co = "%02d月%02d日" % (co.month, co.day)
        if self._date_row_ok(want_ci, want_co):
            return True                            # 日期栏已是目标
        # 开日历：已开着不能再点日期字段（toggle 会关掉）
        if self.screen_state() != "calendar":
            hit = None
            for d in self.ocr():
                t = _norm(d["text"])
                if re.fullmatch(r"\d{1,2}月\d{1,2}日", t) and d["cx"] < 420:
                    hit = (d["cx"], d["cy"])       # 入住侧日期字段
                    break
            if not hit:
                p = self.ocr_find("今天")          # 兜底：点"今天"标签左侧
                if not p:
                    return False
                hit = (p[0] - 80, p[1])
            self.tap(*hit)
            time.sleep(2.5)
        # 点日期字段也可能再弹申请单，wait_state 自动清
        if self.wait_state("calendar", timeout_s=15) != "calendar":
            return False
        # 先入住后离店；今天默认已标"入住"，重选即覆盖
        if not self._tap_calendar_day(ci):
            return False
        time.sleep(1.2)
        if not self._tap_calendar_day(co):
            return False
        time.sleep(1.5)
        if self._date_row_ok(want_ci, want_co):
            return True
        # 没自动关：可能离店日没点上，补一次
        if self.screen_state() == "calendar" and self._tap_calendar_day(co):
            time.sleep(1.5)
        return self._date_row_ok(want_ci, want_co)

    def _date_row_ok(self, want_ci: str, want_co: str) -> bool:
        s = "".join(self.ocr_texts())
        return want_ci in s and want_co in s

    def _tap_calendar_day(self, d) -> bool:
        """滚动到目标日所在月（标题 y 500~1400），dump 找格子（月标题 y 之下
        的唯一文本模式）点击。"""
        import datetime as _dt
        if not isinstance(d, _dt.date):
            d = _dt.date.fromisoformat(str(d))
        ym = f"{d.year}年{d.month}月"
        for attempt in range(10):
            root = self.dump()
            y_top = self._text_y(root, ym)
            if y_top is None or y_top == 0 or y_top > 1400 or y_top < 300:
                # 目标月不在手：按渲染中的最近月判方向滚动
                self.dev.shell(self._calendar_swipe_cmd(root, ym))
                time.sleep(1.0)
                continue
            if self._tap_day_between(root, d.day, y_top, 99999):
                return True
            self.dev.shell("input swipe 540 1500 540 1000 300")   # 标题可见但格子屏外→上滚
            time.sleep(1.0)
        return False

    def _text_y(self, root, txt: str):
        for n in root.iter("node"):
            if (n.attrib.get("text") or "").strip() == txt:
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
                if m:
                    return (int(m.group(2)) + int(m.group(4))) // 2
        return None

    def _tap_day_between(self, root, day: int, y_top: int, y_bottom: int) -> bool:
        """在 [y_top, y_bottom) 带内找日格点击。格子文本="节日+日"或"日+标记"
        （如"国庆节1"/"30入住"）；格子本身必须 clickable 且完整在屏内。"""
        pat = re.compile(r"^(?:[\u4e00-\u9fa5]{0,4})?" + str(day) +
                         r"(?:入住|离店|在店)?$")
        for n in root.iter("node"):
            t = (n.attrib.get("text") or "").strip()
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.attrib.get("bounds") or "")
            if t and m and pat.fullmatch(t) and n.attrib.get("clickable") == "true":
                x1, y1, x2, y2 = map(int, m.groups())
                cy = (y1 + y2) // 2
                if y_top < cy < y_bottom and 150 < cy < 1780:
                    self.tap((x1 + x2) // 2, cy)
                    return True
        return False

    @staticmethod
    def _month_value(ym: str):
        m = re.match(r"(\d{4})年(\d{1,2})月", ym or "")
        return int(m.group(1)) * 12 + int(m.group(2)) if m else None

    def _calendar_swipe_cmd(self, root, target_ym: str) -> str:
        tv = self._month_value(target_ym)
        vis = []
        for n in root.iter("node"):
            tt = (n.attrib.get("text") or "").strip()
            if re.match(r"^20\d{2}年\d{1,2}月$", tt):
                yy = self._text_y(root, tt)
                if yy and yy > 0:
                    vis.append((self._month_value(tt), yy))
        if vis and tv is not None:
            nearest = min(vis, key=lambda v: abs(v[0] - tv))
            if nearest[0] > tv:
                return "input swipe 540 900 540 1500 350"    # 目标在上方→下拉
        return "input swipe 540 1500 540 800 350"            # 默认上滚

    def _read_hotel_list(self, max_screens: int = 5) -> List[Dict[str, Any]]:
        """读列表页价格行（OCR 像素层，滚动合并）；酒店名与其后出现的价格按
        阅读序配对。连续两屏无新增才收手（列表加载/滚动动画中会有假空屏）。"""
        out: Dict[str, Dict[str, Any]] = {}
        cur_name: Optional[str] = None
        no_gain = 0
        for screen in range(max_screens):
            before = len(out)
            for d in sorted(self.ocr(), key=lambda d: (d["cy"], d["cx"])):
                t = _norm(d["text"])
                pm = re.search(r"[¥￥]\s*([0-9][0-9,]*\.?[0-9]*)\s*起", t)
                if pm:
                    key = cur_name or t
                    if key not in out:
                        out[key] = {"price_text": d["text"], "name": cur_name,
                                    "price": round(float(pm.group(1).replace(",", "")))}
                elif 4 < len(t) < 30 and any(k in t for k in ("酒店", "公寓", "宾馆")) \
                        and "关键字" not in t and "酒店名" not in t and "/" not in t \
                        and "协议" not in t and "差标" not in t:
                    cur_name = t
            no_gain = no_gain + 1 if (screen >= 1 and len(out) == before) else 0
            if no_gain >= 2:
                break                      # 连续两屏无新增 → 到底了
            self.dev.shell("input swipe 540 1500 540 700 300")
            time.sleep(1.5)
        return list(out.values())

    def cold_launch(self, pkg: str = HERTZ_PACKAGE, wait_s: int = 30) -> bool:
        """force-stop 冷启动 + 等首页就绪。

        App 渲染层在长时间 RPA 操控后会崩成纯灰屏（DOM 树完好、点击全静默
        失效），冷启动是唯一恢复手段——每轮 probe 前必须执行。"""
        self.dev.shell(f"am force-stop {pkg}")
        time.sleep(2)
        self.dev.shell(f"am start -n {HERTZ_MAIN}")
        deadline = time.time() + wait_s
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                if self.screen_state() == "home":
                    return True
            except Exception:   # noqa: BLE001  冷启初期 nemu/OCR 可能瞬时不可用
                pass
        return False

    def probe(self, hotel: str = "", checkin: str = "", checkout: str = "",
              nights: int = 1, city: str = "杭州") -> Dict[str, Any]:
        """完整查价流程：冷启动→登录检测→搜索页→城市→日期→查询→协议价列表。"""
        base = {"source": "app", "package": HERTZ_PACKAGE}
        try:
            self.cold_launch()
        except Exception as e:  # noqa: BLE001
            return {**base, "ok": False, "reason": f"模拟器不可用: {str(e)[:120]}"}
        need = self._require_login([v[0] for v in self.fg_visible_nodes()])
        if need:
            return need
        if not self._goto_hotel_search():
            return {**base, "ok": False, "reason": "未能进入酒店搜索页（App 改版或卡顿）"}
        if not self.ensure_search_ready():
            return {**base, "ok": False, "reason": "搜索页被弹层占用且无法清理（出差申请/城市浮层）"}
        # 清理遮挡浮层（历史记录/日历）
        for _ in range(2):
            names = [v[0] for v in self.fg_visible_nodes()]
            if any(t in ("历史记录", "选择日期") for t in names):
                self.back(); time.sleep(1.5)
            else:
                break
        # 城市字段已显示目标城市才跳过（像素级字段判定）
        _f = self._city_field_ocr()
        field_is_city = bool(_f and _f[0] == _norm(city))
        if not field_is_city and not self._select_city(city):
            return {**base, "ok": False, "reason": f"城市选择失败: {city}（支持热门城市直点，其他待字母索引校准）"}
        if checkin and checkout:
            want_ci = "%02d月%02d日" % (int(checkin[5:7]), int(checkin[8:10]))
            want_co = "%02d月%02d日" % (int(checkout[5:7]), int(checkout[8:10]))
            if not self._date_row_ok(want_ci, want_co):
                if not self._select_dates(checkin, checkout):
                    return {**base, "ok": False, "reason": f"日期选择失败: {checkin}~{checkout}"}
        # 查询→列表（点查询也可能再弹申请单弹层/凌晨入住提示，逐状态推进）
        at_list = False
        for _ in range(5):
            st = self.screen_state()
            if st == "list":
                at_list = True
                break
            if st == "apply-overlay":
                self.ocr_tap("仅查询", settle=2.0)
                continue
            if st == "wee-hours":
                self._dismiss_wee_hours()
                continue
            if st == "search":
                if not self.ocr_tap("查询", settle=4):
                    time.sleep(1.5)
                continue
            time.sleep(1.5)
        if not at_list:
            return {**base, "ok": False, "reason": f"未进入价格列表页（state={st}）"}
        time.sleep(2)
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
