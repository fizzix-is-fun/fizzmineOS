"""Watchdog: keeps a mining rig mining and its cards cool, without the hub.

Hashrate: after the miner has had time to start, a hashrate under the floor (or none at all) for a few
minutes restarts the miner; when restarts don't help, the rig reboots. If it's still failing within an
hour of a watchdog reboot, it gives up and says so, rather than rebooting in a loop.

Temperature: a card at the limit stops the miner; it starts again when every card has cooled down.
"""

DEFAULTS = {
    "enabled": True,
    "min_hashrate_ths": 0,         # 0: only act when there is no hashrate at all
    "grace_s": 300,                # time a miner gets to start hashing
    "low_for_s": 180,              # how long the hashrate must stay low before acting
    "restarts_before_reboot": 2,
    "temp_limit_c": 80,
    "temp_resume_c": 65,
}
REBOOT_GUARD_S = 3600


def clean_settings(s):
    out = dict(DEFAULTS)
    for k, v in (s or {}).items():
        if k not in DEFAULTS or v in (None, ""):
            continue
        out[k] = bool(v) if k == "enabled" else float(v) if k == "min_hashrate_ths" else int(v)
    if out["temp_resume_c"] >= out["temp_limit_c"]:
        raise ValueError("resume temperature must be below the limit")
    return out


class Watchdog:
    def __init__(self, state):
        """state: the agent's state dict; settings live in state["watchdog"], the last reboot in state["watchdog_reboot_ts"]."""
        self.state = state
        self.low_since = None
        self.restarts = 0
        self.hot = False       # miner stopped for temperature
        self.gave_up = False
        self.status = "ok"

    @property
    def settings(self):
        return clean_settings(self.state.get("watchdog"))

    def reset(self):
        self.low_since, self.restarts, self.hot, self.gave_up, self.status = None, 0, False, False, "ok"

    def tick(self, now, mining, miner_running, started_at, hashrate_hs, temps):
        """One look at the rig. Returns a list of (action, level, message):
        action is "stop", "start", "restart", "reboot" or None (just tell); level is "alert" or "info"."""
        s = self.settings
        if not s["enabled"] or not mining:
            self.reset()
            return []
        hottest = max((t for t in temps if t is not None), default=None)

        if self.hot:
            if hottest is not None and hottest <= s["temp_resume_c"]:
                self.hot = False
                self.status = "ok"
                return [("start", "info", f"cooled to {hottest} °C, mining again")]
            return []
        if hottest is not None and hottest >= s["temp_limit_c"]:
            self.hot = True
            self.low_since = None
            self.status = f"paused: a card hit {hottest} °C (resumes at {s['temp_resume_c']} °C)"
            return [("stop", "alert", f"a card hit {hottest} °C (limit {s['temp_limit_c']} °C): miner stopped until every card is at {s['temp_resume_c']} °C")]

        if not miner_running:
            self.restarts += 1
            return [("start", "alert", "the miner had exited; started it again")]
        if started_at is None or now - started_at < s["grace_s"]:
            return []

        floor = max(s["min_hashrate_ths"] * 1e12, 1)
        if hashrate_hs is not None and hashrate_hs >= floor:
            if self.gave_up or self.restarts:
                msg = f"hashrate is back ({hashrate_hs / 1e12:.1f} TH/s)"
                self.reset()
                return [(None, "info", msg)]
            self.low_since = None
            return []

        if self.gave_up:
            return []
        self.low_since = self.low_since or now
        what = "no hashrate" if not hashrate_hs else f"hashrate {hashrate_hs / 1e12:.1f} TH/s, under {s['min_hashrate_ths']:g}"
        self.status = f"{what} for {int(now - self.low_since) // 60} min"
        if now - self.low_since < s["low_for_s"]:
            return []
        self.low_since = None
        if self.restarts < s["restarts_before_reboot"]:
            self.restarts += 1
            return [("restart", "alert", f"{what}: restarting the miner ({self.restarts} of {s['restarts_before_reboot']})")]
        last = self.state.get("watchdog_reboot_ts")
        if last and now - last < REBOOT_GUARD_S:
            self.gave_up = True
            self.status = f"gave up: {what} even after a reboot"
            return [(None, "alert", f"{what} even after restarts and a reboot; leaving it alone. Check the rig (Miner console, dmesg)")]
        self.state["watchdog_reboot_ts"] = now
        return [("reboot", "alert", f"{what} after {self.restarts} miner restarts: rebooting the rig")]
