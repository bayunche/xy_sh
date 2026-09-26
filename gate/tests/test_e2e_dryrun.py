"""端到端 dry-run 编排测试（不联网、不真调 dsh/闲鱼）。

覆盖：
- 买家消息 → 关键词快答 → send_reply 模拟入库
- 买家消息（无关键词命中）→ 大脑 job 被拉起（run_job 打桩）
- 付款卡片 → 发货内容表 → 风控闸门 dryrun → confirm 台账
- 静默时段拦截
"""
import json

import pytest

from xy_gate.brain import BrainResult
from xy_gate.config import Account, AutoConfirmConfig, BrainConfig, ReplyConfig, RobotConfig
from xy_gate.daemon import Daemon


def make_daemon(tmp_path, mode="dry-run", quiet=None, rules=None, delivery=None,
                confirm_enabled=True):
    cfg = RobotConfig(
        mode=mode,
        db_path=str(tmp_path / "e2e.sqlite3"),
        brain=BrainConfig(dsh_bin="dsh", per_chat_cooldown_sec=0),
        reply=ReplyConfig(quiet_start=quiet[0] if quiet else None,
                          quiet_end=quiet[1] if quiet else None,
                          keyword_rules=rules or []),
        auto_confirm=AutoConfirmConfig(enabled=confirm_enabled, max_amount_cny=100),
        delivery_items=delivery or [],
    )
    acct = Account(name="t", cookies="unb=2200000001; _m_h5_tk=abc_123")
    return Daemon(cfg, acct)


def chat_event(text="请问怎么发货呀", sender="2880000002", item="760000000001", msg_id="m1"):
    return {"kind": "chat", "chat_id": sender, "sender_id": sender, "sender_name": "买家",
            "text": text, "images": [], "item_id": item, "order_id": "",
            "msg_time": "2026-09-18 10:00:00", "msg_id": msg_id, "is_buyer": True}


@pytest.mark.asyncio
async def test_keyword_quick_reply_dryrun(tmp_path):
    d = make_daemon(tmp_path, rules=[{"match": ["怎么发货"], "reply": "拍下秒发~"}])
    await d.handle_chat(chat_event())
    msgs = d.store.history("2880000002")
    assert msgs[-1]["direction"] == "out" and msgs[-1]["text"] == "拍下秒发~"
    # dry-run：只入库模拟，不真发（ws 未连接也不会报错）
    evs = d.store.recent_events()
    assert any(e["kind"] == "reply_simulated" for e in evs)


@pytest.mark.asyncio
async def test_brain_job_dispatch(tmp_path, monkeypatch):
    d = make_daemon(tmp_path)
    calls = []

    async def fake_run(job_text, timeout=None):
        calls.append(job_text)
        return BrainResult(True, 0, "意图: 询价\n动作: 已回复\n备注: 无", "", 0.1)

    monkeypatch.setattr(d.brain, "run_job", fake_run)
    await d.handle_chat(chat_event(text="这个还能刀吗", msg_id="m2"))
    assert len(calls) == 1
    assert "xianyu-auto-reply" in calls[0] or "闲鱼消息处理" in calls[0]
    evs = d.store.recent_events()
    assert any(e["kind"] == "brain:on-message.md" and e["brain_status"] == "ok" for e in evs)


@pytest.mark.asyncio
async def test_order_paid_flow_dryrun(tmp_path, monkeypatch):
    from xy_gate.config import DeliveryItem
    d = make_daemon(tmp_path, delivery=[DeliveryItem(item_id="760000000001",
                                                     content="卡密：ABCD-1234")])

    async def fake_title_amount(order_id):
        return "Switch OLED 日版", 1499.0   # 超过 max_amount 100 → 应被金额闸门拦下

    monkeypatch.setattr(d, "_order_title_amount", fake_title_amount)
    monkeypatch.setattr(d, "_run_brain_job",
                        lambda *a, **k: _noop())

    ev = {"kind": "order_paid", "chat_id": "2880000002", "sender_id": "2880000002",
          "sender_name": "买家", "text": "[买家已付款]", "images": [],
          "item_id": "760000000001", "order_id": "230000000003",
          "msg_time": "2026-09-18 10:05:00", "msg_id": "m3", "is_buyer": True}
    await d.handle_order_paid(ev)

    # 1) 自动发货内容已发（模拟）
    msgs = d.store.history("2880000002")
    assert any("卡密：ABCD-1234" in m["text"] for m in msgs)
    # 2) 确认被金额闸门拦截：decision=block
    confirms = d.store.confirm_history()
    assert len(confirms) == 1 and confirms[0]["decision"] == "block"
    assert "超过上限" in confirms[0]["result"]

    # 3) 金额降到上限内 → dryrun 模拟确认
    async def ok_amount(order_id):
        return "Switch OLED 日版", 88.0
    monkeypatch.setattr(d, "_order_title_amount", ok_amount)
    res = await d.confirm_transaction("230000000004", item_id="760000000001",
                                      buyer_id="2880000002", amount=88.0)
    assert res["decision"] == "dryrun" and res["success"] is True


@pytest.mark.asyncio
async def test_quiet_hours_block(tmp_path):
    d = make_daemon(tmp_path, quiet=("00:00", "23:59"),
                    rules=[{"match": ["在吗"], "reply": "在的"}])
    await d.handle_chat(chat_event(text="在吗", msg_id="m4"))
    evs = d.store.recent_events()
    assert any(e["kind"] == "chat_skipped" and "静默" in e["summary"] for e in evs)
    msgs = d.store.history("2880000002")
    assert msgs and msgs[-1]["direction"] == "in"   # 只有来信，没有任何回复


async def _noop():
    pass
