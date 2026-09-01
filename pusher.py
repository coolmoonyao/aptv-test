"""git 提交推送 live.m3u 到 GitHub。"""
import subprocess
import time
from pathlib import Path

import shutil

# git 可执行文件：自动化环境的 PATH 可能不含完整目录
GIT = shutil.which("git") or "/usr/bin/git"


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _clear_lock(repo_dir: str) -> None:
    """清理 index.lock。

    macOS 上 git 因 com.apple.provenance 扩展属性会在 rename 后无法 unlink
    index.lock，遗留 0 字节锁文件阻塞后续操作。每次 git 写操作后清理之。
    """
    lock = Path(repo_dir) / ".git" / "index.lock"
    if lock.exists():
        try:
            lock.unlink()
        except OSError:
            pass


def _git(cmd: list[str], repo_dir: str) -> subprocess.CompletedProcess:
    r = _run([GIT, *cmd], repo_dir)
    _clear_lock(repo_dir)
    return r


def push(repo_dir: str, filename: str, branch: str, commit_msg: str | None = None) -> bool:
    """git add + commit + push，无变化返回 False，失败抛出异常。"""
    _clear_lock(repo_dir)

    r = _git(["add", filename], repo_dir)
    if r.returncode != 0:
        raise RuntimeError(f"git add 失败：{r.stderr.strip()}")

    status = _run([GIT, "status", "--porcelain", "--", filename], repo_dir)
    _clear_lock(repo_dir)
    if not status.stdout.strip():
        return False

    msg = commit_msg or "chore: 每日自动更新直播源"
    r = _git(["commit", "-m", msg], repo_dir)
    if r.returncode != 0:
        raise RuntimeError(f"git commit 失败：{r.stderr.strip()}")

    # 推送重试 3 次，容忍瞬时网络抖动
    last_err = ""
    for attempt in range(3):
        r = _run([GIT, "push", "origin", branch], repo_dir)
        if r.returncode == 0:
            return True
        last_err = (r.stderr or r.stdout).strip()
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"push 失败（重试3次）：{last_err}")
