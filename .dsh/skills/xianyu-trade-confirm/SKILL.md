---
name: xianyu-trade-confirm
description: 闲鱼自动确认交易（卖家侧确认发货/无需邮寄），含风控预检与执行。当买家已付款、需要完成虚拟商品发货确认或核对订单状态时使用。
whenToUse: 事件 EVENT.kind == "order_paid"；或人工要求核对/执行某个订单的确认。
---

# 闲鱼自动确认交易

⚠️ 这是资金相关技能。你没有任何绕过闸门的权限——`confirm` 命令在
daemon 内会**再次**执行风控校验，闸门拒绝时命令直接失败，不要想法子绕。

## 背景

闲鱼卖家侧"确认交易" = 买家付款后调用
`mtop.taobao.idle.logistic.consign.dummy` 完成无需邮寄确认（虚拟商品即时到账）。
daemon 已经在收到「买家已付款」卡片时按 `sop/sop-order-lifecycle.yaml`
自动走完：查订单 → 自动发货内容表 → 风控预检 → 确认。
本技能主要用于**核对与兜底**（自动流失败、人工指定订单）。

## 流程

1. 拿订单信息：
   ```bash
   uv run --project gate xy-gate orders --query NOT_SHIP   # 待发货订单
   uv run --project gate xy-gate order <order_id>          # 订单详情原始 JSON
   ```
2. **预检**（只读，不改任何状态）：
   ```bash
   uv run --project gate xy-gate confirm-check <order_id> --item <item_id> \
          --buyer <buyer_id> --amount <实付金额>
   ```
   返回 `{action: allow|dryrun|block, reasons[]}`。`block` 时把 reasons 原样转述，
   停止，写"需人工确认"。
3. `allow/dryrun` 才执行：
   ```bash
   uv run --project gate xy-gate confirm <order_id> --item <item_id> \
          --buyer <buyer_id> --amount <实付金额> [--trade-text "无需邮寄凭证说明"]
   ```
4. 查台账确认结果：
   ```bash
   uv run --project gate xy-gate confirms
   ```

## 决策规则

先读 `prompts/confirm-decision.md`。核心：

- 金额必须从订单详情读到，禁止用买家口头说的金额；
- `action=dryrun`（机器人处于 dry-run 模式）是**模拟成功**，如实记录"模拟确认"；
- 同一订单重复确认会被冷却闸门拦下，属正常防重，不要试图绕过；
- 小刀（砍价成交）订单确认报错时，检查是否需要先免拼（错误信息会提示），
  免拼由 daemon 自动处理，手动场景直接报告人工。

## 输出

摘要包含：订单号 / 金额 / 预检 action 与理由 / 执行结果 / 是否模拟。
