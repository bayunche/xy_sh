"""OTA 酒店查价（携程镜像 Trip.com / 同程 / 酒店官网）。

架构：常驻 CDP 浏览器（持久 profile data/ota-profile，登录态长期保留）+
每源一个渲染提取器。所有查价走本模块，agent 通过 CLI/API 调用，
不在 dsh 里直接抓网页。

- trip（携程镜像）：tw.trip.com 与携程同库存同 hotelId，未登录显示真实价；
  先试 www.trip.com + currency=CNY，失败回退 tw.trip.com（TWD，按配置汇率折算）。
- ly（同程）：CDP 填表搜索 -> 列表卡片提取；未登录普遍显示"登录查看最低价"，
  需用户在常驻浏览器里登录一次（hotel-browser open）。
- official（官网）：通用渲染 + 价格提取，best-effort（协议价需集团会员登录）。
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from .browser import find_browser

# ---------------------------------------------------------------- 浏览器会话

_PORTS = (9346, 9347, 9348, 9349, 9350)


class OtaBrowser:
    """常驻 CDP 浏览器（有头、窗口移出屏幕），profile 持久保存登录态。"""

    def __init__(self, user_data_dir: Path, visible: bool = False):
        self.user_data_dir = user_data_dir
        self.visible = visible
        self.proc: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self._ws: Optional[Any] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._mid = 0
        self._lock = asyncio.Lock()

    async def ensure(self) -> int:
        async with self._lock:
            if self.port and await self._cdp_alive(self.port):
                return self.port
            browser = find_browser()
            if not browser:
                raise RuntimeError("未找到 Edge/Chrome，无法启动查价浏览器")
            for port in _PORTS:
                if await self._cdp_alive(port):
                    # 已有本模块的浏览器（daemon 重启前的遗留）——直接接管
                    self.port = port
                    return port
                args = [
                    browser, f"--remote-debugging-port={port}",
                    f"--user-data-dir={self.user_data_dir}",
                    "--no-first-run", "--no-default-browser-check",
                    "--restore-last-session=false",
                ]
                if not self.visible:
                    args += ["--window-position=-32000,-32000", "--window-size=1280,900"]
                args.append("about:blank")
                self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                for _ in range(50):
                    if await self._cdp_alive(port):
                        self.port = port
                        return port
                    await asyncio.sleep(0.3)
            raise RuntimeError("查价浏览器 CDP 端口启动失败")

    async def _cdp_alive(self, port: int) -> bool:
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{port}/json/version",
                                    timeout=aiohttp.ClientTimeout(total=2)) as r:
                    return r.status == 200
        except Exception:
            return False

    async def _connect(self) -> Any:
        port = await self.ensure()
        if self._ws is None:
            self._session = aiohttp.ClientSession()
            async with self._session.get(
                    f"http://127.0.0.1:{port}/json/list",
                    timeout=aiohttp.ClientTimeout(total=5)) as r:
                pages = await r.json()
            ws_url = next(p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page")
            self._ws = await self._session.ws_connect(ws_url, max_msg_size=128 * 1024 * 1024)
        return self._ws

    async def call(self, method: str, params: Optional[Dict] = None, timeout: float = 45.0) -> Any:
        ws = await self._connect()
        self._mid += 1
        mid = self._mid
        await ws.send_json({"id": mid, "method": method, "params": params or {}})
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
            if msg.type == aiohttp.WSMsgType.TEXT:
                ev = json.loads(msg.data)
                if ev.get("id") == mid:
                    if "error" in ev:
                        raise RuntimeError(str(ev["error"]))
                    return ev.get("result", {})
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                self._ws = None
                raise RuntimeError("CDP 连接已断开")

    async def navigate(self, url: str, settle_sec: int = 18) -> None:
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(settle_sec)

    async def eval_js(self, expression: str) -> Any:
        res = await self.call("Runtime.evaluate",
                              {"expression": expression, "returnByValue": True})
        result = (res.get("result") or {})
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description", "js error"))
        return result.get("value")

    async def show(self) -> None:
        """把离屏窗口移回屏幕内并置前（用户登录 OTA 用）。"""
        port = await self.ensure()
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/json/list",
                                timeout=aiohttp.ClientTimeout(total=5)) as r:
                pages = await r.json()
        # 找 browser 级 target 拿 windowId
        async with http.ws_connect(pages[0]["webSocketDebuggerUrl"]) as ws:
            await ws.send_json({"id": 1, "method": "Browser.getWindowForTarget"})
            import json as _json
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=10)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    ev = _json.loads(msg.data)
                    if ev.get("id") == 1:
                        win_id = ev["result"]["windowId"]
                        break
        await self.call("Browser.setWindowBounds",
                        {"windowId": win_id,
                         "bounds": {"left": 120, "top": 80, "windowState": "normal"}})
        await self.call("Page.bringToFront")

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None


# ---------------------------------------------------------------- 提取器（JS）

# Trip.com（zh-TW / en 混排）：抓"最佳價格/Best price"块附近的
# 币种+每晚价+总价+房型+早餐+退改。返回第一个命中的块（即最低价块）。
_TRIP_EXTRACT = r"""(function(){
  var t = document.body ? document.body.innerText : '';
  if (!t) return JSON.stringify({found:false, reason:'blank'});
  var re = /(最佳價格|最佳价格|Best price with breakfast|Best price|lowest price)[^\n]*\n\s*([A-Z]{3})\s?([0-9][0-9,]*)\s*\n\s*(?:Total price|總價|总价|Total)\s*[:：]?\s*[A-Z]{3}\s?([0-9][0-9,]*)/g;
  var blocks = [];
  var m;
  while ((m = re.exec(t)) !== null && blocks.length < 6) {
    var after = t.slice(m.index, m.index + 320).split('\n');
    var room = '', breakfast = '', cancel = '';
    for (var i = 0; i < after.length; i++) {
      var ln = after[i].trim();
      if (!room && /房$|套房|Room$|Room/.test(ln) && ln.length < 24 && !/[0-9,]{4}/.test(ln) && !/Room rate/.test(ln)) room = ln;
      if (/含\s*\d*\s*(客|位|份)?(豐盛)?早餐|Breakfast included|Includes \d+ .*breakfast|含早/.test(ln) && !breakfast) breakfast = ln.slice(0, 30);
      if (!cancel && /(不可退款|不可取消|免費取消|免费取消|Non-?refundable|Free cancellation)/.test(ln)) cancel = RegExp.$1 || ln.slice(0, 16);
    }
    blocks.push({currency: m[2], per_night: parseInt(m[3].replace(/,/g, ''), 10),
                 total: parseInt(m[4].replace(/,/g, ''), 10), room: room,
                 breakfast: breakfast, cancel: cancel});
  }
  var title = (document.title || '').slice(0, 80);
  return JSON.stringify({found: blocks.length > 0, title: title, blocks: blocks,
                         price_sample: (t.match(/[A-Z]{3}\s?[0-9][0-9,]{2,}/g) || []).slice(0, 8)});
})()"""

# 同程列表页：酒店卡片（名称 + 价格 + 详情链接）。价格未登录多为"登录查看最低价"。
_LY_LIST_EXTRACT = r"""(function(){
  var t = document.body ? document.body.innerText : '';
  if (!t) return JSON.stringify({found:false, reason:'blank'});
  // 卡片按"查看详情"分块；名称行 = 块内"条点评"行上方第一个非标签行
  var segs = t.split('查看详情');
  var items = [];
  for (var s = 0; s < segs.length - 1 && items.length < 15; s++) {
    var lines = segs[s].split('\n');
    var name = '', star = '', rating = '', price = null, price_masked = false;
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i].trim();
      if (/条点评$/.test(ln) && i > 0 && !name) {
        // 从"条点评"行往上找名称（跳过标签行）
        for (var j = i - 1; j >= 0 && i - j < 8; j--) {
          var cand = lines[j].trim();
          if (cand && !/^(超棒|很好|不错|一般|近|赞|.*地铁.*)$/.test(cand) &&
              /酒店|公寓|宾馆|民宿|客栈/.test(cand) && cand.length < 40) { name = cand; break; }
        }
        if (j > 0 && /^(五星|四星|三星|豪华|高档|舒适|经济|奢华)/.test(lines[j-1] ? lines[j-1].trim() : '')) {}
      }
      if (!star && /^(五星|四星|三星|豪华|高档|舒适|经济|奢华|顶奢)/.test(ln)) star = ln;
      if (!rating && /^[0-9]\.[0-9]$/.test(ln)) rating = ln;
      var pm = ln.match(/^[¥￥]\s?([0-9][0-9,]{2,})$/);
      if (pm) price = parseInt(pm[1].replace(/,/g, ''), 10);
      if (/^[¥￥]\s?[?？]$/.test(ln)) price_masked = true;
    }
    if (name) items.push({name: name, star: star, rating: rating,
                          price: price, price_masked: price_masked});
  }
  var masked_hint = (t.match(/登入可获取会员优惠|登录查看最低价/g) || []).length;
  return JSON.stringify({found: items.length > 0, items: items,
                         masked_hint: masked_hint,
                         text_head: t.slice(0, 300)});
})()"""

# 官网通用：抓页面里所有 价格+上下文 组合，best-effort。
_OFFICIAL_EXTRACT = r"""(function(){
  var t = document.body ? document.body.innerText : '';
  if (!t) return JSON.stringify({found:false, reason:'blank'});
  var out = [];
  var re = /(?:¥|￥|RMB|CNY)\s?([0-9][0-9,]{2,})/g;
  var m;
  while ((m = re.exec(t)) !== null && out.length < 12) {
    var ctx = t.slice(Math.max(0, m.index - 60), m.index + 40).replace(/\s+/g, ' ').trim();
    out.push({price: parseInt(m[1].replace(/,/g, ''), 10), context: ctx.slice(0, 100)});
  }
  var login_wall = /登录|登錄|login|會員|会员/i.test(t.slice(0, Math.min(t.length, 3000))) &&
                   /(价格|價格|价|價).{0,12}(登录|登錄|會員|会员)|登录.{0,8}(查看|看)|(會員|会员)价/.test(t);
  return JSON.stringify({found: out.length > 0, prices: out, login_wall: !!login_wall,
                         title: (document.title || '').slice(0, 80)});
})()"""


def _name_mismatch(expect: str, title: str) -> bool:
    """expect 与页面标题比对；中文 expect 对英文标题视为无法比对（非 mismatch）。"""
    if not expect or expect[:2] in title:
        return False
    has_cjk_title = any("\u4e00" <= ch <= "\u9fff" for ch in title)
    expect_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in expect)
    return not (expect_cjk and not has_cjk_title)


# ---------------------------------------------------------------- 查价器

# 同程 hotellist city 参数映射（2026-09 从 www.ly.com/hotel 首页城市链接提取）
_LY_CITY_IDS = {
    "北京": "53", "上海": "321", "广州": "80", "深圳": "91", "南京": "224",
    "杭州": "383", "成都": "324", "厦门": "61", "青岛": "292", "三亚": "133",
    "苏州": "226", "西安": "317", "长沙": "199", "贵阳": "114", "桂林": "102",
    "佛山": "79", "天津": "343", "宁波": "388", "武汉": "192", "合肥": "42",
    "郑州": "163", "南昌": "239", "重庆": "394",
}


def _split_date(d: str) -> str:
    return d.replace("-", "")


@dataclass
class OtaConfig:
    browser_port_hint: int = 9346
    page_settle_sec: int = 18
    fx_twd_cny: float = 0.225          # TWD->CNY 近似汇率（tw 站兜底时用）
    min_interval_sec: int = 20         # 两次 CDP 查价最小间隔（对 OTA 友好）
    profile_dir: str = "data/ota-profile"


class OtaProber:
    def __init__(self, cfg: OtaConfig, repo_root: Path, browser: Optional[OtaBrowser] = None):
        self.cfg = cfg
        self.browser = browser or OtaBrowser(repo_root / cfg.profile_dir)
        self._last_ts = 0.0
        self._seq = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._seq:
            wait = self.cfg.min_interval_sec - (time.time() - self._last_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_ts = time.time()

    # ---- 携程镜像（Trip.com）----
    async def probe_trip(self, hotel_id: str, checkin: str, checkout: str,
                         expect: str = "") -> Dict[str, Any]:
        await self._throttle()
        q = f"checkin={checkin}&checkout={checkout}&adult=2&rooms=1"
        candidates = [
            f"https://www.trip.com/hotels/detail?hotelId={hotel_id}&{q}&currency=CNY",
            f"https://tw.trip.com/hotels/detail?hotelId={hotel_id}&{q}&currency=CNY",
            f"https://tw.trip.com/hotels/a-hotel-detail-{hotel_id}/a/?{q}",
        ]
        tried: List[Dict[str, Any]] = []
        for url in candidates:
            try:
                await self.browser.navigate(url, self.cfg.page_settle_sec)
                raw = await self.browser.eval_js(_TRIP_EXTRACT)
                data = json.loads(raw or "{}")
            except Exception as e:  # noqa: BLE001
                tried.append({"url": url, "error": str(e)[:120]})
                continue
            if data.get("found"):
                out = self._trip_result(data, url)
                title = data.get("title") or ""
                if expect and _name_mismatch(expect, title):
                    out["name_mismatch"] = True
                    out["title"] = title[:90]
                return out
            tried.append({"url": url, "title": data.get("title", ""),
                          "sample": data.get("price_sample", [])[:4]})
        return {"ok": False, "source": "trip", "reason": "未解析到可订价格",
                "tried": tried}

    def _trip_result(self, data: Dict[str, Any], url: str) -> Dict[str, Any]:
        blk = data["blocks"][0]
        cur, per, total = blk["currency"], blk["per_night"], blk["total"]
        note = ""
        if cur != "CNY":
            per_cny = round(per * self.cfg.fx_twd_cny)
            total_cny = round(total * self.cfg.fx_twd_cny)
            note = f"{cur} 计价，按 {self.cfg.fx_twd_cny} 折算为近似人民币"
        else:
            per_cny, total_cny = per, total
        return {
            "ok": True, "source": "trip",
            "url": url, "room": blk.get("room", ""),
            "currency": cur, "per_night": per, "total": total,
            "per_night_cny": per_cny, "total_cny": total_cny,
            "breakfast": blk.get("breakfast", ""), "cancel": blk.get("cancel", ""),
            "n_blocks": len(data["blocks"]),
            "note": note,
        }

    # ---- 同程 ----
    async def probe_ly(self, kw: str, city: str, checkin: str, checkout: str) -> Dict[str, Any]:
        await self._throttle()
        city_id = _LY_CITY_IDS.get((city or "").strip())
        if not city_id:
            return {"ok": False, "source": "ly",
                    "reason": f"未知城市: {city}（内置映射仅覆盖 {len(_LY_CITY_IDS)} 个主要城市）"}
        from urllib.parse import quote
        url = (f"https://www.ly.com/hotel/hotellist?city={city_id}"
               f"&inDate={checkin}&outDate={checkout}&keywords={quote(kw)}")
        try:
            await self.browser.navigate(url, self.cfg.page_settle_sec)
            data = json.loads((await self.browser.eval_js(_LY_LIST_EXTRACT)) or "{}")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "source": "ly", "reason": f"列表提取失败: {e}"}
        items = data.get("items") or []
        matched = [i for i in items if kw[:2] in (i.get("name") or "")] or items
        base = {"source": "ly", "url": url, "city_id": city_id}
        if matched and matched[0].get("price"):
            return {**base, "ok": True, "items": matched[:5],
                    "note": "同程列表可见价（登录后为会员价）"}
        if (data.get("masked_hint", 0) > 0 or all(i.get("price_masked") for i in matched)) and matched:
            return {**base, "ok": False, "reason": "needs_login",
                    "items": matched[:5],
                    "hint": "同程未登录隐藏价格（￥?）：运行 xy-gate hotel-browser 唤出浏览器登录同程一次后重试（登录态长期保留）"}
        return {**base, "ok": False, "reason": "搜索无结果或页面结构变化",
                "text_head": (data.get("text_head") or "")[:200]}

    # ---- 官网（通用）----
    async def probe_official(self, url: str, expect: str = "") -> Dict[str, Any]:
        await self._throttle()
        try:
            await self.browser.navigate(url, self.cfg.page_settle_sec)
            data = json.loads((await self.browser.eval_js(_OFFICIAL_EXTRACT)) or "{}")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "source": "official", "reason": str(e)[:160]}
        prices = data.get("prices") or []
        if not prices:
            return {"ok": False, "source": "official", "reason": "页面无可见价格",
                    "title": data.get("title", ""),
                    "login_wall": data.get("login_wall", False)}
        cheapest = min(p["price"] for p in prices)
        out = {"ok": True, "source": "official", "url": url,
               "title": data.get("title", ""),
               "cheapest": cheapest, "prices": prices[:8],
               "note": "官网通用提取（best-effort）：价格是否可订/含早/退改需人工核对"}
        if data.get("login_wall"):
            out["login_wall"] = True
            out["note"] += "；页面疑似需登录/会员才显示协议价"
        if expect and expect[:2] not in (data.get("title") or ""):
            out["name_mismatch"] = True
        return out


_TWD_RE = re.compile(r"\bTWD\s?([0-9][0-9,]+)")


def twd_to_cny(text: str, fx: float) -> List[int]:
    """从文本中提取 TWD 价并折算（供工具/测试用）。"""
    return [round(int(m.replace(",", "")) * fx) for m in _TWD_RE.findall(text)]
