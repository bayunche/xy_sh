"""xy-dsh-robot 全链路 E2E。

对运行中的真实系统（默认 http://127.0.0.1:8790）做端到端验证：
- 真实外部依赖：闲鱼 WS 长连接、mtop（查价/商品/订单）、dsh 大脑（真 LLM 会话）
- 写操作全部走 dry-run 模拟（不会真发消息/真改价/真确认）

用法：uv run python e2e/run_e2e.py [BASE_URL]
退出码 0=全部通过。每步实时打印 ✅/❌ 与关键数据。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
RESULTS: list[tuple[str, bool, str]] = []


def call(method: str, path: str, body: dict | None = None, timeout: int = 240) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def step(name: str, fn):
    t0 = time.time()
    try:
        detail = fn()
        dt = time.time() - t0
        RESULTS.append((name, True, f"{detail} ({dt:.1f}s)"))
        print(f"  ✅ {name}  —— {detail}  [{dt:.1f}s]")
        return True
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, str(e)[:200]))
        print(f"  ❌ {name}  —— {str(e)[:200]}")
        return False


def expect(cond: bool, msg: str):
    if not cond:
        raise AssertionError(msg)


# ── 用例 ────────────────────────────────────────────────────────────

E2E_CI, E2E_CO = "2026-10-01", "2026-10-03"


def s01_health():
    r = call("GET", "/health", timeout=10)
    expect(r.get("ok") is True, f"health={r}")
    return "ok=true"


def s02_status():
    r = call("GET", "/status", timeout=10)
    expect(r.get("ws_connected") is True, f"WS 未连接: {r}")
    expect(r.get("mode") == "dry-run", f"模式应为 dry-run: {r.get('mode')}")
    expect(r.get("user_id"), "缺 user_id")
    return f"ws=在线 mode={r['mode']} unb={r['user_id']} uptime={r['uptime_sec']}s"


def s03_capability():
    r = call("GET", "/capability", timeout=60)
    caps = r.get("capabilities", {})
    ok = [k for k, v in caps.items() if v.get("ok")]
    expect(len(ok) >= 5, f"能力探测通过数不足: {ok}")
    return f"{len(ok)}/{len(caps)} 族可用: {','.join(ok)}"


def s04_search():
    r = call("POST", "/search", {"keyword": "机械键盘", "rows": 20}, timeout=60)
    stats = r.get("stats") or {}
    expect(stats.get("count", 0) >= 5, f"样本过少: {stats}")
    expect(stats.get("median", 0) > 0, "中位价异常")
    with_id = sum(1 for i in r.get("items", []) if i.get("item_id"))
    expect(with_id >= 5, "item_id 提取异常")
    return f"样本 {stats['count']}，中位 ¥{stats['median']}，item_id {with_id}/{len(r['items'])}"


def s05_items():
    r = call("GET", "/items", timeout=60)
    items = r.get("items") or []
    expect(len(items) >= 1, "在售列表为空")
    it = items[0]
    expect(it.get("price"), "挂价缺失")
    return f"在售 {len(items)} 件，首件 ¥{it['price']}（{it['title'][:16]}…）"


def s06_floor():
    r = call("GET", "/items", timeout=60)
    item_id = (r.get("items") or [{}])[0].get("item_id", "")
    r2 = call("GET", f"/floor/{item_id}", timeout=30)
    expect(r2.get("floor_price"), f"底价缺失: {r2}")
    return f"{item_id} 挂价 ¥{r2.get('listed_price')} / 底价 ¥{r2['floor_price']}"


def s07_admin_chat():
    r = call("POST", "/api/chat", {"message": "E2E 自检：请只回复一行，包含当前模式与WS连接状态。"}, timeout=300)
    expect(r.get("ok") is True, f"dsh job 失败: {str(r)[:150]}")
    reply = r.get("reply", "")
    expect(len(reply) > 10, "回复过短")
    hit = [w for w in ("dry-run", "在线", "ws", "连接") if w in reply.lower() or w in reply]
    expect(hit, "回复未包含状态关键词")
    return f"真 dsh 回复 {len(reply)} 字，命中关键词 {hit[:2]}"


def s08_send_dryrun():
    st = call("GET", "/status", timeout=10)
    me = st["user_id"]
    r = call("POST", "/send", {"chat_id": me, "to_user_id": me, "text": "E2E 自检消息（dry-run 模拟）",
                               "why": "e2e"}, timeout=30)
    expect(r.get("simulated") is True, f"应模拟发送: {r}")
    h = call("GET", f"/history?chat_id={me}&limit=5", timeout=10)
    msgs = h.get("messages") or []
    expect(any(m.get("direction") == "out" and "E2E 自检" in m.get("text", "") for m in msgs),
           "发出的消息未入聊天史")
    return "模拟发送成功且已入史"


def s09_reprice_gate():
    r = call("GET", "/items", timeout=60)
    it = (r.get("items") or [{}])[0]
    new_price = round(float(it["price"]) - 1, 2)
    rr = call("POST", "/reprice", {"item_id": it["item_id"], "new_price": new_price,
                                   "source": "e2e"}, timeout=30)
    expect(rr.get("decision") == "dryrun", f"闸门判定异常: {rr}")
    expect(rr.get("success") is True, f"dry-run 改价失败: {rr}")
    return f"{it['item_id']} ¥{it['price']}→¥{new_price} 判定 dryrun（模拟成功）"


def s10_confirm_gate():
    st = call("GET", "/status", timeout=10)
    rr = call("POST", "/confirm/check", {"order_id": "E2E000000001", "amount": 50}, timeout=10)
    expect(rr.get("action") == "block", f"自动确认未关时应拦截: {rr}")
    return f"订单预检正确拦截（auto_confirm={st.get('auto_confirm_enabled')}）"


def s11_snipe():
    r = call("POST", "/snipe/run", {}, timeout=120)
    hits = r.get("hits") or []
    ok_hits = [h for h in hits if not h.get("error")]
    expect(len(ok_hits) >= 1, f"盯货无命中: {str(r)[:120]}")
    return f"命中 {len(ok_hits)} 条，如 ¥{ok_hits[0].get('price')} {str(ok_hits[0].get('title'))[:18]}…"


def s12_settings_roundtrip():
    before = call("GET", "/api/settings", timeout=10)
    r = call("PUT", "/api/settings", before, timeout=30)
    expect(r.get("ok") is True, f"回写失败: {r}")
    after = call("GET", "/api/settings", timeout=10)
    expect(after.get("mode") == before.get("mode"), "模式不一致")
    return f"读写往返一致（mode={after['mode']}），已热重载"


def s13_dsh_model():
    r = call("GET", "/api/dsh-model", timeout=10)
    expect(r.get("configured") is True, "API Key 未配置")
    expect("xy-robot" in r.get("home", "") or "dsh-home" in r.get("home", ""),
           f"隔离 home 异常: {r.get('home')}")
    return f"隔离home={r['home'].split(chr(92))[-2:]}，模型 {r.get('model') or '默认'}，地址 {r.get('base_url')}"


def s14_ledgers():
    ev = call("GET", "/events?limit=50", timeout=10)
    kinds = {e["kind"] for e in ev}
    expect("reply_simulated" in kinds, "台账缺 reply_simulated")
    expect("reprice" in kinds, "台账缺 reprice")
    return f"事件台账含 {sorted(k for k in kinds if k in ('reply_simulated','reprice','snipe_hit','admin_chat'))}"


def s_hotel_quote():
    r = call("POST", "/api/hotel/cost", {"hotel": "E2E测试酒店", "price": 600}, timeout=10)
    expect(r.get("ok") is True, f"补价失败: {r}")
    q = call("GET", "/api/hotel/quote-status", timeout=10)
    expect(q.get("manual_threshold_cny") == 2500, f"报价配置异常: {q}")
    expect(q.get("cache_entries", -1) >= 0, "缓存计数异常")
    return "补价+状态接口 OK（agent 真查价见对话页验证）"


def s17_hotel_probe():
    """真浏览器三源查价（trip 必须出真价；ly 接受 needs_login）。"""
    r = call("POST", "/api/hotel/probe", {
        "source": "trip", "hotel_id": "369764",
        "checkin": E2E_CI, "checkout": E2E_CO, "expect": "开元名都"}, timeout=180)
    expect(r.get("ok") is True, f"trip 查价失败: {str(r)[:300]}")
    expect(r.get("per_night_cny", 0) > 100, f"trip 价格异常: {r.get('per_night_cny')}")
    r2 = call("POST", "/api/hotel/probe", {
        "source": "ly", "kw": "开元名都", "city": "杭州",
        "checkin": E2E_CI, "checkout": E2E_CO}, timeout=180)
    ly_ok = r2.get("ok") is True or r2.get("reason") == "needs_login"
    expect(ly_ok, f"同程通道异常: {str(r2)[:300]}")
    return f"trip 真价 ¥{r.get('per_night_cny')}/晚（{r.get('room')}）；同程 {'可见价' if r2.get('ok') else 'needs_login（待扫码登录）'}"


def s15_web_ui():
    with urllib.request.urlopen(BASE + "/", timeout=10) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    expect("闲鱼卖家机器人" in html, "后台页面标题异常")
    expect("root" in html, "SPA 挂载点缺失")
    return "后台 SPA 正常托管（标题/挂载点在）"


STEPS = [
    ("01 健康检查", s01_health),
    ("02 运行状态（真WS）", s02_status),
    ("03 接口能力探测（真mtop）", s03_capability),
    ("04 查价（真搜索）", s04_search),
    ("05 在售商品（真列表）", s05_items),
    ("06 议价底价", s06_floor),
    ("07 管理对话（真dsh）", s07_admin_chat),
    ("08 发消息 dry-run", s08_send_dryrun),
    ("09 改价闸门 dry-run", s09_reprice_gate),
    ("10 确认闸门拦截", s10_confirm_gate),
    ("11 盯货扫描", s11_snipe),
    ("12 设置读写往返", s12_settings_roundtrip),
    ("13 dsh 隔离与模型", s13_dsh_model),
    ("14 事件台账", s14_ledgers),
    ("15 Web 后台", s15_web_ui),
    ("16 代订酒店接口", s_hotel_quote),
    ("17 三源真查价（真浏览器）", s17_hotel_probe),
]


def main() -> int:
    print(f"\n════ xy-dsh-robot E2E ════  目标 {BASE}\n")
    t0 = time.time()
    for name, fn in STEPS:
        step(name, fn)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n════ 结果：{passed}/{len(RESULTS)} 通过，用时 {time.time()-t0:.0f}s ════")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  失败项：{name} —— {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
