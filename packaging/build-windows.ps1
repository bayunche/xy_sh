# xy-dsh-robot 单体安装包构建（Windows）
# 产出 dist/xy-robot-app/：自带 Node + dsh（DSH_HOME 隔离在包内）+ 应用 + 启动脚本
# 用法：powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
param(
    [string]$OutDir = "dist\xy-robot-app",
    [string]$NodeVersion = "22.14.0"   # LTS
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..").Path
$Out = Join-Path $Repo $OutDir
$Cache = Join-Path $Repo "dist\.cache"

Write-Host "== 1/5 复制应用树 ==" -ForegroundColor Cyan
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
# robocopy 返回码 >=8 才是错误；XD 用完整路径（避免误伤 web/dist）
$xd = @(".git", ".shots", "dist", "docs", "_dsh_research", "runtime", "data") |
    ForEach-Object { Join-Path $Repo $_ }
robocopy $Repo $Out /E /XD $xd @("$Repo\web
ode_modules") /XF ".credentials.yaml*" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制应用树失败" }
# gate 的虚拟环境与测试缓存不要
if (Test-Path "$Out\gate\.venv") { Remove-Item -Recurse -Force "$Out\gate\.venv" }

Write-Host "== 2/5 下载便携 Node v$NodeVersion ==" -ForegroundColor Cyan
$nodeZip = Join-Path $Cache "node-v$NodeVersion-win-x64.zip"
if (-not (Test-Path $nodeZip)) {
    New-Item -ItemType Directory -Force -Path $Cache | Out-Null
    $url = "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip"
    Write-Host "  下载 $url（约 30MB）…"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $nodeZip
}
Expand-Archive -Path $nodeZip -DestinationPath "$Out\runtime" -Force
$nodeDir = Get-ChildItem "$Out\runtime" -Directory | Where-Object Name -like "node-v*" | Select-Object -First 1
Move-Item $nodeDir.FullName "$Out\runtime\node"

Write-Host "== 3/5 安装 dsh（内置，与系统 dsh 完全隔离）==" -ForegroundColor Cyan
Push-Location "$Out\runtime"
$env:PATH = "$Out\runtime\node;$env:PATH"
New-Item -ItemType File -Path "$Out\runtime\package.json" -Value '{"name":"xy-robot-runtime","private":true}' | Out-Null
& "$Out\runtime\node\npm.cmd" install "@deepseek-ai/dsh" --no-audit --no-fund --loglevel=error
if ($LASTEXITCODE -ne 0) { throw "npm install dsh 失败" }
Pop-Location

Write-Host "== 4/5 初始化隔离 DSH_HOME 与配置 ==" -ForegroundColor Cyan
$dshHome = "$Out\data\dsh-home"
New-Item -ItemType Directory -Force -Path $dshHome | Out-Null
$env:DSH_HOME = $dshHome
$env:PATH = "$Out\runtime\node;$Out\runtime\node_modules\.bin;$env:PATH"
# 跑一次 headless 触发 profile 模板初始化（没有 API Key 会失败，属预期，profile 已生成）
try { & "$Out\runtime\node_modules\.bin\dsh.cmd" --profile headless "初始化" 2>$null | Out-Null } catch {}
if (Test-Path "$dshHome\profiles\headless") { Write-Host "  headless profile 已生成" } else { Write-Host "  ⚠ profile 未生成（首次启动时会自动再建）" -ForegroundColor Yellow }

# robot.yaml：指向包内隔离 dsh
$robot = @"
mode: dry-run
listen: 127.0.0.1:8790
account: default
db_path: data/xy_gate.sqlite3

brain:
  dsh_bin: dsh
  dsh_home: data/dsh-home
  profile: headless
  workspace: .
  job_timeout_sec: 300
  max_concurrent: 2
  per_chat_cooldown_sec: 20

reply:
  quiet_hours: ["23:30", "08:00"]
  max_per_hour: 60
  keyword_rules: []

delivery:
  items: []

auto_confirm:
  enabled: false
  max_amount_cny: 100
  item_whitelist: []
  item_blacklist: []
  buyer_blacklist: []
  cooldown_minutes: 3

reprice:
  enabled: false
  floor_ratio: 0.85
  max_drop_pct: 20
  max_per_day: 5
  min_interval_min: 10

snipe:
  enabled: false
  interval_minutes: 30
  watch: []

audit_daily_at: "09:00"
"@
Set-Content -Path "$Out\config\robot.yaml" -Value $robot -Encoding UTF8
if (-not (Test-Path "$Out\config\accounts.yaml")) {
    Set-Content -Path "$Out\config\accounts.yaml" -Value "accounts:`n  default:`n    cookies: `"unb=填我; _m_h5_tk=填我`"" -Encoding UTF8
}

Write-Host "== 5/5 完成 ==" -ForegroundColor Cyan
Write-Host ""
Write-Host "安装包已生成：$Out" -ForegroundColor Green
Write-Host "下一步："
Write-Host "  1) 把整个 $OutDir 文件夹拷给使用者（或压缩分发）"
Write-Host "  2) 使用者 prerequisites：Python 3.10+ 与 uv（或系统 python -m venv）"
Write-Host "  3) 双击 启动机器人.bat（首次自动建 venv）→ 浏览器打开 http://127.0.0.1:8790"
Write-Host "  4) 在「设置」页填 DeepSeek API Key 与闲鱼 Cookie"
