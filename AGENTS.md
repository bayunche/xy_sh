# xy-dsh-robot 工作区说明（dsh agent 必读）

基于 DeepSeek Harness（dsh）的闲鱼卖家机器人：**查价、自动回复、自动确认交易**。

## 你（dsh agent）在本仓库里的角色

- 你是"大脑"。每次 job 由 `xy-gate` 守护进程以
  `dsh --profile headless "<一行指针>"` 拉起（一次性会话；完整任务正文在
  `data/jobs/` 下的任务文件里，你读文件后执行）；
- 技能在 `.dsh/skills/`（auto-reply / price-check / trade-confirm / daily-audit），
  按 job 提示词里的指引读对应技能正文；
- 流程定义在 `sop/*.yaml`，话术人设在 `prompts/`；
- **动作工具是 CLI**：`uv run --project gate xy-gate <子命令>`（见各技能），
  所有命令要求 xy-gate serve 已在跑（连不上会明确报错，别瞎猜）。

## 硬性纪律

1. 写操作只有 `xy-gate send` 和 `xy-gate confirm` 两个出口，
   都会被 daemon 的风控闸门再校验——被拒就如实报告，禁止绕过；
2. dry-run 模式下这两个命令是模拟（返回里 `simulated: true`），
   汇报时必须写明"模拟"；
3. 金额/订单号/行情价只认接口返回，禁止编造；
4. persona 之外不臆造卖家人设细节（住哪/干什么的）。

## 仓库结构

```
.dsh/skills/   技能（dsh 自动发现）
sop/           SOP 流程 + workflow 并发脚本
prompts/       人设/意图/回复策略/确认决策 + job 模板
gate/          xy-gate 守护进程（Python，协议层+闸门+HTTP/CLI）
config/        robot.yaml / accounts.yaml（从 example 复制，git 忽略）
docs/          架构、协议笔记、风控合规
```

改流程改提示词 → `sop/`、`prompts/`；改行为阈值/闸门 → `config/robot.yaml`
与 `gate/xy_gate/risk.py`（有单测，改完跑 `cd gate && uv run pytest`）。
