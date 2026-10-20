"""Reads hashrate and shares from the WildRig HTTP API (start the miner with --api-port)."""
import json
import random
import urllib.request


def parse_wildrig(data):
    hr = data.get("hashrate") or {}
    total = hr.get("total") or [None]
    threads = hr.get("threads") or []
    results = data.get("results") or {}
    good = results.get("shares_good")
    total_shares = results.get("shares_total")
    return {
        "running": True,
        "miner": "wildrig",
        "version": data.get("version"),
        "algo": data.get("algo"),
        "hashrate_hs": total[0],
        # one entry per card, in the miner's device order
        "card_hashrate_hs": [t[0] if t else None for t in threads],
        "shares_accepted": good,
        "shares_rejected": total_shares - good if None not in (good, total_shares) else None,
        "uptime_s": data.get("uptime") or (data.get("connection") or {}).get("uptime"),
    }


class WildRig:
    def __init__(self, port=60050, host="127.0.0.1"):
        self.url = f"http://{host}:{port}/"

    def read(self):
        try:
            with urllib.request.urlopen(self.url, timeout=2) as r:
                return parse_wildrig(json.load(r))
        except (OSError, ValueError):
            # miner not running (AI mode) or API not enabled
            return {"running": False, "miner": "wildrig"}


class FakeMiner:
    def read(self):
        cards = [random.uniform(90e12, 94e12) for _ in range(2)]
        return {
            "running": True,
            "miner": "wildrig",
            "version": "fake",
            "algo": "pearlhash",
            "hashrate_hs": sum(cards),
            "card_hashrate_hs": cards,
            "shares_accepted": 120,
            "shares_rejected": 0,
            "uptime_s": 3600,
        }
