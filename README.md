# xy-dsh-robot

基于 **DeepSeek Harness（dsh）** 的闲鱼卖家机器人：**查价 · 自动回复 · 自动确认交易**，
技能（skills）+ SOP 编排 + 提示词三层分离，自带 **React 管理后台** 与 **单体安装包**。

```
闲鱼服务器 ⇄ [xy-gate 守护进程: WS长连接/mtop/风控闸门/台账]
                    ⇩ 事件渲染 job（prompts/jobs/*.md）
              [dsh 大脑: .dsh/skills/ + sop/ + prompts/]
                    ⇩ 动作走 CLI（uv run --project gate xy-gate send|confirm|search…）
              [管理后台 web/: React+Tailwind，gate 直接托管 :8790]
```

- 架构设计：[docs/architecture.md](docs/architecture.md)
- 协议笔记：[docs/protocol-notes.md](docs/protocol-notes.md)
- **风控合规（先读）**：[docs/risk-and-compliance.md](docs/risk-and-compliance.md)
- **单体安装包**：[packaging/README-安装包.md](packaging/README-安装包.md)（自带隔离 dsh，双击即用）

## 功能一览

| 功能 | 说明 | SOP |
|---|---|---|
| 查价 | PC 搜索同款 + 中位价/四分位统计，支撑报价与每日审计 | sop-price-audit |
| 酒店代订报价 | 四源查价：携程(trip 镜像)/同程/官网(集团+协议码)/**赫兹商旅 App(协议价真源)** + 阶梯加价闸门 | 技能 xianyu-hotel-quote |
| 自动回复 | 关键词秒答 → dsh 大脑拟人回复（询价/砍价/催发货/咨询）→ 风险话题转人工 | sop-inbound-message |
| 议价自动成交 | 买家出价 ≥ 底价 → 自动改价 + 引导拍下（改价需鱼小铺，未开通则口头成交转人工） | sop-bargain-close |
| 自动确认交易 | 买家付款 → 自动发货内容表 → 七道风控闸门 → consign.dummy 确认 → 通知买家 | sop-order-lifecycle |
| 每日审计 | 定时并发查价，输出「挂价 vs 市场中位价」经营简报 | sop-price-audit |
| 盯货捡漏 | 订阅关键词定时扫，低于阈值即事件提醒（付款永远人工） | sop-snipe-watch |
| 商品运营 | 在售列表/改价/下架/能力探测（改价下架需鱼小铺） | 技能 xianyu-listing-ops |

**完全托管/转卖模式**：看 [docs/full-autonomy-roadmap.md](docs/full-autonomy-roadmap.md)
（含实测能力矩阵与解锁步骤）。

## 快速开始

前置：Node + 全局 `@deepseek-ai/dsh`（`npm i -g @deepseek-ai/dsh`，
已初始化 `dsh --profile headless` 会跑通即可）、Python ≥3.10 + [uv](https://docs.astral.sh/uv/)。

```bash
git clone <本仓库> && cd xy-dsh-robot

# 1) 配置（Cookie 获取方法见 config/accounts.example.yaml 内注释）
cp config/robot.example.yaml    config/robot.yaml
cp config/accounts.example.yaml config/accounts.yaml   # 填 Cookie
cp config/watchlist.example.yaml config/watchlist.yaml # 可选：每日审计清单

# 2) 协议层单测
cd gate && uv sync && uv run pytest -q && cd ..

# 3) 启动（默认 dry-run：写操作全部模拟，只记台账）
./run/start-gate.sh            # Windows: powershell -File run\start-gate.ps1

# 4) 另开终端验证
uv run --project gate xy-gate status
uv run --project gate xy-gate search "Switch OLED 日版"   # 真查价（只读）
```

观察 dry-run 台账（`data/` sqlite、`xy-gate confirms`/`events`）一周没问题后，
改 `config/robot.yaml`：`mode: live` + `auto_confirm.enabled: true`（并按需调
白名单/金额上限）再重启。**确认交易默认关、金额默认上限 ¥100。**

## 酒店代订：四源查价（含赫兹商旅 App 协议价源）

酒店代订报价用四个价格源交叉取最低成本：**trip**（Trip.com 携程镜像，免登录真价，
基准源）/ **ly**（同程，需登录一次）/ **official**（酒店官网，集团注册表 + 商旅
协议码自动填入）/ **app**（**赫兹商旅 App，南网协议价真源**——协议价常显著低于
OTA 价，命中时作为成本基准）。

```bash
uv run --project gate xy-gate hotel-probe --source app --kw "亚朵" \
    --checkin 2026-10-01 --checkout 2026-10-03
```

**App 源前置条件（一次装好，长期有效）**：

1. 装 [MuMu 模拟器 12](https://mumu.163.com/)（默认实例 0，ADB 端口 16384）；
2. 在模拟器里安装「赫兹商旅」App 并**人工登录一次**（南网 SSO，登录态保留在
   模拟器里，机器人不碰密码）；登录后若停在「因私出行」入口，走因公首页的
   「酒店预订」即可（流程自动处理出差申请弹层）；
3. 查价时**保持 MuMu 开着**（最小化可以，全程无需人工操作）；
4. 检测状态：`uv run --project gate xy-gate hotel-app-state`。

技术形态：纯 ADB + MuMu 官方外部渲染接口（MAA 同款，**零注入**——该 App 有
加固壳，注入式自动化会触发自杀），截图 OCR 走像素层判定（Weex 页面栈在
uiautomator dump 里分不清前后台页）。一次查询约 **1.5 分钟**（含每次 force-stop
冷启动——App 渲染层长时间自动化后会崩灰屏，冷启动是唯一恢复手段），支持热门
城市直点。详见 [docs/protocol-notes.md](docs/protocol-notes.md)。


## dsh 大脑怎么被拉起

xy-gate 收到买家消息/付款卡片后，渲染 `prompts/jobs/*.md` 模板
（事件 JSON + 聊天史 + 模式），子进程执行：

```
dsh --profile headless "<job>"     # cwd=本仓库根
```

agent 在会话内按 AGENTS.md 的纪律：读 `.dsh/skills/` 技能 → 读 `sop/` →
CLI 动作（闸门二次校验）→ 输出摘要（回写 events 台账）。多商品审计用
`sop/workflows/fanout-price-audit.js` 走 dsh workflow 引擎并发。

## 目录

```
.dsh/skills/    4 个技能（dsh 自动发现：name/description/whenToUse + 正文）
sop/            SOP 流程（YAML 状态机）+ workflow 并发脚本
prompts/        persona / intent-classify / reply-policy / confirm-decision + jobs 模板
gate/           xy-gate（aiohttp + websockets，27 个单测）
config/         robot / accounts / watchlist（example + gitignore 真实文件）
run/            启动脚本
```

## 管理后台（web/）

```bash
cd web && npm install && npm run build   # 产物 web/dist，由 gate 托管
cd ../gate && uv run xy-gate serve       # 打开 http://127.0.0.1:8790
```

7 个页面：仪表盘（状态/事件/能力探测）、**对话 dsh**（自然语言管理机器人）、
消息事件（含聊天史回放）、商品·查价（底价/改价/下架/行情）、交易（订单/确认台账/
手动确认/发消息）、盯货捡漏（**去购买=人工付款**）、设置（模式/闸门/订阅/API Key/
Cookie，保存即热重载）。开发者模式：`cd web && npm run dev`（vite proxy 到 8790）。

## 单体安装包

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

产出 `dist\xy-robot-app\`：自带 Node+dsh（DSH_HOME 隔离在包内，与系统 dsh 互不影响）、
应用与启动脚本，双击 `启动机器人.bat` 即用。详见 packaging/README-安装包.md。

## 常见问题

- **CLI 连不上**：`xy-gate serve` 没起（报错会提示）。
- **RiskControlError/punish_url**：触发风控，停服人工过验证换 Cookie，
  见 risk-and-compliance.md。
- **Cookie 失效**：`FAIL_SYS_SESSION_EXPIRED` → 重新从浏览器复制 Cookie。
- **dsh job 慢/超时**：正常（每消息一次 LLM 会话）；调 brain.job_timeout_sec
  与 max_concurrent；或把高频问题加进 keyword_rules 走秒答。
