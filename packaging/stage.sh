#!/usr/bin/env bash
# 跨平台资源树 staging（CI 用；与 build-electron.ps1 同构）
# 产出 packaging/electron-resources/：gate/web/prompts/sop/.dsh/config/run + 内置 dsh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$REPO/packaging/electron-resources"

echo "== staging 资源树 =="
rm -rf "$STAGE"
mkdir -p "$STAGE"

for d in gate web prompts sop .dsh config run; do
  [ -d "$REPO/$d" ] || continue
  mkdir -p "$STAGE/$d"
  # 排除依赖/运行时目录与凭据
  (cd "$REPO" && tar cf - \
      --exclude='*/node_modules' --exclude='node_modules' \
      --exclude='*/.venv' --exclude='.venv' \
      --exclude='*/__pycache__' --exclude='*/.pytest_tmp' \
      --exclude='*/dist' \
      --exclude='accounts.yaml' --exclude='robot.yaml' --exclude='watchlist.yaml' \
      --exclude='.credentials.yaml*' \
      "$d") | (cd "$STAGE" && tar xf -)
done
# web 只保留构建产物
rm -rf "$STAGE/web"
mkdir -p "$STAGE/web/dist"
cp -R "$REPO/web/dist/." "$STAGE/web/dist/"

[ -f "$STAGE/web/dist/index.html" ] || { echo "web/dist 不存在：先 npm run build"; exit 1; }

# 默认配置（首启用户侧生成 data/；不携带任何真实凭据）
for f in accounts.example.yaml robot.example.yaml watchlist.example.yaml; do
  [ -f "$STAGE/config/$f" ] || cp "$REPO/config/$f" "$STAGE/config/$f"
done
[ -f "$STAGE/config/robot.yaml" ] || cp "$STAGE/config/robot.example.yaml" "$STAGE/config/robot.yaml"

echo "== 安装内置 dsh =="
mkdir -p "$STAGE/dsh"
echo '{"name":"xy-dsh","private":true}' > "$STAGE/dsh/package.json"
(cd "$STAGE/dsh" && npm install "@deepseek-ai/dsh" --no-audit --no-fund --omit=dev --loglevel=error)
rm -rf "$STAGE/dsh/node_modules/electron"

echo "== staging 完成 =="
ls "$STAGE"
