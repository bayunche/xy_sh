#!/usr/bin/env bash
# xy-gate 启动（Linux/macOS/Git-Bash）
# 用法：./run/start-gate.sh [--live]
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f config/robot.yaml ] || cp config/robot.example.yaml config/robot.yaml
if [ ! -f config/accounts.yaml ]; then
  cp config/accounts.example.yaml config/accounts.yaml
  echo "已生成 config/accounts.yaml —— 请填入 Cookie 后重新运行" >&2
  exit 1
fi

if [ "${1:-}" = "--live" ]; then
  sed -i.bak 's/^mode:.*/mode: live/' config/robot.yaml
  echo "!! LIVE 模式：写操作将真实生效 !!"
fi

cd gate && exec uv run xy-gate serve
