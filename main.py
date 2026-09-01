"""总入口：拉取 -> 过滤 -> 探测 -> 生成 -> 推送。"""
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


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def run() -> dict:
    t0 = time.perf_counter()
    cfg = load_config()
    out_filename = cfg.get("output_filename", "live.m3u")

    print(f"[main] 开始，源地址 {len(cfg.get('sources', []))} 个")
    entries = await fetcher.fetch_all(cfg.get("sources", []))
    print(f"[main] 抓取去重后 {len(entries)} 条")

    kept = generator.filter_by_keywords(entries, cfg)
    print(f"[main] 关键词过滤后保留 {len(kept)} 条")

    cache: dict = {}
    results = await prober.probe_all(kept, cfg.get("concurrency", 30), cfg.get("ffprobe_timeout_s", 10), cache)
    ok = generator.apply_probe_filters(results, cfg)
    print(f"[main] 满足 分辨率>={cfg.get('min_width')}x{cfg.get('min_height')} 且 <={cfg.get('max_response_ms')}ms：{len(ok)} 条")

    merged = generator.merge_and_sort(ok)
    print(f"[main] 同名合并后 {len(merged)} 个节目")

    text = generator.generate_m3u(merged)
    out = BASE / out_filename
    out.write_text(text, encoding="utf-8")
    print(f"[main] 已生成 {out}（{len(text)} 字节，{len(merged)} 个节目）")

    changed = False
    if cfg.get("github_repo"):
        try:
            changed = pusher.push(str(BASE), out_filename, cfg.get("github_branch", "main"))
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
