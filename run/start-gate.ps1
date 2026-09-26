# xy-gate 启动（Windows PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File run\start-gate.ps1 [-Live]
param(
    [switch]$Live   # 加 -Live 进入 live 模式（覆盖 robot.yaml 里的 mode）
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path "config\robot.yaml")) {
    Copy-Item "config\robot.example.yaml" "config\robot.yaml"
    Write-Host "已生成 config\robot.yaml，请先填好 config\accounts.yaml 再启动" -ForegroundColor Yellow
}
if (-not (Test-Path "config\accounts.yaml")) {
    Copy-Item "config\accounts.example.yaml" "config\accounts.yaml"
    Write-Host "已生成 config\accounts.yaml —— 请填入 Cookie 后重新运行本脚本" -ForegroundColor Yellow
    exit 1
}

if ($Live) {
    (Get-Content "config\robot.yaml" -Raw) -replace 'mode:\s*dry-run', 'mode: live' |
        Set-Content "config\robot.yaml" -Encoding UTF8
    Write-Host "!! LIVE 模式：写操作将真实生效 !!" -ForegroundColor Red
}

Push-Location gate
uv run xy-gate serve
Pop-Location
