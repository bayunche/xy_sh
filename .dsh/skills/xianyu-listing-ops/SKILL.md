---
name: xianyu-listing-ops
description: 闲鱼商品运营：查在售、改价、下架、能力探测。议价成交要改价、审计要调价、人工要求盘点商品时使用。改价/下架需账号开通鱼小铺且 live 模式。
whenToUse: 议价达成需要改价；每日审计建议调价；人工问"我有哪些在售/帮我下架/这个能不能改到X元"。
---

# 闲鱼商品运营

## 先搞清楚账号能力（改价类操作的前置）

```bash
uv run --project gate xy-gate capability
```

返回各接口族 `{ok, needs_fish_shop?, detail}`。`seller_items`/改价/下架显示
`needs_fish_shop: true` 时，说明账号**未开通鱼小铺**——改价类操作全部不可用，
只能口头成交转人工，如实告知，不要反复尝试。

## 常用命令

```bash
# 在售商品（个人版，任何账号可用）
uv run --project gate xy-gate items
uv run --project gate xy-gate items --seller      # 卖家平台视图（需鱼小铺）

# 议价底价（watchlist.floor_price 优先，否则 挂价×floor_ratio）
uv run --project gate xy-gate floor <item_id>

# 改价（四道闸门：总开关/底价/降幅/频次；需鱼小铺 + live）
uv run --project gate xy-gate reprice <item_id> <new_price> --why "议价成交"

# 批量下架（需鱼小铺 + live）
uv run --project gate xy-gate offline <item_id> [<item_id2> ...]
```

## 议价成交流（结合 xianyu-auto-reply 技能）

买家还价 ≥ 底价（`floor` 命令查）且你决定接受时：

1. `xy-gate reprice <item_id> <成交价>`；
2. 返回 `decision=allow/dryrun` 且 success → 回复买家"改好价了，直接拍"；
3. 返回 blocked（底价/降幅/频次）→ 按 reply-policy 还价或回绝，**不要绕**；
4. 返回"需开通鱼小铺" → 回复买家"这个价可以，你拍下我给你改"，
   并在摘要标注 `需人工：拍后改价`（人工在 App 里操作）。

## 红线

- reprice 只允许**降价方向**且不破底价——闸门强制，被拒如实报告；
- 同一商品 24h 内多次改价会被频次闸门拦截（防抖）；
- 下架是重操作：只有人工明确要求或审计 SOP 指明时才执行；
- 商品资料（成色/配件）以 `xy-gate items` 返回为准，禁止编造。
