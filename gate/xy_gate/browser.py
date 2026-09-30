"""浏览器登录抓 Cookie（CDP 方式，零重量级依赖）。

流程：
1. 用系统已装的 Edge/Chrome（独立 user-data-dir，不碰用户主配置）打开
   https://www.goofish.com，带 --remote-debugging-port；
2. 用户在弹出的浏览器里扫码/登录；
3. 本模块轮询 CDP：/json/list 找到 goofish 页签 → WebSocket 调
   Network.getCookies 拿全部相关域 Cookie；
4. 校验含 unb（登录态标志）即完成。

不依赖 Playwright/Selenium——用户机器上本来就有浏览器。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

GOOFISH_URL = "https://www.goofish.com"
COOKIE_URLS = ["https://www.goofish.com", "https://m.goofish.com", "https://login.taobao.com"]
DEBUG_PORTS = (9222, 9223, 9224)
POLL_INTERVAL = 2.5
TIMEOUT_SEC = 300   # 5 分钟内未登录则超时

# Windows 常见浏览器路径（按优先级）；mac 走 /Applications 包内可执行文件；
# 其余 unix 走 PATH 上的 chromium/msedge/chrome
_WIN_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
_MAC_CANDIDATES = [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
_UNIX_CANDIDATES = ["chromium", "chromium-browser", "google-chrome", "msedge", "chrome"]


def find_browser() -> Optional[str]:
    import sys
    cands = _WIN_CANDIDATES if sys.platform == "win32" else \
        (_MAC_CANDIDATES if sys.platform == "darwin" else [])
    for cand in cands:
        if Path(cand).exists():
            return cand
    for name in _UNIX_CANDIDATES:
        p = shutil.which(name)
        if p:
            return p
    return None


def cookies_to_header(cookies: List[Dict[str, Any]]) -> str:
    """CDP cookies 列表 → 请求头 Cookie 串（保留有效项，去重）。"""
    seen: Dict[str, str] = {}
    for c in cookies:
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "").strip()
        if name and value:
            seen.setdefault(name, value)
    return "; ".join(f"{k}={v}" for k, v in seen.items())


def cookie_is_logged_in(header: str) -> bool:
    return "unb=" in header and len(header) > 200


class CookieCapture:
    """一次抓取会话（daemon 持有，单例）。"""

    def __init__(self, user_data_dir: Path):
        self.user_data_dir = user_data_dir
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.port: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        # 状态机：idle → running（浏览器已开，等登录）→ success / timeout / error
        self.state = "idle"
        self.message = ""
        self.cookie_header = ""
        self.started_at: Optional[float] = None

    # ── 生命周期 ──────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "message": self.message,
            "port": self.port,
            "elapsed_sec": int(time.time() - self.started_at) if self.started_at else 0,
            "timeout_sec": TIMEOUT_SEC,
            "unb": (self.cookie_header.split("unb=")[1].split(";")[0]
                    if "unb=" in self.cookie_header else ""),
        }

    async def start(self) -> Dict[str, Any]:
        if self._task and not self._task.done():
            return {"ok": False, "error": "已有一次抓取进行中"}
        browser = find_browser()
        if not browser:
            self.state, self.message = "error", "未找到 Edge/Chrome，请安装其一或改用手动粘贴 Cookie"
            return {"ok": False, "error": self.message}
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self.state, self.message = "running", "浏览器启动中…"
        self.started_at = time.time()
        self._task = asyncio.create_task(self._run(browser))
        return {"ok": True, "port": self.port}

    async def stop(self) -> Dict[str, Any]:
        self._stop.set()
        await self._cleanup()
        if self.state == "running":
            self.state, self.message = "idle", "已取消"
        return {"ok": True}

    async def _run(self, browser: str) -> None:
        try:
            await self._launch(browser)
            self.message = "浏览器已打开 goofish.com，请在弹出的窗口中扫码/登录"
            deadline = time.time() + TIMEOUT_SEC
            while not self._stop.is_set() and time.time() < deadline:
                header = await self._fetch_cookies()
                if header is not None:
                    if cookie_is_logged_in(header):
                        self.cookie_header = header
                        self.state = "success"
                        self.message = "登录成功，Cookie 已捕获"
                        break
                    self.message = ("检测到浏览器与 Cookie，但还没有登录态（缺 unb）——"
                                    "请在窗口中完成扫码登录")
                await asyncio.sleep(POLL_INTERVAL)
            else:
                if self._stop.is_set():
                    pass
                else:
                    self.state, self.message = "timeout", "5 分钟内未完成登录，已超时"
        except Exception as e:  # noqa: BLE001
            self.state, self.message = "error", f"抓取异常: {e}"
        finally:
            await self._cleanup()

    async def _launch(self, browser: str) -> None:
        last_err = ""
        for port in DEBUG_PORTS:
            args = [
                browser,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self.user_data_dir}",
                "--no-first-run", "--no-default-browser-check", "--restore-last-session=false",
                GOOFISH_URL,
            ]
            self.proc = await asyncio.create_subprocess_exec(*args)
            await asyncio.sleep(2.5)
            if await self._cdp_alive(port):
                self.port = port
                return
            last_err = f"port {port} 未响应"
        raise RuntimeError(f"浏览器调试端口起不来（{last_err}）")

    async def _cdp_alive(self, port: int) -> bool:
        try:
            async with aiohttp.ClientSession() as s, \
                    s.get(f"http://127.0.0.1:{port}/json/version",
                          timeout=aiohttp.ClientTimeout(total=2)) as r:
                return r.status == 200
        except Exception:
            return False

    async def _fetch_cookies(self) -> Optional[str]:
        """CDP：/json/list 找页签 → ws Network.getCookies。失败返回 None。"""
        if not self.port:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{self.port}/json/list",
                                 timeout=aiohttp.ClientTimeout(total=3)) as r:
                    targets = await r.json(content_type=None)
                ws_url = None
                for t in targets or []:
                    if t.get("type") == "page" and "goofish" in str(t.get("url", "")):
                        ws_url = t.get("webSocketDebuggerUrl")
                        break
                if not ws_url:
                    for t in targets or []:
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
                if not ws_url:
                    return None
                async with s.ws_connect(ws_url) as ws:
                    await ws.send_json({"id": 1, "method": "Network.getCookies",
                                        "params": {"urls": COOKIE_URLS}})
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        cookies = (data.get("result") or {}).get("cookies") or []
                        return cookies_to_header(cookies)
        except Exception:
            return None
        return None

    async def _cleanup(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
