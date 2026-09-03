"""过滤、同名合并排序、M3U 生成。

探测结果从 3 元组 (w, h, ms) 扩展为 5 元组 (w, h, ms, ua, referer)，
命中头随 URL 一并透传，供最终 m3u 写入 #EXTVLCOPT 头（VLC 系播放器
会据此用正确的 UA/Referer 请求，从而解锁只认特定客户端的源）。

res 三态：
- (w, h, ms, ua, ref)         探测成功
- (None, None, ms, ua, ref)   HTTP 可达但分辨率未知（软 2xx）
- None                        死链
"""


def filter_by_keywords(entries: list[dict], cfg: dict) -> list[dict]:
    """按 include/exclude 关键词过滤（对 group-title + 名称匹配）。

    keep_groups 中的分组为白名单：整组无条件保留，不参与 include/exclude
    关键词过滤（用于「这几个分类的节目全部保留」的场景）。
    """
    inc = cfg.get("include_keywords", [])
    exc = cfg.get("exclude_keywords", [])
    keep_groups = cfg.get("keep_groups", [])
    out = []
    for e in entries:
        grp = e.get("group", "")
        if keep_groups and any(g and g in grp for g in keep_groups):
            out.append(e)
            continue
        text = f"{grp} {e['name']}"
        if exc and any(k and k in text for k in exc):
            continue
        if inc and any(k and k in text for k in inc):
            out.append(e)
    return out


def apply_probe_filters(
    results: list[tuple[dict, tuple | None]], cfg: dict
) -> list[dict]:
    """按分辨率与响应时间筛选，给通过的条目附加 width/height/response_ms/ua/referer。

    res 三态：
    - (w, h, ms, ua, ref)         探测成功，w/h 为真实分辨率
    - (None, None, ms, ua, ref)   HTTP 可达但 ffprobe 无法确认分辨率（软 2xx）
    - None                        死链（网络错误 / 全部 4xx/5xx）

    白名单组 keep_groups：能确认分辨率且 < min_w×min_h 则去除；无法确认
    （软 2xx）则保留（疑罪从无，不因 ffprobe 解析不出而误杀）。死链一律剔除。
    其余条目照旧：须同时满足分辨率门禁与响应时间门禁。
    """
    min_w = cfg.get("min_width", 1920)
    min_h = cfg.get("min_height", 1080)
    max_ms = cfg.get("max_response_ms", 1000)
    keep_groups = cfg.get("keep_groups", [])
    out = []
    for e, res in results:
        if res is None:
            continue  # 死链，一律剔除
        w, h, ms, ua, ref = res
        grp = e.get("group", "")
        in_keep = keep_groups and any(g and g in grp for g in keep_groups)
        if in_keep:
            # 白名单：确认 <1080 去除，无法确认(软2xx)保留
            if w is None or h is None:
                out.append({
                    **e, "width": 0, "height": 0,
                    "response_ms": ms, "ua": ua, "referer": ref,
                })
            elif w >= min_w and h >= min_h:
                out.append({
                    **e, "width": w, "height": h,
                    "response_ms": ms, "ua": ua, "referer": ref,
                })
            # else: 确认分辨率 <1080 → 去除
        else:
            if w is None or h is None:
                continue  # 非白名单：无法确认分辨率按不达标处理
            if w >= min_w and h >= min_h and ms <= max_ms:
                out.append({
                    **e, "width": w, "height": h,
                    "response_ms": ms, "ua": ua, "referer": ref,
                })
    return out


def merge_groups(entries: list[dict], cfg: dict) -> list[dict]:
    """按配置把多国频道归并到统一分组（如美日韩英法 → 国际直播）。

    group_match 按「分组名」精确归并（避免误伤 name 里带国别的频道，
    如港澳台组的「纬来日本」）；name_match 作为散落频道的兜底（如
    俄罗斯组里的 France 24）。
    """
    merge = cfg.get("merge_international")
    if not merge or not merge.get("enabled"):
        return entries
    target = merge.get("group_name", "国际直播")
    gmatch = merge.get("group_match", [])
    nmatch = merge.get("name_match", [])
    for e in entries:
        grp = e.get("group", "")
        if gmatch and any(k and k in grp for k in gmatch):
            e["group"] = target
            continue
        name = e.get("name", "")
        if nmatch and any(k and k in name for k in nmatch):
            e["group"] = target
    return entries


def merge_and_sort(entries: list[dict]) -> list[dict]:
    """同名节目合并为多链接；分辨率降序，同分辨率按响应时间升序。

    每个链接保留自己的命中头 (ua, referer)，供按 URL 写 EXTVLCOPT。
    """
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
            "urls": [
                {
                    "url": it["url"],
                    "ua": it.get("ua", ""),
                    "referer": it.get("referer", ""),
                }
                for it in items
            ],
        })
    merged.sort(key=lambda m: (m["group"], m["name"]))
    return merged


def generate_m3u(merged: list[dict], embed_extvlcopt: bool = True) -> str:
    """生成 M3U 文本：一个 #EXTINF 名称 + 多行 URL（备用源）。

    embed_extvlcopt 为 True 时，为每个带命中头的 URL 前插入
    #EXTVLCOPT:http-user-agent / #EXTVLCOPT:http-referrer，
    VLC 及多数 IPTV 播放器会按此头发起请求。
    """
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
            if embed_extvlcopt and u.get("ua"):
                lines.append(f'#EXTVLCOPT:http-user-agent={u["ua"]}')
            if embed_extvlcopt and u.get("referer"):
                lines.append(f'#EXTVLCOPT:http-referrer={u["referer"]}')
            lines.append(u["url"])
    return "\n".join(lines) + "\n"
