"""SQLite history of rig snapshots. One row per rig per report, kept for KEEP_DAYS."""
import json
import secrets
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
        self.db.execute("CREATE TABLE IF NOT EXISTS rigs (name TEXT PRIMARY KEY, token TEXT, enrolled_at REAL, info TEXT)")
        # saved setups: kind is "flight_sheet" or "oc_profile"
        self.db.execute("CREATE TABLE IF NOT EXISTS saved (kind TEXT, name TEXT, data TEXT, PRIMARY KEY (kind, name))")
        # watchdog and hub events (what went to Discord)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, rig TEXT, level TEXT, msg TEXT)")

    def add_event(self, rig, level, msg, ts=None):
        self.db.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (ts or time.time(), rig, level, msg))
        self.db.commit()

    def events(self, limit=50):
        rows = self.db.execute("SELECT ts, rig, level, msg FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"ts": ts, "rig": rig, "level": level, "msg": msg} for ts, rig, level, msg in rows]

    def save(self, kind, name, data):
        self.db.execute("INSERT OR REPLACE INTO saved VALUES (?, ?, ?)", (kind, name, json.dumps(data)))
        self.db.commit()

    def saved(self, kind):
        return [json.loads(d) for (d,) in self.db.execute("SELECT data FROM saved WHERE kind = ? ORDER BY name", (kind,))]

    def get_saved(self, kind, name):
        row = self.db.execute("SELECT data FROM saved WHERE kind = ? AND name = ?", (kind, name)).fetchone()
        return json.loads(row[0]) if row else None

    def delete_saved(self, kind, name):
        self.db.execute("DELETE FROM saved WHERE kind = ? AND name = ?", (kind, name))
        self.db.commit()

    def enroll(self, name, info):
        """Registers a rig (or re-registers it after a reinstall) and returns its new token."""
        token = secrets.token_hex(16)
        self.db.execute("INSERT OR REPLACE INTO rigs VALUES (?, ?, ?, ?)", (name, token, time.time(), json.dumps(info)))
        self.db.commit()
        return token

    def rig_tokens(self):
        return dict(self.db.execute("SELECT name, token FROM rigs"))

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
        self.db.execute("DELETE FROM events WHERE ts < ?", (time.time() - 30 * 86400,))
        self.db.commit()
