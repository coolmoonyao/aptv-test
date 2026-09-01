"""抓取 + 解析 M3U 直播源。

抓取阶段支持「多 User-Agent × 多 Referer 轮询」：不少直播源（尤其是
盗转/聚合 OTT 源）对请求头做了白名单校验，只有特定客户端 UA（如
`okHttp/Mod-1.5.0.0`）或特定 Referer 才放行。逐个组合重试，命中即返回。
"""
import re
from urllib.parse import urlsplit

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


def _referer_candidates(url: str, referers: list[str]) -> list[str]:
    """候选 Referer：源站自身首页 + 配置值（去重）+ 空(不设)。

    源站自身域名优先，因为多数 M3U 列表用「来源页」做防盗链。
    """
    cands: list[str] = []
    try:
        p = urlsplit(url)
        cands.append(f"{p.scheme}://{p.netloc}/")
    except Exception:  # noqa: BLE001
        pass
    for r in referers or []:
        if r and r not in cands:
            cands.append(r)
    cands.append("")  # 不设 Referer 作为兜底
    return cands


async def fetch_source(
    client: httpx.AsyncClient, url: str, user_agents: list[str], referers: list[str]
) -> str:
    """抓单个源，依次尝试 UA×Referer 组合，命中返回文本，全部失败抛异常。"""
    last_exc: Exception | None = None
    for ua in user_agents:
        for ref in _referer_candidates(url, referers):
            headers = {"User-Agent": ua}
            if ref:
                headers["Referer"] = ref
            try:
                resp = await client.get(
                    url, headers=headers, timeout=30, follow_redirects=True
                )
                resp.raise_for_status()
                return resp.text
            except Exception as ex:  # noqa: BLE001
                last_exc = ex
    raise last_exc or RuntimeError(f"所有 UA/Referer 组合均失败: {url}")


async def fetch_all(
    sources: list[str], user_agents: list[str], referers: list[str]
) -> list[dict]:
    """并发拉取多个源，合并去重（按 URL）。"""
    entries: list[dict] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in sources:
            try:
                text = await fetch_source(client, url, user_agents, referers)
                parsed = parse_m3u(text)
                added = 0
                for e in parsed:
                    if e["url"] and e["url"] not in seen_urls:
                        seen_urls.add(e["url"])
                        entries.append(e)
                        added += 1
                print(f"[fetch] {url} -> 解析 {len(parsed)} 条，新增 {added} 条")
            except Exception as ex:  # noqa: BLE001
                print(f"[fetch] 源失败 {url}: {ex}")
    return entries
