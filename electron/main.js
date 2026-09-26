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

// ── Python venv ─────────────────────────────────────────────────────
function venvPython() {
  const p = IS_WIN
    ? path.join(APP_ROOT, "gate", ".venv", "Scripts", "python.exe")
    : path.join(APP_ROOT, "gate", ".venv", "bin", "python");
  return fs.existsSync(p) ? p : null;
}

function ensureVenv(log) {
  const existing = venvPython();
  if (existing) return existing;
  const gateDir = path.join(APP_ROOT, "gate");
  const attempts = [
    ["uv", () => execSync("uv sync", { cwd: gateDir, stdio: "pipe", windowsHide: true })],
    ["python", () => {
      const pyBin = IS_WIN ? "python" : "python3";
      execSync(`${pyBin} -m venv .venv`, { cwd: gateDir, stdio: "pipe", windowsHide: true });
      const pip = IS_WIN
        ? path.join(gateDir, ".venv", "Scripts", "pip.exe")
        : path.join(gateDir, ".venv", "bin", "pip");
      execSync(`"${pip}" install -e .`, { cwd: gateDir, stdio: "pipe", windowsHide: true });
    }],
  ];
  for (const [name, fn] of attempts) {
    try { fn(); } catch (e) { log(`[env] 用 ${name} 建环境失败: ${String(e).slice(0, 140)}`); continue; }
    const py = venvPython();
    if (py) return py;
  }
  return null;
}

// ── xy-gate sidecar ─────────────────────────────────────────────────
function startDaemon(log) {
  const py = ensureVenv(log);
  if (!py) {
    log("[env] 未找到 Python 3.10+ 或 uv。请安装后重开本应用。");
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
    startDaemon(log);
    const ok = await waitHealthy();
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
<div id="s">正在启动机器人（首次运行需创建 Python 环境，可能需要几分钟）…</div>
<pre id="lg"></pre></div>
<script>window.__xylog=function(ls){document.getElementById('lg').textContent=ls.join("\\n")}</script>
</body></html>`;
}
