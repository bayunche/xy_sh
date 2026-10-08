"""一键安装引导：MuMu 模拟器 + 赫兹商旅 App（国内源优先）。

- MuMu mac（MuMuPlayer Pro）：直链来自 Homebrew cask 元数据（brew 官方 API），
  文件本体在网易 gdl CDN（国内）；tar.gz 解压即得 MuMuPlayer.app，拷入
  /Applications 并启动——全程自动。
- MuMu win：官网下载页是 JS 渲染拿不到稳定直链，直接开官网下载页让浏览器下载。
- 赫兹商旅 App：本地 data/emulator/hertz.apk 优先（打包不随包），否则走应用宝
  wap 版直链（手机 UA → imtt.dd.qq.com，国内）下载后 adb install。

进度状态在模块级 STATUS（前端轮询 /api/hotel/setup-status）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict

STATUS: Dict[str, Dict[str, Any]] = {
    "mumu": {"state": "idle"},
    "app": {"state": "idle"},
}

BREW_CASK_API = "https://formulae.brew.sh/api/cask/mumuplayer.json"
MUMU_WIN_PAGE = "https://mumu.163.com/"
HERTZ_APK_LOCAL = Path(__file__).resolve().parent.parent.parent / "data" / "emulator" / "hertz.apk"
QQ_WAP = "https://a.app.qq.com/o/simple.jsp?pkgname=com.ygsoft.mup.businesstravelnw"
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
_BUSY = ("resolving", "downloading", "installing")


def _set(what: str, **kw: Any) -> None:
    STATUS[what].update(kw)
    STATUS[what]["ts"] = time.strftime("%H:%M:%S")


def _download(url: str, dest: Path, what: str, ua: str = MOBILE_UA) -> Path:
    """流式下载，进度每 ~8MB 刷新一次到 STATUS。"""
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=90) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done, next_log = 0, 8 << 20
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if done >= next_log:
                _set(what, progress=f"{done // 1048576}/{total // 1048576 if total else '?'}MB")
                next_log = done + (8 << 20)
    if dest.stat().st_size < 1_000_000:
        raise RuntimeError(f"下载文件过小（{dest.stat().st_size}B），疑似镜像页/被拦截: {url}")
    _set(what, progress=f"{dest.stat().st_size // 1048576}MB 完成")
    return dest


def _mac_mumu_url() -> tuple:
    """brew cask API 拿当前版 MuMuPlayerPro 直链（网易 gdl CDN，国内）。"""
    with urllib.request.urlopen(BREW_CASK_API, timeout=25) as r:
        d = json.load(r)
    url, ver = d.get("url") or "", d.get("version") or "?"
    if not url:
        raise RuntimeError("brew cask 元数据无下载直链")
    return url, ver


def _install_mumu() -> None:
    try:
        if sys.platform == "darwin":
            if Path("/Applications/MuMuPlayer.app").exists():
                subprocess.Popen(["open", "-a", "MuMuPlayer"])
                _set("mumu", state="done", progress="已安装（正在启动）")
                return
            _set("mumu", state="resolving", progress="取最新版直链…")
            url, ver = _mac_mumu_url()
            dl = Path.home() / "Downloads"
            tgz = _download(url, dl / f"MuMuPlayerPro-{ver}.tar.gz", "mumu")
            _set("mumu", state="installing", progress="解压并安装到 /Applications…")
            tmp = dl / f"mumu_setup_{int(time.time())}"
            tmp.mkdir(exist_ok=True)
            subprocess.run(["tar", "-xzf", str(tgz), "-C", str(tmp)],
                           check=True, timeout=900)
            app = next(tmp.rglob("MuMuPlayer.app"), None)
            if not app:
                raise RuntimeError("包里没找到 MuMuPlayer.app")
            if Path("/Applications/MuMuPlayer.app").exists():
                shutil.rmtree("/Applications/MuMuPlayer.app", ignore_errors=True)
            shutil.move(str(app), "/Applications/MuMuPlayer.app")
            shutil.rmtree(tmp, ignore_errors=True)
            tgz.unlink(missing_ok=True)
            subprocess.Popen(["open", "-a", "MuMuPlayer"])
            _set("mumu", state="done", progress=f"v{ver} 已安装并启动")
        elif sys.platform == "win32":
            # 官网 JS 渲染无稳定直链 → 开官网下载页（浏览器自动开始下载）
            os.startfile(MUMU_WIN_PAGE)  # noqa: PTH  ShellExecute 打开默认浏览器
            _set("mumu", state="done",
                 progress="已打开官网下载页，请在浏览器完成下载安装（安装时记得勾选默认实例/ADB）")
        else:
            _set("mumu", state="error", error="仅支持 Windows / macOS")
    except Exception as e:  # noqa: BLE001
        _set("mumu", state="error", error=str(e)[:220])


_MUMU_STARTED = False


def _start_mumu_once() -> None:
    """拉起 MuMu 播放器（幂等一次；win=官方 mumu-cli control launch，
    mac=open -a MuMuPlayer）。ADB 端口 16384=实例 0（vmindex 0 基）。"""
    global _MUMU_STARTED
    if _MUMU_STARTED:
        return
    _MUMU_STARTED = True
    try:
        if sys.platform == "win32":
            cli = Path(r"C:\Program Files\Netease\MuMu Player 12\nx_main\mumu-cli.exe")
            if cli.exists():
                subprocess.Popen([str(cli), "control", "-v", "0", "launch"],
                                 cwd=str(cli.parent))
        elif sys.platform == "darwin":
            if Path("/Applications/MuMuPlayer.app").exists():
                subprocess.Popen(["open", "-a", "MuMuPlayer"])
    except Exception:  # noqa: BLE001
        pass


def _mumu_adb_ready(adb_addr: str, timeout_s: float = 240) -> bool:
    """MuMu ADB 端口就绪前反复连接（没起就拉起一次；实测冷启动 Android
    就绪常超 90s，给足 4 分钟）。"""
    import adbutils
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            adbutils.adb.connect(adb_addr, timeout=5)
            if adb_addr in [d.serial for d in adbutils.adb.device_list()]:
                return True
        except Exception:  # noqa: BLE001
            pass
        _start_mumu_once()
        time.sleep(5)
    return False


def _install_app() -> None:
    try:
        from .ota_app import HertzApp
        import adbutils
        app = HertzApp()
        try:
            online = app.adb_addr in [d.serial for d in adbutils.adb.device_list()]
        except Exception:  # noqa: BLE001
            online = False
        if not online:
            _set("app", state="resolving", progress="MuMu 未运行，正在拉起…")
            if not _mumu_adb_ready(app.adb_addr):
                raise RuntimeError("MuMu 无法启动或 ADB 连不上（先点「一键安装 MuMu 模拟器」）")
        app.dev   # 建立连接
        apk: Path
        if HERTZ_APK_LOCAL.exists() and HERTZ_APK_LOCAL.stat().st_size > 10_000_000:
            apk = HERTZ_APK_LOCAL
        else:
            _set("app", state="resolving", progress="应用宝取直链…")
            req = urllib.request.Request(QQ_WAP, headers={"User-Agent": MOBILE_UA})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            m = re.search(r'https?://[^"\'\s<>]+\.apk[^"\'\s<>]*', html)
            if not m:
                raise RuntimeError("应用宝未返回 APK 直链（页面改版？可手动下载后放到 "
                                   f"{HERTZ_APK_LOCAL}）")
            apk = _download(m.group(0), Path.home() / "Downloads" / "hertz-latest.apk", "app")
        _set("app", state="installing", progress="adb 安装到模拟器…")
        app.dev.install(str(apk))
        app.launch()
        _set("app", state="done", progress="已安装并启动——请在模拟器里人工登录一次（长期有效）")
    except Exception as e:  # noqa: BLE001
        _set("app", state="error", error=str(e)[:220])


def start_install(what: str) -> Dict[str, Any]:
    if what not in ("mumu", "app"):
        return {"ok": False, "error": f"未知安装目标: {what}"}
    if STATUS[what].get("state") in _BUSY:
        return {"ok": True, "started": False, "status": STATUS[what]}
    STATUS[what] = {"state": "resolving"}
    target = _install_mumu if what == "mumu" else _install_app
    threading.Thread(target=target, daemon=True).start()
    return {"ok": True, "started": True}


def setup_status() -> Dict[str, Any]:
    return {"mumu": dict(STATUS["mumu"]), "app": dict(STATUS["app"])}
