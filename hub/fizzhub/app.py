"""fizzmineOS hub: receives agent reports over WebSocket and serves the dashboard."""
import asyncio
import hmac
import io
import json
import logging
import os
import re
import tarfile
import time
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .store import Store

log = logging.getLogger("fizzhub")
OFFLINE_AFTER_S = 20

cfg_path = os.environ.get("FIZZHUB_CONFIG", "/etc/fizzmine/hub.toml")
with open(cfg_path, "rb") as f:
    cfg = tomllib.load(f)
static_tokens = cfg.get("rigs", {})  # optional hand-made tokens; enrolled rigs live in the database
enroll_key = cfg.get("enroll_key", "")
store = Store(os.environ.get("FIZZHUB_DB", cfg.get("db", "/var/lib/fizzmine/hub.db")))
# the agent source the hub hands to new rigs; in Docker it is copied into the image
agent_dir = Path(os.environ.get("FIZZHUB_AGENT_DIR", Path(__file__).resolve().parents[2] / "agent"))
RIG_NAME = re.compile(r"[A-Za-z0-9_-]{1,32}")


def tokens():
    return {**static_tokens, **store.rig_tokens()}


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
    if not token or not hmac.compare_digest(tokens().get(rig, ""), token):
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
    for name in sorted(set(tokens()) | set(seen)):
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
    if rig not in tokens():
        raise HTTPException(404, "unknown rig")
    return store.history(rig, min(hours, 24 * 7))


class Enrollment(BaseModel):
    key: str
    rig: str
    info: dict = {}


@app.post("/api/enroll")
def enroll(e: Enrollment):
    if not enroll_key or not hmac.compare_digest(enroll_key, e.key):
        raise HTTPException(403, "wrong enroll key")
    if not RIG_NAME.fullmatch(e.rig):
        raise HTTPException(400, "rig name: letters, digits, - and _ only, up to 32")
    log.info("enrolled rig %s", e.rig)
    return {"rig": e.rig, "token": store.enroll(e.rig, e.info)}


@app.get("/install.sh", response_class=PlainTextResponse)
def install_script(request: Request):
    """The rig installer, with this hub's address filled in: curl http://<hub>:8780/install.sh | sudo bash -s -- --key ..."""
    hub = str(request.base_url).rstrip("/")
    return (agent_dir / "install-rig.sh").read_text().replace("__HUB_URL__", hub)


@app.get("/agent.tar.gz")
def agent_bundle():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(agent_dir, arcname="agent", filter=lambda t: None if "__pycache__" in t.name else t)
    return Response(buf.getvalue(), media_type="application/gzip")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
