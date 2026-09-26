# xy-dsh-robot 启动器：确保 Python 环境 → 起 xy-gate → 打开控制台
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root

# 包内自带 dsh 优先（runtime\node_modules\.bin），否则用系统 dsh
$env:DSH_HOME = Join-Path $Root "data\dsh-home"
New-Item -ItemType Directory -Force -Path $env:DSH_HOME | Out-Null

$venvPython = "$Root\gate\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "首次运行：创建 Python 虚拟环境…" -ForegroundColor Cyan
    Push-Location "$Root\gate"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        uv sync
        if ($LASTEXITCODE -ne 0) { throw "uv sync 失败" }
    } else {
        python -m venv .venv
        & .venv\Scripts\pip.exe install -e .
        if ($LASTEXITCODE -ne 0) { throw "pip install 失败（需要 Python 3.10+）" }
    }
    Pop-Location
}

Write-Host "启动 xy-gate（http://127.0.0.1:8790）… Ctrl+C 停止" -ForegroundColor Green
Start-Process "http://127.0.0.1:8790/"
& $venvPython -m xy_gate serve
