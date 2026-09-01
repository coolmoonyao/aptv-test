"""ffprobe 探测：获取视频流分辨率 + 响应时间（起播耗时）。"""
import asyncio
import time


async def probe_one(url: str, timeout_s: float) -> tuple[int, int, float] | None:
    """探测单条流，返回 (width, height, response_ms)，失败返回 None。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        "-analyzeduration", "3000000",
        "-probesize", "3000000",
        url,
    ]
    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        text = out.decode(errors="ignore").strip()
        parts = [p for p in text.splitlines() if p.strip()]
        if len(parts) >= 2:
            try:
                width = int(parts[0])
                height = int(parts[1])
            except ValueError:
                return None
            if width > 0 and height > 0:
                return width, height, round(elapsed_ms)
        return None
    except Exception:  # noqa: BLE001
        return None


async def probe_all(
    entries: list[dict],
    concurrency: int,
    timeout_s: float,
    cache: dict[str, tuple[int, int, float] | None],
) -> list[tuple[dict, tuple[int, int, float] | None]]:
    """并发探测，返回 [(entry, result), ...]，同 URL 走缓存。"""
    sem = asyncio.Semaphore(concurrency)

    async def worker(e: dict):
        async with sem:
            url = e["url"]
            if url not in cache:
                cache[url] = await probe_one(url, timeout_s)
            return e, cache[url]

    results = await asyncio.gather(*(worker(e) for e in entries))
    return list(results)
