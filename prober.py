"""探测：HTTP 首字节测响应时间 + ffprobe 取真实分辨率。

「响应时间」定义为 HTTP 首字节耗时（连接 + 响应头 + 首个数据字节），
而非 ffprobe 起播耗时——后者需下载视频分片，必然 >1s，会误杀好源。

支持「多 User-Agent × 多 Referer」轮询：不少源对请求头做白名单校验，
只有特定客户端 UA（如 okHttp/Mod-1.5.0.0）或特定 Referer 才放行。
对每个流依次尝试候选组合，记录命中的 (ua, referer)，供下次优先复用
并写入最终 m3u 的 #EXTVLCOPT 头。
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


def _ffprobe_cmd(url: str, ua: str, referer: str) -> list[str]:
    cmd = [
        FFPROBE, "-v", "error",
        "-user_agent", ua,
    ]
    if referer:
        cmd += ["-headers", f"Referer: {referer}\r\n"]
    cmd += [
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        "-analyzeduration", "5000000",
        "-probesize", "5000000",
        "-rw_timeout", "8000000",
        url,
    ]
    return cmd


def _ordered_combos(
    user_agents: list[str], referers: list[str], preferred: list | None
) -> list[tuple[str, str]]:
    """生成 UA×Referer 组合，把上次命中的 preferred 提到最前。"""
    combos: list[tuple[str, str]] = []
    for ua in user_agents:
        for ref in referers:
            combos.append((ua, ref))
    if preferred and len(preferred) == 2:
        p = (preferred[0], preferred[1])
        if p in combos:
            combos.remove(p)
            combos.insert(0, p)
    return combos


async def _http_first_byte(
    client: httpx.AsyncClient, url: str, ua: str, referer: str
) -> tuple[bool, float, int | None]:
    """门禁探测：返回 (是否 2xx/3xx, 首字节毫秒, 状态码)。

    - 2xx/3xx           → (True, ms, status)，过门禁
    - 明确 4xx/5xx      → (False, 0, status)，防盗链拒绝，可换组合重试
    - 网络错误/超时     → (False, 0, None)，死链，无需再试其他组合

    门禁探测要快：单组合 connect 3s / 整体 5s 上限，避免死链把总时长拖爆。
    """
    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
    t0 = time.perf_counter()
    try:
        async with client.stream(
            "GET", url, headers=headers, timeout=httpx.Timeout(5.0, connect=3.0)
        ) as resp:
            async for _ in resp.aiter_bytes(1):
                break
            ms = (time.perf_counter() - t0) * 1000.0
            if 200 <= resp.status_code < 400:
                return True, ms, resp.status_code
            return False, 0.0, resp.status_code
    except Exception:  # noqa: BLE001
        return False, 0.0, None


async def _probe_resolution(
    url: str, timeout_s: float, ua: str, referer: str
) -> tuple[int, int] | str | None:
    """ffprobe 探测分辨率，四态返回：

    - (w, h)   成功拿到分辨率
    - "opened" ffprobe 成功打开流（退出码 0）但无 width/height 元数据
    - "timeout" 超时：流可达但下载分片太慢（海外源常见），视作「活着但未知分辨率」
    - None     打开失败（翻墙 403/错误页/非法数据），真正的死链
    """
    cmd = _ffprobe_cmd(url, ua, referer)
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
        return "timeout"
    text = out.decode(errors="ignore").strip()
    parts = [p for p in text.splitlines() if p.strip()]
    if len(parts) >= 2:
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            w = h = 0
        if w > 0 and h > 0:
            return w, h
    # 无有效分辨率：退出码 0 = 成功打开但无元数据；否则打开失败
    return "opened" if proc.returncode == 0 else None


async def probe_one(
    client: httpx.AsyncClient,
    url: str,
    timeout_s: float,
    user_agents: list[str],
    referers: list[str],
    preferred: list | None = None,
) -> tuple | None:
    """返回探测结果，三态：

    - (w, h, ms, ua, ref)          成功，w/h 为真实分辨率
    - (None, None, ms, ua, ref)    HTTP 可达（2xx/3xx）但 ffprobe 无法
                                    确认分辨率（软 2xx：返回错误页/空流/
                                    格式特殊），记录首个软 2xx 的命中头
    - None                          死链（网络错误/超时，或全部组合 4xx/5xx）

    依次尝试候选 UA×Referer，命中即停：
    - 2xx/3xx + ffprobe 解析成功 → 返回成功
    - 2xx 但 ffprobe 打开无元数据 / 超时 → 活着但未知分辨率（软 2xx），
      立即返回，不再换组合（换 UA 也救不回来，避免海外慢源拖满 8 组合）
    - 2xx 但 ffprobe 打开失败（非法数据/错误页）→ 换组合再试（可能防盗链）
    - 明确 4xx/5xx → 防盗链拒绝，换组合再试
    - 网络错误/超时 → 死链，直接放弃（换 UA 也救不回来）
    """
    for ua, ref in _ordered_combos(user_agents, referers, preferred):
        ok, ms, status = await _http_first_byte(client, url, ua, ref)
        if ok:
            wh = await _probe_resolution(url, timeout_s, ua, ref)
            if isinstance(wh, tuple):
                return wh[0], wh[1], round(ms), ua, ref
            if wh in ("opened", "timeout"):
                # 活着但未知分辨率：软 2xx，立即返回
                return None, None, round(ms), ua, ref
            continue  # 非法数据/错误页：换下一组合
        if status is None:
            return None  # 网络错误/超时：死链，不再尝试
        # 明确 4xx/5xx：防盗链拒绝，继续换下一组合
    return None


async def probe_all(
    entries: list[dict],
    concurrency: int,
    timeout_s: float,
    cache: dict[str, tuple[int, int, float, str, str] | None],
    user_agents: list[str],
    referers: list[str],
    hit_headers: dict[str, list[str]],
) -> list[tuple[dict, tuple[int, int, float, str, str] | None]]:
    """并发探测，返回 [(entry, result), ...]，同 URL 走缓存。

    hit_headers 记录每个 URL 命中的 [ua, referer]，供跨次运行复用。
    """
    sem = asyncio.Semaphore(concurrency)

    async def worker(e: dict):
        async with sem:
            url = e["url"]
            preferred = hit_headers.get(url)
            if url not in cache:
                cache[url] = await probe_one(
                    client, url, timeout_s, user_agents, referers, preferred
                )
            res = cache[url]
            if res is not None:
                hit_headers[url] = [res[3], res[4]]
            return e, res

    # verify=False：部分 CDN 证书过期/自签，此处仅读公开流，可接受
    async with httpx.AsyncClient(
        timeout=12, follow_redirects=True, verify=False
    ) as client:
        results = await asyncio.gather(*(worker(e) for e in entries))
    return list(results)
