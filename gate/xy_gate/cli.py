"""xy-gate CLI：dsh 大脑与运维的动作工具（走本地 HTTP API）。

子命令：
  serve                        启动守护进程（唯一常驻进程）
  status                       运行状态
  history <chat_id> [--limit]  会话聊天史
  search <关键词> [--min --max --rows]   查价（搜索+价格统计）
  orders [--query NOT_SHIP|ALL]          已卖出订单
  order <order_id>                        订单详情（原始 JSON）
  send <chat_id> <to_user_id> <文本>      发消息（过闸门，dry-run 会模拟）
  confirm-check <order_id> [--item --buyer --amount]   确认交易预检（不改任何状态）
  confirm <order_id> [--item --buyer --amount --trade-text]  确认交易（再次过闸门）
  confirms                    确认交易台账
  items [--seller]            我的在售商品（--seller 需鱼小铺）
  capability                  接口能力探测（哪些操作当前账号可用）
  floor <item_id>             查议价底价
  reprice <item_id> <价格>    改价（过闸门；需鱼小铺 + live）
  offline <item_id> ...       批量下架（需鱼小铺 + live）
  rate <trade_id> [--text]    订单评价（live）
  snipe                       手动盯货扫描
  audit                       手动触发每日查价审计 job

全部输出 JSON（UTF-8）。除 status 外均要求 daemon 已在跑。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8790"


def _request(method: str, path: str, body: dict | None = None, base: str = DEFAULT_BASE) -> dict:
    url = base.rstrip("/") + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": f"连不上 xy-gate（{base}）：{e.reason}。请先运行 xy-gate serve。"}


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台 UTF-8 输出
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    p = argparse.ArgumentParser(prog="xy-gate", description="闲鱼卖家机器人协议层 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="启动守护进程")

    sub.add_parser("status", help="运行状态")

    sp = sub.add_parser("history", help="会话聊天史")
    sp.add_argument("chat_id")
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("search", help="查价：搜索同款并给出价格统计")
    sp.add_argument("keyword")
    sp.add_argument("--min", type=float, dest="price_min")
    sp.add_argument("--max", type=float, dest="price_max")
    sp.add_argument("--rows", type=int, default=30)

    sp = sub.add_parser("orders", help="已卖出订单")
    sp.add_argument("--query", choices=["NOT_SHIP", "ALL"], default="NOT_SHIP")

    sp = sub.add_parser("order", help="订单详情")
    sp.add_argument("order_id")

    sp = sub.add_parser("send", help="发消息（过闸门；dry-run 模式模拟）")
    sp.add_argument("chat_id")
    sp.add_argument("to_user_id")
    sp.add_argument("text")

    sp = sub.add_parser("confirm-check", help="确认交易预检（只读）")
    sp.add_argument("order_id")
    sp.add_argument("--item", default="")
    sp.add_argument("--buyer", default="")
    sp.add_argument("--amount", type=float)

    sp = sub.add_parser("confirm", help="确认交易（写操作，再次过闸门）")
    sp.add_argument("order_id")
    sp.add_argument("--item", default="")
    sp.add_argument("--buyer", default="")
    sp.add_argument("--amount", type=float)
    sp.add_argument("--trade-text", default="")

    sub.add_parser("confirms", help="确认交易台账")

    sp = sub.add_parser("items", help="我的在售商品（个人版列表，无需鱼小铺）")
    sp.add_argument("--seller", action="store_true", help="卖家平台列表（需鱼小铺）")

    sub.add_parser("capability", help="接口能力探测（哪些操作当前账号可用）")

    sp = sub.add_parser("floor", help="查议价底价（watchlist 或挂价×floor_ratio）")
    sp.add_argument("item_id")

    sp = sub.add_parser("reprice", help="改价（写操作，过闸门；需鱼小铺+live）")
    sp.add_argument("item_id")
    sp.add_argument("new_price", type=float)
    sp.add_argument("--why", default="cli", help="改价原因（记账用）")

    sp = sub.add_parser("offline", help="批量下架（写操作；需鱼小铺+live）")
    sp.add_argument("item_ids", nargs="+")

    sp = sub.add_parser("rate", help="给已完成订单评价（写操作；live）")
    sp.add_argument("trade_id")
    sp.add_argument("--text", default="", help="评语，默认好评文案")

    sub.add_parser("snipe", help="手动跑一轮盯货扫描（读订阅关键词）")

    sp = sub.add_parser("hotel-cost", help="代订酒店人工补价（会员/协议价成本）；缺 price=只查询")
    sp.add_argument("hotel")
    sp.add_argument("price", type=float, nargs="?")
    sub.add_parser("hotel-cache-show", help="代订酒店报价配置与缓存状态")

    sub.add_parser("audit", help="手动触发每日查价审计")

    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .config import load_account, load_robot_config
        from .server import serve
        import asyncio
        cfg = load_robot_config()
        account = load_account(cfg.account)
        asyncio.run(serve(_daemon(cfg, account)))
        return 0

    base = DEFAULT_BASE
    if args.cmd == "status":
        out = _request("GET", "/status", base=base)
    elif args.cmd == "history":
        q = urllib.parse.urlencode({"chat_id": args.chat_id, "limit": args.limit})
        out = _request("GET", f"/history?{q}", base=base)
    elif args.cmd == "search":
        out = _request("POST", "/search", {"keyword": args.keyword, "price_min": args.price_min,
                                           "price_max": args.price_max, "rows": args.rows}, base)
    elif args.cmd == "orders":
        out = _request("GET", f"/orders?query={args.query}", base=base)
    elif args.cmd == "order":
        out = _request("GET", f"/order/{args.order_id}", base=base)
    elif args.cmd == "send":
        out = _request("POST", "/send", {"chat_id": args.chat_id, "to_user_id": args.to_user_id,
                                         "text": args.text, "why": "CLI"}, base)
    elif args.cmd == "confirm-check":
        out = _request("POST", "/confirm/check", {"order_id": args.order_id, "item_id": args.item,
                                                  "buyer_id": args.buyer, "amount": args.amount}, base)
    elif args.cmd == "confirm":
        out = _request("POST", "/confirm", {"order_id": args.order_id, "item_id": args.item,
                                            "buyer_id": args.buyer, "amount": args.amount,
                                            "trade_text": args.trade_text, "source": "cli"}, base)
    elif args.cmd == "confirms":
        out = _request("GET", "/confirm/history", base=base)
    elif args.cmd == "items":
        q = "?source=seller" if args.seller else ""
        out = _request("GET", f"/items{q}", base=base)
    elif args.cmd == "capability":
        out = _request("GET", "/capability", base=base)
    elif args.cmd == "floor":
        out = _request("GET", f"/floor/{args.item_id}", base=base)
    elif args.cmd == "reprice":
        out = _request("POST", "/reprice", {"item_id": args.item_id,
                                            "new_price": args.new_price,
                                            "source": f"cli:{args.why}"}, base)
    elif args.cmd == "offline":
        out = _request("POST", "/offline", {"item_ids": args.item_ids}, base)
    elif args.cmd == "rate":
        out = _request("POST", "/rate", {"trade_id": args.trade_id,
                                         "feedback": args.text}, base)
    elif args.cmd == "snipe":
        out = _request("POST", "/snipe/run", {}, base)
    elif args.cmd == "hotel-cost":
        out = _request("POST", "/api/hotel/cost",
                       {"hotel": args.hotel, "price": args.price}, base)
    elif args.cmd == "hotel-cache-show":
        out = _request("GET", "/api/hotel/quote-status", base=base)
    elif args.cmd == "audit":
        out = _request("POST", "/audit/run", {}, base)
    else:  # pragma: no cover
        p.error(f"未知子命令: {args.cmd}")

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if not (isinstance(out, dict) and out.get("error")) else 1


def _daemon(cfg, account):
    from .daemon import Daemon
    return Daemon(cfg, account)


if __name__ == "__main__":
    raise SystemExit(main())
