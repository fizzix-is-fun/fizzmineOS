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
    cfg.write_text('[rigs]\nbabyarm = "secret"\narmadillo = "other"\n')
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
