"""dsh 大脑桥：渲染 job 提示词 → 子进程跑 dsh headless → 回收输出。

设计要点：
- job 模板在 prompts/jobs/*.md，占位符形如 {{EVENT}}（无第三方模板依赖）；
- 模板里只放"事件 + 指针"（让 agent 自己用 fs 工具读 sop/ 与技能正文）；
- 完整 job 正文写入工作区 data/jobs/ 任务文件，argv 只传一行指针——
  Windows 的 dsh.cmd shim 会截断多行参数，且命令行有 32k 长度限制；
- 会话内 agent 的动作走 xy-gate CLI（HTTP 到本 daemon），写操作会被闸门再校验。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .config import BrainConfig

log = logging.getLogger("xy_gate.brain")

REPO_ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO_ROOT / "prompts" / "jobs"


@dataclass
class BrainResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float


def render_job(template: str, ctx: Dict[str, str]) -> str:
    out = template
    for key, value in ctx.items():
        out = out.replace("{{" + key + "}}", value)
    # 未填充的占位符清空，避免提示词里留 {{XXX}} 干扰模型
    import re
    return re.sub(r"\{\{[A-Z_]+\}\}", "", out)


class Brain:
    def __init__(self, cfg: BrainConfig):
        self.cfg = cfg
        self.workspace = (REPO_ROOT / cfg.workspace).resolve()
        self.semaphore = asyncio.Semaphore(cfg.max_concurrent)
        # 隔离的 DSH_HOME（默认仓库/包内 data/dsh-home，绝不碰系统 ~/.dsh）。
        # 注意：dsh 的 profiles/node_modules 兜底需要符号链接/junction，
        # 非 NTFS 卷（如 exFAT 的 D 盘）承载不了——此时自动重定向到
        # %LOCALAPPDATA%\xy-robot\dsh-home（NTFS），隔离性不变。
        self.dsh_home = (REPO_ROOT / cfg.dsh_home).resolve() if cfg.dsh_home else None
        if self.dsh_home and not self._fs_supports_links(self.dsh_home):
            alt = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "xy-robot" / "dsh-home"
            log.warning("隔离 DSH_HOME 所在卷不支持链接（%s），重定向到 %s",
                        self.dsh_home, alt)
            self.dsh_home = alt
        if self.dsh_home:
            self._ensure_profile()

    @staticmethod
    def _fs_supports_links(directory: Path) -> bool:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            test = directory / ".link-capability-test"
            if sys.platform == "win32":
                import _winapi
                _winapi.CreateJunction(str(directory), str(test))
                import shutil
                shutil.rmtree(test, ignore_errors=True)
            else:
                os.symlink("linktest", test)
                test.unlink()
            return True
        except OSError:
            return False

    def _ensure_profile(self) -> None:
        """确保隔离 home 里有目标 profile。

        dsh 对新 DSH_HOME 首次会自动初始化 profile 模板，但模板初始化用
        symlink 摆 bundle——Windows 普通权限直接 EISDIR。改为从系统
        ~/.dsh/profiles/<profile> 复制纯文件（bundle 由 dsh 二进制自身位置
        解析，无需 node_modules）。系统也没有时留空，交给 dsh 自身初始化。
        """
        import shutil
        target = self.dsh_home / "profiles" / self.cfg.profile
        if not target.exists():
            src = Path.home() / ".dsh" / "profiles" / self.cfg.profile
            if src.exists():
                try:
                    shutil.copytree(src, target, ignore=shutil.ignore_patterns("node_modules"))
                    log.info("已从系统 profile 复制模板到隔离 home：%s", target)
                except OSError as e:
                    log.warning("复制 profile 模板失败（交由 dsh 自行初始化）: %s", e)



    def _resolve_dsh_cmd(self) -> list:
        """dsh 启动命令。三种形态按优先级：
        1. Electron 桌面壳注入的 XY_DSH_NODE + XY_DSH_SCRIPT（用 Electron 自带
           Node 跑包内 dsh 的 bin.js，环境里带 ELECTRON_RUN_AS_NODE=1）；
        2. 包内自带 dsh（runtime/node_modules/.bin/dsh.cmd）；
        3. PATH 上的 dsh（shutil.which 解决 Windows .cmd 解析）。
        """
        node = os.environ.get("XY_DSH_NODE", "")
        script = os.environ.get("XY_DSH_SCRIPT", "")
        if node and script and Path(script).exists():
            return [node, script]
        bundled = REPO_ROOT / "runtime" / "node_modules" / ".bin"
        for name in ("dsh.cmd", "dsh"):
            cand = bundled / name
            if cand.exists():
                return [str(cand)]
        resolved = shutil.which(self.cfg.dsh_bin)
        return [resolved or self.cfg.dsh_bin]

    def build_job(self, template_name: str, ctx: Dict[str, str]) -> str:
        path = JOBS_DIR / template_name
        if not path.exists():
            raise FileNotFoundError(f"job 模板不存在: {path}")
        return render_job(path.read_text(encoding="utf-8"), ctx)

    async def run_job(self, job_text: str, timeout: Optional[int] = None) -> BrainResult:
        """跑一个 headless job。

        Windows 上 dsh 走 dsh.cmd shim，多行/长参数会被 cmd 的 %* 展开截断，
        因此完整 job 正文写入工作区内 data/jobs/ 下的任务文件，argv 只传
        一行指针（也顺带绕开 32k 命令行长度限制）。
        """
        timeout = timeout or self.cfg.job_timeout_sec
        started = time.time()
        dsh_cmd = self._resolve_dsh_cmd()
        env = dict(os.environ)
        if self.dsh_home:
            env["DSH_HOME"] = str(self.dsh_home)   # 与全局 ~/.dsh 隔离
        if self.cfg.api_base_url:
            env["DEEPSEEK_BASE_URL"] = self.cfg.api_base_url  # LLM API 地址

        jobs_dir = self.workspace / "data" / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_file = jobs_dir / f"job-{int(time.time() * 1000)}-{os.urandom(3).hex()}.md"
        pointer = (f"你的完整任务在任务文件 data/jobs/{job_file.name}（相对当前工作目录）。"
                   "先读它，然后严格按其中的指令执行，最后按其要求输出摘要。")
        try:
            job_file.write_text(job_text, encoding="utf-8")
            async with self.semaphore:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *dsh_cmd, "--profile", self.cfg.profile, pointer,
                        cwd=str(self.workspace), env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except FileNotFoundError:
                    return BrainResult(False, -1, "",
                                       f"找不到 dsh 可执行文件: {' '.join(dsh_cmd)}", 0.0)
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return BrainResult(False, -1, "",
                                       f"dsh job 超时（{timeout}s）已终止", time.time() - started)
        finally:
            try:
                job_file.unlink(missing_ok=True)
            except OSError:
                pass
        out = stdout.decode("utf-8", errors="ignore").strip()
        err = stderr.decode("utf-8", errors="ignore").strip()
        return BrainResult(proc.returncode == 0,
                           proc.returncode if proc.returncode is not None else -1,
                           out, err, time.time() - started)


def summarize_result(result: BrainResult) -> str:
    """取 stdout 末尾若干行作为事件台账里的大脑输出摘要。"""
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    if lines:
        return "\n".join(lines[-15:])
    if result.ok:
        # 静默成功（实测复现：思考型/兼容端点 content 为空、内容只在
        # reasoning 通道，dsh 拿不到正文就 exit 0 无输出）
        return ("(大脑空输出：模型疑似思考型，响应无 content——"
                "换非思考模型或让供应商输出 content)")
    return result.stderr[-500:] or "(无输出)"
