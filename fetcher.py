"""抓取 + 解析 M3U 直播源。"""
import re
import httpx

# 解析 #EXTINF 中的属性，如 tvg-logo="..." group-title="..."
_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


def parse_m3u(text: str) -> list[dict]:
    """把 M3U 文本解析为条目列表。

    每条目: {"name", "group", "logo", "url"}
    本实现按「一条 #EXTINF 对应一条 URL」解析（主流源格式）。
    """
    entries: list[dict] = []
    current: dict | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            body = line[len("#EXTINF:"):]
            # 形如: -1 tvg-logo="..." group-title="...",频道名
            if "," in body:
                attr_part, name = body.split(",", 1)
            else:
                attr_part, name = body, ""
            attrs = {}
            for m in _ATTR_RE.finditer(attr_part):
                attrs[m.group(1)] = m.group(2)
            current = {
                "name": name.strip(),
                "group": attrs.get("group-title", ""),
                "logo": attrs.get("tvg-logo", ""),
                "url": None,
            }
        elif line.startswith("#"):
            continue
        else:
            # URL 行，紧跟在 #EXTINF 之后
            if current is not None and current["url"] is None:
                current["url"] = line
                entries.append(current)
                current = None
    return entries


async def fetch_source(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


async def fetch_all(sources: list[str]) -> list[dict]:
    """并发拉取多个源，合并去重（按 URL）。"""
    entries: list[dict] = []
    seen_urls: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for url in sources:
            try:
                text = await fetch_source(client, url)
                for e in parse_m3u(text):
                    if e["url"] and e["url"] not in seen_urls:
                        seen_urls.add(e["url"])
                        entries.append(e)
                print(f"[fetch] {url} -> 解析 {len(parse_m3u(text))} 条")
            except Exception as ex:  # noqa: BLE001
                print(f"[fetch] 源失败 {url}: {ex}")
    return entries
