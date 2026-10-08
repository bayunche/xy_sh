// xy-robot-desktop — Electron 壳。
//  ┌ 可写区 userData/app/：gate(含 venv)、config、prompts、sop、.dsh、web/dist、data
//  │  （安装目录可能只读，故首启把小体量应用树同步到 userData，配置永不被覆盖）
//  └ 只读区 resources/：dsh 的 node_modules（~300MB，经 XY_DSH_SCRIPT 引用）
// 大脑 dsh 用 Electron 自带 Node（ELECTRON_RUN_AS_NODE）运行，无需单独装 Node。
const { app, BrowserWindow, Menu } = require("electron");
const { spawn, execSync } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

const IS_WIN = process.platform === "win32";
const PORT = 8790;
const URL = `http://127.0.0.1:${PORT}`;

const RES = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..");
const APP_ROOT = app.isPackaged
  ? path.join(app.getPath("userData"), "app")
  : RES;   // 开发模式直接用仓库

let win = null;
let daemon = null;
let quitting = false;

// ── 首启同步：resources → userData/app（配置只补缺，代码随版本刷新）────
function syncAppRoot() {
  if (!app.isPackaged) return;
  const dirs = [["gate", true], ["web", true], ["prompts", true], ["sop", true], [".dsh", true], ["config", false]];
  const marker = path.join(APP_ROOT, ".version");
  const ver = app.getVersion();
  if (fs.existsSync(marker) && fs.readFileSync(marker, "utf8") === ver) return;
  fs.mkdirSync(APP_ROOT, { recursive: true });
  for (const [dir, overwrite] of dirs) {
    const src = path.join(RES, dir);
    if (!fs.existsSync(src)) continue;
    const dst = path.join(APP_ROOT, dir);
    fs.mkdirSync(dst, { recursive: true });
    if (overwrite) {
      copyTree(src, dst);   // 纯 Node 递归复制（跨平台；排除 venv/依赖目录）
    } else {
      // 配置目录：只补缺失文件（robot.example.yaml 等），绝不覆盖用户配置
      walk(src, (f) => {
        const rel = path.relative(src, f);
        const target = path.join(dst, rel);
        if (!fs.existsSync(target)) {
          fs.mkdirSync(path.dirname(target), { recursive: true });
          fs.copyFileSync(f, target);
        }
      });
    }
  }
  fs.mkdirSync(path.join(APP_ROOT, "data"), { recursive: true });
  fs.writeFileSync(marker, ver);
}

const COPY_SKIP = new Set([".venv", "node_modules", ".pytest_tmp", "__pycache__"]);
function copyTree(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (COPY_SKIP.has(e.name)) continue;
    const sp = path.join(src, e.name), dp = path.join(dst, e.name);
    if (e.isDirectory()) copyTree(sp, dp);
    else if (e.isSymbolicLink()) {
      try { fs.copyFileSync(fs.realpathSync(sp), dp); } catch {}
    } else fs.copyFileSync(sp, dp);
  }
}

function walk(dir, fn) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, fn);
    else fn(p);
  }
}

// ── Python venv（uv 自动安装 + 国内源优先，失败回退官方源）──────────
const HOME_DIR = process.env.HOME || process.env.USERPROFILE;
const CN_MIRRORS = {
  uvBin: "https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download",
  pypi: "https://mirrors.aliyun.com/pypi/simple/",
  python: "https://ghproxy.net/https://github.com/astral-sh/python-build-standalone/releases/download",
};

function venvPython() {
  const p = IS_WIN
    ? path.join(APP_ROOT, "gate", ".venv", "Scripts", "python.exe")
    : path.join(APP_ROOT, "gate", ".venv", "bin", "python");
  return fs.existsSync(p) ? p : null;
}

// spawn 异步跑命令并收集输出（execSync 会卡死主进程，首启下载几分钟不可接受）
function run(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    let p;
    try {
      p = spawn(cmd, args, Object.assign(
        { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }, opts));
    } catch (e) {
      resolve({ code: -1, out: String(e) });
      return;
    }
    let out = "";
    const timer = setTimeout(() => { try { p.kill(); } catch {} },
      opts.timeout || 10 * 60 * 1000);
    p.stdout.on("data", (d) => { out += d; });
    p.stderr.on("data", (d) => { out += d; });
    p.on("error", (e) => { clearTimeout(timer); resolve({ code: -1, out: out + String(e) }); });
    p.on("close", (code) => { clearTimeout(timer); resolve({ code, out }); });
  });
}

function findUv() {
  const r = require("child_process").spawnSync(
    IS_WIN ? "where" : "which", ["uv"], { encoding: "utf8" });
  if (r.status === 0 && r.stdout.trim()) return r.stdout.trim().split(/\r?\n/)[0];
  for (const dir of [".local/bin", ".cargo/bin"]) {
    const f = path.join(HOME_DIR, dir, IS_WIN ? "uv.exe" : "uv");
    if (fs.existsSync(f)) return f;
  }
  return null;
}

async function installUv(log) {
  const attempts = [["国内镜像", CN_MIRRORS.uvBin], ["官方源", ""]];
  for (const [name, dlUrl] of attempts) {
    log(`[env] 未找到 uv，自动安装（${name}）…`);
    let r;
    if (IS_WIN) {
      const ps = dlUrl
        ? `$env:UV_DOWNLOAD_URL='${dlUrl}'; irm https://astral.sh/uv/install.ps1 | iex`
        : `irm https://astral.sh/uv/install.ps1 | iex`;
      r = await run("powershell",
        ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], { timeout: 300000 });
    } else {
      const script = dlUrl
        ? `export UV_DOWNLOAD_URL="${dlUrl}"; curl -LsSf https://astral.sh/uv/install.sh | sh`
        : `curl -LsSf https://astral.sh/uv/install.sh | sh`;
      r = await run("sh", ["-c", script], { timeout: 300000 });
    }
    const uv = findUv();
    if (uv) return uv;
    log(`[env] uv 安装（${name}）失败: ${(r.out || "").trim().split(/\r?\n/).slice(-2).join(" ").slice(0, 160)}`);
  }
  return null;
}

async function ensureVenv(log) {
  const existing = venvPython();
  if (existing) return existing;
  const gateDir = path.join(APP_ROOT, "gate");
  let uv = findUv() || await installUv(log);
  if (uv) {
    for (const [name, extra] of [
      ["国内源", { UV_DEFAULT_INDEX: CN_MIRRORS.pypi, UV_INDEX_URL: CN_MIRRORS.pypi,
                   UV_PYTHON_INSTALL_MIRROR: CN_MIRRORS.python }],
      ["官方源", {}],
    ]) {
      log(`[env] uv sync（${name}）… 首次需下载 Python 与依赖，可能几分钟`);
      const r = await run(uv, ["sync"], { cwd: gateDir, env: Object.assign({}, process.env, extra) });
      const tail = (r.out || "").trim().split(/\r?\n/).filter(Boolean).slice(-3).join(" | ");
      if (tail) log(`[env] ${tail.slice(0, 300)}`);
      const py = venvPython();
      if (r.code === 0 && py) return py;
      log(`[env] uv sync（${name}）失败 code=${r.code}`);
    }
  }
  // 最后兜底：系统 python 直接建 venv（非 editable——旧 pip 不支持 PEP 660）
  const pyBin = IS_WIN ? "python" : "python3";
  log(`[env] uv 不可用，尝试 ${pyBin} -m venv 兜底…`);
  await run(pyBin, ["-m", "venv", ".venv"], { cwd: gateDir, timeout: 300000 });
  const pip = IS_WIN ? path.join(gateDir, ".venv", "Scripts", "pip.exe")
    : path.join(gateDir, ".venv", "bin", "pip");
  let r = await run(pip, ["install", ".", "-i", CN_MIRRORS.pypi],
    { cwd: gateDir, timeout: 15 * 60 * 1000 });
  if (r.code !== 0) r = await run(pip, ["install", "."], { cwd: gateDir, timeout: 15 * 60 * 1000 });
  return venvPython();
}

// ── xy-gate sidecar ─────────────────────────────────────────────────
async function startDaemon(log) {
  const py = await ensureVenv(log);
  if (!py) {
    log("[env] 环境创建失败（uv 自动安装与 python 兜底都没成）。请检查网络后重开本应用。");
    return null;
  }
  const dshScript = path.join(RES, "dsh", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js");
  const env = Object.assign({}, process.env, {
    DSH_HOME: path.join(APP_ROOT, "data", "dsh-home"),
    ELECTRON_RUN_AS_NODE: "1",               // dsh 子进程继承，用 Electron 当 Node
    XY_DSH_NODE: process.execPath,
    XY_DSH_SCRIPT: fs.existsSync(dshScript) ? dshScript : "",
    PYTHONIOENCODING: "utf-8",
  });
  fs.mkdirSync(path.join(APP_ROOT, "data"), { recursive: true });
  daemon = spawn(py, ["-m", "xy_gate", "serve"], { cwd: APP_ROOT, env, windowsHide: true });
  daemon.stdout.on("data", (d) => log(String(d).trim()));
  daemon.stderr.on("data", (d) => log(String(d).trim()));
  daemon.on("exit", (code) => {
    daemon = null;
    if (!quitting) log(`[gate] 退出 code=${code}`);
  });
}

function stopDaemon() {
  if (!daemon) return;
  quitting = true;
  if (IS_WIN) {
    try { execSync(`taskkill /PID ${daemon.pid} /T /F`, { stdio: "ignore", windowsHide: true }); }
    catch { try { daemon.kill(); } catch {} }
  } else {
    try { process.kill(daemon.pid, "SIGTERM"); } catch {}
  }
  daemon = null;
}

function waitHealthy(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const ping = () => http.get(`${URL}/health`, (r) => resolve(r.statusCode === 200))
      .on("error", () => (Date.now() > deadline ? resolve(false) : setTimeout(ping, 800)));
    ping();
  });
}

// ── 生命周期 ─────────────────────────────────────────────────────────
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { win.show(); win.focus(); } });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    syncAppRoot();
    win = new BrowserWindow({
      width: 1380, height: 860, minWidth: 1024, minHeight: 700,
      title: "闲鱼卖家机器人", backgroundColor: "#f8fafc",
      webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true },
    });

    const logs = [];
    // 启动日志同时落盘（userData/startup.log），便于排查静默退出
    const logFile = path.join(app.getPath("userData"), "startup.log");
    const log = (line) => {
      if (!line) return;
      logs.push(line.slice(0, 300));
      try { fs.appendFileSync(logFile, new Date().toLocaleTimeString() + " " + line.slice(0, 500) + "\n"); } catch {}
      win && win.webContents.executeJavaScript(
        `window.__xylog && window.__xylog(${JSON.stringify(logs.slice(-12))})`, true).catch(() => {});
    };

    await win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(bootPage()));
    const hadVenv = !!venvPython();
    await startDaemon(log);
    const ok = await waitHealthy(hadVenv ? 120000 : 15 * 60 * 1000);
    if (!ok) {
      win.webContents.executeJavaScript(
        `document.getElementById('s').textContent = ${JSON.stringify(
          "后端启动失败：请确认已安装 Python 3.10+（或 uv）后重开本应用。日志见下方。")}`,
        true).catch(() => {});
      return;
    }
    await win.loadURL(URL);
    win.setTitle("闲鱼卖家机器人");
  });

  app.on("window-all-closed", () => { stopDaemon(); app.quit(); });
  app.on("before-quit", stopDaemon);
}

function bootPage() {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
body{font-family:system-ui,"Microsoft YaHei";background:#f8fafc;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.c{text-align:center;color:#475569;font-size:14px}
.spin{width:34px;height:34px;border:3px solid #99f6e4;border-top-color:#0d9488;
border-radius:50%;margin:0 auto 18px;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
pre{white-space:pre-wrap;text-align:left;max-width:680px;max-height:200px;overflow:auto;
font-size:11px;color:#94a3b8;margin-top:16px}
</style></head><body><div class="c"><div class="spin"></div>
<div id="s">正在启动机器人（首次运行会自动安装 uv 并用国内源拉取 Python 环境与依赖，可能需要几分钟）…</div>
<pre id="lg"></pre></div>
<script>window.__xylog=function(ls){document.getElementById('lg').textContent=ls.join("\\n")}</script>
</body></html>`;
}
