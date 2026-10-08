"""daemon：事件 → SOP 路由 →（关键词快答 | 风控闸门 | dsh 大脑 job）。

路由总表见 docs/architecture.md 与 sop/README.md：
- chat          → sop/sop-inbound-message.yaml
- order_paid    → sop/sop-order-lifecycle.yaml
- 定时审计      → sop/sop-price-audit.yaml
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .brain import Brain, REPO_ROOT, summarize_result
from .browser import CookieCapture
from .config import Account, RobotConfig
from .hotel_quote import HotelQuoteGate
from .ota import OtaProber
from .items import (FishShopRequiredError, capability_probe, my_items,
                    offline_items, rate_buyer, seller_items, update_price)
from .mtop import MtopClient, find_key_recursive
from .orders import agree_freeshipping, confirm_consign, order_detail, sold_orders
from .risk import RiskGate
from .search import search_items
from .store import Store
from .wsclient import XianyuWS

log = logging.getLogger("xy_gate.daemon")


class Daemon:
    def __init__(self, cfg: RobotConfig, account: Account):
        self.cfg = cfg
        self.account = account
        from pathlib import Path
        db_path = Path(cfg.db_path)
        if not db_path.is_absolute():
            db_path = REPO_ROOT / db_path
        self.store = Store(db_path)
        self.mtop = MtopClient(account.cookies)
        self.risk = RiskGate(cfg, self.store)
        self.brain = Brain(cfg.brain)
        self.hotel_gate = HotelQuoteGate(cfg, self.store)
        self._ota_prober = None
        device_id = self.store.kv_get("device_id") or None
        if not device_id:
            from .cookies import generate_device_id
            device_id = generate_device_id(account.user_id)
            self.store.kv_set("device_id", device_id)
        self.ws = XianyuWS(self.mtop, account.user_id, self.on_ws_events, device_id=device_id)
        self.started_at = time.time()
        self._audit_task: Optional[asyncio.Task] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._snipe_task: Optional[asyncio.Task] = None
        # 浏览器登录抓 Cookie（独立 user-data-dir，不碰用户主浏览器配置）
        self.capture = CookieCapture(REPO_ROOT / "data" / "browser-profile")

    # ── 生命周期 ──────────────────────────────────────────────────────

    async def start(self) -> None:
        if "unb=" not in (self.account.cookies or ""):
            # 未登录模式（桌面版首启）：后台/设置页/扫码抓取照常，跳过需要
            # 登录态的常驻任务；apply_new_cookies 扫码成功后会补启 WS
            print("未登录模式：消息/查价等闲鱼功能不可用，请在设置页扫码登录"
                  "（登录成功自动热切换，无需重启）", flush=True)
            return
        self._ws_task = asyncio.create_task(self._ws_guard(), name="ws")
        if self.cfg.audit_daily_at:
            self._audit_task = asyncio.create_task(self._audit_loop(), name="audit")
        if self.cfg.snipe.enabled and self.cfg.snipe.watch:
            self._snipe_task = asyncio.create_task(self._snipe_loop(), name="snipe")

    async def stop(self) -> None:
        await self.ws.stop()
        for t in (self._ws_task, self._audit_task, self._snipe_task):
            if t:
                t.cancel()
        await self.mtop.close()

    async def _ws_guard(self) -> None:
        try:
            await self.ws.start()
        except asyncio.CancelledError:
            pass

    def status(self) -> Dict:
        return {
            "mode": self.cfg.mode,
            "account": self.account.name,
            "user_id": self.account.user_id,
            "ws_connected": self.ws.connected,
            "uptime_sec": int(time.time() - self.started_at),
            "last_pong_age_sec": int(time.time() - self.ws.last_pong) if self.ws.last_pong else None,
            "audit_daily_at": self.cfg.audit_daily_at,
            "auto_confirm_enabled": self.cfg.auto_confirm.enabled,
            "reprice_enabled": self.cfg.reprice.enabled,
            "snipe_enabled": self.cfg.snipe.enabled,
            "dsh_home": self.brain.dsh_home and str(self.brain.dsh_home),
        }

    # ── 配置热重载（settings 页保存后调用）───────────────────────────

    def reload_config(self) -> Dict:
        from .config import load_robot_config
        cfg = load_robot_config()
        self.cfg = cfg
        self.risk = RiskGate(cfg, self.store)      # store 复用
        self.brain = Brain(cfg.brain)
        self.hotel_gate = HotelQuoteGate(cfg, self.store)
        # 循环任务下一轮迭代自动读新 cfg（audit/snipe 均每轮取 self.cfg）
        if cfg.snipe.enabled and cfg.snipe.watch and (self._snipe_task is None or self._snipe_task.done()):
            self._snipe_task = asyncio.create_task(self._snipe_loop(), name="snipe")
        if cfg.audit_daily_at and (self._audit_task is None or self._audit_task.done()):
            self._audit_task = asyncio.create_task(self._audit_loop(), name="audit")
        log.info("配置已热重载（mode=%s, auto_confirm=%s, reprice=%s, snipe=%s）",
                 cfg.mode, cfg.auto_confirm.enabled, cfg.reprice.enabled, cfg.snipe.enabled)
        return {"ok": True, "mode": cfg.mode}

    def settings_dict(self) -> Dict:
        cfg = self.cfg
        return {
            "mode": cfg.mode,
            "audit_daily_at": cfg.audit_daily_at,
            "reply": {
                "quiet_start": cfg.reply.quiet_start,
                "quiet_end": cfg.reply.quiet_end,
                "max_per_hour": cfg.reply.max_per_hour,
                "keyword_rules": cfg.reply.keyword_rules,
            },
            "delivery_items": [
                {"item_id": i.item_id, "title_contains": i.title_contains, "content": i.content}
                for i in cfg.delivery_items
            ],
            "auto_confirm": {
                "enabled": cfg.auto_confirm.enabled,
                "max_amount_cny": cfg.auto_confirm.max_amount_cny,
                "item_whitelist": cfg.auto_confirm.item_whitelist,
                "item_blacklist": cfg.auto_confirm.item_blacklist,
                "buyer_blacklist": cfg.auto_confirm.buyer_blacklist,
                "cooldown_minutes": cfg.auto_confirm.cooldown_minutes,
            },
            "reprice": {
                "enabled": cfg.reprice.enabled,
                "floor_ratio": cfg.reprice.floor_ratio,
                "max_drop_pct": cfg.reprice.max_drop_pct,
                "max_per_day": cfg.reprice.max_per_day,
                "min_interval_min": cfg.reprice.min_interval_min,
            },
            "snipe": {
                "enabled": cfg.snipe.enabled,
                "interval_minutes": cfg.snipe.interval_minutes,
                "watch": [
                    {"keyword": w.keyword, "max_price": w.max_price,
                     "max_ratio": w.max_ratio, "note": w.note}
                    for w in cfg.snipe.watch
                ],
            },
            "brain": {
                "dsh_bin": cfg.brain.dsh_bin,
                "dsh_home": cfg.brain.dsh_home,
                "profile": cfg.brain.profile,
                "job_timeout_sec": cfg.brain.job_timeout_sec,
                "max_concurrent": cfg.brain.max_concurrent,
                "per_chat_cooldown_sec": cfg.brain.per_chat_cooldown_sec,
            },
            "hotel_quotes": {
                "tiers": cfg.hotel_quotes.tiers,
                "manual_threshold_cny": cfg.hotel_quotes.manual_threshold_cny,
                "cache_ttl_min": cfg.hotel_quotes.cache_ttl_min,
                "max_queries_per_hour": cfg.hotel_quotes.max_queries_per_hour,
            },
            "ota": {
                "page_settle_sec": cfg.ota.page_settle_sec,
                "fx_twd_cny": cfg.ota.fx_twd_cny,
                "min_interval_sec": cfg.ota.min_interval_sec,
                "corporate_codes": cfg.ota.corporate_codes,
                "extra_sources": cfg.ota.extra_sources,
            },
            "watchlist": [
                {"item_id": k, "title": v.get("title", ""),
                 "listed_price": v.get("listed_price"), "floor_price": v.get("floor_price"),
                 "keyword": v.get("keyword", "")}
                for k, v in self.risk.watchlist.items()
            ],
        }

    # ── 管理对话（后台 Chat 页 ⇄ dsh）────────────────────────────────

    async def admin_chat(self, message: str) -> Dict:
        """与 dsh 的一次管理对话：带上下文跑 headless job，回复回存。"""
        import json as _json
        history = _json.loads(self.store.kv_get("admin_chat") or "[]")
        history.append({"role": "user", "text": message, "ts": int(time.time())})
        try:
            job = self.brain.build_job("on-admin-chat.md", {
                "QUESTION": message,
                "HISTORY": _json.dumps(history[-10:], ensure_ascii=False),
                "MODE": self.cfg.mode,
                "ACCOUNT": self.account.name,
            })
            result = await self.brain.run_job(job, timeout=max(120, self.cfg.brain.job_timeout_sec))
            reply = result.stdout.strip() or f"（dsh 无输出，exit={result.exit_code}）\n{result.stderr[-300:]}"
            ok = result.ok
        except FileNotFoundError as e:
            reply, ok = f"job 模板缺失: {e}", False
        history.append({"role": "assistant", "text": reply[:4000], "ts": int(time.time())})
        self.store.kv_set("admin_chat", _json.dumps(history[-40:], ensure_ascii=False))
        self.store.add_event("admin_chat", "", "", f"管理对话：{message[:60]} → {'OK' if ok else 'FAIL'}")
        return {"ok": ok, "reply": reply}

    def ota_prober(self):
        if self._ota_prober is None:
            from .config import REPO_ROOT
            self._ota_prober = OtaProber(self.cfg.ota, REPO_ROOT)
        else:
            self._ota_prober.cfg = self.cfg.ota
        return self._ota_prober

    def admin_chat_history(self) -> list:
        import json as _json
        return _json.loads(self.store.kv_get("admin_chat") or "[]")

    # ── dsh 大脑 API 三件套（Key/地址/模型，全部写入隔离 DSH_HOME）─────

    def dsh_credentials_path(self):
        home = self.brain.dsh_home or Path.home() / ".dsh"
        return home / ".credentials.yaml"

    def _dsh_settings_path(self):
        return self.dsh_credentials_path().parent / "settings.yaml"

    def _read_dsh_settings(self) -> Dict:
        import yaml
        p = self._dsh_settings_path()
        if not p.exists():
            return {}
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_dsh_settings(self, data: Dict) -> None:
        import yaml
        p = self._dsh_settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def dsh_key_status(self) -> Dict:
        path = self.dsh_credentials_path()
        key = ""
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY:"):
                    key = line.split(":", 1)[1].strip()
        masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
        model_cfg = self._read_dsh_settings().get("agent-default-model") or {}
        return {
            "configured": bool(key),
            "home": str(path.parent),
            "masked": masked,
            "base_url": self.cfg.brain.api_base_url or "https://api.deepseek.com",
            "model": model_cfg.get("model") or "",
            "provider": model_cfg.get("provider") or "",
        }

    def save_dsh_model(self, api_key: str = "", base_url: str = "",
                       model: str = "") -> Dict:
        """保存 dsh 大脑三件套（各自独立可选）：
        - api_key → <DSH_HOME>/.credentials.yaml（扁平 ref 格式）
        - base_url → robot.yaml brain.api_base_url（brain 以 DEEPSEEK_BASE_URL 注入）
        - model → <DSH_HOME>/settings.yaml 的 agent-default-model 段
        """
        home = self.dsh_credentials_path().parent
        home.mkdir(parents=True, exist_ok=True)
        if api_key:
            (home / ".credentials.yaml").write_text(
                f"DEEPSEEK_API_KEY: {api_key.strip()}\n", encoding="utf-8")
        if base_url:
            self.cfg.brain.api_base_url = (
                "" if base_url.strip() == "https://api.deepseek.com" else base_url.strip())
        if model:
            data = self._read_dsh_settings()
            data["agent-default-model"] = {"provider": "deepseek-official", "model": model.strip()}
            self._write_dsh_settings(data)
        from .config import render_robot_yaml
        (REPO_ROOT / "config" / "robot.yaml").write_text(
            render_robot_yaml(self.cfg), encoding="utf-8")
        self.reload_config()
        return {"ok": True, "home": str(home)}

    # ── 事件入口 ──────────────────────────────────────────────────────

    async def on_ws_events(self, events: List[Dict]) -> None:
        for ev in events:
            try:
                if ev["kind"] == "chat":
                    await self.handle_chat(ev)
                elif ev["kind"] == "order_paid":
                    await self.handle_order_paid(ev)
                elif ev["kind"] == "order_refund":
                    self.store.add_event("order_refund", ev["chat_id"], ev["item_id"],
                                         f"退款/售后事件（转人工）: {ev['text'][:80]}")
                    log.info("退款事件，转人工: %s", ev["text"][:60])
                else:
                    self.store.add_event("card", ev["chat_id"], ev["item_id"],
                                         f"系统卡片: {ev['text'][:80]}")
            except Exception:
                log.exception("事件处理异常: %s", json.dumps(ev, ensure_ascii=False, default=str)[:300])

    # ── 聊天：SOP sop-inbound-message ─────────────────────────────────

    async def handle_chat(self, ev: Dict) -> None:
        if not ev["is_buyer"]:
            return  # 自己/系统消息只入史
        fresh = self.store.add_message(ev["chat_id"], ev["msg_id"], "in",
                                       ev["sender_id"], ev["sender_name"], ev["text"], ev["item_id"])
        if not fresh:
            return  # 消息 ID 去重

        reply_gate = self.risk.check_reply(ev["chat_id"])
        if not reply_gate.ok:
            self.store.add_event("chat_skipped", ev["chat_id"], ev["item_id"],
                                 f"收到买家消息但被回复闸门拦截: {'; '.join(reply_gate.reasons)}")
            return

        # 第一级：关键词快答（0 成本秒回）
        rule = self.risk.match_keyword_rule(ev["text"], ev["item_id"])
        if rule:
            await self.send_reply(ev["chat_id"], ev["sender_id"], str(rule["reply"]),
                                  why=f"关键词规则命中: {rule['match']}")
            return

        # 第二级：dsh 大脑（技能 xianyu-auto-reply + SOP）
        ok, why_not = self.risk.check_brain_job(ev["chat_id"])
        if not ok:
            self.store.add_event("chat_skipped", ev["chat_id"], ev["item_id"], why_not)
            return
        await self._run_brain_job("on-message.md", ev, kind="chat")

    # ── 订单：SOP sop-order-lifecycle ─────────────────────────────────

    async def handle_order_paid(self, ev: Dict) -> None:
        order_id = ev.get("order_id") or ""
        item_id = ev.get("item_id") or ""
        buyer_id = ev.get("sender_id") or ""
        if not order_id:
            order_id = await self._guess_order_id(buyer_id, item_id)
        self.store.add_event("order_paid", ev["chat_id"], item_id,
                             f"买家付款 order={order_id} buyer={buyer_id}")

        # 1) 自动发货内容表（可选，虚拟商品秒发）
        title, amount = await self._order_title_amount(order_id)
        content = self._match_delivery(item_id, title)
        if content:
            await self.send_reply(ev["chat_id"], buyer_id, content,
                                  why=f"自动发货内容命中 order={order_id}")

        # 2) 自动确认交易（风控闸门 + consign.dummy / freeshipping）
        await self.confirm_transaction(
            order_id=order_id, item_id=item_id, buyer_id=buyer_id,
            amount=amount, source="daemon", notify_chat=ev["chat_id"],
        )

        # 3) 大脑 job：发货后话术/异常兜底（回复仍过闸门）
        await self._run_brain_job("on-order-paid.md", {
            **ev, "order_id": order_id, "order_title": title, "order_amount": amount,
        }, kind="order_paid")

    async def confirm_transaction(
        self,
        order_id: str,
        item_id: str = "",
        buyer_id: str = "",
        amount: Optional[float] = None,
        trade_text: str = "",
        source: str = "manual",
        notify_chat: str = "",
    ) -> Dict:
        """自动确认交易唯一入口（daemon 自动流 / HTTP / CLI 都走这里）。"""
        if not order_id:
            return {"success": False, "decision": "block", "reasons": ["缺少订单号"]}
        decision = self.risk.check_confirm(order_id, item_id, buyer_id, amount)
        result_text = ""
        ok = False
        try:
            if decision.action == "allow":
                res = await confirm_consign(self.mtop, order_id, trade_text)
                ok = bool(res.get("success"))
                result_text = json.dumps(res, ensure_ascii=False)
            elif decision.action == "dryrun":
                ok = True
                result_text = "dry-run：模拟确认交易成功（未调用真实 API）"
            else:
                result_text = "blocked: " + "; ".join(decision.reasons)
        finally:
            self.store.record_confirm(order_id, item_id, buyer_id, amount,
                                      decision.action, result_text,
                                      simulated=decision.action != "allow")
            self.store.add_event("confirm", notify_chat, item_id,
                                 f"[{source}] order={order_id} decision={decision.action} {result_text[:120]}")
        if ok and notify_chat and buyer_id:
            note = "已确认发货，卡密/内容请查收，有问题随时找我~" if decision.action == "allow" \
                else "[dry-run] 已模拟确认发货"
            await self.send_reply(notify_chat, buyer_id, note, why="确认交易后通知")
        return {"success": ok, "decision": decision.action, "reasons": decision.reasons,
                "result": result_text}

    # ── 大脑 job ──────────────────────────────────────────────────────

    async def _run_brain_job(self, template: str, ev: Dict, kind: str) -> None:
        history = self.store.history(ev.get("chat_id", ""), limit=12)
        event_id = self.store.add_event(f"brain:{template}", ev.get("chat_id", ""),
                                        ev.get("item_id", ""),
                                        f"{kind}: {ev.get('text', '')[:100]}")
        try:
            job = self.brain.build_job(template, {
                "EVENT": json.dumps({k: v for k, v in ev.items() if k != "raw"},
                                    ensure_ascii=False, default=str),
                "HISTORY": json.dumps(history, ensure_ascii=False),
                "MODE": self.cfg.mode,
                "ACCOUNT": self.account.name,
            })
        except FileNotFoundError as e:
            self.store.update_event_brain(event_id, "no_template", str(e))
            return
        self.store.kv_set(f"brain_last:{ev.get('chat_id', '')}", str(time.time()))
        result = await self.brain.run_job(job)
        self.store.update_event_brain(event_id,
                                      "ok" if result.ok else f"exit={result.exit_code}",
                                      summarize_result(result))
        log.info("大脑 job %s %s（%.1fs）", template, "成功" if result.ok else "失败",
                 result.duration_sec)

    # ── 每日查价审计：SOP sop-price-audit ─────────────────────────────

    async def _audit_loop(self) -> None:
        try:
            while True:
                now = datetime.now()
                try:
                    hh, mm = str(self.cfg.audit_daily_at).split(":")
                    nxt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                except ValueError:
                    log.error("audit_daily_at 配置非法: %s", self.cfg.audit_daily_at)
                    return
                if nxt <= now:
                    nxt += timedelta(days=1)
                await asyncio.sleep((nxt - now).total_seconds())
                last = self.store.kv_get("audit_last_date")
                today = datetime.now().strftime("%Y-%m-%d")
                if last == today:
                    continue
                self.store.kv_set("audit_last_date", today)
                event_id = self.store.add_event("brain:daily-audit.md", "", "",
                                                f"每日查价审计 {today}")
                try:
                    job = self.brain.build_job("daily-audit.md",
                                               {"MODE": self.cfg.mode, "DATE": today,
                                                "ACCOUNT": self.account.name})
                    result = await self.brain.run_job(job)
                    self.store.update_event_brain(event_id,
                                                  "ok" if result.ok else f"exit={result.exit_code}",
                                                  summarize_result(result))
                except Exception:
                    log.exception("每日审计失败")
                    self.store.update_event_brain(event_id, "error", "审计 job 异常")
        except asyncio.CancelledError:
            pass

    # ── 动作工具（HTTP/CLI 也复用）────────────────────────────────────

    async def send_reply(self, chat_id: str, to_user_id: str, text: str, why: str = "") -> Dict:
        """唯一发消息出口：dry-run 模拟，live 真发；一律入聊天史。"""
        self.store.add_message(chat_id, "", "out", self.account.user_id, "我", text)
        if not self.cfg.live:
            self.store.add_event("reply_simulated", chat_id, "", f"[dry-run] {why} → {text[:80]}")
            return {"success": True, "simulated": True, "chat_id": chat_id, "content": text}
        res = await self.ws.send_msg(chat_id, to_user_id, text)
        self.store.add_event("reply_sent" if res.get("success") else "reply_failed",
                             chat_id, "", f"{why} → {text[:80]} | {res.get('error', '')}")
        return res

    async def do_search(self, keyword: str, price_min: float = None,
                        price_max: float = None, rows: int = 30) -> Dict:
        return await search_items(self.mtop, keyword, price_min=price_min,
                                  price_max=price_max, rows=rows)

    async def do_orders(self, query_code: str = "NOT_SHIP") -> Dict:
        return await sold_orders(self.mtop, query_code=query_code)

    async def do_order_detail(self, order_id: str) -> Dict:
        return await order_detail(self.mtop, order_id)

    # ── 商品运营（卖侧）───────────────────────────────────────────────

    async def do_my_items(self, page: int = 1, rows: int = 20) -> Dict:
        return await my_items(self.mtop, self.account.user_id, page, rows)

    async def do_seller_items(self) -> Dict:
        return await seller_items(self.mtop)

    async def do_capability(self) -> Dict:
        return await capability_probe(self.mtop, self.account.user_id)

    async def do_reprice(self, item_id: str, new_price: float,
                         source: str = "manual") -> Dict:
        """自动改价唯一入口（议价成交/审计调价/CLI 三路同闸）。"""
        current = None
        try:
            items = (await my_items(self.mtop, self.account.user_id, rows=50)).get("items", [])
            current = next((i["price"] for i in items
                            if i["item_id"] == str(item_id) and i.get("price") is not None), None)
        except Exception:
            log.exception("改价前拉取现价失败（不阻断预检）")
        decision = self.risk.check_reprice(item_id, current, new_price)
        result_text = ""
        ok = False
        try:
            if decision.action in ("allow", "dryrun"):
                if decision.action == "allow":
                    res = await update_price(self.mtop, item_id, new_price)
                    ok = bool(res.get("success"))
                    result_text = json.dumps(res, ensure_ascii=False)
                else:
                    ok = True
                    result_text = "dry-run：模拟改价成功（未调用真实 API）"
            else:
                result_text = "blocked: " + "; ".join(decision.reasons)
        except FishShopRequiredError as e:
            result_text = f"需开通鱼小铺：{e}"
        except Exception as e:  # noqa: BLE001
            result_text = f"改价失败: {e}"
        finally:
            if decision.action != "block" or "总开关" not in result_text:
                self.risk.record_reprice(item_id, current, new_price,
                                         decision.action, result_text)
        return {"success": ok, "decision": decision.action,
                "reasons": decision.reasons, "current_price": current,
                "result": result_text}

    async def do_offline(self, item_ids: list) -> Dict:
        if not self.cfg.live:
            return {"success": True, "simulated": True, "item_ids": item_ids,
                    "note": "dry-run：模拟下架"}
        return await offline_items(self.mtop, item_ids)

    async def do_rate(self, trade_id: str, feedback: str = "") -> Dict:
        if not self.cfg.live:
            return {"success": True, "simulated": True,
                    "note": "dry-run：模拟评价"}
        return await rate_buyer(self.mtop, trade_id, feedback)

    # ── 盯货（捡漏）循环 ──────────────────────────────────────────────

    async def _snipe_loop(self) -> None:
        """定时扫订阅关键词，命中阈值即写事件（付款永远人工，不做自动下单）。"""
        try:
            while True:
                for w in self.cfg.snipe.watch:
                    try:
                        res = await search_items(self.mtop, w.keyword, rows=20)
                        median = res["stats"].get("median")
                        hits = []
                        for it in res["items"]:
                            price = it.get("price")
                            if price is None:
                                continue
                            below_abs = w.max_price is not None and price <= w.max_price
                            below_ratio = (w.max_ratio is not None and median
                                           and price <= median * w.max_ratio)
                            if not (below_abs or below_ratio):
                                continue
                            # 去重：同一 item 只报一次
                            if self.store.kv_get(f"snipe_seen:{it['item_id']}"):
                                continue
                            self.store.kv_set(f"snipe_seen:{it['item_id']}", "1")
                            hits.append({**it, "median": median})
                        if hits:
                            for h in hits[:5]:
                                self.store.add_event(
                                    "snipe_hit", "", h.get("item_id", ""),
                                    f"盯货命中[{w.keyword}] ¥{h.get('price')}"
                                    f"（中位 ¥{h.get('median')}）{h.get('title', '')[:40]} "
                                    f"{h.get('url', '')} —— 人工核验后自行拍下")
                    except Exception:
                        log.exception("盯货扫描失败 keyword=%s", w.keyword)
                await asyncio.sleep(self.cfg.snipe.interval_minutes * 60)
        except asyncio.CancelledError:
            pass

    async def snipe_run_once(self) -> Dict:
        """手动触发一轮盯货扫描（不等定时）。"""
        hits_total = []
        for w in self.cfg.snipe.watch or []:
            try:
                res = await search_items(self.mtop, w.keyword, rows=20)
                median = res["stats"].get("median")
                for it in res["items"]:
                    price = it.get("price")
                    if price is None:
                        continue
                    below_abs = w.max_price is not None and price <= w.max_price
                    below_ratio = (w.max_ratio is not None and median
                                   and price <= median * w.max_ratio)
                    if below_abs or below_ratio:
                        hits_total.append({"keyword": w.keyword, **it, "median": median})
            except Exception as e:  # noqa: BLE001
                hits_total.append({"keyword": w.keyword, "error": str(e)})
        for h in hits_total:
            if not h.get("error"):
                self.store.add_event("snipe_hit", "", h.get("item_id", ""),
                                     f"手动盯货[{h['keyword']}] ¥{h.get('price')} {h.get('title', '')[:40]}")
        return {"hits": hits_total}

    # ── Cookie 换血（抓取成功/手动保存后热切换登录态）────────────────

    async def apply_new_cookies(self, cookies: str) -> Dict:
        target = REPO_ROOT / "config" / "accounts.yaml"
        target.write_text(
            "accounts:\n  " + self.account.name + ':\n    cookies: "' + cookies + '"\n',
            encoding="utf-8")
        self.account = Account(name=self.account.name, cookies=cookies)
        await self.mtop.close()
        self.mtop = MtopClient(cookies)
        if self._ws_task:
            self._ws_task.cancel()
        await self.ws.stop()
        from .cookies import generate_device_id
        device_id = self.store.kv_get("device_id") or generate_device_id(self.account.user_id)
        self.store.kv_set("device_id", device_id)
        self.ws = XianyuWS(self.mtop, self.account.user_id, self.on_ws_events,
                           device_id=device_id)
        self._ws_task = asyncio.create_task(self._ws_guard())
        log.info("登录态已热切换（unb=%s，WS 重连中）", self.account.user_id)
        return {"ok": True, "unb": self.account.user_id}

    async def start_cookie_capture(self) -> Dict:
        res = await self.capture.start()
        if res.get("ok"):
            asyncio.create_task(self._watch_capture_done())
        return res

    async def _watch_capture_done(self) -> None:
        # 抓取会话结束后：成功则自动换血
        t = self.capture._task
        if t:
            try:
                await t
            except Exception:
                pass
        if self.capture.state == "success" and self.capture.cookie_header:
            try:
                await self.apply_new_cookies(self.capture.cookie_header)
                self.store.add_event("cookie_captured", "", "",
                                     f"浏览器登录抓取成功，unb={self.account.user_id}，已热切换")
            except Exception:
                log.exception("抓取成功但换血失败")

    # ── 内部工具 ──────────────────────────────────────────────────────

    async def _guess_order_id(self, buyer_id: str, item_id: str) -> str:
        """卡片里没带订单号时，从待发货订单里按买家/商品匹配一个。"""
        try:
            res = await sold_orders(self.mtop, query_code="NOT_SHIP")
            for o in res.get("orders", []):
                if (item_id and o.get("item_id") == item_id) or \
                   (buyer_id and o.get("buyer_id") == buyer_id):
                    return o.get("order_id", "")
        except Exception:
            log.exception("猜测订单号失败")
        return ""

    async def _order_title_amount(self, order_id: str):
        """订单标题与金额（尽力而为，失败不阻断主流程）。"""
        if not order_id:
            return "", None
        try:
            res = await order_detail(self.mtop, order_id)
            raw = res.get("raw") or {}
            title = str(find_key_recursive(raw, "itemTitle", "title") or "")
            fee = find_key_recursive(raw, "actualPayFee", "payFee", "realPayFee", "price")
            try:
                amount = float(str(fee)) if fee is not None else None
            except (TypeError, ValueError):
                amount = None
            return title, amount
        except Exception:
            log.exception("订单详情获取失败 order=%s", order_id)
            return "", None

    def _match_delivery(self, item_id: str, title: str) -> str:
        for it in self.cfg.delivery_items:
            if it.item_id and item_id and it.item_id == item_id:
                return it.content
            if it.title_contains and title and it.title_contains in title:
                return it.content
        return ""
