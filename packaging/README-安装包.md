# 桌面版（Electron，推荐分发形态）

```powershell
cd web && npm install && npm run build && cd ..   # 前置：前端构建
powershell -ExecutionPolicy Bypass -File packaging\build-electron.ps1
```

产出 `dist-electron\xy-robot-setup-0.1.0.exe`（约 130MB，NSIS 安装器，可选安装目录、
桌面快捷方式「闲鱼卖家机器人」）。开发调试：`cd electron && npm start`。

## 设计要点

- **无需单独 Node**：dsh 用 Electron 自带的 Node 运行（`ELECTRON_RUN_AS_NODE=1`
  + `XY_DSH_NODE/XY_DSH_SCRIPT` 注入，gate 的 brain 自动识别）；
- **可写区隔离**：安装目录可能只读（Program Files），首启自动把小体量应用树
  （gate/config/prompts/sop/.dsh/web）同步到 `%APPDATA%\XianyuRobot\app\`，
  **用户配置永不被升级覆盖**；大件 dsh node_modules（~300MB）留在 resources 只读引用；
- **后端托管**：窗口加载 `http://127.0.0.1:8790`（gate 托管的 React 后台），
  gate 由主进程拉起（隐藏控制台、退出自动 `taskkill /T` 收尾、单实例锁）；
- 首启自动建 Python venv（uv 优先，回退 python -m venv），启动页实时滚动日志；
- 坑（已修）：robocopy 成功返回码是 1，execSync 需吞掉 <8 的码；exe 名用 ASCII
  （build.productName），中文放 shortcutName，避免 NSIS CJK 产物名乱码。

## 使用者三步

1. 安装 → 桌面「闲鱼卖家机器人」→ 首启等 venv 创建（几分钟，仅一次）；
2. 设置 → dsh API Key（写入 `%APPDATA%\XianyuRobot\app\data\dsh-home`，与系统 dsh 隔离）；
3. 设置 → 浏览器扫码登录闲鱼（自动抓 Cookie 热切换）。

前置：Python 3.10+（或 uv）。浏览器（扫码抓取用）需 Edge/Chrome 其一。

### 可选：酒店代订「赫兹商旅 App」协议价源（第四源）

不配置不影响其余功能（酒店报价还有携程/同程/官网三源）。要用协议价真源（价格
常显著低于 OTA）需要：

1. 安装 [MuMu 模拟器 12](https://mumu.163.com/)（默认实例即可，ADB 端口 16384）；
2. 模拟器里安装「赫兹商旅」App 并**人工登录一次**（企业 SSO 自己登，机器人
   不碰密码；登录态保留在模拟器里）；
3. 查价时保持 MuMu 开着（最小化可以，全程无需人工操作，一次约 1.5~2 分钟）。

装好后到 **设置 → 赫兹商旅 App（协议价真源）→「检测 App 源状态」** 验证；
命令行用 `xy-gate hotel-app-state`。自动化走 MuMu 官方外部渲染接口（MAA 同款
零注入方案），对模拟器里的账号无侵入。

---

# 文件夹形态安装包（备选，无 Electron）

一个文件夹 = 完整机器人：**自带 Node + dsh（与你系统里的 dsh 完全隔离）** + 协议层
+ 管理后台。拷贝给任何人，填两个凭据即可用。

## 构建（开发者，在本仓库执行）

```powershell
cd web && npm install && npm run build && cd ..   # 先构建前端
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

产出 `dist\xy-robot-app\`（实际磁盘占用约 400MB；Git Bash 的 du 会把硬链接
重复计数显示十几 G，以资源管理器为准）。`dist\.cache\` 缓存 Node 压缩包，重复构建免下载。

## 包结构

```
xy-robot-app\
├─ runtime\            自带 Node 22 + @deepseek-ai/dsh（0.1.5-rc.x）
│   └─ node_modules\.bin\dsh.cmd
├─ data\dsh-home\      隔离的 DSH_HOME（profiles/credentials 全在包内，
│                      不碰系统 ~/.dsh）
├─ gate\               xy-gate 协议层（Python）
├─ web\dist\           管理后台（由 gate 直接托管）
├─ config\             robot.yaml / accounts.yaml（git 不入库）
├─ 启动机器人.bat       双击启动（首次自动建 Python 虚拟环境）并打开控制台
└─ 停止机器人.bat
```

## 使用者安装（3 步）

前置：Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)（或系统 python 自带 venv）。

1. 双击 **启动机器人.bat**（首次自动建 venv）→ 浏览器打开 `http://127.0.0.1:8790`
2. **设置 → dsh 大脑 · API Key**：粘贴 DeepSeek API Key（写入包内隔离 home）
3. **设置 → 闲鱼账号 Cookie**：从浏览器登录 goofish.com 复制整段 Cookie（须含
   `unb`、`_m_h5_tk`）

默认 `dry-run`（一切写操作只记台账），观察后台「消息事件」几天后，在设置页切
`live` + 打开自动确认/改价。

## 与系统 dsh 的隔离

- 内置 dsh 版本独立（可能比系统的新），二进制在 `runtime\node_modules\.bin\`；
- 启动器设 `DSH_HOME=包内\data\dsh-home`，brain 子进程继承该环境变量；
- API Key 写 `data\dsh-home\.credentials.yaml`（扁平 `DEEPSEEK_API_KEY: sk-…`），
  与 `~/.dsh/.credentials.yaml` 互不影响；
- 系统里没装 dsh 也能用这个包。

## 常见问题

- **首次对话 dsh 报错**：第一次 headless 会话会自动初始化 profile，再发一次即可；
- **改价/下架"无权限"**：闲鱼账号需开通鱼小铺（见 docs/full-autonomy-roadmap.md）；
- **连不上 8790**：跑一次 `停止机器人.bat` 再启动（端口残留）。
