---
name: xianyu-daily-audit
description: 闲鱼每日经营审计：在售商品逐一查价对比挂价、汇总消息/订单/确认台账，输出经营简报与调价建议。当收到 daily-audit job（定时或手动 xy-gate audit）时使用。
whenToUse: job 模板为 daily-audit.md；或人工要求"跑一次审计/盘点"。
---

# 闲鱼每日审计

## 输入

- `config/robot.yaml` 的 `delivery.items` 与卖家的在售商品清单（人工维护在
  `config/watchlist.yaml`，格式见文件内注释；没有就只做台账汇总）。

## 流程

1. **台账汇总**（只读）：
   ```bash
   uv run --project gate xy-gate status          # 连接/模式状态
   uv run --project gate xy-gate confirms        # 昨日确认交易台账
   ```
   并从 daemon 事件接口拉最近事件（见 sop/sop-price-audit.yaml 的 curl 示例）。
2. **批量查价**：对 watchlist 每个商品执行查价技能命令。商品多（>3）时，
   用 workflow 工具并发跑 `sop/workflows/fanout-price-audit.js` 的脚本
   （脚本内 agent() 会逐品查价并回传结构化结果）。
3. **对比与建议**：每个商品输出
   `挂价 / 市场中位价 / 偏差% / 建议（保持|降价至X|涨价至X|下架）`。
   偏差阈值 ±15%；建议只写报告，**不自动改价**。
4. **汇总简报**（最终输出，给卖家本人看）：
   - 昨日消息量 / 自动回复量 / 转人工量
   - 确认交易笔数与金额（标注 dry-run 模拟）
   - 价格偏离商品清单
   - 风险提示（风控、Cookie、连接异常）

## 红线

- 审计全程只读 + 建议，不执行任何写操作（send/confirm 都不许碰）；
- 报价结论必须带样本量；样本 <5 的注明不可比。
