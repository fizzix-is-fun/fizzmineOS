"""fizzmine agent: reports this rig's GPU and miner stats to the hub every few seconds.

usage: python -m fizzagent [--config /etc/fizzmine/agent.toml] [--fake]
"""
import argparse
import asyncio
import json
import logging
import socket
import time
import tomllib

import websockets

from .gpu import FakeGpus, NvmlGpus
from .miner import FakeMiner, WildRig

log = logging.getLogger("fizzagent")


def snapshot(rig, gpus, miner):
    return {"type": "stats", "rig": rig, "ts": time.time(), "gpus": gpus.read(), "miner": miner.read()}


async def run(cfg, fake):
    rig = cfg.get("rig") or socket.gethostname()
    url = f"{cfg['hub_url'].rstrip('/')}/ws/agent?rig={rig}&token={cfg['token']}"
    interval = cfg.get("interval_s", 5)
    gpus = FakeGpus() if fake else NvmlGpus()
    miner = FakeMiner() if fake else WildRig(cfg.get("wildrig_api_port", 60050))

    # websockets' connect() as an async iterator reconnects with backoff when the hub goes away
    async for ws in websockets.connect(url, open_timeout=10):
        log.info("connected to hub as %s", rig)
        try:
            while True:
                await ws.send(json.dumps(snapshot(rig, gpus, miner)))
                await asyncio.sleep(interval)
        except websockets.ConnectionClosed as e:
            log.warning("hub connection closed (%s), reconnecting", e)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="/etc/fizzmine/agent.toml")
    p.add_argument("--fake", action="store_true", help="report pretend GPUs and miner, for testing")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    asyncio.run(run(cfg, args.fake))


if __name__ == "__main__":
    main()
