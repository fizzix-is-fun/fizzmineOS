import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "hub")]

from fizzagent.gpu import FakeGpus  # noqa: E402
from fizzagent.miner import FakeMiner, parse_wildrig  # noqa: E402


def test_parse_wildrig():
    out = parse_wildrig({
        "version": "1.0.0l", "algo": "pearlhash",
        "hashrate": {"total": [184e12, 183e12, None], "threads": [[92e12, 91e12], [92e12, 92e12]]},
        "results": {"shares_good": 98, "shares_total": 100},
        "connection": {"uptime": 600},
    })
    assert out["hashrate_hs"] == 184e12
    assert out["card_hashrate_hs"] == [92e12, 92e12]
    assert out["shares_accepted"] == 98 and out["shares_rejected"] == 2
    assert out["uptime_s"] == 600


def test_parse_wildrig_missing_fields():
    out = parse_wildrig({})
    assert out["running"] and out["hashrate_hs"] is None and out["shares_rejected"] is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "hub.toml"
    cfg.write_text('enroll_key = "joinme"\n[rigs]\nbabyarm = "secret"\narmadillo = "other"\n')
    monkeypatch.setenv("FIZZHUB_CONFIG", str(cfg))
    monkeypatch.setenv("FIZZHUB_DB", str(tmp_path / "hub.db"))
    import fizzhub.app
    app = importlib.reload(fizzhub.app).app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def snap(rig="whoever"):
    return {"type": "stats", "rig": rig, "ts": 0, "gpus": FakeGpus().read(), "miner": FakeMiner().read()}


def test_agent_report_shows_on_dashboard(client):
    with client.websocket_connect("/ws/agent?rig=babyarm&token=secret") as ws:
        ws.send_json(snap("spoofed"))
        ws.send_json(snap("spoofed"))
    rigs = {r["rig"]: r for r in client.get("/api/rigs").json()}
    assert set(rigs) == {"babyarm", "armadillo"}  # payload's rig name is ignored
    assert rigs["babyarm"]["online"] and len(rigs["babyarm"]["gpus"]) == 2
    assert not rigs["armadillo"]["online"] and rigs["armadillo"]["gpus"] == []
    hist = client.get("/api/rigs/babyarm/history").json()
    assert len(hist) == 2 and hist[0]["hashrate_hs"] > 0 and hist[0]["power_w"] > 0


def test_bad_token_rejected(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/agent?rig=babyarm&token=wrong") as ws:
            ws.receive_text()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/agent?rig=nobody&token=") as ws:
            ws.receive_text()
    assert all(not r["online"] for r in client.get("/api/rigs").json())


def test_dashboard_served(client):
    assert "fizzmineOS" in client.get("/").text
    assert client.get("/api/rigs/nobody/history").status_code == 404


def test_enroll_gives_working_token(client):
    assert client.post("/api/enroll", json={"key": "wrong", "rig": "oldboard1"}).status_code == 403
    assert client.post("/api/enroll", json={"key": "joinme", "rig": "bad name!"}).status_code == 400
    token = client.post("/api/enroll", json={"key": "joinme", "rig": "oldboard1", "info": {"cpu": "G530"}}).json()["token"]
    with client.websocket_connect(f"/ws/agent?rig=oldboard1&token={token}") as ws:
        ws.send_json(snap())
    rigs = {r["rig"]: r for r in client.get("/api/rigs").json()}
    assert rigs["oldboard1"]["online"]
    # re-enrolling (a reinstall) replaces the old token
    new = client.post("/api/enroll", json={"key": "joinme", "rig": "oldboard1"}).json()["token"]
    assert new != token


def test_installer_and_agent_bundle(client):
    import io
    import tarfile
    script = client.get("/install.sh").text
    assert 'HUB="http://testserver"' in script and "__HUB_URL__" not in script
    names = tarfile.open(fileobj=io.BytesIO(client.get("/agent.tar.gz").content)).getnames()
    assert "agent/fizzagent/__main__.py" in names and "agent/requirements.txt" in names
    assert not any("__pycache__" in n for n in names)


# captured from WildRig-NG 1.0.0l on babyarmadilloai (2x RTX 5060 Ti), 2026-10-21
WILDRIG_NG = {"algo": "pearlhash", "connection": {"error_log": [], "failures": 0, "ping": 376, "pool": "pool.pearlhash.xyz:9000", "uptime": 465}, "donate_level": 0, "hashrate": {"threads": [[89044043115986, 89043485055611, 0], [87668518888619, 87635445107966, 0]], "total": [176712562004605, 176678930163577, 0]}, "hwmon": {"busID": [1, 3], "cclk": [2550, 2550], "fan": [48, 36], "mclk": [810, 810], "power": [96, 94], "temp": [65, 58]}, "id": "4e8b610a77c6d4f3", "kind": "wildrig-ng", "results": {"avg_time": 0, "best": [], "diff_current": 36028797018963968, "error_log": [], "hashes_total": 0, "shares_accepted": [2, 3], "shares_good": 5, "shares_ignored": [0, 0], "shares_rejected": [0, 0], "shares_total": 5}, "ua": "WildRig/1.0.0l", "uptime": 466, "version": "1.0.0l", "worker_id": "babyarmadilloai"}


def test_parse_real_wildrig_ng():
    from fizzagent.miner import attach_to_gpus
    m = parse_wildrig(WILDRIG_NG)
    assert m["miner"] == "wildrig-ng" and m["pool"] == "pool.pearlhash.xyz:9000"
    assert m["hashrate_hs"] == 176712562004605 and m["shares_accepted"] == 5 and m["shares_rejected"] == 0
    # NVML lists the cards in its own order; the miner's numbers must follow the PCI bus
    gpus = [{"index": 0, "bus_id": "00000000:03:00.0"}, {"index": 1, "bus_id": "00000000:01:00.0"}]
    attach_to_gpus(gpus, m)
    assert gpus[0]["hashrate_hs"] == 87668518888619 and gpus[0]["shares_accepted"] == 3
    assert gpus[1]["hashrate_hs"] == 89044043115986 and gpus[1]["shares_rejected"] == 0


def test_attach_skips_cards_the_miner_does_not_use():
    from fizzagent.miner import attach_to_gpus
    gpus = [{"index": 0, "bus_id": "00000000:05:00.0"}]
    attach_to_gpus(gpus, parse_wildrig(WILDRIG_NG))
    assert "hashrate_hs" not in gpus[0]


def test_short_brand():
    from fizzagent.gpu import short_brand
    assert short_brand("Micro-Star International Co., Ltd. [MSI]") == "MSI"
    assert short_brand("ASUSTeK Computer Inc.") == "ASUS"
    assert short_brand("Gigabyte Technology Co., Ltd") == "Gigabyte"
    assert short_brand("ZOTAC International (MCO) Ltd.") == "ZOTAC"
    assert short_brand("Palit Microsystems Inc.") == "Palit"
    assert short_brand("NVIDIA Corporation") == "NVIDIA"
    assert short_brand("Red Hat, Inc.") == "Red Hat"
    assert short_brand(None) is None


# ---------- controls ----------

def make_agent(tmp_path):
    from fizzagent.__main__ import Agent
    return Agent({"rig": "babyarm"}, fake=True, state_path=tmp_path / "state.json")


FS = {"name": "pearl", "miner": "wildrig-ng", "miner_path": "/opt/x", "algo": "pearlhash",
      "pool": "pool.pearlhash.xyz:9000", "wallet": "prl1abc@nock=xyz", "worker": "%RIG%", "extra_args": "--pearlhash-kernel 2"}


def test_build_args_fills_placeholders():
    from fizzagent.runner import build_args
    args = build_args(FS, "babyarm")
    assert args == ["--algo", "pearlhash", "--url", "pool.pearlhash.xyz:9000", "--user", "prl1abc@nock=xyz",
                    "--worker", "babyarm", "--api-port", "60050", "--pearlhash-kernel", "2"]
    custom = build_args({"miner": "custom", "custom_args": "-a {algo} -u {wallet}.{worker}", "algo": "kawpow", "wallet": "W"}, "r1")
    assert custom == ["-a", "kawpow", "-u", "W.r1"]


def test_oc_saved_and_applied_only_while_mining(tmp_path):
    a = make_agent(tmp_path)
    u0 = a.cards()[0]
    assert a.handle("set_oc", {"cards": {u0: {"power_w": "140", "core_lock_mhz": 2685, "core_offset_mhz": 495}}}).startswith("saved")
    assert a.gpus.oc[0] == {}  # not mining yet: nothing touched
    a.handle("flight_sheet", {"flight_sheet": FS, "start": True})
    assert a.state["mode"] == "mining" and a.runner.running()
    assert a.gpus.oc[0] == {"power_w": 140, "core_lock_mhz": 2685, "core_offset_mhz": 495}
    snap = a.snapshot()
    assert snap["agent"] == {"mode": "mining", "flight_sheet": "pearl", "miner_running": True, "ai": {"start_cmd": "", "stop_cmd": ""}}
    assert snap["gpus"][0]["oc"]["power_w"] == 140 and snap["gpus"][1]["oc"] is None
    # clearing a card's OC puts it back to stock right away
    a.handle("set_oc", {"cards": {u0: None}})
    assert a.gpus.oc[0] == {} and u0 not in a.state["oc"]


def test_ai_mode_stops_miner_and_resets_clocks(tmp_path):
    a = make_agent(tmp_path)
    u0 = a.cards()[0]
    a.handle("set_oc", {"cards": {u0: {"power_w": 140, "fan_mode": "fixed", "fan_value": 80}}})
    a.handle("flight_sheet", {"flight_sheet": FS, "start": True})
    a.tick()
    assert a.gpus.fan[0] == 80
    a.handle("set_mode", {"mode": "ai"})
    assert a.state["mode"] == "ai" and not a.runner.running()
    assert a.gpus.oc[0] == {} and a.gpus.fan[0] is None  # stock clocks, fans back on auto
    a.handle("set_mode", {"mode": "mining"})
    assert a.runner.running() and a.gpus.oc[0]["power_w"] == 140


def test_state_survives_restart(tmp_path):
    a = make_agent(tmp_path)
    a.handle("flight_sheet", {"flight_sheet": FS, "start": True})
    b = make_agent(tmp_path)  # a reboot
    assert not b.runner.running()
    b.boot()
    assert b.runner.running() and b.state["flight_sheet"]["name"] == "pearl"
    b.handle("miner_stop", {})
    c = make_agent(tmp_path)
    c.boot()
    assert not c.runner.running()  # stopped on purpose stays stopped


def test_bad_fan_mode_rejected(tmp_path):
    a = make_agent(tmp_path)
    with pytest.raises(ValueError):
        a.handle("set_oc", {"cards": {a.cards()[0]: {"fan_mode": "turbo"}}})


def test_fan_target_loop():
    from fizzagent.fans import FanController
    from fizzagent.gpu import FakeGpus
    g = FakeGpus()
    f = FanController(g)
    f.tick({0: {"fan_mode": "target", "fan_value": 60}})  # fake temp is 66, so fans speed up
    first = g.fan[0]
    f.tick({0: {"fan_mode": "target", "fan_value": 60}})
    assert g.fan[0] > first
    f.tick({0: {"fan_mode": "auto"}})
    assert g.fan[0] is None


def test_login_required_when_password_set(tmp_path, monkeypatch):
    cfg = tmp_path / "hub.toml"
    cfg.write_text('enroll_key = "joinme"\nadmin_password = "hunter2"\n[rigs]\n')
    monkeypatch.setenv("FIZZHUB_CONFIG", str(cfg))
    monkeypatch.setenv("FIZZHUB_DB", str(tmp_path / "hub.db"))
    import fizzhub.app
    app = importlib.reload(fizzhub.app).app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        assert c.get("/api/rigs").status_code == 401
        assert c.get("/api/me").json() == {"logged_in": False, "password_set": True}
        assert c.post("/api/login", json={"password": "nope"}).status_code == 403
        # rigs can still enroll and fetch the installer without logging in
        assert c.post("/api/enroll", json={"key": "joinme", "rig": "r1"}).status_code == 200
        assert c.get("/install.sh").status_code == 200
        assert c.post("/api/login", json={"password": "hunter2"}).status_code == 200
        assert c.get("/api/rigs").status_code == 200


def test_commands_reach_the_agent_and_come_back(client):
    import threading
    token = client.post("/api/enroll", json={"key": "joinme", "rig": "r1"}).json()["token"]
    with client.websocket_connect(f"/ws/agent?rig=r1&token={token}") as ws:
        ws.send_json({"type": "stats", "gpus": [{"index": 0, "uuid": "GPU-a", "name": "NVIDIA GeForce RTX 5060 Ti"}], "miner": {}})

        def fake_agent():
            for _ in range(3):
                msg = ws.receive_json()
                ws.send_json({"type": "result", "id": msg["id"], "ok": True, "msg": f"did {msg['cmd']} {sorted(msg['args'])}"})

        t = threading.Thread(target=fake_agent)
        t.start()
        assert client.post("/api/rigs/r1/cmd", json={"cmd": "miner_restart"}).json() == {"rig": "r1", "ok": True, "msg": "did miner_restart []"}
        client.put("/api/flight-sheets/pearl", json=FS)
        assert [s["name"] for s in client.get("/api/flight-sheets").json()] == ["pearl"]
        res = client.post("/api/flight-sheets/pearl/push", json={"rigs": ["r1", "armadillo"]}).json()
        assert res[0]["ok"] and "flight_sheet" in res[0]["msg"]
        assert res[1] == {"rig": "armadillo", "ok": False, "msg": "rig is offline"}
        client.put("/api/oc-profiles/5060", json={"gpu_model": "NVIDIA GeForce RTX 5060 Ti", "oc": {"power_w": 140}})
        res = client.post("/api/oc-profiles/5060/apply").json()
        assert res == [{"rig": "r1", "ok": True, "msg": "did set_oc ['cards']"}]
        t.join(timeout=5)
    assert client.post("/api/rigs/r1/cmd", json={"cmd": "rm -rf"}).status_code == 400
    assert client.post("/api/rigs/r1/cmd", json={"cmd": "reboot"}).json()["msg"] == "rig is offline"


def test_read_log_follows_and_survives_rotation(tmp_path):
    from fizzagent.runner import read_log
    log = tmp_path / "miner.log"
    assert read_log(None, path=log) == {"text": "", "offset": 0}
    log.write_text("a" * 100 + "\nstarted\n")
    first = read_log(None, path=log, tail=8)
    assert first["text"] == "started\n" and first["offset"] == 109
    with open(log, "a") as f:
        f.write("hashrate 93 TH/s\n")
    assert read_log(first["offset"], path=log)["text"] == "hashrate 93 TH/s\n"
    log.write_text("new\n")  # rotated: the old offset is past the end
    assert read_log(500, path=log)["text"] == "new\n"


def test_console_reads_the_miner_log(client, tmp_path):
    import threading
    from fizzagent.__main__ import Agent
    agent = Agent({"rig": "r1"}, fake=True, state_path=tmp_path / "state.json")
    agent.log_path = tmp_path / "miner.log"
    agent.log_path.write_text("\x1b[32mshare accepted\x1b[0m\n")
    token = client.post("/api/enroll", json={"key": "joinme", "rig": "r1"}).json()["token"]
    with client.websocket_connect(f"/ws/agent?rig=r1&token={token}") as ws:
        ws.send_json({"type": "stats", "gpus": [], "miner": {}})

        def fake_agent():
            msg = ws.receive_json()
            ws.send_json({"type": "result", "id": msg["id"], "ok": True, "msg": agent.handle(msg["cmd"], msg["args"])})

        t = threading.Thread(target=fake_agent)
        t.start()
        out = client.get("/api/rigs/r1/log").json()
        t.join(timeout=5)
    assert out == {"text": "\x1b[32mshare accepted\x1b[0m\n", "offset": 24, "running": False}
    assert client.get("/api/rigs/r1/log?offset=0").status_code == 503  # rig gone
    assert client.get("/console.html").status_code == 200


def test_miner_screen_and_keys(tmp_path):
    from fizzagent.__main__ import Agent
    agent = Agent({"rig": "r1"}, fake=True, state_path=tmp_path / "state.json")
    assert agent.handle("miner_screen", {}) == {"screen": None, "running": False}
    agent.runner.start({})
    shown = agent.handle("miner_screen", {})
    assert shown["running"] and "TH/s" in shown["screen"]
    assert agent.handle("miner_keys", {"keys": ["h", "Enter"]}) == "sent"
    with pytest.raises(ValueError):
        agent.handle("miner_keys", {"keys": ["C-c"]})
