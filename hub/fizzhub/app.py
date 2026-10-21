"""fizzmineOS hub: receives agent reports over WebSocket, sends them commands, and serves the dashboard."""
import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
import tarfile
import time
import tomllib
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response
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
admin_password = cfg.get("admin_password", "")
SESSION_COOKIE = "fizz_session"
# paths that work without logging in: the rig installer, enrollment (has its own key), agents (own tokens)
OPEN_PATHS = ("/api/login", "/api/me", "/api/enroll", "/install.sh", "/agent.tar.gz", "/ws/")
CMD_TIMEOUT_S = 180  # a flight sheet may download a miner first
connections = {}  # rig name -> its agent's open WebSocket
pending = {}      # command id -> future waiting for the agent's result


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


def session_value():
    return hmac.new(admin_password.encode(), b"fizzmine-session", hashlib.sha256).hexdigest()


def logged_in(request):
    if not admin_password:  # no password set: open, as before
        return True
    return hmac.compare_digest(request.cookies.get(SESSION_COOKIE, ""), session_value())


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(OPEN_PATHS) and not logged_in(request):
        return JSONResponse({"detail": "log in first"}, status_code=401)
    return await call_next(request)


class Login(BaseModel):
    password: str


@app.post("/api/login")
def login(body: Login):
    if not admin_password or not hmac.compare_digest(body.password, admin_password):
        raise HTTPException(403, "wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, session_value(), max_age=30 * 86400, httponly=True, samesite="strict")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/me")
def me(request: Request):
    return {"logged_in": logged_in(request), "password_set": bool(admin_password)}


@app.websocket("/ws/agent")
async def agent_socket(ws: WebSocket, rig: str, token: str):
    if not token or not hmac.compare_digest(tokens().get(rig, ""), token):
        await ws.close(code=4401)
        log.warning("rejected agent %r: bad token", rig)
        return
    await ws.accept()
    log.info("agent %s connected", rig)
    connections[rig] = ws
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "stats":
                msg["rig"] = rig  # trust the authenticated name, not the payload
                msg["ts"] = time.time()
                store.add(msg)
            elif msg.get("type") == "result" and msg.get("id") in pending:
                pending.pop(msg["id"]).set_result(msg)
    except WebSocketDisconnect:
        log.info("agent %s disconnected", rig)
    finally:
        if connections.get(rig) is ws:
            del connections[rig]


async def send_command(rig, cmd, args=None):
    ws = connections.get(rig)
    if not ws:
        return {"rig": rig, "ok": False, "msg": "rig is offline"}
    cid = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    pending[cid] = fut
    try:
        await ws.send_text(json.dumps({"type": "cmd", "id": cid, "cmd": cmd, "args": args or {}}))
        res = await asyncio.wait_for(fut, CMD_TIMEOUT_S)
        return {"rig": rig, "ok": res.get("ok", False), "msg": res.get("msg", "")}
    except asyncio.TimeoutError:
        return {"rig": rig, "ok": False, "msg": "no answer from the rig"}
    except Exception as e:
        return {"rig": rig, "ok": False, "msg": f"couldn't reach the rig: {e}"}
    finally:
        pending.pop(cid, None)


class Command(BaseModel):
    cmd: str
    args: dict = {}


RIG_COMMANDS = {"set_oc", "flight_sheet", "miner_start", "miner_stop", "miner_restart", "set_mode", "set_ai", "reboot"}


@app.post("/api/rigs/{rig}/cmd")
async def rig_command(rig: str, c: Command):
    if c.cmd not in RIG_COMMANDS:
        raise HTTPException(400, "unknown command")
    return await send_command(rig, c.cmd, c.args)


@app.get("/api/rigs/{rig}/log")
async def rig_log(rig: str, offset: int | None = None):
    """Miner output for the console page: what's new since `offset`."""
    res = await send_command(rig, "miner_log", {"offset": offset})
    if not res["ok"]:
        raise HTTPException(503, res["msg"])
    return res["msg"]


@app.get("/api/rigs/{rig}/screen")
async def rig_screen(rig: str):
    """The miner's terminal as it looks right now."""
    res = await send_command(rig, "miner_screen")
    if not res["ok"]:
        raise HTTPException(503, res["msg"])
    return res["msg"]


class Keys(BaseModel):
    keys: list[str]


@app.post("/api/rigs/{rig}/keys")
async def rig_keys(rig: str, k: Keys):
    """Keys typed in the console go to the miner, like typing in its window."""
    return await send_command(rig, "miner_keys", {"keys": k.keys[:32]})


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
            "agent": snap.get("agent", {}) if snap else {},
        })
    return out


@app.get("/api/rigs/{rig}/history")
def history(rig: str, hours: float = 24):
    if rig not in tokens():
        raise HTTPException(404, "unknown rig")
    return store.history(rig, min(hours, 24 * 7))


# --- flight sheets and OC profiles, kept on the hub and pushed to rigs

KINDS = {"flight-sheets": "flight_sheet", "oc-profiles": "oc_profile"}


def kind_of(kind):
    if kind not in KINDS:
        raise HTTPException(404)
    return KINDS[kind]


@app.get("/api/{kind}")
def list_saved(kind: str):
    return store.saved(kind_of(kind))


@app.put("/api/{kind}/{name}")
def put_saved(kind: str, name: str, data: dict):
    if not name.strip() or len(name) > 64:
        raise HTTPException(400, "name: 1 to 64 characters")
    data["name"] = name
    store.save(kind_of(kind), name, data)
    return data


@app.delete("/api/{kind}/{name}")
def delete_saved(kind: str, name: str):
    store.delete_saved(kind_of(kind), name)
    return {"ok": True}


class Push(BaseModel):
    rigs: list[str]
    start: bool = True


@app.post("/api/flight-sheets/{name}/push")
async def push_flight_sheet(name: str, p: Push):
    fs = store.get_saved("flight_sheet", name)
    if not fs:
        raise HTTPException(404, "no such flight sheet")
    return await asyncio.gather(*(send_command(r, "flight_sheet", {"flight_sheet": fs, "start": p.start}) for r in p.rigs))


@app.post("/api/oc-profiles/{name}/apply")
async def apply_oc_profile(name: str):
    """Applies the profile to every card of its model on every online rig."""
    prof = store.get_saved("oc_profile", name)
    if not prof:
        raise HTTPException(404, "no such OC profile")
    jobs = []
    for snap in store.latest():
        cards = {g["uuid"]: prof["oc"] for g in snap["gpus"] if g.get("name") == prof["gpu_model"] and g.get("uuid")}
        if cards and snap["rig"] in connections:
            jobs.append(send_command(snap["rig"], "set_oc", {"cards": cards}))
    results = await asyncio.gather(*jobs)
    return results or [{"rig": "-", "ok": False, "msg": f"no online rig has a {prof['gpu_model']}"}]


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
