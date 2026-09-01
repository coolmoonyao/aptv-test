"""git 提交推送 live.m3u 到 GitHub。"""
import subprocess
import time
from pathlib import Path

import shutil

# git 可执行文件：与 ffprobe 同理，自动化环境的 PATH 可能不含完整目录
GIT = shutil.which("git") or "/usr/bin/git"


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _clear_stale_lock(repo_dir: str) -> None:
    """清理上一次异常退出遗留的 index.lock（0 字节/陈旧锁）。"""
    lock = Path(repo_dir) / ".git" / "index.lock"
    if not lock.exists():
        return
    # 仅当没有正在运行的 git 进程时才删除，避免误删活动锁
    alive = subprocess.run(
        ["pgrep", "-f", f"git .*{Path(repo_dir).name}"],
        capture_output=True, text=True,
    )
    if alive.returncode == 0 and alive.stdout.strip():
        return
    try:
        lock.unlink()
    except OSError:
        pass


def push(repo_dir: str, filename: str, branch: str, commit_msg: str | None = None) -> bool:
    """git add + commit + push，无变化返回 False，失败抛出异常。"""
    _clear_stale_lock(repo_dir)

    r = _run([GIT, "add", filename], repo_dir)
    if r.returncode != 0:
        raise RuntimeError(f"git add 失败：{r.stderr.strip()}")

    status = _run([GIT, "status", "--porcelain", "--", filename], repo_dir)
    if not status.stdout.strip():
        return False

    msg = commit_msg or "chore: 每日自动更新直播源"
    r = _run([GIT, "commit", "-m", msg], repo_dir)
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
