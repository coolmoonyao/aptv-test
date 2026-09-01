"""git 提交推送 live.m3u 到 GitHub。"""
import subprocess


def push(repo_dir: str, filename: str, branch: str, commit_msg: str | None = None) -> bool:
    """git add + commit + push，无变化返回 False。"""
    subprocess.run(["git", "add", filename], cwd=repo_dir, check=True, capture_output=True)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", filename],
        cwd=repo_dir, capture_output=True, text=True,
    )
    if not status.stdout.strip():
        return False
    msg = commit_msg or "chore: 每日自动更新直播源"
    subprocess.run(["git", "commit", "-m", msg], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", branch], cwd=repo_dir, check=True, capture_output=True)
    return True
