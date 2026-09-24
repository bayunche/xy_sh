"""本地 HTTP API（默认 127.0.0.1:8790，只监听回环）。

dsh 大脑通过 CLI（xy-gate …）访问本 API；写操作（send/confirm）在
daemon 层还会再过一次风控闸门，大脑无法绕过。
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from aiohttp import web

from .brain import REPO_ROOT
from .daemon import Daemon
from .mtop import MtopError, RiskControlError


def _json(data, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str, indent=2),
        content_type="application/json", status=status,
    )


def build_app(daemon: Daemon) -> web.Application:
    async def health(_req):
        return _json({"ok": True})

    async def status(_req):
        return _json(daemon.status())

    async def events(req):
        limit = int(req.query.get("limit", "50"))
        return _json(daemon.store.recent_events(limit))

    async def history(req):
        chat_id = req.query.get("chat_id", "")
        limit = int(req.query.get("limit", "20"))
        if not chat_id:
            return _json({"error": "缺少 chat_id"}, 400)
        return _json({"chat_id": chat_id, "messages": daemon.store.history(chat_id, limit)})

    async def search(req):
        body = await req.json()
        keyword = str(body.get("keyword") or "").strip()
        if not keyword:
            return _json({"error": "缺少 keyword"}, 400)
        try:
            res = await daemon.do_search(
                keyword,
                price_min=body.get("price_min"),
                price_max=body.get("price_max"),
                rows=int(body.get("rows") or 30),
            )
            return _json(res)
        except RiskControlError as e:
            return _json({"error": str(e), "punish_url": e.punish_url,
                          "hint": "触发风控，需人工处理 Cookie"}, 503)
        except MtopError as e:
            return _json({"error": str(e)}, 502)

    async def send(req):
        body = await req.json()
        chat_id = str(body.get("chat_id") or "")
        to_user = str(body.get("to_user_id") or "")
        text = str(body.get("text") or "")
        if not (chat_id and to_user and text):
            return _json({"error": "缺少 chat_id / to_user_id / text"}, 400)
        res = await daemon.send_reply(chat_id, to_user, text,
                                      why=str(body.get("why") or "手动/API 发送"))
        return _json(res, 200 if res.get("success") else 502)

    async def orders(req):
        query = req.query.get("query", "NOT_SHIP")
        if query not in ("NOT_SHIP", "ALL"):
            return _json({"error": "query 只能是 NOT_SHIP 或 ALL"}, 400)
        try:
            return _json(await daemon.do_orders(query))
        except (MtopError, RiskControlError) as e:
            return _json({"error": str(e)}, 502)

    async def order_detail(req):
        order_id = req.match_info["order_id"]
        try:
            return _json(await daemon.do_order_detail(order_id))
        except (MtopError, RiskControlError) as e:
            return _json({"error": str(e)}, 502)

    async def confirm_check(req):
        body = await req.json()
        d = daemon.risk.check_confirm(
            order_id=str(body.get("order_id") or ""),
            item_id=str(body.get("item_id") or ""),
            buyer_id=str(body.get("buyer_id") or ""),
            amount=body.get("amount"),
        )
        return _json(d.to_dict())

    async def confirm(req):
        body = await req.json()
        res = await daemon.confirm_transaction(
            order_id=str(body.get("order_id") or ""),
            item_id=str(body.get("item_id") or ""),
            buyer_id=str(body.get("buyer_id") or ""),
            amount=body.get("amount"),
            trade_text=str(body.get("trade_text") or ""),
            source=str(body.get("source") or "api"),
            notify_chat=str(body.get("notify_chat") or ""),
        )
        return _json(res, 200 if res.get("success") else 403)

    async def confirm_history(_req):
        return _json(daemon.store.confirm_history())

    # ── 商品运营（卖侧）───────────────────────────────────────────────

    async def items(req):
        try:
            if req.query.get("source") == "seller":
                return _json(await daemon.do_seller_items())
            page = int(req.query.get("page", "1"))
            return _json(await daemon.do_my_items(page=page))
        except MtopError as e:
            return _json({"error": str(e)}, 502)

    async def capability(_req):
        return _json(await daemon.do_capability())

    async def floor(req):
        item_id = req.match_info["item_id"]
        listed = None
        try:
            items = (await daemon.do_my_items(rows=50)).get("items", [])
            listed = next((i.get("price") for i in items if i["item_id"] == item_id), None)
        except MtopError:
            pass
        fp = daemon.risk.floor_price_of(item_id, listed)
        return _json({"item_id": item_id, "listed_price": listed, "floor_price": fp,
                      "note": "floor_price 为空表示无 watchlist 底价且查不到挂价，禁止自动接受还价"})

    async def reprice(req):
        body = await req.json()
        item_id = str(body.get("item_id") or "")
        try:
            new_price = float(body.get("new_price"))
        except (TypeError, ValueError):
            return _json({"error": "new_price 必须是数字"}, 400)
        res = await daemon.do_reprice(item_id, new_price,
                                      source=str(body.get("source") or "api"))
        return _json(res, 200 if res.get("success") or res.get("decision") == "dryrun" else 403)

    async def offline(req):
        body = await req.json()
        ids = [str(x) for x in (body.get("item_ids") or []) if str(x).strip()]
        if not ids:
            return _json({"error": "缺少 item_ids"}, 400)
        try:
            return _json(await daemon.do_offline(ids))
        except MtopError as e:
            return _json({"error": str(e)}, 502)

    async def rate(req):
        body = await req.json()
        trade_id = str(body.get("trade_id") or "")
        if not trade_id:
            return _json({"error": "缺少 trade_id"}, 400)
        try:
            return _json(await daemon.do_rate(trade_id, str(body.get("feedback") or "")))
        except MtopError as e:
            return _json({"error": str(e)}, 502)

    async def snipe_run(_req):
        return _json(await daemon.snipe_run_once())

    # ── 管理后台 API ─────────────────────────────────────────────────

    async def settings_get(_req):
        return _json(daemon.settings_dict())

    async def settings_put(req):
        from .config import render_robot_yaml
        from pathlib import Path
        body = await req.json()
        cur = daemon.settings_dict()

        def merge(section, defaults):
            merged = dict(defaults)
            incoming = body.get(section)
            if isinstance(incoming, dict):
                merged.update(incoming)
            return merged

        reply = merge("reply", cur["reply"])
        ac = merge("auto_confirm", cur["auto_confirm"])
        rp = merge("reprice", cur["reprice"])
        sn = merge("snipe", cur["snipe"])
        brain = merge("brain", cur["brain"])
        mode = body.get("mode", cur["mode"] if isinstance(cur, dict) else daemon.cfg.mode)
        if mode not in ("dry-run", "live"):
            return _json({"error": "mode 只能是 dry-run 或 live"}, 400)
        # 直接以当前配置对象为底改写，保证未知字段不丢
        cfg = daemon.cfg
        cfg.mode = mode
        cfg.audit_daily_at = body.get("audit_daily_at", cfg.audit_daily_at) or None
        cfg.reply.quiet_start = reply.get("quiet_start") or None
        cfg.reply.quiet_end = reply.get("quiet_end") or None
        cfg.reply.max_per_hour = int(reply.get("max_per_hour") or 60)
        cfg.reply.keyword_rules = reply.get("keyword_rules") or []
        cfg.delivery_items = [
            __import__("xy_gate.config", fromlist=["DeliveryItem"]).DeliveryItem(
                item_id=str(i.get("item_id") or ""), title_contains=str(i.get("title_contains") or ""),
                content=str(i.get("content") or ""))
            for i in (body.get("delivery_items") if isinstance(body.get("delivery_items"), list)
                      else cfg.delivery_items)
            if isinstance(i, dict) and i.get("content")
        ]
        cfg.auto_confirm.enabled = bool(ac.get("enabled"))
        cfg.auto_confirm.max_amount_cny = float(ac.get("max_amount_cny") or 100)
        cfg.auto_confirm.item_whitelist = [str(x) for x in ac.get("item_whitelist") or []]
        cfg.auto_confirm.item_blacklist = [str(x) for x in ac.get("item_blacklist") or []]
        cfg.auto_confirm.buyer_blacklist = [str(x) for x in ac.get("buyer_blacklist") or []]
        cfg.auto_confirm.cooldown_minutes = int(ac.get("cooldown_minutes") or 3)
        cfg.reprice.enabled = bool(rp.get("enabled"))
        cfg.reprice.floor_ratio = float(rp.get("floor_ratio") or 0.85)
        cfg.reprice.max_drop_pct = float(rp.get("max_drop_pct") or 20)
        cfg.reprice.max_per_day = int(rp.get("max_per_day") or 5)
        cfg.reprice.min_interval_min = int(rp.get("min_interval_min") or 10)
        cfg.snipe.enabled = bool(sn.get("enabled"))
        cfg.snipe.interval_minutes = int(sn.get("interval_minutes") or 30)
        from .config import SnipeWatch
        cfg.snipe.watch = [
            SnipeWatch(keyword=str(w.get("keyword") or ""), max_price=w.get("max_price"),
                       max_ratio=w.get("max_ratio"), note=str(w.get("note") or ""))
            for w in (sn.get("watch") or []) if isinstance(w, dict) and w.get("keyword")
        ]
        cfg.brain.dsh_home = str(brain.get("dsh_home") or cfg.brain.dsh_home)
        hq = body.get("hotel_quotes")
        if isinstance(hq, dict):
            if hq.get("manual_threshold_cny") is not None:
                cfg.hotel_quotes.manual_threshold_cny = float(hq["manual_threshold_cny"])
            if hq.get("cache_ttl_min") is not None:
                cfg.hotel_quotes.cache_ttl_min = int(hq["cache_ttl_min"])
            if hq.get("max_queries_per_hour") is not None:
                cfg.hotel_quotes.max_queries_per_hour = int(hq["max_queries_per_hour"])
        cfg.brain.job_timeout_sec = int(brain.get("job_timeout_sec") or cfg.brain.job_timeout_sec)
        cfg.brain.max_concurrent = max(1, int(brain.get("max_concurrent") or cfg.brain.max_concurrent))
        cfg.brain.per_chat_cooldown_sec = int(brain.get("per_chat_cooldown_sec") or cfg.brain.per_chat_cooldown_sec)
        if isinstance(ac.get("buyer_blacklist"), list):
            cfg.auto_confirm.buyer_blacklist = [str(x) for x in ac["buyer_blacklist"]]

        robot_yaml = REPO_ROOT / "config" / "robot.yaml"
        robot_yaml.write_text(render_robot_yaml(cfg), encoding="utf-8")

        # watchlist（议价底价表）写独立文件
        wl = body.get("watchlist")
        wl_lines = ["# 议价底价表（管理后台生成）", "items:"]
        for it in (wl if isinstance(wl, list) else []):
            if not (isinstance(it, dict) and it.get("item_id")):
                continue
            wl_lines.append(f'  - item_id: "{str(it["item_id"])}"')
            if it.get("title"):
                wl_lines.append(f'    title: "{str(it["title"])}"')
            for k in ("listed_price", "floor_price"):
                if it.get(k) not in (None, ""):
                    wl_lines.append(f'    {k}: {it[k]}')
            if it.get("keyword"):
                wl_lines.append(f'    keyword: "{str(it["keyword"])}"')
        if not isinstance(wl, list) or len(wl) == 0:
            wl_lines.append("  []")
        (REPO_ROOT / "config" / "watchlist.yaml").write_text(
            "\n".join(wl_lines) + "\n", encoding="utf-8")

        daemon.reload_config()
        return _json({"ok": True, "saved": str(robot_yaml)})

    async def account_get(_req):
        acct = daemon.account
        return {"configured": True, "name": acct.name, "unb": acct.user_id,
                "cookie_length": len(acct.cookies)}

    async def account_put(req):
        body = await req.json()
        cookies = str(body.get("cookies") or "").strip()
        if "unb=" not in cookies or "_m_h5_tk" not in cookies:
            return _json({"error": "Cookie 必须包含 unb 与 _m_h5_tk（从 goofish.com 登录后复制整段）"}, 400)
        res = await daemon.apply_new_cookies(cookies)
        return _json({"ok": True, **res, "note": "Cookie 已保存并热切换（WS 正在重连）"})

    async def dsh_key_get(_req):
        return _json(daemon.dsh_key_status())

    async def dsh_key_put(req):
        body = await req.json()
        key = str(body.get("api_key") or "").strip()
        base_url = str(body.get("base_url") or "").strip()
        model = str(body.get("model") or "").strip()
        if key and len(key) < 16:
            return _json({"error": "API Key 长度异常"}, 400)
        if not (key or base_url or model):
            return _json({"error": "至少提供 api_key / base_url / model 之一"}, 400)
        return _json(daemon.save_dsh_model(api_key=key, base_url=base_url, model=model))

    async def chat_post(req):
        body = await req.json()
        message = str(body.get("message") or "").strip()
        if not message:
            return _json({"error": "消息为空"}, 400)
        return _json(await daemon.admin_chat(message))

    async def chat_history(_req):
        return _json({"messages": daemon.admin_chat_history()})

    async def snipe_hits(_req):
        events = daemon.store.recent_events(300)
        hits = [e for e in events if e["kind"] == "snipe_hit"]
        out = []
        for h in hits[:100]:
            item_id = str(h.get("item_id") or "")
            out.append({
                **h,
                "url": f"https://www.goofish.com/item?id={item_id}" if item_id else "",
                "handled": bool(daemon.store.kv_get(f"snipe_handled:{item_id}")),
            })
        return _json({"hits": out})

    async def snipe_handle(req):
        body = await req.json()
        item_id = str(body.get("item_id") or "")
        if not item_id:
            return _json({"error": "缺少 item_id"}, 400)
        daemon.store.kv_set(f"snipe_handled:{item_id}", "1")
        return _json({"ok": True})

    async def reload_cfg(_req):
        return _json(daemon.reload_config())

    async def hotel_cost(req):
        body = await req.json()
        hotel = str(body.get("hotel") or "").strip()
        if not hotel:
            return _json({"error": "缺少 hotel"}, 400)
        price = body.get("price")
        if price in (None, ""):
            return _json({"hotel": hotel, "cost": daemon.hotel_gate.cost_get(hotel)})
        try:
            daemon.hotel_gate.cost_set(hotel, float(price))
        except (TypeError, ValueError):
            return _json({"error": "price 必须是数字"}, 400)
        return _json({"ok": True, "hotel": hotel, "cost": float(price)})

    async def hotel_quote_status(_req):
        return _json({
            "tiers": daemon.cfg.hotel_quotes.tiers,
            "manual_threshold_cny": daemon.cfg.hotel_quotes.manual_threshold_cny,
            "cache_ttl_min": daemon.cfg.hotel_quotes.cache_ttl_min,
            "max_queries_per_hour": daemon.cfg.hotel_quotes.max_queries_per_hour,
            "cache_entries": daemon.store.hotel_quote_cache_count(),
        })

    async def cookie_capture_start(_req):
        return _json(await daemon.start_cookie_capture())

    async def cookie_capture_status(_req):
        return _json(daemon.capture.status())

    async def cookie_capture_stop(_req):
        return _json(await daemon.capture.stop())

    async def audit_now(_req):
        """手动触发每日审计 job（不等定时）。"""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        event_id = daemon.store.add_event("brain:daily-audit.md", "", "", f"手动审计 {today}")
        try:
            job = daemon.brain.build_job("daily-audit.md",
                                         {"MODE": daemon.cfg.mode, "DATE": today,
                                          "ACCOUNT": daemon.account.name})
            result = await daemon.brain.run_job(job)
            daemon.store.update_event_brain(event_id,
                                            "ok" if result.ok else f"exit={result.exit_code}",
                                            result.stdout[-2000:])
            return _json({"ok": result.ok, "stdout_tail": result.stdout[-2000:],
                          "stderr": result.stderr[-500:]})
        except Exception as e:
            daemon.store.update_event_brain(event_id, "error", str(e))
            return _json({"ok": False, "error": str(e)}, 500)

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_get("/events", events)
    app.router.add_get("/history", history)
    app.router.add_post("/search", search)
    app.router.add_post("/send", send)
    app.router.add_get("/orders", orders)
    app.router.add_get("/order/{order_id}", order_detail)
    app.router.add_post("/confirm/check", confirm_check)
    app.router.add_post("/confirm", confirm)
    app.router.add_get("/confirm/history", confirm_history)
    app.router.add_get("/items", items)
    app.router.add_get("/capability", capability)
    app.router.add_get("/floor/{item_id}", floor)
    app.router.add_post("/reprice", reprice)
    app.router.add_post("/offline", offline)
    app.router.add_post("/rate", rate)
    app.router.add_post("/snipe/run", snipe_run)
    app.router.add_get("/snipe/hits", snipe_hits)
    app.router.add_post("/snipe/handle", snipe_handle)
    app.router.add_get("/api/settings", settings_get)
    app.router.add_put("/api/settings", settings_put)
    app.router.add_get("/api/account", account_get)
    app.router.add_put("/api/account", account_put)
    app.router.add_get("/api/dsh-key", dsh_key_get)
    app.router.add_put("/api/dsh-key", dsh_key_put)
    app.router.add_get("/api/dsh-model", dsh_key_get)   # 别名：语义即模型三件套
    app.router.add_put("/api/dsh-model", dsh_key_put)
    app.router.add_post("/api/chat", chat_post)
    app.router.add_get("/api/chat/history", chat_history)
    app.router.add_post("/api/reload", reload_cfg)
    app.router.add_post("/api/hotel/cost", hotel_cost)
    app.router.add_get("/api/hotel/quote-status", hotel_quote_status)
    app.router.add_post("/api/cookie-capture/start", cookie_capture_start)
    app.router.add_get("/api/cookie-capture/status", cookie_capture_status)
    app.router.add_post("/api/cookie-capture/stop", cookie_capture_stop)
    app.router.add_post("/audit/run", audit_now)

    # ── 管理后台静态文件（web/dist，SPA 回退到 index.html）──────────
    dist = REPO_ROOT / "web" / "dist"
    if dist.exists():
        app.router.add_get("/", index_handler)
        app.router.add_static("/assets/", dist / "assets")
    return app


async def index_handler(request):
    from aiohttp import web as aioweb
    index = REPO_ROOT / "web" / "dist" / "index.html"
    if not index.exists():
        return aioweb.Response(text="web/dist 未构建（开发者：cd web && npm run build）", status=503)
    return aioweb.FileResponse(index)


async def serve(daemon: Daemon) -> None:
    from aiohttp import web as aioweb
    await daemon.start()
    runner = web.AppRunner(build_app(daemon))
    await runner.setup()
    site = web.TCPSite(runner, daemon.cfg.listen_host, daemon.cfg.listen_port)
    await site.start()
    print(f"xy-gate 已启动: http://{daemon.cfg.listen_host}:{daemon.cfg.listen_port}"
          f"（mode={daemon.cfg.mode}, account={daemon.account.name}）")
    try:
        await asyncio.Event().wait()   # 常驻
    finally:
        await runner.cleanup()
        await daemon.stop()
