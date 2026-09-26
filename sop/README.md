# SOP（标准作业流程）

本目录是**流程单一事实源**：人和 dsh agent 共读。daemon 只做「事件 → SOP 名」
的硬路由，流程步骤本身全部在这里维护，改流程不改代码。

## 文件

| 文件 | 触发 | 一句话 |
|---|---|---|
| `sop-inbound-message.yaml` | 买家发来消息 | 关键词快答 → 大脑回复 → 转人工，三级降速 |
| `sop-order-lifecycle.yaml` | 订单卡片「买家已付款」 | 发货内容 → 风控闸门 → 确认交易 → 通知买家 |
| `sop-price-audit.yaml` | 每日定时（robot.yaml `audit.daily_at`） | 台账汇总 + 并发查价 + 经营简报 |
| `workflows/*.js` | 大脑在会话内通过 workflow 工具执行 | 多商品并发查价的编排脚本 |

## 通用约定（三个 SOP 共同遵守）

1. **写操作只有两个出口**：发消息 `xy-gate send`、确认交易 `xy-gate confirm`，
   都在 daemon 内强制过风控闸门；
2. **dry-run 全局生效**：`robot.yaml mode: dry-run` 时一切写操作只记台账；
3. **转人工是合法终态**，不是失败：摘要里写清原因即可；
4. **每步都留痕**：daemon 自动把事件与大脑输出写 sqlite（`xy-gate` events 可查）。

## 与 dsh 的关系

- 大脑 job 由 daemon 渲染 `prompts/jobs/*.md` 模板后以
  `dsh --profile headless "<job>"` 启动；
- job 提示词只含「事件 + 指针」，agent 在会话内自行读本目录 SOP 与
  `.dsh/skills/` 技能正文（详见 `docs/architecture.md`）。
