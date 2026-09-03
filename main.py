"""总入口：拉取 -> 过滤 -> 探测 -> 生成 -> 推送。

多 UA/Referer 命中组合通过 hit_headers.json 跨次运行复用，
避免每次都从零遍历全部候选组合。
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import fetcher
import generator
import prober
import pusher

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
LOG_PATH = BASE / "logs" / "runs.log"

DEFAULT_USER_AGENTS = [
    "okHttp/Mod-1.5.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Dart/3.2.0 (dart:io)",
]
DEFAULT_REFERERS = ["", "https://live.445569.xyz/"]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_hit_headers(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: list(v) for k, v in data.items() if isinstance(v, list)}
    except Exception:  # noqa: BLE001
        return {}


def _save_hit_headers(path: Path, hit_headers: dict[str, list[str]]) -> None:
    try:
        path.write_text(
            json.dumps(hit_headers, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def _log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def run() -> dict:
    t0 = time.perf_counter()
    cfg = load_config()
    out_filename = cfg.get("output_filename", "live.m3u")

    user_agents = cfg.get("user_agents") or DEFAULT_USER_AGENTS
    referers = cfg.get("referers") if cfg.get("referers") is not None else DEFAULT_REFERERS
    embed_extvlcopt = cfg.get("embed_extvlcopt", True)
    hit_cache_path = BASE / cfg.get("hit_headers_cache", "hit_headers.json")
    hit_headers = _load_hit_headers(hit_cache_path)

    print(f"[main] 开始，源地址 {len(cfg.get('sources', []))} 个，"
          f"UA {len(user_agents)} 个，Referer {len(referers)} 个")
    entries = await fetcher.fetch_all(cfg.get("sources", []), user_agents, referers)
    print(f"[main] 抓取去重后 {len(entries)} 条")

    kept = generator.filter_by_keywords(entries, cfg)
    print(f"[main] 关键词过滤后保留 {len(kept)} 条")

    cache: dict = {}
    results = await prober.probe_all(
        kept,
        cfg.get("concurrency", 30),
        cfg.get("ffprobe_timeout_s", 10),
        cache,
        user_agents,
        referers,
        hit_headers,
    )
    _save_hit_headers(hit_cache_path, hit_headers)

    ok = generator.apply_probe_filters(results, cfg)
    print(f"[main] 探测筛选后保留 {len(ok)} 条")

    ok = generator.merge_groups(ok, cfg)
    merged = generator.merge_and_sort(ok)
    print(f"[main] 同名合并后 {len(merged)} 个节目")

    text = generator.generate_m3u(merged, embed_extvlcopt)
    out = BASE / out_filename
    out.write_text(text, encoding="utf-8")
    print(f"[main] 已生成 {out}（{len(text)} 字节，{len(merged)} 个节目）")

    changed = False
    if cfg.get("github_repo"):
        try:
            changed = pusher.push(
                str(BASE), out_filename, cfg.get("github_branch", "main")
            )
            print(f"[main] GitHub 推送：{'已更新' if changed else '无变化'}")
        except Exception as ex:  # noqa: BLE001
            print(f"[main] GitHub 推送失败：{ex}")

    elapsed = time.perf_counter() - t0
    summary = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "sources": len(cfg.get("sources", [])),
        "fetched": len(entries),
        "kept_by_keyword": len(kept),
        "passed_probe": len(ok),
        "merged": len(merged),
        "hit_headers": len(hit_headers),
        "elapsed_s": round(elapsed, 1),
        "pushed": changed,
    }
    _log(json.dumps(summary, ensure_ascii=False))
    print(f"[main] 完成，耗时 {elapsed:.1f}s")
    return summary


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
