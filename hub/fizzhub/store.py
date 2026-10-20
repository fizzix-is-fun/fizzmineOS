"""SQLite history of rig snapshots. One row per rig per report, kept for KEEP_DAYS."""
import json
import sqlite3
import time

KEEP_DAYS = 7


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS samples (rig TEXT, ts REAL, hashrate REAL, power REAL, max_temp REAL, data TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS samples_rig_ts ON samples (rig, ts)")

    def add(self, snap):
        gpus = snap.get("gpus") or []
        miner = snap.get("miner") or {}
        power = sum(g.get("power_w") or 0 for g in gpus)
        temps = [g["temp_c"] for g in gpus if g.get("temp_c") is not None]
        self.db.execute("INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?)", (
            snap["rig"], snap["ts"], miner.get("hashrate_hs") if miner.get("running") else None,
            power, max(temps, default=None), json.dumps(snap)))
        self.db.commit()

    def latest(self):
        rows = self.db.execute(
            "SELECT data FROM samples s WHERE ts = (SELECT MAX(ts) FROM samples WHERE rig = s.rig) ORDER BY rig")
        return [json.loads(d) for (d,) in rows]

    def history(self, rig, hours):
        rows = self.db.execute(
            "SELECT ts, hashrate, power, max_temp FROM samples WHERE rig = ? AND ts > ? ORDER BY ts",
            (rig, time.time() - hours * 3600))
        return [{"ts": ts, "hashrate_hs": hr, "power_w": p, "max_temp_c": t} for ts, hr, p, t in rows]

    def prune(self):
        self.db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        self.db.commit()
