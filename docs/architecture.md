# xy-dsh-robot 架构设计

> 基于 DeepSeek Harness（dsh）的闲鱼卖家机器人：查价、自动回复、自动确认交易。

## 总览

```
┌───────────────────────────── 闲鱼服务器 ─────────────────────────────┐
│  wss://wss-goofish.dingtalk.com   私信长连接（收发消息）               │
│  h5api.m.goofish.com              mtop 网关（查价/订单/确认发货）      │
└──────────────┬──────────────────────────────────┬───────────────────┘
               │ WebSocket + HTTPS（Cookie 登录态） │
┌──────────────▼──────────────────────────────────▼───────────────────┐
│  xy-gate 守护进程（Python asyncio，gate/）                            │
│  ├─ wsclient    /reg 注册、15s 心跳、断线重连、收 syncPushPackage     │
│  ├─ mtop       统一签名调用 + _m_h5_tk 令牌自动刷新重试               │
│  ├─ inbound    MessagePack 解包 → 结构化事件（聊天/订单卡片/系统）    │
│  ├─ risk       风控闸门（金额上限/黑白名单/冷却/干跑/回复限速）        │
│  ├─ store      sqlite（聊天史/消息去重/确认台账/事件日志）             │
│  ├─ daemon     事件 → SOP 路由（关键词快答 或 转大脑）                 │
│  ├─ brain      dsh 桥（渲染 job 提示词 → 子进程跑 dsh headless）      │
│  └─ server/cli 本地 HTTP :8790 + xy-gate CLI（大脑的动作工具）         │
└──────────────┬───────────────────────────────────────────────────────┘
               │ 子进程：dsh --profile headless（argv 只传一行任务文件指针，
               │        完整 job 正文在工作区 data/jobs/*.md，跑完即删）
┌──────────────▼───────────────────────────────────────────────────────┐
│  dsh 大脑（DeepSeek Harness；技能自动发现于 .dsh/skills/）            │
│  ├─ .dsh/skills/  xianyu-auto-reply / price-check / trade-confirm    │
│  │                / daily-audit（何时用、怎么做、禁区）                │
│  ├─ sop/          SOP 流程定义（YAML 状态机）+ workflow JS 并发脚本    │
│  └─ prompts/      人设 / 意图分类 / 回复策略 / 确认决策 / job 模板     │
└──────────────────────────────────────────────────────────────────────┘
```

## 为什么这样分层

- **协议进程与大脑分离**：闲鱼 WS 长连接、心跳、Cookie 续期、滑块风控这些"体力活"是
  长驻有状态的，放在独立守护进程里最稳；dsh 会话是短生命周期（每事件一个 job），
  两者用"事件 → job → CLI 动作"解耦，dsh 升级（当前 rc 版常有破坏性变更）不影响协议层。
- **风控闸门在 gate 而不在提示词**：自动确认交易涉及资金流，必须由确定性代码把关
  （金额上限、商品白名单、冷却、干跑开关），提示词只是"建议层"，CLI 的 confirm
  子命令会**再次**过闸，大脑无法绕过。
- **SOP 是文件不是代码**：sop/*.yaml 是人和 agent 共读的流程单一事实源；
  daemon 只做"事件 → SOP 名"的路由，流程内容改动不用改代码。

## 事件 → SOP 路由（daemon 内置）

| 事件 | SOP | 快速路径（不惊动大模型） |
|---|---|---|
| 买家发来文本/图片 | `sop/sop-inbound-message.yaml` | 关键词规则表命中 → 直接回复；静默时段 → 只记录 |
| 订单卡片「已付款」 | `sop/sop-order-lifecycle.yaml` | 发货内容表命中 → 发内容；风控闸门放行 → consign.dummy 确认发货 |
| 每日定时 | `sop/sop-price-audit.yaml` | 无（整单交给大脑跑 workflow 并发查价） |
| 其他系统卡片 | 仅记录 | — |

## 自动回复三级降速

1. **关键词快答**（0 成本，秒回）：robot.yaml `keyword_rules` 命中即回。
2. **dsh 技能回复**（一次 headless job）：未命中 → 渲染 `prompts/jobs/on-message.md`
   模板 → agent 读技能与 SOP → 调 CLI 查上下文/查价 → 发出回复。
3. **转人工**：黑名单买家、超限价砍价、售后纠纷 → 不自动回复，记录并（可选）告知买家
   "稍后人工回复"。

## 自动确认交易（卖家侧）

闲鱼卖家侧"确认交易" = 虚拟/无需邮寄商品买家付款后，调
`mtop.taobao.idle.logistic.consign.dummy` 确认发货（`newUnconsign:true`）；
小刀（砍价成交）订单需先调 `mtop.idle.groupon.activity.seller.freeshipping` 免拼。
闸门顺序：**干跑开关 → 商品白/黑名单 → 金额上限 → 买家黑名单 → 冷却 → 订单未重复**，
全过才真调 API；dry-run 模式下只记台账不真调。

## 已知边界（v1 不做）

- 多账号并行（配置结构已预留 `accounts[]`，daemon 一次跑一个）；
- 滑块验证码自动求解（触发 `FAIL_SYS_USER_VALIDATE`/punish 时告警转人工，人工刷新 Cookie）;
- 图片消息发送、语音、自动上下架改价（协议已摸清，留扩展位）。
