"""Discord: alerts go to the channel you watch, routine updates (and the hourly summary) to the status channel."""
import asyncio
import json
import logging
import urllib.request

log = logging.getLogger("fizzhub")
DEFAULTS = {"alerts_url": "", "status_url": "", "status_every_min": 60}


def post_webhook(url, text):
    req = urllib.request.Request(url, data=json.dumps({"content": text[:1900]}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "fizzmineOS"})
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


class Notifier:
    def __init__(self, store, post=post_webhook):
        self.store, self.post = store, post

    def settings(self):
        return {**DEFAULTS, **(self.store.get_saved("settings", "discord") or {})}

    async def send(self, channel, text):
        """channel: "alerts" or "status". Returns an error message, or None when sent (or no webhook set)."""
        url = self.settings()[f"{channel}_url"]
        if not url:
            return None
        try:
            await asyncio.to_thread(self.post, url, text)
        except Exception as e:
            log.warning("discord %s webhook failed: %s", channel, e)
            return str(e)
        return None

    async def event(self, rig, level, msg):
        self.store.add_event(rig, level, msg)
        icon = "\N{POLICE CARS REVOLVING LIGHT}" if level == "alert" else "\N{WHITE HEAVY CHECK MARK}"
        await self.send("alerts" if level == "alert" else "status", f"{icon} **{rig}**: {msg}")


def summary(rigs):
    """The hourly status message, from /api/rigs-style rows."""
    live = [r for r in rigs if r["online"]]
    hr = sum((r["miner"].get("hashrate_hs") or 0) for r in live if r["miner"].get("running"))
    watts = sum(g.get("power_w") or 0 for r in live for g in r["gpus"])
    lines = [f"**fizzmineOS** · {len(live)}/{len(rigs)} rigs online · {hr / 1e12:.1f} TH/s · {watts:.0f} W"]
    for r in rigs:
        if not r["online"]:
            lines.append(f"• {r['rig']}: offline")
            continue
        m = r["miner"]
        temps = [g["temp_c"] for g in r["gpus"] if g.get("temp_c") is not None]
        rate = f"{(m.get('hashrate_hs') or 0) / 1e12:.1f} TH/s" if m.get("running") else (r["agent"].get("mode") or "idle")
        shares = f", {m['shares_accepted']}/{m.get('shares_rejected') or 0} shares" if m.get("running") and m.get("shares_accepted") is not None else ""
        lines.append(f"• {r['rig']}: {rate}, {sum(g.get('power_w') or 0 for g in r['gpus']):.0f} W, max {max(temps, default=0):.0f} °C{shares}")
    return "\n".join(lines)
