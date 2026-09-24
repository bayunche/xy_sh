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
        for attempt in (1, 2):
            ws = await self._connect()
            self._mid += 1
            mid = self._mid
            try:
                await ws.send_json({"id": mid, "method": method, "params": params or {}})
            except Exception:
                self._ws = None
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
                continue
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
                except asyncio.TimeoutError:
                    raise
                if msg.type == aiohttp.WSMsgType.TEXT:
                    ev = json.loads(msg.data)
                    if ev.get("id") == mid:
                        if "error" in ev:
                            raise RuntimeError(str(ev["error"]))
                        return ev.get("result", {})
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    self._ws = None
                    if attempt == 2:
                        raise RuntimeError("CDP 连接已断开")
                    break  # 重连重试

    async def navigate(self, url: str, settle_sec: int = 18) -> None:
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(settle_sec)

    async def eval_js(self, expression: str) -> Any:
        res = await self.call("Runtime.evaluate",
                              {"expression": expression, "returnByValue": True,
                               "awaitPromise": True})
        result = (res.get("result") or {})
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description", "js error"))
        return result.get("value")

    async def show(self, url: Optional[str] = None) -> None:
        """把离屏窗口移回屏幕内并置前（用户登录 OTA 用）；可选先导航到 url。"""
        port = await self.ensure()
        win_id = None
        async with aiohttp.ClientSession() as http:
            async with http.get(f"http://127.0.0.1:{port}/json/list",
                                timeout=aiohttp.ClientTimeout(total=5)) as r:
                pages = await r.json()
            page = next((p for p in pages if p.get("type") == "page"), None)
            if not page:
                raise RuntimeError("浏览器没有可用页签")
            async with http.ws_connect(page["webSocketDebuggerUrl"]) as ws:
                await ws.send_json({"id": 1, "method": "Browser.getWindowForTarget",
                                    "params": {"targetId": page["id"]}})
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=10)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        ev = json.loads(msg.data)
                        if ev.get("id") == 1:
                            if "error" in ev:
                                raise RuntimeError(str(ev["error"].get("message", ""))[:120])
                            win_id = ev["result"]["windowId"]
                            break
        if not win_id:
            raise RuntimeError("未取到浏览器窗口 ID")
        await self.call("Browser.setWindowBounds",
                        {"windowId": win_id,
                         "bounds": {"left": 120, "top": 80, "windowState": "normal"}})
        if url:
            await self.call("Page.navigate", {"url": url})
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


# ------------------------------------------------------- 酒店集团注册表（官网源决策表）
# status: web_ok=可直接 probe；needs_login=站可达但价格需登录；partial=可达但
#         流程长/慢；anti_bot=防护强可能被拦；app_only=无 web 预订；unknown=现场搜
# （2026-09-24 实测：开元无 web 预订；华住 hworld 可达；亚朵 wechat.yaduo.com 有价；
#   洲际可达但慢；雅高有地区选择页；万豪/希尔顿疑似反爬墙；锦江 bestwe 已下线）
_HOTEL_GROUPS: Dict[str, Dict[str, Any]] = {
    "huazhu": {
        "name": "华住", "status": "needs_login",
        "aliases": ["全季", "汉庭", "桔子水晶", "桔子", "你好酒店", "海友", "漫心",
                    "禧玥", "花间堂", "CitiGO", "馨乐庭", "城家", "施柏阁", "Intercity"],
        "urls": ["https://www.hworld.com/"],
        "notes": "华住会 web 预订站（hworld.com，huazhu.com 跳转）；门市价或需登录，"
                 "会员价需登录华住会；协议价=华住商旅协议账号（在 hotel-browser 登录一次）",
    },
    "jinjiang": {
        "name": "锦江", "status": "unknown",
        "aliases": ["锦江之星", "麗枫", "丽枫", "维也纳", "希岸", "欢朋", "7天",
                    "锦江都城", "白玉兰", "铂涛"],
        "urls": [],
        "notes": "锦江荟/WeHotel 主推 App（bestwe.com 已下线）；agent 现场 web 搜索"
                 "具体酒店预订页，或人工在锦江荟 App 查协议价",
    },
    "atour": {
        "name": "亚朵", "status": "app_only",
        "aliases": ["亚朵轻居", "亚朵S", "亚朵", "A.T House", "萨和"],
        "urls": [],
        "notes": "wechat.yaduo.com 为 App/微信壳（无 web 价）；亚朵直销价在「亚朵」"
                 "小程序/App——官网源记 App-only 人工核价",
    },
    "kaiyuan": {
        "name": "开元", "status": "app_only",
        "aliases": ["开元名都", "开元度假村", "开元曼居", "开元颐居", "开元悦居",
                    "芳草青青", "开元观堂", "开元森泊", "开元大酒店"],
        "urls": ["https://www.kaiyuanhotels.com/"],
        "notes": "开元无 web 预订（官网为招商加盟站）；直销价/协议价在开元 App/小程序，"
                 "需人工查询——官网源对开元直接记「App-only，人工核价」",
    },
    "btg": {
        "name": "首旅如家", "status": "unknown",
        "aliases": ["如家商旅", "如家精选", "如家", "和颐", "璞隐", "莫泰", "建国"],
        "urls": [],
        "notes": "web 预订弱化（btghotels.com 证书异常）；现场搜索或 App 人工",
    },
    "marriott": {
        "name": "万豪", "status": "anti_bot",
        "aliases": ["丽思卡尔顿", "Ritz", "JW万豪", "瑞吉", "威斯汀", "喜来登",
                    "豪华精选", "万丽", "万怡", "艾美", "臻品之选", "W酒店", "Moxy",
                    "万豪"],
        "urls": ["https://www.marriott.com/"],
        "notes": "web 预订全但 Akamai 防护强，CDP 可能被拦；未登录显示门市价，"
                 "会员/协议价（MMP 等）需登录万豪旅享家（hotel-browser 登录一次）",
    },
    "hilton": {
        "name": "希尔顿", "status": "anti_bot",
        "aliases": ["华尔道夫", "康莱德", "希尔顿欢朋", "希尔顿逸林", "希尔顿"],
        "urls": ["https://www.hilton.com/"],
        "notes": "防护较强；HHonors 会员价需登录（hotel-browser 登录一次）",
    },
    "ihg": {
        "name": "洲际", "status": "partial",
        "aliases": ["皇冠假日", "英迪格", "智选假日", "假日酒店", "华邑", "逸衡",
                    "voco", "洲际"],
        "urls": ["https://www.ihg.com/hotels/cn/zh/reservation"],
        "notes": "web 预订可用但加载慢（20s+）；详情页 URL 含酒店代码（如 hghic-xxx），"
                 "必须现场 web 搜索拿到正确链接再 probe（猜代码会 404）；"
                 "部分价未登录可见，IHG 优悦会会员价/协议价需登录",
    },
    "accor": {
        "name": "雅高", "status": "partial",
        "aliases": ["索菲特", "铂尔曼", "诺富特", "美居", "宜必思", "瑞士酒店",
                    "莱佛士", "费尔蒙", "诗铂", "瑞享", "雅高"],
        "urls": ["https://all.accor.com/"],
        "notes": "all.accor.com 有地区选择页（选中国后进主站）；ALL 会员价需登录",
    },
}


def match_hotel_group(hotel_name: str) -> Optional[Dict[str, Any]]:
    """按品牌别名识别酒店所属集团（返回含 group key 的条目）。"""
    for key, g in _HOTEL_GROUPS.items():
        for alias in g["aliases"]:
            if alias in (hotel_name or ""):
                return {"group": key, **g}
    return None


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
    # 商旅协议码库：[{group: marriott, code: "XXX", label: "中油"}...]
    # 官网源查价时按 group 自动带码（万豪/希尔顿/洲际等 Corporate/Promo Code 输入框）
    corporate_codes: List[Dict[str, str]] = field(default_factory=list)
    # 登录制商旅平台（第四源）：[{name: "石化商旅", url: "https://trip.sinopec.com",
    #   note: "登录后可查中石化协议价"}...]——用户在 hotel-browser 登录后可用 official 源抓
    extra_sources: List[Dict[str, str]] = field(default_factory=list)


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
        blocks = data["blocks"]
        blk = blocks[0]
        cur, per, total = blk["currency"], blk["per_night"], blk["total"]
        note = ""
        if cur != "CNY":
            per_cny = round(per * self.cfg.fx_twd_cny)
            total_cny = round(total * self.cfg.fx_twd_cny)
            note = f"{cur} 计价，按 {self.cfg.fx_twd_cny} 折算为近似人民币"
        else:
            per_cny, total_cny = per, total
        fx = self.cfg.fx_twd_cny if cur != "CNY" else 1.0
        rooms = [{"room": b.get("room", ""), "per_night": b["per_night"],
                  "total": b["total"],
                  "per_night_cny": round(b["per_night"] * fx),
                  "total_cny": round(b["total"] * fx),
                  "breakfast": b.get("breakfast", ""), "cancel": b.get("cancel", "")}
                 for b in blocks]
        return {
            "ok": True, "source": "trip",
            "url": url, "room": blk.get("room", ""),
            "currency": cur, "per_night": per, "total": total,
            "per_night_cny": per_cny, "total_cny": total_cny,
            "breakfast": blk.get("breakfast", ""), "cancel": blk.get("cancel", ""),
            "rooms": rooms, "n_blocks": len(blocks),
            "note": note + ("；rooms 为解析到的房型价格块（agent 按客户房型匹配，"
                            "无匹配时用最低价块并注明房型差异）" if len(rooms) > 1 else ""),
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

    # ---- 商旅协议码 ----
    _FIND_CODE_INPUT = r"""(async function(){
      // 万豪/希尔顿的码框藏在 "Special Rates" 折叠钮里——先展开
      var exp = /Special Rates|特殊价格|特殊费率|Advanced|更多筛选/i;
      var els = document.querySelectorAll('button, a, div[role=button], span, label');
      for (var i=0;i<els.length;i++){
        var t = (els[i].innerText || '').trim();
        if (t && t.length < 30 && exp.test(t)) { els[i].click(); break; }
      }
      await new Promise(r=>setTimeout(r, 900));
      var key = /corp|promo|group.?code|rate.?code|set.?#|优惠码|协议码|折扣码|集团码/i;
      var inps = document.querySelectorAll('input[type=text],input:not([type])');
      for (var i=0;i<inps.length;i++){
        var el = inps[i];
        var hint = [el.placeholder, el.name, el.id,
                    el.getAttribute('aria-label')||'',
                    (el.labels && el.labels[0] ? el.labels[0].innerText : '')].join(' ');
        if (key.test(hint) && !el.readOnly && !el.disabled) {
          el.focus();
          el.value = '';
          return JSON.stringify({found: true, hint: hint.slice(0, 60)});
        }
      }
      return JSON.stringify({found: false});
    })()"""

    _APPLY_CODE = r"""(function(){
      var btns = document.querySelectorAll('button, a, input[type=button], input[type=submit], div[role=button]');
      var key = /apply|apply now|提交|应用|确定|search|查找|go/i;
      for (var i=0;i<btns.length;i++){
        var t = (btns[i].innerText || btns[i].value || '').trim();
        if (t && t.length < 20 && key.test(t)) { btns[i].click(); return 'clicked:' + t; }
      }
      var ae = document.activeElement;
      if (ae && (ae.tagName === 'INPUT')) {
        ae.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
        return 'enter';
      }
      return 'none';
    })()"""

    async def apply_corporate_code(self, code: str) -> Dict[str, Any]:
        """在当前页面找协议码输入框并填入（万豪/希尔顿/洲际 Corporate/Promo Code）。"""
        try:
            found = json.loads((await self.browser.eval_js(self._FIND_CODE_INPUT)) or "{}")
        except Exception as e:  # noqa: BLE001
            return {"applied": False, "reason": str(e)[:100]}
        if not found.get("found"):
            return {"applied": False, "reason": "页面无协议码输入框"}
        # native 输入（组件认真实键盘事件）
        await self.browser.call("Input.insertText", {"text": code})
        await asyncio.sleep(1)
        try:
            act = await self.browser.eval_js(self._APPLY_CODE)
        except Exception as e:  # noqa: BLE001
            act = f"apply error: {e}"
        await asyncio.sleep(self.cfg.page_settle_sec)   # 等价格按新费率刷新
        return {"applied": True, "action": str(act)[:60], "input_hint": found.get("hint")}

    # ---- 官网（通用 + 集团注册表 + 商旅协议码）----
    async def probe_official(self, url: str, expect: str = "",
                             group: str = "", code: str = "") -> Dict[str, Any]:
        await self._throttle()
        ginfo = _HOTEL_GROUPS.get(group) if group else None
        base = {"source": "official", "url": url, "group": group or None}
        if ginfo:
            base["group_status"] = ginfo["status"]
            if ginfo["status"] == "app_only":
                return {**base, "ok": False, "reason": "app_only",
                        "hint": ginfo["notes"]}
        try:
            await self.browser.navigate(url, 8)
            # 轮询等待：expect 关键词或任意价格出现（最多 page_settle_sec*2）
            deadline = asyncio.get_event_loop().time() + self.cfg.page_settle_sec * 2
            data = None
            while True:
                data = json.loads((await self.browser.eval_js(_OFFICIAL_EXTRACT)) or "{}")
                hit = bool(data.get("found")) or (
                    expect and expect[:2] in (data.get("title") or ""))
                if hit or asyncio.get_event_loop().time() > deadline:
                    break
                await asyncio.sleep(3)
        except Exception as e:  # noqa: BLE001
            return {**base, "ok": False, "reason": str(e)[:160]}
        rate_info: Dict[str, Any] = {}
        if code:
            rate_info = await self.apply_corporate_code(code)
            if rate_info.get("applied"):
                # 按协议费率重新提取
                try:
                    data = json.loads((await self.browser.eval_js(_OFFICIAL_EXTRACT)) or "{}")
                except Exception:  # noqa: BLE001
                    pass
        prices = data.get("prices") or []
        if not prices:
            out = {**base, "ok": False, "reason": "页面无可见价格",
                   "title": data.get("title", ""),
                   "login_wall": data.get("login_wall", False)}
            if ginfo:
                out["hint"] = ginfo["notes"]
            if rate_info:
                out["rate_code"] = rate_info
            return out
        cheapest = min(p["price"] for p in prices)
        note = "官网通用提取（best-effort）：价格是否可订/含早/退改需人工核对"
        if rate_info:
            if rate_info.get("applied"):
                note += f"；已按协议码刷新价格（{rate_info.get('input_hint', '')}）"
            else:
                note += f"；协议码未生效（{rate_info.get('reason', '')}）"
        if ginfo:
            note += f"；集团[{ginfo['name']}] {ginfo['notes']}"
        out = {**base, "ok": True,
               "title": data.get("title", ""),
               "cheapest": cheapest, "prices": prices[:8], "note": note}
        if rate_info:
            out["rate_code"] = rate_info
        if data.get("login_wall"):
            out["login_wall"] = True
            out["note"] += "；页面疑似需登录/会员才显示协议价（hotel-browser 登录一次长期有效）"
        if expect and _name_mismatch(expect, data.get("title") or ""):
            out["name_mismatch"] = True
        return out


_TWD_RE = re.compile(r"\bTWD\s?([0-9][0-9,]+)")


def twd_to_cny(text: str, fx: float) -> List[int]:
    """从文本中提取 TWD 价并折算（供工具/测试用）。"""
    return [round(int(m.replace(",", "")) * fx) for m in _TWD_RE.findall(text)]
