---
name: xianyu-price-check
description: 闲鱼查价：搜同款在售商品并输出价格统计（中位价/四分位/想要人数），支撑回复询价、砍价决策与每日审计。当需要"这个东西现在市场什么价"的数据时使用。
whenToUse: 买家询价/砍价需要行情依据；每日审计盘点自有商品竞争力；人工问"XX 现在值多少钱"。
---

# 闲鱼查价

## 命令

```bash
# 基础查价（30 条同款，个人闲置过滤）
uv run --project gate xy-gate search "iPhone 13 128G 国行" --rows 30

# 价格区间过滤（排除干扰品）
uv run --project gate xy-gate search "Kindle paperwhite 5" --min 300 --max 900
```

返回 JSON：`items[]`（item_id/title/price/want_count/area/url）与 `stats`
（count/min/p25/median/p75/max/mean）。

## 判读规则

1. **样本量**：`stats.count < 5` 时结论必须标注"样本不足，仅供参考"。
2. **剔除异常**：肉眼扫一遍 items，标题明显不同型号/配件单卖/空箱的，
   心算剔除后以 median 为锚点，偏差不要自己重算分位数，报告原始值+剔除说明。
3. **想要人数**：want_count 高（如 >20）说明需求旺，砍价空间小；
   挂了很久没人要的（低 want）可适当让利。
4. **报价口径**：对买家报 `median` 到 `p25` 之间；低于 p25 的砍价要求按
   reply-policy 的底价规则处理。

## 在每日审计中的用法

配合 `sop/sop-price-audit.yaml` 与 `sop/workflows/fanout-price-audit.js`：
每个在售商品独立查价，输出「我的挂价 vs 市场中位价」对比表，
偏差 >15% 的商品给出调价建议（只建议，不自动改价——v1 无改价接口）。

## 注意

- 搜索接口偶发风控（返回 error 含 punish_url）：不要重试，报告"查价被风控，需人工"；
- 关键词越具体越准：带容量/版本/成色关键词；
- 该接口为只读，无账号风险。
