# xy-dsh-robot Electron 桌面版构建（Windows → NSIS 安装器）
# 产出 dist-electron\xy-robot-setup-<ver>.exe
# 前置：web 已构建（cd web; npm i; npm run build）；本机可上网（npm/镜像）
# 用法：powershell -ExecutionPolicy Bypass -File packaging\build-electron.ps1
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..").Path
$Stage = Join-Path $Repo "packaging\electron-resources"

Write-Host "== 1/5 准备资源树 ==" -ForegroundColor Cyan
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
$xd = @(".git", ".shots", "dist", "dist-electron", "docs", "node_modules",
         "runtime", "data", "electron", "packaging", ".pytest_tmp") |
    ForEach-Object { Join-Path $Repo $_ }
robocopy $Repo $Stage /E /XD $xd @("$Repo\web\node_modules") /XF ".credentials.yaml*" "accounts.yaml" "robot.yaml" "watchlist.yaml" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制资源失败" }
if (Test-Path "$Stage\gate\.venv") { Remove-Item -Recurse -Force "$Stage\gate\.venv" }
if (-not (Test-Path "$Stage\web\dist\index.html")) { throw "web\dist 不存在：先 cd web; npm run build" }

Write-Host "== 2/5 安装内置 dsh（Electron 自带 Node 运行，无需单独 Node）==" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "$Stage\dsh" | Out-Null
Set-Content -Path "$Stage\dsh\package.json" -Value '{"name":"xy-dsh","private":true}'
Push-Location "$Stage\dsh"
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
npm install "@deepseek-ai/dsh" --no-audit --no-fund --omit=dev --loglevel=error
if ($LASTEXITCODE -ne 0) { throw "npm install dsh 失败" }
Pop-Location
# dsh 的 npm 依赖里不需要二进制 Electron，卸掉假装的 electron 下载
if (Test-Path "$Stage\dsh\node_modules\electron") { Remove-Item -Recurse -Force "$Stage\dsh\node_modules\electron" }

Write-Host "== 3/5 默认配置（首次启动在用户侧生成 data/）==" -ForegroundColor Cyan
if (-not (Test-Path "$Stage\config\robot.yaml")) {
    Copy-Item "$Stage\config\robot.example.yaml" "$Stage\config\robot.yaml"
}

Write-Host "== 4/5 electron-builder（下载 Electron 一次，之后有缓存）==" -ForegroundColor Cyan
Push-Location "$Repo\electron"
if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
if ($LASTEXITCODE -ne 0) { throw "electron devDependencies 安装失败" }
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
npx electron-builder --win nsis
if ($LASTEXITCODE -ne 0) { throw "electron-builder 失败" }
Pop-Location

Write-Host "== 5/5 完成 ==" -ForegroundColor Cyan
Get-ChildItem "$Repo\dist-electron\*.exe" | ForEach-Object {
    Write-Host ("安装器：{0}（{1:N0} MB）" -f $_.FullName, ($_.Length / 1MB)) -ForegroundColor Green
}
Write-Host "安装后：桌面「闲鱼卖家机器人」→ 首启自动建 Python venv → 设置页填 API Key + 扫码登录"
