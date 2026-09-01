"""过滤、同名合并排序、M3U 生成。"""


def filter_by_keywords(entries: list[dict], cfg: dict) -> list[dict]:
    """按 include/exclude 关键词过滤（对 group-title + 名称匹配）。"""
    inc = cfg.get("include_keywords", [])
    exc = cfg.get("exclude_keywords", [])
    out = []
    for e in entries:
        text = f"{e['group']} {e['name']}"
        if exc and any(k and k in text for k in exc):
            continue
        if inc and any(k and k in text for k in inc):
            out.append(e)
    return out


def apply_probe_filters(
    results: list[tuple[dict, tuple[int, int, float] | None]], cfg: dict
) -> list[dict]:
    """按分辨率与响应时间筛选，给通过的条目附加 width/height/response_ms。"""
    min_w = cfg.get("min_width", 1920)
    min_h = cfg.get("min_height", 1080)
    max_ms = cfg.get("max_response_ms", 1000)
    out = []
    for e, res in results:
        if res is None:
            continue
        w, h, ms = res
        if w >= min_w and h >= min_h and ms <= max_ms:
            out.append({**e, "width": w, "height": h, "response_ms": ms})
    return out


def merge_and_sort(entries: list[dict]) -> list[dict]:
    """同名节目合并为多链接；分辨率降序，同分辨率按响应时间升序。"""
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(e["name"], []).append(e)

    merged = []
    for name, items in groups.items():
        # 分辨率(像素)降序 -> 响应时间升序
        items.sort(key=lambda x: (-(x["width"] * x["height"]), x["response_ms"]))
        best = items[0]
        merged.append({
            "name": name,
            "group": best["group"],
            "logo": best["logo"],
            "urls": [it["url"] for it in items],
        })
    merged.sort(key=lambda m: (m["group"], m["name"]))
    return merged


def generate_m3u(merged: list[dict]) -> str:
    """生成 M3U 文本：一个 #EXTINF 名称 + 多行 URL（备用源）。"""
    lines = ["#EXTM3U"]
    for m in merged:
        attrs = []
        if m["logo"]:
            attrs.append(f'tvg-logo="{m["logo"]}"')
        if m["group"]:
            attrs.append(f'group-title="{m["group"]}"')
        attr_str = " ".join(attrs)
        lines.append(f"#EXTINF:-1 {attr_str},{m['name']}".replace("  ", " "))
        for u in m["urls"]:
            lines.append(u)
    return "\n".join(lines) + "\n"
