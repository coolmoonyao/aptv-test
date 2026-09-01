"""探测：HTTP 首字节测响应时间 + ffprobe 取真实分辨率。

「响应时间」定义为 HTTP 首字节耗时（连接 + 响应头 + 首个数据字节），
而非 ffprobe 起播耗时——后者需下载视频分片，必然 >1s，会误杀好源。
"""
import asyncio
import shutil
import time

import httpx

# ffprobe 可执行文件：自动化/定时环境的 PATH 可能不含 Homebrew 目录，
# 因此显式解析，找不到再回退到常见安装路径。
FFPROBE = (
    shutil.which("ffprobe")
    or shutil.which("ffprobe", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    or "/opt/homebrew/bin/ffprobe"
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# 多数源要求携带 Referer，否则返回 403/空流
REFERER = "https://live.445569.xyz/"


def _ffprobe_cmd(url: str) -> list[str]:
    return [
        FFPROBE, "-v", "error",
        "-user_agent", UA,
        "-headers", f"Referer: {REFERER}\r\n",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        "-analyzeduration", "5000000",
        "-probesize", "5000000",
        "-rw_timeout", "8000000",
        url,
    ]


async def _http_first_byte(client: httpx.AsyncClient, url: str) -> tuple[bool, float]:
    """返回 (是否 2xx/3xx, 首字节响应毫秒)。失败返回 (False, 0)。"""
    t0 = time.perf_counter()
    try:
        async with client.stream("GET", url) as resp:
            async for _ in resp.aiter_bytes(1):
                break
            ms = (time.perf_counter() - t0) * 1000.0
            return (200 <= resp.status_code < 400), ms
    except Exception:  # noqa: BLE001
        return False, 0.0


async def _probe_resolution(url: str, timeout_s: float) -> tuple[int, int] | None:
    """ffprobe 取 (width, height)，失败返回 None。"""
    cmd = _ffprobe_cmd(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    text = out.decode(errors="ignore").strip()
    parts = [p for p in text.splitlines() if p.strip()]
    if len(parts) >= 2:
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if w > 0 and h > 0:
            return w, h
    return None


async def probe_one(
    client: httpx.AsyncClient, url: str, timeout_s: float
) -> tuple[int, int, float] | None:
    """返回 (width, height, response_ms)，失败返回 None。"""
    ok, ms = await _http_first_byte(client, url)
    if not ok:
        return None
    wh = await _probe_resolution(url, timeout_s)
    if wh is None:
        return None
    return wh[0], wh[1], round(ms)


async def probe_all(
    entries: list[dict],
    concurrency: int,
    timeout_s: float,
    cache: dict[str, tuple[int, int, float] | None],
) -> list[tuple[dict, tuple[int, int, float] | None]]:
    """并发探测，返回 [(entry, result), ...]，同 URL 走缓存。"""
    sem = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": UA, "Referer": REFERER}

    async def worker(e: dict):
        async with sem:
            url = e["url"]
            if url not in cache:
                cache[url] = await probe_one(client, url, timeout_s)
            return e, cache[url]

    # verify=False：部分 CDN 证书过期/自签，此处仅读公开流，可接受
    async with httpx.AsyncClient(
        headers=headers, timeout=12, follow_redirects=True, verify=False
    ) as client:
        results = await asyncio.gather(*(worker(e) for e in entries))
    return list(results)
