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
        "miner": data.get("kind") or "wildrig",
        "version": data.get("version"),
        "algo": data.get("algo"),
        "pool": (data.get("connection") or {}).get("pool"),
        "hashrate_hs": total[0],
        # per card, in the miner's device order; card_bus says which PCI bus each one is on
        "card_hashrate_hs": [t[0] if t else None for t in threads],
        "card_bus": (data.get("hwmon") or {}).get("busID") or [],
        "card_accepted": results.get("shares_accepted") or [],
        "card_rejected": results.get("shares_rejected") or [],
        "shares_accepted": good,
        "shares_rejected": total_shares - good if None not in (good, total_shares) else None,
        "uptime_s": data.get("uptime") or (data.get("connection") or {}).get("uptime"),
    }


def attach_to_gpus(gpus, miner):
    """Copies the miner's per-card numbers onto the matching NVML cards, matched by PCI bus
    (miner order and NVML order can differ on multi-card boards), else by position."""
    if not miner.get("running"):
        return
    buses = miner.get("card_bus") or []
    for g in gpus:
        try:
            bus = int(g["bus_id"].split(":")[1], 16)
        except (AttributeError, IndexError, KeyError, ValueError):
            bus = None
        i = buses.index(bus) if bus in buses else (g["index"] if not buses else None)
        if i is None:
            continue
        for src, dst in (("card_hashrate_hs", "hashrate_hs"), ("card_accepted", "shares_accepted"),
                         ("card_rejected", "shares_rejected")):
            vals = miner.get(src) or []
            if i < len(vals):
                g[dst] = vals[i]


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
            "pool": "pool.pearlhash.xyz:9000",
            "hashrate_hs": sum(cards),
            "card_hashrate_hs": cards,
            "card_bus": [1, 2],
            "card_accepted": [60, 60],
            "card_rejected": [0, 0],
            "shares_accepted": 120,
            "shares_rejected": 0,
            "uptime_s": 3600,
        }
