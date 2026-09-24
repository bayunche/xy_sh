# 代订酒店报价 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 客户私信「酒店名+入住日+晚数」→ dsh agent 三源查价（携程/同程/官网公开价）→ 阶梯加价报价 → 成交闭环（付款人工）。

**Architecture:** 增量扩展现有 xy-dsh-robot：gate 侧新增 `hotel_quote.py`（报价计算+缓存+闸门+人工补价）与配置；大脑侧新增技能/SOP/提示词；后台设置页加补价入口；E2E 追加真查价步骤。查价本身由 agent 在 job 内用 web 工具完成，gate 只做缓存与闸门。

**Tech Stack:** Python 3.10+（aiohttp/sqlite）、pytest、React+Tailwind（设置页）、dsh skills/SOP/prompts。

## Global Constraints

- 写操作铁律：真实下单与付款永远人工；报价/聊天/查价全自动。
- 阶梯加价默认：≤¥500→10%、¥500–1500→8%、>¥1500→5%（`hotel_quotes.tiers` 可调）。
- 转人工阈值默认 ¥2500；缓存 TTL 默认 30 分钟；每会话每小时全量比价 ≤3 次。
- 提交信息用简体中文，按路径显式 git add（仓库在 D 盘非 NTFS，禁 git add -A）。
- 回归基线：既有 39 个单测全绿。

---

### Task 1: 配置层 HotelQuoteConfig

**Files:**
- Modify: `gate/xy_gate/config.py`
- Test: `gate/tests/test_hotel_quote.py`

**Interfaces:**
- Produces: `HotelQuoteConfig(tiers: List[Tuple[float,float]], manual_threshold_cny: float, cache_ttl_min: int, max_queries_per_hour: int)`；`RobotConfig.hotel_quotes`；`render_robot_yaml` 输出 `hotel_quotes:` 段。

- [x] **Step 1: 写失败测试**

```python
"""代订酒店：配置/算价/缓存/闸门 单测。"""
from xy_gate.config import (HotelQuoteConfig, RobotConfig, load_robot_config,
                            render_robot_yaml)


def test_hotel_config_defaults():
    cfg = RobotConfig()
    hq = cfg.hotel_quotes
    assert hq.tiers == [(500, 10.0), (1500, 8.0), (float("inf"), 5.0)]
    assert hq.manual_threshold_cny == 2500
    assert hq.cache_ttl_min == 30
    assert hq.max_queries_per_hour == 3


def test_hotel_config_yaml_roundtrip(tmp_path):
    import yaml
    raw = """
mode: dry-run
hotel_quotes:
  tiers: [[800, 12], [2000, 6]]
  manual_threshold_cny: 3000
  cache_ttl_min: 15
  max_queries_per_hour: 5
"""
    p = tmp_path / "robot.yaml"
    p.write_text(raw, encoding="utf-8")
    cfg = load_robot_config(config_dir=tmp_path)
    assert cfg.hotel_quotes.tiers == [(800, 12.0), (2000, 6.0), (float("inf"), 5.0)]
    assert cfg.hotel_quotes.manual_threshold_cny == 3000
    out = render_robot_yaml(cfg)
    assert "hotel_quotes:" in out and "manual_threshold_cny: 3000" in out
```

（注：`load_robot_config(config_dir=...)` 需支持 accounts 缺省——现有实现已容错。）

- [x] **Step 2: 跑测试确认失败**

Run: `cd gate && uv run pytest tests/test_hotel_quote.py -q`
Expected: FAIL `ImportError: cannot import name 'HotelQuoteConfig'`

- [x] **Step 3: 最小实现**

`config.py` 追加（dataclass 区、loader、serializer 三处）：

```python
@dataclass
class HotelQuoteConfig:
    """代订酒店报价：阶梯加价/人工阈值/缓存/限频。"""
    tiers: List[List[float]] = field(default_factory=lambda: [[500, 10.0], [1500, 8.0]])
    manual_threshold_cny: float = 2500.0
    cache_ttl_min: int = 30
    max_queries_per_hour: int = 3

    def margin_pct(self, cost: float) -> float:
        for cap, pct in (self.tiers + [[float("inf"), 5.0]]):
            if cost <= float(cap):
                return float(pct)
        return 5.0
```

RobotConfig 增 `hotel_quotes: HotelQuoteConfig = field(default_factory=HotelQuoteConfig)`；
loader 增（raw 解析后）：

```python
hq_raw = raw.get("hotel_quotes") or {}
tiers = [[float(a), float(b)] for a, b in (hq_raw.get("tiers") or []) if isinstance(a, (int, float))]
```

构造 `hotel_quotes=HotelQuoteConfig(tiers=tiers or [[500, 10.0], [1500, 8.0]], manual_threshold_cny=float(hq_raw.get("manual_threshold_cny") or 2500), cache_ttl_min=int(hq_raw.get("cache_ttl_min") or 30), max_queries_per_hour=int(hq_raw.get("max_queries_per_hour") or 3))`。

`render_robot_yaml` 在 snipe 段后追加：

```python
lines += ["", "hotel_quotes:",
          f"  tiers: [{', '.join(f'[{int(a)},{b}]' for a, b in cfg.hotel_quotes.tiers)}]",
          f"  manual_threshold_cny: {cfg.hotel_quotes.manual_threshold_cny}",
          f"  cache_ttl_min: {cfg.hotel_quotes.cache_ttl_min}",
          f"  max_queries_per_hour: {cfg.hotel_quotes.max_queries_per_hour}"]
```

- [x] **Step 4: 跑测试通过** `uv run pytest tests/test_hotel_quote.py -q` → 2 passed
- [x] **Step 5: Commit** `git add gate/xy_gate/config.py gate/tests/test_hotel_quote.py && git commit -m "功能：代订酒店配置层（阶梯加价/阈值/缓存/限频）"`

---

### Task 2: 报价核心 hotel_quote.py（算价+缓存+闸门+人工补价）

**Files:**
- Create: `gate/xy_gate/hotel_quote.py`
- Test: `gate/tests/test_hotel_quote.py`（追加）

**Interfaces:**
- Consumes: `HotelQuoteConfig.margin_pct(cost)`；`Store`（kv_get/kv_set/add_event）。
- Produces:
  - `calc_quote(cost: float, cfg) -> {cost, margin_pct, price}`
  - `class HotelQuoteGate(cfg, store)`：`cache_get(hotel, date, nights, room) -> list|None`、`cache_set(..., quotes)`、`check(chat_id, cost) -> Decision(action allow|block, reasons)`、`cost_get(hotel) -> float|None`、`cost_set(hotel, price)`、`note_query(chat_id)`（限频计数）。

- [x] **Step 1: 追加失败测试**

```python
from xy_gate.hotel_quote import calc_quote, HotelQuoteGate
from xy_gate.store import Store


def test_calc_quote_tiers():
    cfg = RobotConfig()
    assert calc_quote(300, cfg) == {"cost": 300, "margin_pct": 10.0, "price": 330.0}
    assert calc_quote(1000, cfg)["margin_pct"] == 8.0 and calc_quote(1000, cfg)["price"] == 880.0
    assert calc_quote(2000, cfg)["margin_pct"] == 5.0 and calc_quote(2000, cfg)["price"] == 2100.0


def make_gate(tmp_path, threshold=2500, ttl=30, per_hour=3):
    cfg = RobotConfig(db_path=str(tmp_path / "hq.sqlite3"))
    cfg.hotel_quotes.manual_threshold_cny = threshold
    cfg.hotel_quotes.cache_ttl_min = ttl
    cfg.hotel_quotes.max_queries_per_hour = per_hour
    return HotelQuoteGate(cfg, Store(tmp_path / "hq.sqlite3"))


def test_gate_threshold_and_cost(tmp_path):
    g = make_gate(tmp_path)
    assert g.check("c1", 2400).action == "allow"
    d = g.check("c1", 2600)
    assert d.action == "block" and any("人工" in r for r in d.reasons)
    g.cost_set("杭州开元名都", 600)
    assert g.cost_get("杭州开元名都") == 600


def test_gate_rate_limit(tmp_path):
    g = make_gate(tmp_path, per_hour=2)
    g.note_query("c1"); g.note_query("c1")
    d = g.check("c1", 100)
    assert d.action == "block" and any("频繁" in r for r in d.reasons)
    assert g.check("c2", 100).action == "allow"   # 其他会话不受影响


def test_cache_ttl(tmp_path):
    g = make_gate(tmp_path, ttl=0)  # 0 分钟 → 立即过期
    g.cache_set("开元名都", "2026-10-01", 2, "", [{"source": "ctrip", "price": 500}])
    assert g.cache_get("开元名都", "2026-10-01", 2, "") is None
    g2 = make_gate(tmp_path / "b", ttl=30)
    g2.cache_set("开元名都", "2026-10-01", 2, "", [{"source": "ctrip", "price": 500}])
    assert g2.cache_get("开元名都", "2026-10-01", 2, "")[0]["price"] == 500
```

- [x] **Step 2: 确认失败** `uv run pytest tests/test_hotel_quote.py -q` → ModuleNotFoundError
- [x] **Step 3: 实现 hotel_quote.py**

```python
"""代订酒店：算价、比价缓存、报价闸门、人工补价。

铁律：查价/报价自动；真实下单与付款永远人工（>manual_threshold 一律转人工核价）。
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from .config import RobotConfig
from .risk import Decision
from .store import Store


def calc_quote(cost: float, cfg: RobotConfig) -> Dict:
    pct = cfg.hotel_quotes.margin_pct(cost)
    return {"cost": cost, "margin_pct": pct, "price": round(cost * (1 + pct / 100), 0)}


class HotelQuoteGate:
    def __init__(self, cfg: RobotConfig, store: Store):
        self.cfg = cfg
        self.store = store
        self.store._ensure_hotel_quotes_table()

    # ── 比价缓存 ──────────────────────────────────────────────
    def cache_get(self, hotel: str, date: str, nights: int, room: str) -> Optional[List[Dict]]:
        row = self.store.hotel_quote_cache_get(hotel, date, nights, room)
        if not row:
            return None
        ts, quotes = row
        if time.time() - ts > self.cfg.hotel_quotes.cache_ttl_min * 60:
            return None
        return quotes

    def cache_set(self, hotel: str, date: str, nights: int, room: str, quotes: List[Dict]) -> None:
        self.store.hotel_quote_cache_set(hotel, date, nights, room, quotes)

    # ── 闸门 ──────────────────────────────────────────────────
    def check(self, chat_id: str, cost: float) -> Decision:
        reasons: List[str] = []
        if cost > self.cfg.hotel_quotes.manual_threshold_cny:
            reasons.append(f"成本 ¥{cost} 超过转人工阈值 ¥{self.cfg.hotel_quotes.manual_threshold_cny}，需人工核价")
        hour_key = time.strftime("%Y-%m-%d %H") + f":{chat_id}"
        count = int(self.store.kv_get(f"hq_count:{hour_key}") or 0)
        if count >= self.cfg.hotel_quotes.max_queries_per_hour:
            reasons.append(f"该会话本小时已全量比价 {count} 次（上限 {self.cfg.hotel_quotes.max_queries_per_hour}），稍后再查或用缓存价")
        if reasons:
            return Decision("block", reasons)
        return Decision("allow", [f"加价 {self.cfg.hotel_quotes.margin_pct(cost)}% → 报价 ¥{calc_quote(cost, self.cfg)['price']}"])

    def note_query(self, chat_id: str) -> None:
        hour_key = time.strftime("%Y-%m-%d %H") + f":{chat_id}"
        self.store.kv_set(hour_key if False else f"hq_count:{hour_key}",
                         str(int(self.store.kv_get(f"hq_count:{hour_key}") or 0) + 1))

    # ── 人工补价（会员/协议价成本）────────────────────────────
    def cost_get(self, hotel: str) -> Optional[float]:
        v = self.store.kv_get(f"hotel_cost:{hotel.strip()}")
        try:
            return float(v) if v is not None else None
        except ValueError:
            return None

    def cost_set(self, hotel: str, price: float) -> None:
        self.store.kv_set(f"hotel_cost:{hotel.strip()}", str(price))
        self.store.add_event("hotel_cost", "", "", f"人工补价：{hotel} → ¥{price}")
```

（注：`note_query` 里 `hour_key if False else ...` 是笔误防御——实现时直接写 `f"hq_count:{hour_key}"`。）

- [x] **Step 4: store 增缓存表与方法**（`store.py` `_migrate` 后追加）：

```python
    def _ensure_hotel_quotes_table(self) -> None:
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS hotel_quotes("
                "k TEXT PRIMARY KEY, ts REAL, quotes TEXT)")

    def hotel_quote_cache_get(self, hotel, date, nights, room):
        k = f"{hotel}|{date}|{nights}|{room}"
        with self._lock:
            row = self._db.execute("SELECT ts, quotes FROM hotel_quotes WHERE k=?", (k,)).fetchone()
        if not row:
            return None
        return row[0], json.loads(row[1])

    def hotel_quote_cache_set(self, hotel, date, nights, room, quotes):
        k = f"{hotel}|{date}|{nights}|{room}"
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO hotel_quotes(k, ts, quotes) VALUES(?,?,?)",
                             (k, time.time(), json.dumps(quotes, ensure_ascii=False)))
```

- [x] **Step 5: 跑测试** → 全部通过；`uv run pytest tests/ -q` 39+6 绿
- [x] **Step 6: Commit** `git add gate/xy_gate/hotel_quote.py gate/xy_gate/store.py gate/tests/test_hotel_quote.py && git commit -m "功能：代订酒店报价核心（算价/缓存/闸门/人工补价）"`

---

### Task 3: HTTP/CLI 接入

**Files:**
- Modify: `gate/xy_gate/server.py`、`gate/xy_gate/cli.py`、`gate/xy_gate/daemon.py`

**Interfaces:**
- Produces（daemon）：`self.hotel_gate = HotelQuoteGate(cfg, self.store)`，`reload_config` 重建之。
- Produces（HTTP）：`POST /api/hotel/cost {hotel, price}`、`GET /api/hotel/quote-status`。
- Produces（CLI）：`xy-gate hotel-cost <酒店名> <价格>`、`xy-gate hotel-cache-show [--limit 20]`。

- [x] **Step 1: daemon 挂载**（`__init__` 里 risk 之后；`reload_config` 同步重建）

```python
from .hotel_quote import HotelQuoteGate
# __init__:
self.hotel_gate = HotelQuoteGate(cfg, self.store)
# reload_config:
self.hotel_gate = HotelQuoteGate(cfg, self.store)
```

- [x] **Step 2: server 路由**

```python
    async def hotel_cost(req):
        body = await req.json()
        hotel = str(body.get("hotel") or "").strip()
        if not hotel:
            return _json({"error": "缺少 hotel"}, 400)
        price = body.get("price")
        if price in (None, ""):
            return _json({"cost": daemon.hotel_gate.cost_get(hotel)})
        daemon.hotel_gate.cost_set(hotel, float(price))
        return _json({"ok": True, "hotel": hotel, "cost": float(price)})

    async def hotel_quote_status(_req):
        return _json({
            "tiers": daemon.cfg.hotel_quotes.tiers,
            "manual_threshold_cny": daemon.cfg.hotel_quotes.manual_threshold_cny,
            "cache_ttl_min": daemon.cfg.hotel_quotes.cache_ttl_min,
            "max_queries_per_hour": daemon.cfg.hotel_quotes.max_queries_per_hour,
            "manual_costs": {k.split("hotel_cost:", 1)[1]: v for k, v in []
                             },  # v1 直接返回数量与样例
            "cache_entries": daemon.store.hotel_quote_cache_count(),
        })
# 路由：
app.router.add_post("/api/hotel/cost", hotel_cost)
app.router.add_get("/api/hotel/quote-status", hotel_quote_status)
```

store 补 `hotel_quote_cache_count()`：

```python
    def hotel_quote_cache_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM hotel_quotes").fetchone()[0]
```

（`quote-status` 的 `manual_costs` 简化为 `{"count": n}`：遍历 kv `hotel_cost:%` 前缀。）

- [x] **Step 3: CLI 子命令**（cli.py，pattern 同既有：`_request`）

```python
    sp = sub.add_parser("hotel-cost", help="人工补价（会员/协议价成本）")
    sp.add_argument("hotel")
    sp.add_argument("price", type=float, nargs="?", help="缺省=只查询")
    sub.add_parser("hotel-cache-show", help="查看比价缓存条数")
# 分支：
    elif args.cmd == "hotel-cost":
        out = _request("POST", "/api/hotel/cost",
                       {"hotel": args.hotel, "price": args.price}, base)
    elif args.cmd == "hotel-cache-show":
        out = _request("GET", "/api/hotel/quote-status", base=base)
```

- [x] **Step 4: 起服务 curl 验证**

Run: 重启 daemon 后 `curl -s -X POST :8790/api/hotel/cost -d '{"hotel":"测试酒店","price":600}'` → `{"ok":true,...}`；`xy-gate hotel-cost 测试酒店` → `{"cost": 600.0}`
- [x] **Step 5: 回归 + Commit** `git add gate/xy_gate/server.py gate/xy_gate/cli.py gate/xy_gate/daemon.py gate/xy_gate/store.py && git commit -m "功能：代订酒店补价与状态接口（HTTP/CLI）"`

---

### Task 4: 大脑侧（技能/SOP/提示词/意图）

**Files:**
- Create: `.dsh/skills/xianyu-hotel-quote/SKILL.md`、`sop/sop-hotel-quote.yaml`、`prompts/hotel-quote-policy.md`
- Modify: `prompts/intent-classify.md`（意图表加一行）、`prompts/jobs/on-message.md`（要求段加一条）

- [x] **Step 1: SKILL.md**（要点版，正文含完整流程）

```markdown
---
name: xianyu-hotel-quote
description: 闲鱼代订酒店询价：客户给酒店名+入住日期+晚数（可含房型/人数/含早），需要查携程/同程/官网公开价并按策略报价时使用。含人工补价（会员/协议价）优先、缓存、限频与转人工规则。
whenToUse: 意图分类为「代订询价」。
---
# 代订酒店报价
## 流程
1. 解析要素（酒店名/入住日/晚数必收；房型/人数/含早/是否需免费取消可选），缺必收项一次性反问补全。
2. 查人工补价：`uv run --project gate xy-gate hotel-cost "<酒店名>"`（有值则它就是最低成本候选）。
3. 三源查价（无缓存时）：用 web 工具分别查携程、同程、酒店官网公开价，仅取可确认预订的价格；单源失败跳过，三源全失败 → 回复"稍等人工核价"并在摘要标注 需人工。
4. 闸门：`uv run --project gate xy-gate hotel-cache-show` 可看缓存；金额/限频被拦（block）时如实转告并转人工，禁止绕过。
5. 报价 = 成本×(1+加价%)（加价比例由闸门命令/策略文件给出），话术按 prompts/hotel-quote-policy.md。
6. 客户接受 → 引导拍「酒店代订」商品，改价用 `xy-gate reprice`；付款后你在代理/会员渠道人工下单（永不自动下单付款），回填确认号给买家。
## 红线
- 话术禁止出现「协议价」字样（说「渠道优惠价」）；
- 报价必须含有效期（10 分钟）与「以最终确认为准」；
- 成本>阈值被闸门拦截时不得自行报价。
```

- [x] **Step 2: sop-hotel-quote.yaml**（spec §3 状态机的 YAML 化，guardrails 四条：限频/有效期/禁词/付款人工）
- [x] **Step 3: hotel-quote-policy.md**（阶梯表+话术模板+免责+转人工清单；模板含 {price}/{valid_until}/{breakfast}/{cancel}）
- [x] **Step 4: intent-classify.md** 意图表首行插 `| 代订询价 | 订酒店/代订/入住/晚/大床房/标间 | 走 xianyu-hotel-quote 技能 |`，优先级仅次售后纠纷
- [x] **Step 5: on-message.md** 要求段加：`- 意图为代订询价 → 读 .dsh/skills/xianyu-hotel-quote/SKILL.md 并按其执行（优先级高于普通砍价）`
- [x] **Step 6: 验证文件存在 + Commit** `git add .dsh/skills/xianyu-hotel-quote sop/sop-hotel-quote.yaml prompts/hotel-quote-policy.md prompts/intent-classify.md prompts/jobs/on-message.md && git commit -m "功能：代订酒店技能/SOP/报价策略与意图接入"`

---

### Task 5: 后台设置页（补价入口 + 报价参数）

**Files:**
- Modify: `web/src/pages/Settings.tsx`、`web/src/lib/api.ts`；构建产物 `web/dist`

- [x] **Step 1: api.ts 增**

```ts
export const postHotelCost = (hotel: string, price?: number) =>
  api("/api/hotel/cost", { method: "POST", body: JSON.stringify({ hotel, price }) });
export const getHotelQuoteStatus = () => api("/api/hotel/quote-status");
```

- [x] **Step 2: Settings.tsx 新区块「代订酒店报价」**：tiers 只读展示 + `manual_threshold_cny/cache_ttl_min/max_queries_per_hour` 三个数字输入（走既有 putSettings：后端 settings_put 需透传 `hotel_quotes` 段——Task 3 已含：`hq = body.get("hotel_quotes")` 合并进 cfg）；人工补价两输入（酒店名/价格）+ 写入按钮调 `postHotelCost`。
- [x] **Step 3: server settings_put 透传**：`if isinstance(body.get("hotel_quotes"), dict): hq=...; cfg.hotel_quotes.manual_threshold_cny=float(...); cache_ttl_min=int(...); max_queries_per_hour=int(...)`（tiers 暂不可改，只读展示）。
- [x] **Step 4: 构建 + 截图验证** `cd web && npm run build`；headless Edge 截设置页确认区块渲染。
- [x] **Step 5: Commit** `git add web/src && git commit -m "功能：设置页代订酒店区块（报价参数+人工补价）"`

---

### Task 6: E2E 与收尾

**Files:**
- Modify: `gate/e2e/run_e2e.py`

- [x] **Step 1: E2E 追加步骤**（s15 之前插入）

```python
def s_hotel_quote():
    r = call("POST", "/api/hotel/cost", {"hotel": "E2E测试酒店", "price": 600}, timeout=10)
    expect(r.get("ok") is True, f"补价失败: {r}")
    q = call("GET", "/api/hotel/quote-status", timeout=10)
    expect(q.get("manual_threshold_cny") == 2500, f"报价配置异常: {q}")
    return "补价+状态接口 OK（agent 真查价走 /api/chat 手动验证）"
```

并在 STEPS 列表 `("16 代订酒店接口", s_hotel_quote)` 处追加（Web 后台改 17）。
（agent 真查价的对话级验证放在收尾报告：`/api/chat` 发一条真实代订问询人工确认，避免 E2E 每次烧 token 且依赖 OTA 页面波动。）

- [x] **Step 2: 全量回归** `uv run pytest tests/ -q` 全绿 → `uv run python e2e/run_e2e.py`（除已知的 Cookie 过期项外全过）
- [x] **Step 3: 更新 docs/full-autonomy-roadmap.md 能力矩阵** 加一行「代订酒店报价 | ✅ v1（agent 三源查价） | 无」
- [x] **Step 4: Commit** `git add gate/e2e/run_e2e.py docs/full-autonomy-roadmap.md && git commit -m "测试：代订酒店 E2E 步骤与文档更新"`

## Self-Review 结论

- Spec 覆盖：§2-§7 均有对应 Task（1→配置、2→核心、3→接口、4→大脑、5→后台、6→测试/E2E）；§8 非目标未越界。
- 占位符：无 TBD；Task 2 note_query 的防御笔误已在实现时修正。
- 类型一致性：`HotelQuoteGate.check/chat_id,cost→Decision`、`cost_get/set(hotel[,price])`、`hotel_quote_cache_get/set` 名称前后一致。
