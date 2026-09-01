"""轻量 REST API：管理源地址、关键词，触发运行。"""
import asyncio
import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

import main as runner

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"

app = FastAPI(title="直播源管理 API", version="1.0.0")

# 保持后台任务引用，避免被 GC
_tasks: set[asyncio.Task] = set()


def _load() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@app.get("/api/config")
def get_config():
    return _load()


@app.put("/api/config")
def set_config(cfg: dict = Body(...)):
    _save(cfg)
    return {"ok": True, "config": cfg}


@app.get("/api/sources")
def list_sources():
    return _load().get("sources", [])


@app.post("/api/sources")
def add_source(url: str = Body(..., embed=True)):
    cfg = _load()
    if url not in cfg.setdefault("sources", []):
        cfg["sources"].append(url)
        _save(cfg)
    return {"ok": True, "sources": cfg["sources"]}


@app.delete("/api/sources/{idx}")
def del_source(idx: int):
    cfg = _load()
    try:
        cfg["sources"].pop(idx)
        _save(cfg)
    except IndexError:
        raise HTTPException(404, "index out of range")
    return {"ok": True, "sources": cfg["sources"]}


def _keyword_ops(kind: str):
    key = f"{kind}_keywords"

    @app.post(f"/api/keywords/{kind}")
    def add_kw(kw: str = Body(..., embed=True)):
        cfg = _load()
        if kw not in cfg.setdefault(key, []):
            cfg[key].append(kw)
            _save(cfg)
        return {"ok": True, key: cfg[key]}

    @app.delete(f"/api/keywords/{kind}/{{idx}}")
    def del_kw(idx: int):
        cfg = _load()
        try:
            cfg[key].pop(idx)
            _save(cfg)
        except IndexError:
            raise HTTPException(404, "index out of range")
        return {"ok": True, key: cfg[key]}

    return add_kw, del_kw


add_inc, del_inc = _keyword_ops("include")
add_exc, del_exc = _keyword_ops("exclude")


@app.get("/api/keywords")
def list_keywords():
    cfg = _load()
    return {
        "include_keywords": cfg.get("include_keywords", []),
        "exclude_keywords": cfg.get("exclude_keywords", []),
    }


@app.post("/api/run")
async def run_now():
    task = asyncio.create_task(runner.run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"ok": True, "status": "started"}


@app.get("/api/runs")
def list_runs():
    log = BASE / "logs" / "runs.log"
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").splitlines()[-100:]


@app.get("/live.m3u")
def get_m3u():
    p = BASE / "live.m3u"
    if not p.exists():
        raise HTTPException(404, "尚未生成，请先运行")
    return FileResponse(p, media_type="application/vnd.apple.mpegurl")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
