"""fizzmineOS hub: receives agent reports over WebSocket and serves the dashboard."""
import asyncio
import hmac
import json
import logging
import os
import time
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .store import Store

log = logging.getLogger("fizzhub")
OFFLINE_AFTER_S = 20

cfg_path = os.environ.get("FIZZHUB_CONFIG", "/etc/fizzmine/hub.toml")
with open(cfg_path, "rb") as f:
    cfg = tomllib.load(f)
tokens = cfg.get("rigs", {})
store = Store(os.environ.get("FIZZHUB_DB", cfg.get("db", "/var/lib/fizzmine/hub.db")))


async def prune_forever():
    while True:
        store.prune()
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(prune_forever())
    yield
    task.cancel()


app = FastAPI(title="fizzmineOS hub", lifespan=lifespan)


@app.websocket("/ws/agent")
async def agent_socket(ws: WebSocket, rig: str, token: str):
    if not hmac.compare_digest(tokens.get(rig, ""), token) or not token:
        await ws.close(code=4401)
        log.warning("rejected agent %r: bad token", rig)
        return
    await ws.accept()
    log.info("agent %s connected", rig)
    try:
        while True:
            snap = json.loads(await ws.receive_text())
            if snap.get("type") == "stats":
                snap["rig"] = rig  # trust the authenticated name, not the payload
                snap["ts"] = time.time()
                store.add(snap)
    except WebSocketDisconnect:
        log.info("agent %s disconnected", rig)


@app.get("/api/rigs")
def rigs():
    now = time.time()
    seen = {s["rig"]: s for s in store.latest()}
    out = []
    for name in sorted(set(tokens) | set(seen)):
        snap = seen.get(name)
        out.append({
            "rig": name,
            "online": bool(snap) and now - snap["ts"] < OFFLINE_AFTER_S,
            "last_seen": snap["ts"] if snap else None,
            "gpus": snap["gpus"] if snap else [],
            "miner": snap["miner"] if snap else {"running": False},
        })
    return out


@app.get("/api/rigs/{rig}/history")
def history(rig: str, hours: float = 24):
    if rig not in tokens:
        raise HTTPException(404, "unknown rig")
    return store.history(rig, min(hours, 24 * 7))


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
